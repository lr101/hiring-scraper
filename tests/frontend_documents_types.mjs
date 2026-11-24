import assert from 'node:assert/strict'
import {
  DocumentValidationError,
  MAX_UNITS_PER_SECTION,
  MAX_UNIT_TEXT_LENGTH,
  isCvDocument,
  isLetterDocument,
  parseApplicationDocument,
  parseCvDocument,
  parseFinding,
  parseLetterDocument,
} from '../frontend/src/documents/types.ts'

function makeUnit(id, text, overrides = {}) {
  return {
    id,
    text,
    lead_in: '',
    origin: 'source',
    sources: [],
    role: '',
    hidden: false,
    ...overrides,
  }
}

function makeSampleCv() {
  return {
    format: 'radius-cv-v2',
    language: 'en',
    template: 'classic-serif-v1',
    page: 'a4',
    photo: true,
    header: {
      name: 'Alex Example',
      contacts: [
        { kind: 'phone', text: '+49 170 0000000', href: 'tel:+491700000000' },
        { kind: 'location', text: 'Musterstadt, Germany', href: '' },
        { kind: 'email', text: 'alex@example.test', href: 'mailto:alex@example.test' },
        { kind: 'link', text: 'example.test/alex', href: 'https://example.test/alex' },
      ],
    },
    headline: makeUnit('u-headline', 'Senior Systems Engineer'),
    sections: [
      {
        id: 's-profile',
        kind: 'profile',
        title: 'Profile',
        layout: 'paragraphs',
        units: [
          makeUnit(
            'u-profile-1',
            'Systems engineer focused on reliable backend platforms and clear technical writing.',
            { origin: 'ai', sources: ['cv:profile:p1'] },
          ),
        ],
        entries: [],
      },
      {
        id: 's-experience',
        kind: 'experience',
        title: 'Professional Experience',
        layout: 'bullets',
        units: [],
        entries: [
          {
            id: 'work-1',
            kind: 'work',
            locked: {
              company: 'Example GmbH',
              title: 'Lead Platform Engineer',
              location: 'Musterstadt',
              start: '2022',
              end: '2025',
              current: false,
            },
            label: '',
            intro: [makeUnit('u-work-1-i1', 'Selected deliveries:')],
            units: [
              makeUnit(
                'u-work-1-b1',
                'Reduced deployment lead time across six internal services.',
                { lead_in: 'Platform CI:', origin: 'ai', sources: ['cv:work-1:b1'] },
              ),
            ],
          },
        ],
      },
      {
        id: 's-skills',
        kind: 'skills',
        title: 'Technical Skills',
        layout: 'inline',
        units: [
          makeUnit('u-skill-1', 'Python'),
          makeUnit('u-skill-2', 'PostgreSQL'),
          makeUnit('u-skill-3', 'TypeScript'),
        ],
        entries: [],
      },
      {
        id: 's-languages',
        kind: 'list',
        title: 'Languages',
        layout: 'columns',
        units: [
          makeUnit('u-lang-1', 'Native', { lead_in: 'German:' }),
          makeUnit('u-lang-2', 'Fluent', { lead_in: 'English:' }),
        ],
        entries: [],
      },
    ],
  }
}

function makeSampleLetter() {
  return {
    format: 'radius-letter-v2',
    language: 'en',
    template: 'classic-serif-v1',
    page: 'letter',
    sender: {
      name: 'Alex Example',
      lines: ['Beispielweg 10', '12345 Musterstadt', 'alex@example.test'],
    },
    recipient_lines: ['Sample Systems AG', 'Hiring Team', 'Testallee 4', '12345 Musterstadt'],
    place_date: 'Musterstadt, 10 October 2026',
    subject: makeUnit('u-subject', 'Application for Senior Systems Engineer'),
    salutation: makeUnit('u-salutation', 'Dear Hiring Team,'),
    paragraphs: [
      makeUnit('u-p1', 'I am applying to contribute to your distributed platform team.', {
        origin: 'ai',
        role: 'motivation',
        sources: ['job'],
      }),
      makeUnit('u-p2', 'At Example GmbH, I led reliability work across six services.', {
        origin: 'ai',
        role: 'experience',
        sources: ['cv:work-1:b1'],
      }),
    ],
    closing: makeUnit('u-closing', 'Kind regards,', { role: 'close' }),
    signature: 'Alex Example',
  }
}

// 1. Valid documents parse cleanly
const parsedCv = parseCvDocument(makeSampleCv())
assert.equal(parsedCv.format, 'radius-cv-v2')
assert.equal(parsedCv.header.name, 'Alex Example')
assert.equal(isCvDocument(parsedCv), true)
assert.equal(isLetterDocument(parsedCv), false)

const groupedCv = makeSampleCv()
groupedCv.sections[1].group = 'Additional Qualifications'
assert.equal(parseCvDocument(groupedCv).sections[1].group, 'Additional Qualifications')
assert.equal(parsedCv.sections[1].group, '')
groupedCv.sections[1].group = 'Invalid\nheading'
assert.throws(() => parseCvDocument(groupedCv), DocumentValidationError)

const parsedLetter = parseLetterDocument(makeSampleLetter())
assert.equal(parsedLetter.format, 'radius-letter-v2')
assert.equal(parsedLetter.page, 'letter')
assert.equal(isLetterDocument(parsedLetter), true)
assert.equal(isCvDocument(parsedLetter), false)

assert.equal(parseApplicationDocument(makeSampleCv()).format, 'radius-cv-v2')
assert.equal(parseApplicationDocument(makeSampleLetter()).format, 'radius-letter-v2')

