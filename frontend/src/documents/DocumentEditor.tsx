import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from 'react'
import { createPortal } from 'react-dom'
import type { ClipboardEvent, CSSProperties, KeyboardEvent, ReactNode } from 'react'
import { DocumentReview, findingReplacement, findingTitle } from './DocumentReview.tsx'
import { CvView } from './CvView.tsx'
import { LetterView } from './LetterView.tsx'
import type { UnitDisplayKind, UnitRenderContext } from './viewShared.tsx'
import {
  countHiddenUnits,
  createHistory,
  isAiChange,
  isReviewed,
  restoreOriginal,
  reviewUnits,
  setUnitReviewed,
  pushHistory,
  redoHistory,
  removeUnit,
  sanitizeUnitText,
  splitUnit,
  toggleHidden,
  undoHistory,
  updateUnitText,
} from './editing.ts'
import type { HistoryState } from './editing.ts'
import { CV_FORMAT } from './types.ts'
import type {
  ApplicationDocument,
  Finding,
  FindingSeverity,
} from './types.ts'
import './document-editor.css'

export type DocumentEditorSavingState = 'idle' | 'saving' | 'saved' | 'error' | boolean

export interface DocumentEditorProps<T extends ApplicationDocument = ApplicationDocument> {
  value: T
  onChange: (doc: T) => void
  findings?: Finding[]
  photoUrl?: string | null
  onDownloadPdf?: () => void
  downloadDisabled?: boolean
  /** Called with the unit and the instruction typed into the prompt that opens at the unit. */
  onImproveUnit?: (unitId: string, instruction: string) => void
  improveDisabled?: boolean
  /** A pending AI rewrite of one unit, shown next to that unit. */
  suggestion?: UnitSuggestion | null
  onApplyFinding?: (finding: Finding) => void
  onDismissFinding?: (finding: Finding) => void
  saving?: DocumentEditorSavingState
  saveError?: string | null
  readOnly?: boolean
  className?: string
}

export interface UnitSuggestion {
  unitId: string
  text: string
  notes: string[]
  canAccept: boolean
  onAccept: () => void
  onDismiss: () => void
}

interface PendingFocus {
  unitId: string
  position: 'start' | 'end'
}

interface PageMetrics {
  pageCount: number
  guideOffsetsPx: number[]
  targetPages: number
  overflows: boolean
}

const PAGE_VERTICAL_MARGIN_MM = 16
const EDIT_COALESCE_MS = 450
const DEFAULT_PX_PER_MM = 96 / 25.4

type SheetFit = { scale: number; width: number; height: number }

const SEVERITY_RANK: Record<FindingSeverity, number> = {
  high: 3,
  medium: 2,
  info: 1,
}

let cachedPlaintextSupport: boolean | null = null

function supportsPlaintextOnly(): boolean {
  if (cachedPlaintextSupport !== null) return cachedPlaintextSupport
  if (typeof document === 'undefined') {
    cachedPlaintextSupport = false
    return false
  }
  const probe = document.createElement('div')
  probe.contentEditable = 'plaintext-only'
  cachedPlaintextSupport = probe.contentEditable === 'plaintext-only'
  return cachedPlaintextSupport
}

function highestSeverity(list: Finding[]): FindingSeverity | null {
  let best: FindingSeverity | null = null
  for (const item of list) {
    if (!best || SEVERITY_RANK[item.severity] > SEVERITY_RANK[best]) {
      best = item.severity
    }
  }
  return best
}

function getCaretOffset(container: HTMLElement): { offset: number; collapsed: boolean } {
  const selection = window.getSelection()
  if (!selection || selection.rangeCount === 0) {
    return { offset: (container.textContent ?? '').length, collapsed: true }
  }
  const range = selection.getRangeAt(0)
  if (!container.contains(range.startContainer)) {
    return { offset: (container.textContent ?? '').length, collapsed: true }
  }
  const preRange = document.createRange()
  preRange.selectNodeContents(container)
  preRange.setEnd(range.startContainer, range.startOffset)
  return {
    offset: preRange.toString().length,
    collapsed: range.collapsed,
  }
}

function setCaretPosition(container: HTMLElement, position: 'start' | 'end' | number): void {
  const selection = window.getSelection()
  if (!selection) return
  const range = document.createRange()
  const textNode = container.firstChild

  if (textNode && textNode.nodeType === Node.TEXT_NODE) {
    const len = textNode.textContent?.length ?? 0
    const offset =
      position === 'start'
        ? 0
        : position === 'end'
          ? len
          : Math.max(0, Math.min(len, position))
    range.setStart(textNode, offset)
    range.collapse(true)
  } else {
    range.selectNodeContents(container)
    range.collapse(position === 'start' || position === 0)
  }

  selection.removeAllRanges()
  selection.addRange(range)
}

const NARROW_QUERY = '(max-width: 640px)'

function useNarrowScreen(): boolean {
  const [narrow, setNarrow] = useState(() =>
    typeof window !== 'undefined' && typeof window.matchMedia === 'function' && window.matchMedia(NARROW_QUERY).matches)
  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return
    const query = window.matchMedia(NARROW_QUERY)
    const update = () => setNarrow(query.matches)
    update()
    query.addEventListener('change', update)
    return () => query.removeEventListener('change', update)
  }, [])
  return narrow
}

