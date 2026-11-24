import type { ReactNode } from 'react'
import type { Contact, DocumentLanguage, EntryLocked, Unit } from './types.ts'

export type UnitDisplayKind =
  | 'headline'
  | 'paragraph'
  | 'bullet'
  | 'inline'
  | 'column'
  | 'entry-intro'
  | 'subject'
  | 'salutation'
  | 'closing'

export interface UnitRenderContext {
  unit: Unit
  kind: UnitDisplayKind
  splittable: boolean
  sectionId?: string
  entryId?: string
  index?: number
  defaultContent: ReactNode
}

export type RenderUnit = (context: UnitRenderContext) => ReactNode

const HYPHEN_TOKEN_RE =
  /([A-Za-z0-9\u00C0-\u024F]{2,24}-\s+(?:und|oder|sowie|and|or)\b|[A-Za-z0-9\u00C0-\u024F]{1,20}(?:-[A-Za-z0-9\u00C0-\u024F]{1,20}){1,3})/gu

function surroundingWhitespaceToken(text: string, start: number, end: number): string {
  let left = start
  while (left > 0 && !/\s/.test(text[left - 1])) left -= 1
  let right = end
  while (right < text.length && !/\s/.test(text[right])) right += 1
  return text.slice(left, right)
}

export function renderPrintText(text: string): ReactNode {
  if (!text.includes('-')) {
    return text
  }
  const nodes: ReactNode[] = []
  let lastIndex = 0
  HYPHEN_TOKEN_RE.lastIndex = 0
  let match: RegExpExecArray | null
  while ((match = HYPHEN_TOKEN_RE.exec(text)) !== null) {
    const token = match[0]
    const start = match.index
    const end = start + token.length
    const prevChar = start > 0 ? text[start - 1] : ''
    const nextChar = end < text.length ? text[end] : ''
    const validBoundary =
      (!prevChar || /[\s([{"'„“«»–—/]/.test(prevChar)) &&
      (!nextChar || /[\s.,;:!?)}\]"'”»«–—/]/.test(nextChar))
    if (!validBoundary || token.length > 36) {
      continue
    }
    const word = surroundingWhitespaceToken(text, start, end)
    if (word.includes('://') || word.includes('@')) {
      continue
    }
    if (start > lastIndex) {
      nodes.push(text.slice(lastIndex, start))
    }
    nodes.push(
      <span key={start} className="doc-nowrap">
        {token}
      </span>,
    )
    lastIndex = end
  }
  if (nodes.length === 0) {
    return text
  }
  if (lastIndex < text.length) {
    nodes.push(text.slice(lastIndex))
  }
  return nodes
}

export function renderDefaultUnitContent(unit: Unit): ReactNode {
  if (!unit.lead_in) {
    return renderPrintText(unit.text)
  }
  if (!unit.text) {
    return <strong className="doc-unit-lead-in">{renderPrintText(unit.lead_in)}</strong>
  }
  return (
    <>
      <strong className="doc-unit-lead-in">{renderPrintText(unit.lead_in)}</strong>{' '}
      {renderPrintText(unit.text)}
    </>
  )
}

export function renderUnitContent(
  renderUnit: RenderUnit | undefined,
  context: Omit<UnitRenderContext, 'defaultContent'>,
): ReactNode {
  const defaultContent = renderDefaultUnitContent(context.unit)
  if (!renderUnit) {
    return defaultContent
  }
  return renderUnit({ ...context, defaultContent })
}

export function safeContactHref(contact: Contact): string | null {
  const raw = contact.href.trim()
  if (raw) {
    if (/^(https?:|mailto:|tel:)/i.test(raw)) {
      return raw
    }
    return null
  }
  if (contact.kind === 'email') {
    const email = contact.text.trim()
    if (email.includes('@') && !/\s/.test(email)) {
      return `mailto:${email}`
    }
  }
  return null
}

export function formatEntryDates(locked: EntryLocked, language: DocumentLanguage): string {
  const start = locked.start.trim()
  const end = (locked.end ?? '').trim()
  const effectiveEnd = locked.current ? (language === 'de' ? 'heute' : 'Present') : end
  if (start && effectiveEnd && start !== effectiveEnd) {
    return `${start}-${effectiveEnd}`
  }
  return start || effectiveEnd
}