// Optional unit defaults populate cleanly
const minimalCv = makeSampleCv()
minimalCv.headline = { id: 'u-headline', text: 'Engineer', origin: 'user' }
minimalCv.sections[1].entries[0].id = 'Work-Entry_01'
minimalCv.sections[1].entries[0].locked = {
  ...minimalCv.sections[1].entries[0].locked,
  end: null,
  current: true,
}
const parsedMinimal = parseCvDocument(minimalCv)
assert.deepEqual(parsedMinimal.headline, {
  id: 'u-headline',
  text: 'Engineer',
  lead_in: '',
  origin: 'user',
  sources: [],
  role: '',
  hidden: false,
})
assert.equal(parsedMinimal.sections[1].entries[0].id, 'Work-Entry_01')
assert.equal(parsedMinimal.sections[1].entries[0].locked.end, null)
assert.equal(parsedMinimal.sections[1].entries[0].locked.current, true)

// Boundary length: exactly 1200 chars is accepted; 1201 is rejected
const exactLimitCv = makeSampleCv()
exactLimitCv.headline.text = 'a'.repeat(MAX_UNIT_TEXT_LENGTH)
assert.equal(parseCvDocument(exactLimitCv).headline.text.length, MAX_UNIT_TEXT_LENGTH)

const overLimitCv = makeSampleCv()
overLimitCv.headline.text = 'a'.repeat(MAX_UNIT_TEXT_LENGTH + 1)
assert.throws(() => parseCvDocument(overLimitCv), DocumentValidationError)

// Section unit limit: 60 accepted, 61 rejected
const sixtyUnitsCv = makeSampleCv()
sixtyUnitsCv.sections[2].units = Array.from({ length: MAX_UNITS_PER_SECTION }, (_, idx) =>
  makeUnit(`u-max-${idx}`, `Skill ${idx}`),
)
assert.equal(parseCvDocument(sixtyUnitsCv).sections[2].units.length, MAX_UNITS_PER_SECTION)

const sixtyOneUnitsCv = makeSampleCv()
sixtyOneUnitsCv.sections[2].units = Array.from({ length: MAX_UNITS_PER_SECTION + 1 }, (_, idx) =>
  makeUnit(`u-max-${idx}`, `Skill ${idx}`),
)
assert.throws(() => parseCvDocument(sixtyOneUnitsCv), /maximum of 60 units/i)

// 2. Reject malformed structures & extra fields
assert.throws(() => parseCvDocument(null), DocumentValidationError)
assert.throws(() => parseCvDocument([]), DocumentValidationError)
assert.throws(() => parseApplicationDocument({ format: 'radius-cv-v1' }), DocumentValidationError)

const extraRootField = { ...makeSampleCv(), unexpected: true }
assert.throws(() => parseCvDocument(extraRootField), /unknown field/i)

const extraUnitField = makeSampleCv()
extraUnitField.headline = { ...extraUnitField.headline, extra: 1 }
assert.throws(() => parseCvDocument(extraUnitField), /unknown field/i)

const badLanguage = { ...makeSampleCv(), language: 'fr' }
assert.throws(() => parseCvDocument(badLanguage), /language/i)

const badPage = { ...makeSampleCv(), page: 'a5' }
assert.throws(() => parseCvDocument(badPage), /page/i)

const badPhoto = { ...makeSampleCv(), photo: 'yes' }
assert.throws(() => parseCvDocument(badPhoto), /boolean/i)

const badUnitId = makeSampleCv()
badUnitId.headline.id = 'Invalid_ID!'
assert.throws(() => parseCvDocument(badUnitId), /headline\.id/i)

const newlineInText = makeSampleCv()
newlineInText.headline.text = 'Line 1\nLine 2'
assert.throws(() => parseCvDocument(newlineInText), /newlines/i)

const duplicateUnitId = makeSampleCv()
duplicateUnitId.sections[0].units[0].id = 'u-headline'
assert.throws(() => parseCvDocument(duplicateUnitId), /duplicate unit id/i)

const duplicateLetterUnitId = makeSampleLetter()
duplicateLetterUnitId.closing.id = 'u-subject'
assert.throws(() => parseLetterDocument(duplicateLetterUnitId), /duplicate unit id/i)

const entriesOnSkills = makeSampleCv()
entriesOnSkills.sections[2].entries = entriesOnSkills.sections[1].entries
entriesOnSkills.sections[1].entries = []
assert.throws(() => parseCvDocument(entriesOnSkills), /experience or education/i)

const badLetterRole = makeSampleLetter()
badLetterRole.paragraphs[0].role = 'greeting'
assert.throws(() => parseLetterDocument(badLetterRole), /role/i)

// 3. Finding validation
const finding = parseFinding({
  id: 'f-1',
  document: 'cv',
  unit_id: 'u-work-1-b1',
  code: 'unsupported_number',
  severity: 'high',
  message: 'Metric "six" is not backed by evidence.',
  suggestion: 'Verify service count.',
})
assert.equal(finding.severity, 'high')

const docLevelFinding = parseFinding({
  id: 'f-2',
  document: 'letter',
  unit_id: null,
  code: 'extra_page',
  severity: 'medium',
  message: 'Letter exceeds one page.',
})
assert.equal(docLevelFinding.unit_id, null)
assert.equal(docLevelFinding.suggestion, '')

assert.throws(
  () =>
    parseFinding({
      id: 'f-3',
      document: 'cv',
      unit_id: null,
      code: 'not_a_real_code',
      severity: 'info',
      message: 'Bad code.',
    }),
  DocumentValidationError,
)

console.log('Document types and strict runtime validators passed.')
