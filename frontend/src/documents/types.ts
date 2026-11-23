export const CV_FORMAT = 'radius-cv-v2' as const
export const LETTER_FORMAT = 'radius-letter-v2' as const
export const DEFAULT_TEMPLATE = 'classic-serif-v1' as const

export const MAX_UNIT_TEXT_LENGTH = 1200
export const MAX_UNITS_PER_SECTION = 60
export const UNIT_ID_PATTERN = /^[a-z0-9-]{1,64}$/
const SECTION_ID_PATTERN = /^[a-z0-9_-]{1,64}$/
const ENTRY_ID_PATTERN = /^[a-zA-Z0-9_-]{1,100}$/
const NEWLINE_PATTERN = /[\r\n]/

export const DOCUMENT_LANGUAGES = ['de', 'en'] as const
export type DocumentLanguage = (typeof DOCUMENT_LANGUAGES)[number]

export const DOCUMENT_TEMPLATES = ['classic-serif-v1'] as const
export type DocumentTemplate = (typeof DOCUMENT_TEMPLATES)[number]

export const PAGE_FORMATS = ['a4', 'letter'] as const
export type PageFormat = (typeof PAGE_FORMATS)[number]

export const UNIT_ORIGINS = ['source', 'ai', 'user'] as const
export type UnitOrigin = (typeof UNIT_ORIGINS)[number]

export const LETTER_UNIT_ROLES = ['', 'motivation', 'experience', 'education', 'bring', 'close'] as const
export type LetterUnitRole = (typeof LETTER_UNIT_ROLES)[number]

export const CONTACT_KINDS = ['phone', 'email', 'location', 'link'] as const
export type ContactKind = (typeof CONTACT_KINDS)[number]

export const SECTION_KINDS = ['profile', 'experience', 'education', 'skills', 'list', 'text'] as const
export type SectionKind = (typeof SECTION_KINDS)[number]

export const SECTION_LAYOUTS = ['paragraphs', 'bullets', 'inline', 'columns'] as const
export type SectionLayout = (typeof SECTION_LAYOUTS)[number]

export const ENTRY_KINDS = ['work', 'study'] as const
export type EntryKind = (typeof ENTRY_KINDS)[number]

export const FINDING_DOCUMENTS = ['cv', 'letter'] as const
export type FindingDocumentKind = (typeof FINDING_DOCUMENTS)[number]

export const FINDING_SEVERITIES = ['high', 'medium', 'info'] as const
export type FindingSeverity = (typeof FINDING_SEVERITIES)[number]

export const FINDING_CODES = [
  'unsupported_number',
  'unsupported_term',
  'misattributed',
  'sensitive_fact',
  'placeholder',
  'language_mix',
  'repetition',
  'overflow',
  'extra_page',
  'review_grounding',
  'review_coverage',
  'review_style',
  'review_format',
] as const
export type FindingCode = (typeof FINDING_CODES)[number]

export interface Unit {
  id: string
  text: string
  lead_in: string
  origin: UnitOrigin
  sources: string[]
  role: LetterUnitRole
  hidden: boolean
}

export interface Contact {
  kind: ContactKind
  text: string
  href: string
}

export interface CvHeader {
  name: string
  contacts: Contact[]
}

export interface EntryLocked {
  company: string
  title: string
  location: string
  start: string
  end: string | null
  current: boolean
}

export interface Entry {
  id: string
  kind: EntryKind
  locked: EntryLocked
  label: string
  intro: Unit[]
  units: Unit[]
}

export interface Section {
  id: string
  kind: SectionKind
  title: string
  layout: SectionLayout
  units: Unit[]
  entries: Entry[]
}

export interface CvDocument {
  format: typeof CV_FORMAT
  language: DocumentLanguage
  template: DocumentTemplate
  page: PageFormat
  photo: boolean
  header: CvHeader
  headline: Unit
  sections: Section[]
}

export interface LetterSender {
  name: string
  lines: string[]
}

export interface LetterDocument {
  format: typeof LETTER_FORMAT
  language: DocumentLanguage
  template: DocumentTemplate
  page: PageFormat
  sender: LetterSender
  recipient_lines: string[]
  place_date: string
  subject: Unit
  salutation: Unit
  paragraphs: Unit[]
  closing: Unit
  signature: string
}

export type ApplicationDocument = CvDocument | LetterDocument

export interface Finding {
  id: string
  document: FindingDocumentKind
  unit_id: string | null
  code: FindingCode
  severity: FindingSeverity
  message: string
  suggestion: string
}

export class DocumentValidationError extends Error {
  readonly path: string

  constructor(path: string, message: string) {
    super(path ? `${path}: ${message}` : message)
    this.name = 'DocumentValidationError'
    this.path = path
  }
}

