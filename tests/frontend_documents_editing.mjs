import assert from 'node:assert/strict'
import {
  MAX_UNITS_PER_SECTION,
  MAX_UNIT_TEXT_LENGTH,
  UNIT_ID_PATTERN,
  parseCvDocument,
  parseLetterDocument,
} from '../frontend/src/documents/types.ts'
import {
  collectUnitIds,
  countHiddenUnits,
  createHistory,
  findUnitById,
  newUnitId,
  pushHistory,
  redoHistory,
  removeUnit,
  splitUnit,
  toggleHidden,
  undoHistory,
  updateUnitText,
} from '../frontend/src/documents/editing.ts'

function makeUnit(id, text, overrides = {}) {
  return {
    id,
    text,
    lead_in: '',
    origin: 'ai',
    sources: ['cv:work-1:b1'],
    role: '',
    hidden: false,
    ...overrides,
  }
}

function makeCv() {
  return {
    format: 'radius-cv-v2',
    language: 'en',
    template: 'classic-serif-v1',
    page: 'a4',
    photo: false,
    header: {
      name: 'Alex Example',
      contacts: [{ kind: 'email', text: 'alex@example.test', href: 'mailto:alex@example.test' }],
    },
    headline: makeUnit('u-1', 'Platform Engineer', { origin: 'source', sources: [] }),
    sections: [
      {
        id: 's-profile',
        kind: 'profile',
        title: 'Profile',
        layout: 'paragraphs',
        units: [makeUnit('u-2', 'Builds dependable tooling for engineering teams.')],
        entries: [],
      },
      {
        id: 's-experience',
        kind: 'experience',
        title: 'Experience',
        layout: 'bullets',
        units: [],
        entries: [
          {
            id: 'work-1',
            kind: 'work',
            locked: {
              company: 'Example GmbH',
              title: 'Senior Engineer',
              location: 'Musterstadt',
              start: '2022',
              end: '2025',
              current: false,
            },
            label: '',
            intro: [makeUnit('u-3', 'Key achievements:')],
            units: [
              makeUnit('u-4', 'Automated build pipelines and improved release stability.', {
                lead_in: 'CI/CD:',
              }),
              makeUnit('u-5', ''),
            ],
          },
        ],
      },
    ],
  }
}

function makeLetter() {
  return {
    format: 'radius-letter-v2',
    language: 'en',
    template: 'classic-serif-v1',
    page: 'a4',
    sender: { name: 'Alex Example', lines: ['Musterstadt'] },
    recipient_lines: ['Example GmbH'],
    place_date: 'Musterstadt, 10 October 2026',
    subject: makeUnit('u-subj', 'Application for Platform Engineer'),
    salutation: makeUnit('u-sal', 'Dear Hiring Team,'),
    paragraphs: [
      makeUnit('u-p1', 'First sentence. Second sentence.', { role: 'motivation' }),
      makeUnit('u-p2', '', { role: 'experience' }),
    ],
    closing: makeUnit('u-close', 'Best regards,', { role: 'close' }),
    signature: 'Alex Example',
  }
}

// 1. newUnitId avoids collisions and matches contract pattern
const cv = makeCv()
const generated = newUnitId(cv, 'u')
assert.equal(generated, 'u-6')
assert.match(generated, UNIT_ID_PATTERN)
assert.equal(collectUnitIds(cv).has(generated), false)

const customPrefixId = newUnitId(cv, 'Work Bullet #1!')
assert.equal(customPrefixId, 'work-bullet-1-1')
assert.match(customPrefixId, UNIT_ID_PATTERN)

// 2. updateUnitText updates immutably, sets origin='user', strips newlines and clamps length
const updatedCv = updateUnitText(cv, 'u-4', 'Line one\nLine two\r\nLine three')
assert.notEqual(updatedCv, cv)
assert.equal(findUnitById(cv, 'u-4')?.text, 'Automated build pipelines and improved release stability.')
assert.equal(findUnitById(updatedCv, 'u-4')?.text, 'Line one Line two Line three')
assert.equal(findUnitById(updatedCv, 'u-4')?.origin, 'user')
assert.doesNotThrow(() => parseCvDocument(updatedCv))

const clampedCv = updateUnitText(cv, 'u-2', 'x'.repeat(MAX_UNIT_TEXT_LENGTH + 50))
assert.equal(findUnitById(clampedCv, 'u-2')?.text.length, MAX_UNIT_TEXT_LENGTH)
assert.doesNotThrow(() => parseCvDocument(clampedCv))

// Referential stability when no-op
assert.equal(updateUnitText(cv, 'u-missing', 'anything'), cv)
assert.equal(updateUnitText(updatedCv, 'u-4', 'Line one Line two Line three', 'user'), updatedCv)