interface UnitPopoverProps {
  narrow: boolean
  label: string
  title: string
  className?: string
  onClose: () => void
  children: ReactNode
}

/**
 * Findings and AI prompts for one unit. On wide screens they open next to the unit; on a
 * phone the sheet is scaled down to fit, so they open as a readable bottom sheet instead.
 */
function UnitPopover({ narrow, label, title, className = '', onClose, children }: UnitPopoverProps) {
  if (!narrow) {
    return <div className={`doc-editor-finding-popover ${className}`} role="dialog" aria-label={label}>{children}</div>
  }
  return createPortal(
    <div className={`doc-editor-popover-sheet ${className}`} role="dialog" aria-label={label}>
      <div className="doc-editor-popover-sheet-head">
        <strong>{title}</strong>
        <button type="button" onClick={onClose}>Close</button>
      </div>
      {children}
    </div>,
    document.body,
  )
}

function FindingHead({ finding }: { finding: Finding }) {
  const { urgency, topic } = findingTitle(finding)
  return (
    <div className="doc-editor-finding-head">
      <span className={`doc-editor-doc-finding-severity doc-editor-doc-finding-severity--${finding.severity}`}>
        {urgency}
      </span>
      <span className="doc-editor-finding-code">{topic}</span>
    </div>
  )
}

function unitKindLabel(kind: UnitDisplayKind): string {
  switch (kind) {
    case 'headline':
      return 'headline'
    case 'bullet':
      return 'bullet'
    case 'inline':
      return 'skill item'
    case 'column':
      return 'list item'
    case 'entry-intro':
      return 'entry introduction'
    case 'subject':
      return 'subject line'
    case 'salutation':
      return 'salutation'
    case 'closing':
      return 'closing line'
    case 'paragraph':
    default:
      return 'paragraph'
  }
}

interface EditableUnitTextProps {
  unitId: string
  text: string
  kind: UnitDisplayKind
  splittable: boolean
  changeLabel?: string
  readOnly: boolean
  onTextChange: (unitId: string, nextText: string) => void
  onSplit: (unitId: string, offset: number) => void
  onRemoveEmpty: (unitId: string) => void
}

function EditableUnitText({
  unitId,
  text,
  kind,
  splittable,
  changeLabel,
  readOnly,
  onTextChange,
  onSplit,
  onRemoveEmpty,
}: EditableUnitTextProps) {
  const spanRef = useRef<HTMLSpanElement | null>(null)
  const contentEditableMode = readOnly
    ? undefined
    : supportsPlaintextOnly()
      ? ('plaintext-only' as const)
      : ('true' as const)

  useLayoutEffect(() => {
    const el = spanRef.current
    if (!el) return
    if (el.textContent !== text) {
      el.textContent = text
    }
  }, [text, readOnly])

  useLayoutEffect(() => {
    const el = spanRef.current
    if (!el || readOnly) return
    function preventRichInput(event: InputEvent) {
      if (/^(insertFrom(Paste|Drop|Yank)|insertHTML|insertLineBreak|insertParagraph|format)/.test(event.inputType)) {
        event.preventDefault()
      }
    }
    el.addEventListener('beforeinput', preventRichInput)
    return () => el.removeEventListener('beforeinput', preventRichInput)
  }, [readOnly])

  const handleInput = useCallback(() => {
    const el = spanRef.current
    if (!el || readOnly) return
    const raw = el.textContent ?? ''
    const sanitized = sanitizeUnitText(raw)
    if (sanitized !== raw || Array.from(el.childNodes).some(node => node.nodeType !== Node.TEXT_NODE)) {
      const { offset } = getCaretOffset(el)
      el.textContent = sanitized
      setCaretPosition(el, Math.min(offset, sanitized.length))
    }
    onTextChange(unitId, sanitized)
  }, [onTextChange, readOnly, unitId])

  const handlePaste = useCallback(
    (event: ClipboardEvent<HTMLSpanElement>) => {
      if (readOnly) return
      event.preventDefault()
      const el = spanRef.current
      if (!el) return
      const pasted = sanitizeUnitText(event.clipboardData.getData('text/plain'))
      const selection = window.getSelection()
      if (selection && selection.rangeCount > 0 && el.contains(selection.anchorNode)) {
        const range = selection.getRangeAt(0)
        range.deleteContents()
        const node = document.createTextNode(pasted)
        range.insertNode(node)
        range.setStartAfter(node)
        range.collapse(true)
        selection.removeAllRanges()
        selection.addRange(range)
      } else {
        el.textContent = `${el.textContent ?? ''}${pasted}`
      }
      handleInput()
    },
    [handleInput, readOnly],
  )

  const handleKeyDown = useCallback(
    (event: KeyboardEvent<HTMLSpanElement>) => {
      if (readOnly) return
      const el = spanRef.current
      if (!el) return

      if (event.key === 'Enter' && !event.ctrlKey && !event.metaKey && !event.altKey) {
        event.preventDefault()
        if (splittable) {
          const { offset } = getCaretOffset(el)
          onSplit(unitId, offset)
        }
        return
      }

      if (event.key === 'Backspace' && splittable && !event.ctrlKey && !event.metaKey && !event.altKey) {
        const currentText = el.textContent ?? ''
        const { offset, collapsed } = getCaretOffset(el)
        if (currentText.length === 0 && collapsed && offset === 0) {
          event.preventDefault()
          onRemoveEmpty(unitId)
        }
      }
    },
    [onRemoveEmpty, onSplit, readOnly, splittable, unitId],
  )

  if (readOnly) {
    return <span className="doc-editor-editable">{text}</span>
  }

  return (
    <span
      ref={spanRef}
      className="doc-editor-editable"
      contentEditable={contentEditableMode}
      suppressContentEditableWarning
      role="textbox"
      aria-multiline="false"
      aria-label={`Edit ${unitKindLabel(kind)}${changeLabel ? `, ${changeLabel}` : ''}`}
      data-editable-unit={unitId}
      data-placeholder={splittable ? 'Type here…' : 'Empty'}
      onInput={handleInput}
      onPaste={handlePaste}
      onDrop={event => event.preventDefault()}
      onKeyDown={handleKeyDown}
    />
  )
}

