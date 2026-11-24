import {
  CV_FORMAT,
  INVALID_DOCUMENT_CHARACTER,
  MAX_ENTRY_INTRO_UNITS,
  MAX_LETTER_PARAGRAPHS,
  MAX_UNITS_PER_SECTION,
  MAX_UNIT_TEXT_LENGTH,
  UNIT_ID_PATTERN,
} from './types.ts'
import type {
  ApplicationDocument,
  CvDocument,
  Entry,
  LetterDocument,
  Section,
  Unit,
  UnitOrigin,
} from './types.ts'

export interface SplitUnitResult<T extends ApplicationDocument> {
  document: T
  newUnitId: string
}

export interface HistoryState<T> {
  past: T[]
  present: T
  future: T[]
}

const DEFAULT_MAX_HISTORY = 100

export function sanitizeUnitText(text: string): string {
  const compatible = text.replace(/\r\n|\r|\n/g, ' ')
    .replace(new RegExp(INVALID_DOCUMENT_CHARACTER, 'gu'), '')
  return Array.from(compatible).slice(0, MAX_UNIT_TEXT_LENGTH).join('')
}

export function walkUnits(doc: ApplicationDocument): Unit[] {
  const units: Unit[] = []
  if (doc.format === CV_FORMAT) {
    units.push(doc.headline)
    for (const section of doc.sections) {
      units.push(...section.units)
      for (const entry of section.entries) {
        units.push(...entry.intro)
        units.push(...entry.units)
      }
    }
  } else {
    units.push(doc.subject, doc.salutation, ...doc.paragraphs, doc.closing)
  }
  return units
}

export function collectUnitIds(doc: ApplicationDocument): Set<string> {
  return new Set(walkUnits(doc).map((unit) => unit.id))
}

export function findUnitById(doc: ApplicationDocument, unitId: string): Unit | null {
  for (const unit of walkUnits(doc)) {
    if (unit.id === unitId) return unit
  }
  return null
}

export function countHiddenUnits(doc: ApplicationDocument): number {
  let count = 0
  for (const unit of walkUnits(doc)) {
    if (unit.hidden) count += 1
  }
  return count
}

export function newUnitId(doc: ApplicationDocument, prefix = 'u'): string {
  const existing = collectUnitIds(doc)
  const sanitized = prefix
    .toLowerCase()
    .replace(/[^a-z0-9-]+/g, '-')
    .replace(/-+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 48)
  const base = sanitized || 'u'

  for (let counter = 1; counter < 1_000_000; counter += 1) {
    const candidate = `${base}-${counter}`
    if (UNIT_ID_PATTERN.test(candidate) && !existing.has(candidate)) {
      return candidate
    }
  }
  throw new Error('Unable to allocate a unique unit id')
}

function mapUnitsInCv(
  doc: CvDocument,
  updater: (unit: Unit) => Unit,
): CvDocument {
  let changed = false

  const nextHeadline = updater(doc.headline)
  if (nextHeadline !== doc.headline) changed = true

  const nextSections: Section[] = doc.sections.map((section) => {
    let sectionChanged = false

    const nextSectionUnits = section.units.map((unit) => {
      const nextUnit = updater(unit)
      if (nextUnit !== unit) sectionChanged = true
      return nextUnit
    })

    const nextEntries: Entry[] = section.entries.map((entry) => {
      let entryChanged = false
      const nextIntro = entry.intro.map((unit) => {
        const nextUnit = updater(unit)
        if (nextUnit !== unit) entryChanged = true
        return nextUnit
      })
      const nextEntryUnits = entry.units.map((unit) => {
        const nextUnit = updater(unit)
        if (nextUnit !== unit) entryChanged = true
        return nextUnit
      })
      if (!entryChanged) return entry
      sectionChanged = true
      return { ...entry, intro: nextIntro, units: nextEntryUnits }
    })

    if (!sectionChanged) return section
    changed = true
    return { ...section, units: nextSectionUnits, entries: nextEntries }
  })

  if (!changed) return doc
  return { ...doc, headline: nextHeadline, sections: nextSections }
}