function fail(path: string, message: string): never {
  throw new DocumentValidationError(path, message)
}

function asRecord(value: unknown, path: string, allowedKeys: readonly string[]): Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    fail(path, 'must be a plain object')
  }
  const record = value as Record<string, unknown>
  const allowed = new Set(allowedKeys)
  for (const key of Object.keys(record)) {
    if (!allowed.has(key)) {
      fail(path ? `${path}.${key}` : key, 'unknown field is not allowed')
    }
  }
  return record
}

function asString(value: unknown, path: string, options?: { noNewlines?: boolean; maxLength?: number; nonEmpty?: boolean }): string {
  if (typeof value !== 'string') {
    fail(path, 'must be a string')
  }
  if (options?.noNewlines && NEWLINE_PATTERN.test(value)) {
    fail(path, 'must not contain newlines')
  }
  if (options?.maxLength !== undefined && value.length > options.maxLength) {
    fail(path, `must be at most ${options.maxLength} characters`)
  }
  if (options?.nonEmpty && value.trim().length === 0) {
    fail(path, 'must not be empty')
  }
  return value
}

function asOptionalString(
  value: unknown,
  path: string,
  defaultValue: string,
  options?: { noNewlines?: boolean; maxLength?: number },
): string {
  if (value === undefined) return defaultValue
  return asString(value, path, options)
}

function asBoolean(value: unknown, path: string): boolean {
  if (typeof value !== 'boolean') {
    fail(path, 'must be a boolean')
  }
  return value
}

function asOptionalBoolean(value: unknown, path: string, defaultValue: boolean): boolean {
  if (value === undefined) return defaultValue
  return asBoolean(value, path)
}

function asLiteral<const T extends readonly string[]>(value: unknown, path: string, allowed: T): T[number] {
  if (typeof value !== 'string' || !allowed.includes(value)) {
    fail(path, `must be one of: ${allowed.join(', ')}`)
  }
  return value as T[number]
}

function asArray(value: unknown, path: string): unknown[] {
  if (!Array.isArray(value)) {
    fail(path, 'must be an array')
  }
  return value
}

function asStringArray(value: unknown, path: string, options?: { noNewlines?: boolean; maxLength?: number }): string[] {
  return asArray(value, path).map((item, idx) => asString(item, `${path}[${idx}]`, options))
}

function parseUnit(value: unknown, path: string, seenUnitIds: Set<string>): Unit {
  const rec = asRecord(value, path, ['id', 'text', 'lead_in', 'origin', 'sources', 'role', 'hidden'])
  const id = asString(rec.id, `${path}.id`)
  if (!UNIT_ID_PATTERN.test(id)) {
    fail(`${path}.id`, 'must match /^[a-z0-9-]{1,64}$/')
  }
  if (seenUnitIds.has(id)) {
    fail(`${path}.id`, `duplicate unit id "${id}"`)
  }
  seenUnitIds.add(id)

  const text = asString(rec.text, `${path}.text`, {
    noNewlines: true,
    maxLength: MAX_UNIT_TEXT_LENGTH,
  })
  const lead_in = asOptionalString(rec.lead_in, `${path}.lead_in`, '', {
    noNewlines: true,
    maxLength: MAX_UNIT_TEXT_LENGTH,
  })
  const origin = asLiteral(rec.origin, `${path}.origin`, UNIT_ORIGINS)
  const sources =
    rec.sources === undefined
      ? []
      : asStringArray(rec.sources, `${path}.sources`, { noNewlines: true, maxLength: 256 })
  const role =
    rec.role === undefined ? '' : asLiteral(rec.role, `${path}.role`, LETTER_UNIT_ROLES)
  const hidden = asOptionalBoolean(rec.hidden, `${path}.hidden`, false)

  return { id, text, lead_in, origin, sources, role, hidden }
}

function parseContact(value: unknown, path: string): Contact {
  const rec = asRecord(value, path, ['kind', 'text', 'href'])
  const kind = asLiteral(rec.kind, `${path}.kind`, CONTACT_KINDS)
  const text = asString(rec.text, `${path}.text`, { noNewlines: true, maxLength: 300 })
  const href = asOptionalString(rec.href, `${path}.href`, '', { noNewlines: true, maxLength: 500 })
  return { kind, text, href }
}

function parseCvHeader(value: unknown, path: string): CvHeader {
  const rec = asRecord(value, path, ['name', 'contacts'])
  const name = asString(rec.name, `${path}.name`, { noNewlines: true, maxLength: 200 })
  const contacts = asArray(rec.contacts, `${path}.contacts`).map((item, idx) =>
    parseContact(item, `${path}.contacts[${idx}]`),
  )
  return { name, contacts }
}

