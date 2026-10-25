import assert from 'node:assert/strict'
import * as helpers from '../frontend/src/profile.ts'
assert.equal(typeof helpers.profileSetupSections, 'function', 'Profile setup needs readiness hints')
const { profileSetupSections } = helpers

// Missing setup hints must follow actual saved facts, including German-only profiles.
const blank = { name: 'My search', skills: [], desired_roles: [], education: [], experience_years: null }
assert.deepEqual(profileSetupSections(blank).map(section => section.ready), [false, false, false, false])
const german = { ...blank, localized_content: { de: { desired_roles: ['Product Owner'], skills: ['Research'] } },
  application_details: { first_name: 'Ana', last_name: 'López', email: 'ana@example.org' },
  cv: { languages: { de: { filename: 'lebenslauf.tex' } } } }
assert.deepEqual(profileSetupSections(german).map(section => section.ready), [true, true, true, true])
assert.deepEqual(profileSetupSections({ ...german, application_details: { email: ' ' }, cv: { filename: 'old.pdf' } }).map(section => section.ready), [true, true, false, false])
assert.equal(profileSetupSections({ ...blank, experience_years: 0 })[1].ready, true)
console.log('Setup hints reflect bilingual content, confirmed contacts, current CVs and explicit zero experience')

assert.equal(typeof helpers.profileDraftFingerprint, 'function', 'Draft changes need comparison independent of the visible language')
const original = { name: 'Ana', localized_content: {}, application_details: { preferences: { sponsorship_required: false } } }
const switched = { ...original, localized_content: { de: { summary: '', skills: [], application_preferences: { notice_period: '' } } } }
assert.equal(helpers.profileDraftFingerprint(original), helpers.profileDraftFingerprint(switched))
assert.notEqual(helpers.profileDraftFingerprint(original), helpers.profileDraftFingerprint({ ...original, name: 'Ana updated' }))
assert.notEqual(helpers.profileDraftFingerprint(original), helpers.profileDraftFingerprint({ ...original, application_details: { preferences: { sponsorship_required: null } } }))
assert.notEqual(helpers.profileDraftFingerprint(original), helpers.profileDraftFingerprint({ ...original, experience_years: 0 }))
console.log('Draft comparison ignores empty language tabs and keeps meaningful false/zero changes')
