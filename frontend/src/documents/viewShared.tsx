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

export function renderDefaultUnitContent(unit: Unit): ReactNode {
  if (!unit.lead_in) {
    return unit.text
  }
  if (!unit.text) {
    return <strong className="doc-unit-lead-in">{unit.lead_in}</strong>
  }
  return (
    <>
      <strong className="doc-unit-lead-in">{unit.lead_in}</strong> {unit.text}
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
  const effectiveEnd = locked.current && !end ? (language === 'de' ? 'heute' : 'Present') : end
  if (start && effectiveEnd) {
    return `${start}-${effectiveEnd}`
  }
  return start || effectiveEnd
}