// 3. splitUnit splits bullets and preserves contract invariants
const splitResult = splitUnit(cv, 'u-4', 25)
assert.ok(splitResult)
assert.match(splitResult.newUnitId, UNIT_ID_PATTERN)
const splitEntryUnits = splitResult.document.sections[1].entries[0].units
assert.equal(splitEntryUnits.length, 3)
assert.equal(splitEntryUnits[0].id, 'u-4')
assert.equal(splitEntryUnits[0].text, 'Automated build pipelines')
assert.equal(splitEntryUnits[0].lead_in, 'CI/CD:')
assert.equal(splitEntryUnits[0].origin, 'user')
assert.equal(splitEntryUnits[1].id, splitResult.newUnitId)
assert.equal(splitEntryUnits[1].text, 'and improved release stability.')
assert.equal(splitEntryUnits[1].lead_in, '')
assert.equal(splitEntryUnits[1].origin, 'user')
assert.deepEqual(splitEntryUnits[1].sources, [])
assert.equal(splitEntryUnits[2].id, 'u-5')
assert.doesNotThrow(() => parseCvDocument(splitResult.document))

// Split at end creates an empty follow-up bullet
const splitAtEnd = splitUnit(cv, 'u-4', 999)
assert.ok(splitAtEnd)
assert.equal(splitAtEnd.document.sections[1].entries[0].units[1].text, '')

// Non-splittable singleton unit returns null
assert.equal(splitUnit(cv, 'u-1', 4), null)
assert.equal(splitUnit(cv, 'u-nonexistent', 0), null)

// Split respects 60-unit-per-section limit
const fullSectionCv = makeCv()
fullSectionCv.sections[0].units = Array.from({ length: MAX_UNITS_PER_SECTION }, (_, idx) =>
  makeUnit(`u-full-${idx}`, `Paragraph ${idx}`),
)
assert.equal(splitUnit(fullSectionCv, 'u-full-0', 4), null)

// Split works on letter paragraphs and preserves role
const letter = makeLetter()
const splitLetter = splitUnit(letter, 'u-p1', 15)
assert.ok(splitLetter)
assert.equal(splitLetter.document.paragraphs.length, 3)
assert.equal(splitLetter.document.paragraphs[0].text, 'First sentence.')
assert.equal(splitLetter.document.paragraphs[1].text, 'Second sentence.')
assert.equal(splitLetter.document.paragraphs[1].role, 'motivation')
assert.equal(splitUnit(letter, 'u-subj', 5), null)
assert.doesNotThrow(() => parseLetterDocument(splitLetter.document))

// 4. removeUnit removes list units and ignores singletons
const afterRemove = removeUnit(cv, 'u-5')
assert.equal(afterRemove.sections[1].entries[0].units.length, 1)
assert.equal(findUnitById(afterRemove, 'u-5'), null)
assert.doesNotThrow(() => parseCvDocument(afterRemove))
assert.equal(removeUnit(cv, 'u-1'), cv)
assert.equal(removeUnit(cv, 'u-missing'), cv)

const letterAfterRemove = removeUnit(letter, 'u-p2')
assert.equal(letterAfterRemove.paragraphs.length, 1)
assert.equal(removeUnit(letter, 'u-sal'), letter)
assert.doesNotThrow(() => parseLetterDocument(letterAfterRemove))

// 5. toggleHidden hides and restores any unit
assert.equal(countHiddenUnits(cv), 0)
const hiddenCv = toggleHidden(cv, 'u-4')
assert.equal(findUnitById(hiddenCv, 'u-4')?.hidden, true)
assert.equal(countHiddenUnits(hiddenCv), 1)
const restoredCv = toggleHidden(hiddenCv, 'u-4', false)
assert.equal(findUnitById(restoredCv, 'u-4')?.hidden, false)
assert.equal(countHiddenUnits(restoredCv), 0)
assert.equal(toggleHidden(cv, 'u-4', false), cv)
assert.doesNotThrow(() => parseCvDocument(hiddenCv))

// 6. Undo/redo snapshot history
let hist = createHistory(cv)
const step1 = updateUnitText(cv, 'u-2', 'First edit')
const step2 = updateUnitText(step1, 'u-2', 'First edit continued')
const step3 = toggleHidden(step2, 'u-4', true)

hist = pushHistory(hist, step1)
assert.equal(hist.past.length, 1)
hist = pushHistory(hist, step2, { coalesce: true })
assert.equal(hist.past.length, 1)
assert.equal(hist.present, step2)
hist = pushHistory(hist, step3)
assert.equal(hist.past.length, 2)

hist = undoHistory(hist)
assert.equal(hist.present, step2)
hist = undoHistory(hist)
assert.equal(hist.present, cv)
hist = undoHistory(hist)
assert.equal(hist.present, cv)

hist = redoHistory(hist)
assert.equal(hist.present, step2)
const branch = updateUnitText(step2, 'u-1', 'Staff Engineer')
hist = pushHistory(hist, branch)
assert.equal(hist.future.length, 0)
assert.equal(hist.present, branch)

console.log('Document editing mutations and undo/redo history passed.')