function mapUnitsInLetter(
  doc: LetterDocument,
  updater: (unit: Unit) => Unit,
): LetterDocument {
  let changed = false
  const nextSubject = updater(doc.subject)
  if (nextSubject !== doc.subject) changed = true

  const nextSalutation = updater(doc.salutation)
  if (nextSalutation !== doc.salutation) changed = true

  const nextParagraphs = doc.paragraphs.map((unit) => {
    const nextUnit = updater(unit)
    if (nextUnit !== unit) changed = true
    return nextUnit
  })

  const nextClosing = updater(doc.closing)
  if (nextClosing !== doc.closing) changed = true

  if (!changed) return doc
  return {
    ...doc,
    subject: nextSubject,
    salutation: nextSalutation,
    paragraphs: nextParagraphs,
    closing: nextClosing,
  }
}

function mapDocumentUnits<T extends ApplicationDocument>(
  doc: T,
  updater: (unit: Unit) => Unit,
): T {
  if (doc.format === CV_FORMAT) {
    return mapUnitsInCv(doc, updater) as T
  }
  return mapUnitsInLetter(doc, updater) as T
}

export function updateUnitText<T extends ApplicationDocument>(
  doc: T,
  unitId: string,
  text: string,
  origin: UnitOrigin = 'user',
): T {
  const sanitized = sanitizeUnitText(text)
  return mapDocumentUnits(doc, (unit) => {
    if (unit.id !== unitId) return unit
    if (unit.text === sanitized && unit.origin === origin) return unit
    return { ...unit, text: sanitized, origin }
  })
}

export function toggleHidden<T extends ApplicationDocument>(
  doc: T,
  unitId: string,
  hidden?: boolean,
): T {
  return mapDocumentUnits(doc, (unit) => {
    if (unit.id !== unitId) return unit
    const nextHidden = hidden === undefined ? !unit.hidden : hidden
    if (unit.hidden === nextHidden) return unit
    return { ...unit, hidden: nextHidden }
  })
}

function countSectionUnits(section: Section): number {
  return (
    section.units.length +
    section.entries.reduce((sum, entry) => sum + entry.intro.length + entry.units.length, 0)
  )
}

function splitInList(
  list: Unit[],
  unitId: string,
  offset: number,
  createdId: string,
): Unit[] | null {
  const index = list.findIndex((unit) => unit.id === unitId)
  if (index === -1) return null

  const target = list[index]
  const clamped = Math.max(0, Math.min(target.text.length, Math.trunc(offset)))
  const before = sanitizeUnitText(target.text.slice(0, clamped).trimEnd())
  const after = sanitizeUnitText(target.text.slice(clamped).trimStart())

  const updatedUnit: Unit = {
    ...target,
    text: before,
    origin: 'user',
  }
  const insertedUnit: Unit = {
    id: createdId,
    text: after,
    lead_in: '',
    origin: 'user',
    sources: [],
    role: target.role,
    hidden: false,
  }

  return [...list.slice(0, index), updatedUnit, insertedUnit, ...list.slice(index + 1)]
}