function parseEntryLocked(value: unknown, path: string): EntryLocked {
  const rec = asRecord(value, path, ['company', 'title', 'location', 'start', 'end', 'current'])
  const end =
    rec.end === undefined || rec.end === null
      ? null
      : asString(rec.end, `${path}.end`, { noNewlines: true, maxLength: 64 })
  return {
    company: asString(rec.company, `${path}.company`, { noNewlines: true, maxLength: 300 }),
    title: asString(rec.title, `${path}.title`, { noNewlines: true, maxLength: 300 }),
    location: asOptionalString(rec.location, `${path}.location`, '', { noNewlines: true, maxLength: 300 }),
    start: asOptionalString(rec.start, `${path}.start`, '', { noNewlines: true, maxLength: 64 }),
    end,
    current: asOptionalBoolean(rec.current, `${path}.current`, false),
  }
}

function parseEntry(
  value: unknown,
  path: string,
  seenUnitIds: Set<string>,
  seenEntryIds: Set<string>,
): Entry {
  const rec = asRecord(value, path, ['id', 'kind', 'locked', 'label', 'intro', 'units'])
  const id = asString(rec.id, `${path}.id`)
  if (!ENTRY_ID_PATTERN.test(id)) {
    fail(`${path}.id`, 'must match /^[a-zA-Z0-9_-]{1,100}$/')
  }
  if (seenEntryIds.has(id)) {
    fail(`${path}.id`, `duplicate entry id "${id}"`)
  }
  seenEntryIds.add(id)

  const kind = asLiteral(rec.kind, `${path}.kind`, ENTRY_KINDS)
  const locked = parseEntryLocked(rec.locked, `${path}.locked`)
  const label = asOptionalString(rec.label, `${path}.label`, '', { noNewlines: true, maxLength: 200 })
  const intro =
    rec.intro === undefined
      ? []
      : asArray(rec.intro, `${path}.intro`).map((item, idx) =>
          parseUnit(item, `${path}.intro[${idx}]`, seenUnitIds),
        )
  const units =
    rec.units === undefined
      ? []
      : asArray(rec.units, `${path}.units`).map((item, idx) =>
          parseUnit(item, `${path}.units[${idx}]`, seenUnitIds),
        )
  return { id, kind, locked, label, intro, units }
}

function parseSection(
  value: unknown,
  path: string,
  seenUnitIds: Set<string>,
  seenSectionIds: Set<string>,
  seenEntryIds: Set<string>,
): Section {
  const rec = asRecord(value, path, ['id', 'kind', 'title', 'layout', 'units', 'entries'])
  const id = asString(rec.id, `${path}.id`)
  if (!SECTION_ID_PATTERN.test(id)) {
    fail(`${path}.id`, 'must match /^[a-z0-9_-]{1,64}$/')
  }
  if (seenSectionIds.has(id)) {
    fail(`${path}.id`, `duplicate section id "${id}"`)
  }
  seenSectionIds.add(id)

  const kind = asLiteral(rec.kind, `${path}.kind`, SECTION_KINDS)
  const title = asString(rec.title, `${path}.title`, { noNewlines: true, maxLength: 200 })
  const layout = asLiteral(rec.layout, `${path}.layout`, SECTION_LAYOUTS)
  const units =
    rec.units === undefined
      ? []
      : asArray(rec.units, `${path}.units`).map((item, idx) =>
          parseUnit(item, `${path}.units[${idx}]`, seenUnitIds),
        )
  const entries =
    rec.entries === undefined
      ? []
      : asArray(rec.entries, `${path}.entries`).map((item, idx) =>
          parseEntry(item, `${path}.entries[${idx}]`, seenUnitIds, seenEntryIds),
        )

  if (entries.length > 0 && kind !== 'experience' && kind !== 'education') {
    fail(`${path}.entries`, `entries are only allowed for experience or education sections, not "${kind}"`)
  }

  const totalUnits =
    units.length + entries.reduce((sum, entry) => sum + entry.intro.length + entry.units.length, 0)
  if (totalUnits > MAX_UNITS_PER_SECTION) {
    fail(path, `section exceeds maximum of ${MAX_UNITS_PER_SECTION} units (got ${totalUnits})`)
  }

  return { id, kind, title, layout, units, entries }
}

