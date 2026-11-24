import { useId, useMemo, useRef, useLayoutEffect } from 'react'
import { isReviewed, wordDiff } from './editing.ts'
import type { Finding, Unit } from './types.ts'

export function findingReplacement(finding: Finding): string {
  return finding.replacement ?? (finding.message === 'Review suggested a manual wording change.'
    ? finding.suggestion : '')
}

function sourceLabel(id: string): string {
  if (id === 'job') return 'Job posting'
  if (id.startsWith('cv:')) return 'Base CV'
  if (id.startsWith('evidence:')) return 'Career evidence'
  return 'Profile'
}

interface DocumentReviewProps {
  unit: Unit
  position: number
  total: number
  remaining: number
  sources?: Record<string, string>
  findings: Finding[]
  readOnly: boolean
  onNavigate: (direction: -1 | 1) => void
  onClose: () => void
  onEdit: () => void
  onAccept: () => void
  onRestore: () => void
  onApply: (text: string) => void
}

export function DocumentReview({
  unit, position, total, remaining, sources, findings, readOnly,
  onNavigate, onClose, onEdit, onAccept, onRestore, onApply,
}: DocumentReviewProps) {
  const titleId = useId()
  const heading = useRef<HTMLHeadingElement>(null)
  const original = unit.original_text
  const diff = useMemo(() => original == null ? null : wordDiff(original, unit.text), [original, unit.text])
  const citations = [...new Set(unit.sources)].filter(id => sources && Object.hasOwn(sources, id))
  const originalCitation = citations.find(id => sources?.[id] === [unit.lead_in, original].filter(Boolean).join(' '))
  const otherCitations = citations.filter(id => id !== originalCitation)

  useLayoutEffect(() => {
    heading.current?.focus()
  }, [unit.id])

  return (
    <section className="doc-review" aria-labelledby={titleId}>
      <header className="doc-review-header">
        <div>
          <h3 id={titleId} ref={heading} tabIndex={-1}>Review AI changes</h3>
          <p className="doc-review-progress" role="status">
            Change {position} of {total} · {remaining} to review{isReviewed(unit) ? ' · Reviewed' : ''}
          </p>
        </div>
        <button type="button" className="doc-editor-btn" onClick={onClose}>Close review</button>
      </header>
      {findings.length > 0 && <ul className="doc-review-findings" aria-label="Findings for this change">
        {findings.map(finding => {
          const replacement = findingReplacement(finding)
          return <li key={finding.id} className="doc-review-finding" data-severity={finding.severity}>
            <strong>{finding.severity === 'high' ? 'Needs attention' : 'Check this change'}</strong>
            {finding.claim && <p><q>{finding.claim}</q></p>}
            <p>{finding.message}</p>
            {finding.suggestion && !replacement.startsWith(finding.suggestion) && <p>{finding.suggestion}</p>}
            {replacement && <p><strong>Suggested wording</strong> {replacement}</p>}
            {!readOnly && replacement && replacement !== unit.text && (
              <button type="button" className="doc-editor-btn" onClick={() => onApply(replacement)}>
                Apply suggestion
              </button>
            )}
          </li>
        })}
      </ul>}
      <div className="doc-review-comparison">
        <div>
          <h4>{unit.ai_edited ? 'Your edit' : 'AI wording'}</h4>
          <p>{unit.lead_in && <><strong>{unit.lead_in}</strong>{' '}</>}
            {diff ? diff.after.map((part, index) => part.changed
              ? <ins key={index}>{part.text}</ins> : part.text) : unit.text}
          </p>
        </div>
        <div>
          <h4>{diff ? 'Original wording' : 'Cited sources'}</h4>
          {diff && <p>{unit.lead_in && <><strong>{unit.lead_in}</strong>{' '}</>}
            {diff.before.map((part, index) => part.changed
              ? <del key={index}>{part.text}</del> : part.text)}
          </p>}
          {otherCitations.length > 0 && <ul className="doc-review-sources" aria-label="Cited sources">
            {otherCitations.map(id => {
              const text = sources![id]
              return <li key={id}>{text.length > 650
                ? <details><summary>{sourceLabel(id)}</summary><p>{text}</p></details>
                : <><strong>{sourceLabel(id)}</strong><p>{text}</p></>}
              </li>
            })}
          </ul>}
          {citations.length === 0 && !diff && <p>No source text is available for this change.</p>}
          {citations.length > 0 && citations.length < new Set(unit.sources).size && <p>Some cited sources are unavailable.</p>}
        </div>
      </div>
      <footer className="doc-review-actions">
        <div className="doc-editor-toolbar-group">
          <button type="button" className="doc-editor-btn" disabled={position === 1}
            title="Previous change (Alt+Shift+Left)" aria-keyshortcuts="Alt+Shift+ArrowLeft" onClick={() => onNavigate(-1)}>Previous</button>
          <button type="button" className="doc-editor-btn" disabled={position === total}
            title="Next change (Alt+Shift+Right)" aria-keyshortcuts="Alt+Shift+ArrowRight" onClick={() => onNavigate(1)}>Next</button>
        </div>
        {!readOnly && <div className="doc-editor-toolbar-group">
          {original != null && <button type="button" className="doc-editor-btn" onClick={onRestore}>
            Restore original
          </button>}
          <button type="button" className="doc-editor-btn" onClick={onEdit}>Edit in document</button>
          <button type="button" className="doc-editor-btn doc-editor-btn--primary"
            title="Accept change (Alt+Enter)" aria-keyshortcuts="Alt+Enter" disabled={isReviewed(unit)} onClick={onAccept}>Accept change</button>
        </div>}
      </footer>
    </section>
  )
}