export function splitUnit<T extends ApplicationDocument>(
  doc: T,
  unitId: string,
  offset: number,
): SplitUnitResult<T> | null {
  const createdId = newUnitId(doc, 'u')

  if (doc.format === CV_FORMAT) {
    for (let sIdx = 0; sIdx < doc.sections.length; sIdx += 1) {
      const section = doc.sections[sIdx]
      const sectionUnitCount = countSectionUnits(section)

      const splitSectionUnits = splitInList(section.units, unitId, offset, createdId)
      if (splitSectionUnits) {
        if (sectionUnitCount >= MAX_UNITS_PER_SECTION) return null
        const nextSections = doc.sections.slice()
        nextSections[sIdx] = { ...section, units: splitSectionUnits }
        return {
          document: { ...doc, sections: nextSections } as T,
          newUnitId: createdId,
        }
      }

      for (let eIdx = 0; eIdx < section.entries.length; eIdx += 1) {
        const entry = section.entries[eIdx]
        const splitIntro = splitInList(entry.intro, unitId, offset, createdId)
        if (splitIntro) {
          if (entry.intro.length >= MAX_ENTRY_INTRO_UNITS || sectionUnitCount >= MAX_UNITS_PER_SECTION) return null
          const nextEntries = section.entries.slice()
          nextEntries[eIdx] = { ...entry, intro: splitIntro }
          const nextSections = doc.sections.slice()
          nextSections[sIdx] = { ...section, entries: nextEntries }
          return {
            document: { ...doc, sections: nextSections } as T,
            newUnitId: createdId,
          }
        }

        const splitEntryUnits = splitInList(entry.units, unitId, offset, createdId)
        if (splitEntryUnits) {
          if (sectionUnitCount >= MAX_UNITS_PER_SECTION) return null
          const nextEntries = section.entries.slice()
          nextEntries[eIdx] = { ...entry, units: splitEntryUnits }
          const nextSections = doc.sections.slice()
          nextSections[sIdx] = { ...section, entries: nextEntries }
          return {
            document: { ...doc, sections: nextSections } as T,
            newUnitId: createdId,
          }
        }
      }
    }
    return null
  }

  if (doc.paragraphs.length >= MAX_LETTER_PARAGRAPHS) return null
  const splitParagraphs = splitInList(doc.paragraphs, unitId, offset, createdId)
  if (!splitParagraphs) return null
  return {
    document: { ...doc, paragraphs: splitParagraphs } as T,
    newUnitId: createdId,
  }
}

export function removeUnit<T extends ApplicationDocument>(doc: T, unitId: string): T {
  if (doc.format === CV_FORMAT) {
    let changed = false
    const nextSections = doc.sections.map((section) => {
      let sectionChanged = false
      const nextSectionUnits = section.units.filter((unit) => unit.id !== unitId)
      if (nextSectionUnits.length !== section.units.length) sectionChanged = true

      const nextEntries = section.entries.map((entry) => {
        const nextIntro = entry.intro.filter((unit) => unit.id !== unitId)
        const nextEntryUnits = entry.units.filter((unit) => unit.id !== unitId)
        if (nextIntro.length === entry.intro.length && nextEntryUnits.length === entry.units.length) {
          return entry
        }
        sectionChanged = true
        return { ...entry, intro: nextIntro, units: nextEntryUnits }
      })

      if (!sectionChanged) return section
      changed = true
      return { ...section, units: nextSectionUnits, entries: nextEntries }
    })
    if (!changed) return doc
    return { ...doc, sections: nextSections } as T
  }

  const nextParagraphs = doc.paragraphs.filter((unit) => unit.id !== unitId)
  if (nextParagraphs.length === doc.paragraphs.length) return doc
  return { ...doc, paragraphs: nextParagraphs } as T
}

export function createHistory<T>(initial: T): HistoryState<T> {
  return { past: [], present: initial, future: [] }
}

export function pushHistory<T>(
  history: HistoryState<T>,
  next: T,
  options?: { coalesce?: boolean; maxHistory?: number },
): HistoryState<T> {
  if (Object.is(history.present, next)) return history
  if (options?.coalesce && history.past.length > 0) {
    return { past: history.past, present: next, future: [] }
  }
  const maxHistory = options?.maxHistory ?? DEFAULT_MAX_HISTORY
  const nextPast =
    history.past.length >= maxHistory
      ? [...history.past.slice(history.past.length - maxHistory + 1), history.present]
      : [...history.past, history.present]
  return { past: nextPast, present: next, future: [] }
}

export function undoHistory<T>(history: HistoryState<T>): HistoryState<T> {
  if (history.past.length === 0) return history
  const previous = history.past[history.past.length - 1]
  return {
    past: history.past.slice(0, -1),
    present: previous,
    future: [history.present, ...history.future],
  }
}

export function redoHistory<T>(history: HistoryState<T>): HistoryState<T> {
  if (history.future.length === 0) return history
  const next = history.future[0]
  return {
    past: [...history.past, history.present],
    present: next,
    future: history.future.slice(1),
  }
}