export function parseCvDocument(value: unknown): CvDocument {
  const rec = asRecord(value, '', [
    'format',
    'language',
    'template',
    'page',
    'photo',
    'header',
    'headline',
    'sections',
  ])

  const format = asLiteral(rec.format, 'format', [CV_FORMAT])
  const language = asLiteral(rec.language, 'language', DOCUMENT_LANGUAGES)
  const template = asLiteral(rec.template, 'template', DOCUMENT_TEMPLATES)
  const page = asLiteral(rec.page, 'page', PAGE_FORMATS)
  const photo = asBoolean(rec.photo, 'photo')
  const header = parseCvHeader(rec.header, 'header')

  const seenUnitIds = new Set<string>()
  const seenSectionIds = new Set<string>()
  const seenEntryIds = new Set<string>()

  const headline = parseUnit(rec.headline, 'headline', seenUnitIds)
  const sections = asArray(rec.sections, 'sections').map((item, idx) =>
    parseSection(item, `sections[${idx}]`, seenUnitIds, seenSectionIds, seenEntryIds),
  )

  return {
    format,
    language,
    template,
    page,
    photo,
    header,
    headline,
    sections,
  }
}

export function parseLetterDocument(value: unknown): LetterDocument {
  const rec = asRecord(value, '', [
    'format',
    'language',
    'template',
    'page',
    'sender',
    'recipient_lines',
    'place_date',
    'subject',
    'salutation',
    'paragraphs',
    'closing',
    'signature',
  ])

  const format = asLiteral(rec.format, 'format', [LETTER_FORMAT])
  const language = asLiteral(rec.language, 'language', DOCUMENT_LANGUAGES)
  const template = asLiteral(rec.template, 'template', DOCUMENT_TEMPLATES)
  const page = asLiteral(rec.page, 'page', PAGE_FORMATS)

  const senderRec = asRecord(rec.sender, 'sender', ['name', 'lines'])
  const sender: LetterSender = {
    name: asString(senderRec.name, 'sender.name', { noNewlines: true, maxLength: 200 }),
    lines: asStringArray(senderRec.lines, 'sender.lines', { noNewlines: true, maxLength: 300 }),
  }

  const recipient_lines = asStringArray(rec.recipient_lines, 'recipient_lines', {
    noNewlines: true,
    maxLength: 300,
  })
  const place_date = asString(rec.place_date, 'place_date', { noNewlines: true, maxLength: 200 })

  const seenUnitIds = new Set<string>()
  const subject = parseUnit(rec.subject, 'subject', seenUnitIds)
  const salutation = parseUnit(rec.salutation, 'salutation', seenUnitIds)
  const paragraphsRaw = asArray(rec.paragraphs, 'paragraphs')
  if (paragraphsRaw.length > MAX_UNITS_PER_SECTION) {
    fail('paragraphs', `exceeds maximum of ${MAX_UNITS_PER_SECTION} paragraphs`)
  }
  const paragraphs = paragraphsRaw.map((item, idx) =>
    parseUnit(item, `paragraphs[${idx}]`, seenUnitIds),
  )
  const closing = parseUnit(rec.closing, 'closing', seenUnitIds)
  const signature = asString(rec.signature, 'signature', { noNewlines: true, maxLength: 200 })

  return {
    format,
    language,
    template,
    page,
    sender,
    recipient_lines,
    place_date,
    subject,
    salutation,
    paragraphs,
    closing,
    signature,
  }
}

export function parseApplicationDocument(value: unknown): ApplicationDocument {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    fail('', 'document must be a plain object')
  }
  const format = (value as Record<string, unknown>).format
  if (format === CV_FORMAT) return parseCvDocument(value)
  if (format === LETTER_FORMAT) return parseLetterDocument(value)
  fail('format', `expected "${CV_FORMAT}" or "${LETTER_FORMAT}"`)
}

export function parseFinding(value: unknown): Finding {
  const rec = asRecord(value, '', ['id', 'document', 'unit_id', 'code', 'severity', 'message', 'suggestion'])
  const id = asString(rec.id, 'id', { noNewlines: true, nonEmpty: true, maxLength: 128 })
  const document = asLiteral(rec.document, 'document', FINDING_DOCUMENTS)
  let unit_id: string | null
  if (rec.unit_id === null || rec.unit_id === undefined) {
    unit_id = null
  } else {
    unit_id = asString(rec.unit_id, 'unit_id')
    if (!UNIT_ID_PATTERN.test(unit_id)) {
      fail('unit_id', 'must match /^[a-z0-9-]{1,64}$/ or be null')
    }
  }
  const code = asLiteral(rec.code, 'code', FINDING_CODES)
  const severity = asLiteral(rec.severity, 'severity', FINDING_SEVERITIES)
  const message = asString(rec.message, 'message', { maxLength: 2000 })
  const suggestion = asOptionalString(rec.suggestion, 'suggestion', '', { maxLength: 2000 })
  return { id, document, unit_id, code, severity, message, suggestion }
}

export function isCvDocument(doc: ApplicationDocument): doc is CvDocument {
  return doc.format === CV_FORMAT
}

export function isLetterDocument(doc: ApplicationDocument): doc is LetterDocument {
  return doc.format === LETTER_FORMAT
}