function formatSavingLabel(saving: DocumentEditorSavingState | undefined, saveError?: string | null): {
  text: string
  isError: boolean
} | null {
  if (saveError) return { text: saveError, isError: true }
  if (saving === true || saving === 'saving') return { text: 'Saving…', isError: false }
  if (saving === 'saved') return { text: 'Saved', isError: false }
  if (saving === 'error') return { text: 'Save failed', isError: true }
  return null
}

export function DocumentEditor<T extends ApplicationDocument = ApplicationDocument>({
  value,
  onChange,
  findings = [],
  photoUrl,
  onDownloadPdf,
  downloadDisabled,
  onImproveUnit,
  improveDisabled,
  suggestion,
  onApplyFinding,
  onDismissFinding,
  saving,
  saveError,
  readOnly = false,
  className,
}: DocumentEditorProps<T>) {
  const [history, setHistory] = useState<HistoryState<T>>(() => createHistory(value))
  const [showHidden, setShowHidden] = useState(false)
  const markAi = true
  const narrow = useNarrowScreen()
  const [prompt, setPrompt] = useState<{ unitId: string; text: string } | null>(null)
  const [reviewId, setReviewId] = useState<string | null>(null)
  const reviewButton = useRef<HTMLButtonElement>(null)
  const [openFindingUnitId, setOpenFindingUnitId] = useState<string | null>(null)
  // The unit being edited shows its tools; touch screens have no reliable hover for this.
  const [activeUnitId, setActiveUnitId] = useState<string | null>(null)
  const [metrics, setMetrics] = useState<PageMetrics>(() => ({
    pageCount: 1,
    guideOffsetsPx: [],
    targetPages: value.format === CV_FORMAT ? 2 : 1,
    overflows: false,
  }))

  const pageRef = useRef<HTMLDivElement | null>(null)
  const contentRef = useRef<HTMLDivElement | null>(null)
  const stageRef = useRef<HTMLDivElement | null>(null)
  const [fit, setFit] = useState<SheetFit>({ scale: 1, width: 0, height: 0 })
  const rootRef = useRef<HTMLDivElement | null>(null)
  const valueRef = useRef<T>(value)
  const historyRef = useRef<HistoryState<T>>(history)
  const lastTextEditRef = useRef<{ unitId: string; at: number } | null>(null)
  const pendingFocusRef = useRef<PendingFocus | null>(null)

  valueRef.current = value
  historyRef.current = history

  // Synchronize external document replacements (e.g. switching version or server refresh).
  useEffect(() => {
    if (value !== historyRef.current.present) {
      lastTextEditRef.current = null
      const nextHistory = createHistory(value)
      historyRef.current = nextHistory
      setHistory(nextHistory)
    }
  }, [value])

  const commitDocument = useCallback(
    (nextDoc: T, options?: { coalesceUnitId?: string }) => {
      if (nextDoc === valueRef.current) return
      const now = Date.now()
      let coalesce = false
      if (options?.coalesceUnitId) {
        const last = lastTextEditRef.current
        if (last && last.unitId === options.coalesceUnitId && now - last.at <= EDIT_COALESCE_MS) {
          coalesce = true
        }
        lastTextEditRef.current = { unitId: options.coalesceUnitId, at: now }
      } else {
        lastTextEditRef.current = null
      }
      const nextHistory = pushHistory(historyRef.current, nextDoc, { coalesce })
      historyRef.current = nextHistory
      setHistory(nextHistory)
      onChange(nextDoc)
    },
    [onChange],
  )

  const handleUndo = useCallback(() => {
    if (readOnly || historyRef.current.past.length === 0) return
    lastTextEditRef.current = null
    const nextHistory = undoHistory(historyRef.current)
    historyRef.current = nextHistory
    setHistory(nextHistory)
    onChange(nextHistory.present)
  }, [onChange, readOnly])

  const handleRedo = useCallback(() => {
    if (readOnly || historyRef.current.future.length === 0) return
    lastTextEditRef.current = null
    const nextHistory = redoHistory(historyRef.current)
    historyRef.current = nextHistory
    setHistory(nextHistory)
    onChange(nextHistory.present)
  }, [onChange, readOnly])

  const handleTextChange = useCallback(
    (unitId: string, nextText: string) => {
      const nextDoc = updateUnitText(valueRef.current, unitId, nextText, 'user')
      commitDocument(nextDoc, { coalesceUnitId: unitId })
    },
    [commitDocument],
  )

  const handleSplit = useCallback(
    (unitId: string, offset: number) => {
      const result = splitUnit(valueRef.current, unitId, offset)
      if (!result) return
      pendingFocusRef.current = { unitId: result.newUnitId, position: 'start' }
      commitDocument(result.document)
    },
    [commitDocument],
  )

  const handleRemoveEmpty = useCallback(
    (unitId: string) => {
      const root = rootRef.current
      let previousUnitId: string | null = null
      if (root) {
        const editables = Array.from(root.querySelectorAll<HTMLElement>('[data-editable-unit]'))
        const currentIndex = editables.findIndex((el) => el.dataset.editableUnit === unitId)
        if (currentIndex > 0) {
          previousUnitId = editables[currentIndex - 1].dataset.editableUnit ?? null
        }
      }
      const nextDoc = removeUnit(valueRef.current, unitId)
      if (nextDoc === valueRef.current) return
      if (previousUnitId) {
        pendingFocusRef.current = { unitId: previousUnitId, position: 'end' }
      }
      commitDocument(nextDoc)
    },
    [commitDocument],
  )

  const handleToggleHidden = useCallback(
    (unitId: string, nextHidden: boolean) => {
      const nextDoc = toggleHidden(valueRef.current, unitId, nextHidden)
      commitDocument(nextDoc)
    },
    [commitDocument],
  )

  // Apply queued focus move after split or empty-bullet removal.
  useEffect(() => {
    const pending = pendingFocusRef.current
    if (!pending) return
    pendingFocusRef.current = null
    const root = rootRef.current
    if (!root) return
    const target = root.querySelector<HTMLElement>(
      `[data-editable-unit="${CSS.escape(pending.unitId)}"]`,
    )
    if (!target) return
    target.focus()
    setCaretPosition(target, pending.position)
  }, [value])

  useEffect(() => {
    if (!activeUnitId) return
    const handlePointerDown = (event: PointerEvent) => {
      const target = event.target as HTMLElement | null
      if (!target?.closest('.doc-editor-unit, .doc-editor-popover-sheet')) setActiveUnitId(null)
    }
    document.addEventListener('pointerdown', handlePointerDown)
    return () => document.removeEventListener('pointerdown', handlePointerDown)
  }, [activeUnitId])

  // Close open finding popover on Escape or outside click.
  useEffect(() => {
    if (!openFindingUnitId) return
    const handlePointerDown = (event: PointerEvent) => {
      const target = event.target as HTMLElement | null
      if (!target?.closest('.doc-editor-finding-anchor, .doc-editor-popover-sheet')) {
        setOpenFindingUnitId(null)
      }
    }
    const handleKey = (event: globalThis.KeyboardEvent) => {
      if (event.key === 'Escape') {
        setOpenFindingUnitId(null)
      }
    }
    document.addEventListener('pointerdown', handlePointerDown)
    document.addEventListener('keydown', handleKey)
    return () => {
      document.removeEventListener('pointerdown', handlePointerDown)
      document.removeEventListener('keydown', handleKey)
    }
  }, [openFindingUnitId])

  const changes = useMemo(() => reviewUnits(value), [value])
  const remaining = changes.filter(unit => !isReviewed(unit)).length
  const reviewIndex = changes.findIndex(unit => unit.id === reviewId)
  const reviewUnit = changes[reviewIndex]

  useEffect(() => {
    if (reviewId && reviewIndex < 0) setReviewId(null)
  }, [reviewId, reviewIndex])

  function startReview() {
    const first = changes.find(unit => !isReviewed(unit)) ?? changes[0]
    if (first?.id === reviewId) rootRef.current?.querySelector<HTMLElement>('.doc-review h3')?.focus()
    else setReviewId(first?.id ?? null)
  }

  function closeReview() {
    setReviewId(null)
    reviewButton.current?.focus()
  }

  function navigateReview(direction: -1 | 1) {
    const next = changes[reviewIndex + direction]
    if (next) setReviewId(next.id)
  }

  function finishChange(action: 'accept' | 'restore') {
    if (!reviewUnit || readOnly) return
    const result = action === 'restore' ? restoreOriginal(value, reviewUnit.id)
      : setUnitReviewed(value, reviewUnit.id)
    commitDocument(result)
    const pending = [...changes.slice(reviewIndex + 1), ...changes.slice(0, reviewIndex)]
      .find(unit => !isReviewed(unit))
    if (pending) setReviewId(pending.id)
    else closeReview()
  }

  function editInDocument(unitId: string) {
    const target = rootRef.current?.querySelector<HTMLElement>(`[data-editable-unit="${CSS.escape(unitId)}"]`)
    if (!target) return
    target.focus()
    setCaretPosition(target, 'end')
  }

  function handleEditorKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if ((event.target as HTMLElement).closest('.doc-editor-ai-popover')) return
    if (reviewUnit && event.key === 'Escape' && (event.target as HTMLElement).matches('[data-editable-unit]')) {
      event.preventDefault()
      rootRef.current?.querySelector<HTMLElement>('.doc-review h3')?.focus()
      return
    }
    if (reviewUnit && event.altKey && !event.ctrlKey && !event.metaKey) {
      if (event.shiftKey && (event.key === 'ArrowLeft' || event.key === 'ArrowRight')) {
        event.preventDefault()
        navigateReview(event.key === 'ArrowLeft' ? -1 : 1)
      } else if (!event.shiftKey && event.key === 'Enter' && !isReviewed(reviewUnit)) {
        event.preventDefault()
        finishChange('accept')
      }
      return
    }
    if (readOnly || !(event.ctrlKey || event.metaKey) || event.altKey) return
    const key = event.key.toLowerCase()
    if (key === 'z') {
      event.preventDefault()
      if (event.shiftKey) handleRedo()
      else handleUndo()
    } else if (key === 'y' && !event.shiftKey) {
      event.preventDefault()
      handleRedo()
    }
  }

  // Measure DOM height to calculate page count, overflow status, and page break guides.
  const measurePages = useCallback(() => {
    const pageEl = pageRef.current
    const contentEl = contentRef.current
    if (!pageEl || !contentEl) return

    const pageWidthMm = value.page === 'letter' ? 215.9 : 210
    const pageHeightMm = value.page === 'letter' ? 279.4 : 297
    // Layout width ignores the fit-to-screen transform, matching scrollHeight below.
    const measuredWidth = pageEl.offsetWidth
    const pxPerMm = measuredWidth > 0 ? measuredWidth / pageWidthMm : DEFAULT_PX_PER_MM
    const printableHeightPx = (pageHeightMm - PAGE_VERTICAL_MARGIN_MM * 2) * pxPerMm
    const contentHeightPx = contentEl.scrollHeight

    const pageCount = Math.max(1, Math.ceil(Math.max(0, contentHeightPx - 1) / printableHeightPx))
    const targetPages = value.format === CV_FORMAT ? 2 : 1
    const guideOffsetsPx: number[] = []
    for (let pageIdx = 1; pageIdx < pageCount; pageIdx += 1) {
      guideOffsetsPx.push(PAGE_VERTICAL_MARGIN_MM * pxPerMm + pageIdx * printableHeightPx)
    }

    setMetrics((prev) => {
      if (
        prev.pageCount === pageCount &&
        prev.targetPages === targetPages &&
        prev.guideOffsetsPx.length === guideOffsetsPx.length &&
        prev.guideOffsetsPx.every((val, idx) => Math.abs(val - guideOffsetsPx[idx]) < 0.5)
      ) {
        return prev
      }
      return {
        pageCount,
        guideOffsetsPx,
        targetPages,
        overflows: pageCount > targetPages,
      }
    })
  }, [value.format, value.page])

  useEffect(() => {
    measurePages()
    const contentEl = contentRef.current
    const pageEl = pageRef.current
    if (!contentEl || !pageEl || typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(() => {
      measurePages()
    })
    observer.observe(contentEl)
    observer.observe(pageEl)
    return () => observer.disconnect()
  }, [measurePages, value, showHidden])

  // Keep the sheet at its true paper width so text wraps exactly as in the PDF,
  // and scale it down as a whole when the screen is narrower than the page.
  const fitSheet = useCallback(() => {
    const stageEl = stageRef.current
    const pageEl = pageRef.current
    if (!stageEl || !pageEl) return
    const style = window.getComputedStyle(stageEl)
    const available = stageEl.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight)
    const width = pageEl.offsetWidth
    const height = pageEl.offsetHeight
    const scale = width > 0 && available > 0 ? Math.min(1, available / width) : 1
    setFit((prev) =>
      Math.abs(prev.scale - scale) < 0.001 && prev.width === width && prev.height === height
        ? prev
        : { scale, width, height },
    )
  }, [])

  useLayoutEffect(() => {
    fitSheet()
    const stageEl = stageRef.current
    const pageEl = pageRef.current
    if (!stageEl || !pageEl || typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(() => fitSheet())
    observer.observe(stageEl)
    observer.observe(pageEl)
    return () => observer.disconnect()
  }, [fitSheet, value.format])

  const docKind = value.format === CV_FORMAT ? 'cv' : 'letter'

  const { findingsByUnit, documentFindings } = useMemo(() => {
    const byUnit = new Map<string, Finding[]>()
    const docLevel: Finding[] = []
    const relevant = findings.filter((item) => item.document === docKind)
    for (const item of relevant) {
      if (item.unit_id === null) {
        docLevel.push(item)
      } else {
        const list = byUnit.get(item.unit_id) ?? []
        list.push(item)
        byUnit.set(item.unit_id, list)
      }
    }
    return {
      findingsByUnit: byUnit,
      documentFindings: docLevel,
      totalFindings: relevant.length,
      topSeverity: highestSeverity(relevant),
    }
  }, [docKind, findings])

  const hiddenCount = useMemo(() => countHiddenUnits(value), [value])

  useEffect(() => {
    if (hiddenCount === 0 && showHidden) {
      setShowHidden(false)
    }
  }, [hiddenCount, showHidden])

  const renderEditableUnit = useCallback(
    (context: UnitRenderContext) => {
      const { unit, kind, splittable } = context
      const unitFindings = findingsByUnit.get(unit.id) ?? []
      const severity = highestSeverity(unitFindings)
      const isPopoverOpen = openFindingUnitId === unit.id
      const isBlock = kind !== 'inline' && kind !== 'bullet' && kind !== 'column'
      const showChange = markAi && isAiChange(unit) && !unit.hidden
      const changeLabel = showChange ? isReviewed(unit) ? 'AI change reviewed' : 'AI change to review' : undefined

      const wrapperClasses = [
        'doc-editor-unit',
        isBlock ? 'doc-editor-unit--block' : '',
        unit.hidden ? 'doc-editor-unit--hidden' : '',
        severity ? `doc-editor-unit--finding-${severity}` : '',
        showChange ? isReviewed(unit) ? 'doc-editor-unit--ai-reviewed' : 'doc-editor-unit--ai' : '',
        reviewId === unit.id ? 'doc-editor-unit--in-review' : '',
        activeUnitId === unit.id ? 'doc-editor-unit--active' : '',
      ]
        .filter(Boolean)
        .join(' ')

      return (
        <span
          className={wrapperClasses}
          title={changeLabel}
          onFocus={(event) => {
            if ((event.target as HTMLElement).closest('.doc-editor-unit') === event.currentTarget) setActiveUnitId(unit.id)
          }}
        >
          {unit.lead_in ? (
            <>
              <strong className="doc-unit-lead-in">{unit.lead_in}</strong>{' '}
            </>
          ) : null}
          <EditableUnitText
            unitId={unit.id}
            text={unit.text}
            kind={kind}
            splittable={splittable}
            changeLabel={changeLabel}
            readOnly={readOnly}
            onTextChange={handleTextChange}
            onSplit={handleSplit}
            onRemoveEmpty={handleRemoveEmpty}
          />
          {(!readOnly || unitFindings.length > 0) ? (
            <span className="doc-editor-unit-tools" contentEditable={false}>
              {unitFindings.length > 0 && severity ? (
                <span className="doc-editor-finding-anchor">
                  <button
                    type="button"
                    className={`doc-editor-finding-badge doc-editor-finding-badge--${severity}`}
                    aria-label={`${unitFindings.length} ${severity} review finding${unitFindings.length === 1 ? '' : 's'} on this ${unitKindLabel(kind)}`}
                    aria-expanded={isPopoverOpen}
                    title={unitFindings.map((item) => item.message).join(' | ')}
                    onClick={() =>
                      setOpenFindingUnitId((prev) => (prev === unit.id ? null : unit.id))
                    }
                  >
                    {unitFindings.length}
                  </button>
                  {isPopoverOpen ? (
                    <UnitPopover
                      narrow={narrow}
                      label="Unit review findings"
                      title={`Review notes on this ${unitKindLabel(kind)}`}
                      onClose={() => setOpenFindingUnitId(null)}
                    >
                      {unitFindings.map((finding) => (
                        <div key={finding.id} className="doc-editor-finding-item">
                          <FindingHead finding={finding} />
                          {finding.claim ? <p className="doc-editor-finding-claim"><q>{finding.claim}</q></p> : null}
                          <p className="doc-editor-finding-msg">{finding.message}</p>
                          {finding.suggestion ? (
                            <p className="doc-editor-finding-suggestion">
                              Suggestion: {finding.suggestion}
                            </p>
                          ) : null}
                          <div className="doc-editor-finding-actions">
                            {!readOnly && findingReplacement(finding) && (
                              <button type="button" className="doc-editor-primary-action" onClick={() => {
                                setOpenFindingUnitId(null)
                                commitDocument(updateUnitText(valueRef.current, unit.id, findingReplacement(finding)))
                              }}>Apply suggestion</button>
                            )}
                            {!readOnly && finding.suggestion && !findingReplacement(finding) && onApplyFinding && (
                              <button type="button" className="doc-editor-primary-action" onClick={() => { setOpenFindingUnitId(null); onApplyFinding(finding) }}>Edit with guidance</button>
                            )}
                            {!readOnly && onDismissFinding && (
                              <button type="button" onClick={() => onDismissFinding(finding)}>Dismiss</button>
                            )}
                          </div>
                        </div>
                      ))}
                    </UnitPopover>
                  ) : null}
                </span>
              ) : null}
              {!readOnly && !unit.hidden && onImproveUnit && <span className="doc-editor-finding-anchor">
                <button
                  type="button" className="doc-editor-unit-action" disabled={improveDisabled}
                  aria-label={`Improve ${unitKindLabel(kind)} with AI`}
                  aria-expanded={prompt?.unitId === unit.id}
                  onClick={() => {
                    setOpenFindingUnitId(null)
                    setPrompt(current => current?.unitId === unit.id ? null : { unitId: unit.id, text: '' })
                  }}
                >Improve with AI</button>
                {prompt?.unitId === unit.id ? (
                  <UnitPopover
                    narrow={narrow}
                    label="Improve with AI"
                    title={`Improve this ${unitKindLabel(kind)}`}
                    className="doc-editor-ai-popover"
                    onClose={() => setPrompt(null)}
                  >
                  <form
                    onSubmit={(event) => {
                      event.preventDefault()
                      const instruction = prompt.text.trim()
                      if (!instruction || improveDisabled) return
                      onImproveUnit(unit.id, instruction)
                      setPrompt(null)
                    }}
                    onKeyDown={(event) => {
                      if (event.key === 'Escape') { event.preventDefault(); setPrompt(null) }
                    }}
                  >
                    <label>
                      How should AI improve this {unitKindLabel(kind)}?
                      <textarea
                        value={prompt.text} maxLength={500} rows={3} autoFocus
                        onChange={(event) => setPrompt({ unitId: unit.id, text: event.target.value })}
                      />
                    </label>
                    <div className="doc-editor-finding-actions">
                      <button type="submit" className="doc-editor-primary-action" disabled={improveDisabled || !prompt.text.trim()}>Request suggestion</button>
                      <button type="button" onClick={() => setPrompt(null)}>Cancel</button>
                    </div>
                  </form>
                  </UnitPopover>
                ) : null}
                {suggestion?.unitId === unit.id ? (
                  <UnitPopover
                    narrow={narrow}
                    label="AI revision suggestion"
                    title="AI suggestion"
                    className="doc-editor-ai-popover"
                    onClose={suggestion.onDismiss}
                  >
                    <p className="doc-editor-finding-msg"><strong>Suggested</strong> {suggestion.text}</p>
                    {suggestion.notes.map((note) => <p key={note} className="doc-editor-finding-suggestion">{note}</p>)}
                    {!suggestion.canAccept ? (
                      <p className="doc-editor-finding-suggestion">This text changed after the request. Ask again to revise the current text.</p>
                    ) : null}
                    <div className="doc-editor-finding-actions">
                      <button type="button" className="doc-editor-primary-action" disabled={!suggestion.canAccept} onClick={suggestion.onAccept}>Accept suggestion</button>
                      <button type="button" onClick={suggestion.onDismiss}>Dismiss</button>
                    </div>
                  </UnitPopover>
                ) : null}
              </span>}
              {!readOnly ? (
                <button
                  type="button"
                  className="doc-editor-unit-action"
                  aria-label={unit.hidden ? `Restore ${unitKindLabel(kind)}` : `Hide ${unitKindLabel(kind)}`}
                  onClick={() => handleToggleHidden(unit.id, !unit.hidden)}
                >
                  {unit.hidden ? 'Restore' : 'Hide'}
                </button>
              ) : null}
            </span>
          ) : null}
        </span>
      )
    },
    [
      findingsByUnit,
      commitDocument,
      markAi,
      reviewId,
      improveDisabled,
      onApplyFinding,
      onDismissFinding,
      onImproveUnit,
      prompt,
      suggestion,
      handleRemoveEmpty,
      handleSplit,
      handleTextChange,
      handleToggleHidden,
      activeUnitId,
      narrow,
      openFindingUnitId,
      readOnly,
    ],
  )

  const pageGuidesOverlay =
    metrics.guideOffsetsPx.length > 0 ? (
      <div className="doc-editor-page-guides" aria-hidden="true">
        {metrics.guideOffsetsPx.map((offsetPx, index) => {
          const nextPageNumber = index + 2
          const isOverflowBreak = nextPageNumber > metrics.targetPages
          return (
            <div
              key={`guide-${nextPageNumber}`}
              className={`doc-editor-page-guide${isOverflowBreak ? ' doc-editor-page-guide--overflow' : ''}`}
              style={{ top: `${offsetPx}px` }}
            >
              <span className="doc-editor-page-guide-label">
                Page {nextPageNumber}
                {isOverflowBreak ? ' (overflow)' : ''}
              </span>
            </div>
          )
        })}
      </div>
    ) : null

  const savingBadge = formatSavingLabel(saving, saveError)
  const pageCountLabel = `${metrics.pageCount} ${metrics.pageCount === 1 ? 'page' : 'pages'}`
  const overflowLabel =
    metrics.overflows
      ? `${pageCountLabel} — exceeds ${metrics.targetPages}-page ${docKind === 'cv' ? 'CV' : 'letter'} target`
      : pageCountLabel

  return (
    <div
      ref={rootRef}
      className={`doc-editor${className ? ` ${className}` : ''}`}
      role="region"
      aria-label={docKind === 'cv' ? 'CV document editor' : 'Cover letter document editor'}
      onKeyDown={handleEditorKeyDown}
    >
      <div className="doc-editor-toolbar" role="toolbar" aria-label="Document editor controls">
        <div className="doc-editor-toolbar-group">
          {!readOnly ? (
            <>
              <button
                type="button"
                className="doc-editor-btn"
                onClick={handleUndo}
                disabled={history.past.length === 0}
                aria-label="Undo last edit"
                aria-keyshortcuts="Control+Z Meta+Z"
                title="Undo (Ctrl+Z)"
              >
                Undo
              </button>
              <button
                type="button"
                className="doc-editor-btn"
                onClick={handleRedo}
                disabled={history.future.length === 0}
                aria-label="Redo edit"
                aria-keyshortcuts="Control+Shift+Z Meta+Shift+Z"
                title="Redo (Ctrl+Shift+Z)"
              >
                Redo
              </button>
            </>
          ) : null}

          {changes.length > 0 && <>
            <button type="button" className="doc-editor-btn" ref={reviewButton} aria-expanded={Boolean(reviewUnit)}
              onClick={reviewUnit ? closeReview : startReview}>
              Review AI changes{remaining > 0 ? ` (${remaining})` : ''}
            </button>
          </>}

          {hiddenCount > 0 ? (
            <button
              type="button"
              className={`doc-editor-btn${showHidden ? ' doc-editor-btn--active' : ''}`}
              aria-pressed={showHidden}
              aria-label={
                showHidden
                  ? `Hide ${hiddenCount} hidden units`
                  : `Show ${hiddenCount} hidden units to restore`
              }
              onClick={() => setShowHidden((prev) => !prev)}
            >
              {showHidden ? `Showing hidden (${hiddenCount})` : `Hidden (${hiddenCount})`}
            </button>
          ) : null}
        </div>

        <div className="doc-editor-toolbar-group">
          <span
            className={`doc-editor-pill${metrics.overflows ? ' doc-editor-pill--warning' : ''}`}
            role="status"
            aria-live="polite"
            data-page-count={metrics.pageCount}
            data-page-overflow={metrics.overflows ? 'true' : 'false'}
          >
            {overflowLabel}
          </span>

          {savingBadge ? (
            <span
              className={`doc-editor-save-state${savingBadge.isError ? ' doc-editor-save-state--error' : ''}`}
              role="status"
              aria-live="polite"
            >
              {savingBadge.text}
            </span>
          ) : null}

          {onDownloadPdf ? (
            <button
              type="button"
              className="doc-editor-btn doc-editor-btn--primary"
              onClick={onDownloadPdf}
              disabled={downloadDisabled}
            >
              Download PDF
            </button>
          ) : null}
        </div>
      </div>

      {reviewUnit && (
        <DocumentReview
          unit={reviewUnit}
          position={reviewIndex + 1}
          total={changes.length}
          remaining={remaining}
          sources={value.review_sources}
          findings={[...(findingsByUnit.get(reviewUnit.id) ?? [])]
            .sort((a, b) => SEVERITY_RANK[b.severity] - SEVERITY_RANK[a.severity])}
          readOnly={readOnly}
          onNavigate={navigateReview}
          onClose={closeReview}
          onEdit={() => editInDocument(reviewUnit.id)}
          onAccept={() => finishChange('accept')}
          onRestore={() => finishChange('restore')}
          onApply={text => {
            commitDocument(updateUnitText(value, reviewUnit.id, text))
            editInDocument(reviewUnit.id)
          }}
        />
      )}

      {documentFindings.length > 0 ? (
        <details
          className="doc-editor-doc-findings"
          role="region"
          aria-label="Document-level review findings"
        >
          <summary>{documentFindings.length} review {documentFindings.length === 1 ? 'note' : 'notes'}</summary>
          {documentFindings.map((finding) => (
            <p key={finding.id} className="doc-editor-doc-finding">
              <span
                className={`doc-editor-doc-finding-severity doc-editor-doc-finding-severity--${finding.severity}`}
              >
                {findingTitle(finding).urgency}
              </span>
              <span>{finding.message}</span>
              {finding.suggestion ? <em> — {finding.suggestion}</em> : null}
              {!readOnly && onDismissFinding && <button type="button" onClick={() => onDismissFinding(finding)}>Dismiss</button>}
            </p>
          ))}
        </details>
      ) : null}

      <div
        ref={stageRef}
        className="doc-editor-stage"
        style={{ '--doc-editor-scale': fit.scale } as CSSProperties}
      >
        <div
          className="doc-editor-sheet-frame"
          style={fit.scale < 1 ? { width: fit.width * fit.scale, height: fit.height * fit.scale } : undefined}
        >
        {value.format === CV_FORMAT ? (
          <CvView
            document={value}
            photoUrl={photoUrl}
            renderUnit={renderEditableUnit}
            showHidden={showHidden}
            className="doc-editor-sheet"
            pageRef={pageRef}
            contentRef={contentRef}
            overlay={pageGuidesOverlay}
          />
        ) : (
          <LetterView
            document={value}
            renderUnit={renderEditableUnit}
            showHidden={showHidden}
            className="doc-editor-sheet"
            pageRef={pageRef}
            contentRef={contentRef}
            overlay={pageGuidesOverlay}
          />
        )}
        </div>
      </div>
    </div>
  )
}
