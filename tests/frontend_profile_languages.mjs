import assert from 'node:assert/strict'
import { profileForLanguage } from '../extensions/application-companion/profile-language.js'
import * as helpers from '../frontend/src/profile.ts'
assert.equal(typeof helpers.updateProfileLanguage, 'function')
const base = {summary:'English',custom_instructions:'English voice',skills:['Research'],application_details:{email:'alex@example.org',preferences:{salary_amount:'60000',notice_period:'Three months'}},localized_content:{de:{summary:'Deutsch',application_preferences:{notice_period:'Drei Monate'}}}}
const de = profileForLanguage(base,'de')
const changed = helpers.updateProfileLanguage(base,{...de,summary:'Neue Zusammenfassung',application_details:{...de.application_details,email:'new@example.org'}},'de')
assert.equal(changed.summary,'English')
assert.equal(changed.localized_content.de.summary,'Neue Zusammenfassung')
assert.equal(changed.application_details.email,'new@example.org')
assert.equal(changed.application_details.preferences.notice_period,'Three months')
assert.equal(profileForLanguage(changed,'de').application_details.preferences.notice_period,'Drei Monate')
assert.equal(profileForLanguage(changed,'en').application_details.email,'new@example.org')
assert.equal(profileForLanguage(changed,'de').custom_instructions,undefined)
console.log('Independent narratives and shared contact/salary persistence passed')

const { validateLocalizedContent } = await import('../extensions/application-companion/profile-language.js')
const examples = ['en', 'de'].map(language => ({ language, kind: 'cv_summary', text: 'Fictional studio' }))
const settings = { voice_notes: 'Write directly.', style_exemplars: examples }
assert.deepEqual(validateLocalizedContent({ en: { application_personalization: settings } }),
  { en: { application_personalization: settings } })
assert.throws(() => validateLocalizedContent({ en: { application_personalization: {
  style_exemplars: Array(4).fill(examples[0]),
} } }), /three style examples/)
