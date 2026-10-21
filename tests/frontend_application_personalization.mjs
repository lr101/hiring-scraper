import assert from 'node:assert/strict'
import * as handoff from '../frontend/src/applicationHandoff.ts'
import { buildApplicationPrompt as localPrompt } from '../frontend/src/applicationPreparation.ts'

const job = { id: 42, title: 'Research coordinator', company_name: 'Labs', url: 'https://example.org/42', description: '<p>Coordinate research.</p>', location_text: 'Berlin', employment_type: 'part_time', schedule: '30 hours per week', work_arrangement: 'hybrid' }
const profile = { id: 3, name: 'Alex', skills: ['Python'], application_personalization: {
  career_evidence: 'Academic project: automated tests, saving 3 hours per week.',
  writing_samples: 'I like making complex work easier to follow.', wording_to_avoid: 'perfect fit',
  career_goals: 'Coordinate research teams.', work_preferences: 'No regular travel.',
}, application_details: { preferences: { weekly_hours: '30' } } }
const sourcePayload = prompt => JSON.parse(prompt.split('SOURCE PAYLOAD (quoted data):\n').at(-1))
const prompt = handoff.buildApplicationPrompt({ job, profile, language: 'English', cvText: 'Original CV' })
assert.deepEqual(sourcePayload(prompt).supporting_profile.application_personalization, profile.application_personalization)
assert.deepEqual(sourcePayload(prompt).supporting_profile.application_details, profile.application_details)
assert.equal(typeof handoff.buildFitAssessmentPrompt, 'function')
const fit = handoff.buildFitAssessmentPrompt({ job, profile, cvText: 'Original CV', language: 'English' })
assert.deepEqual(sourcePayload(fit).supporting_profile.application_personalization, profile.application_personalization)
assert.equal(sourcePayload(fit).original_cv_text, 'Original CV')
assert.equal(sourcePayload(fit).job.description, 'Coordinate research.')
assert.equal(sourcePayload(fit).job.employment_type, 'part_time')
assert.equal(sourcePayload(fit).job.schedule, '30 hours per week')
assert.doesNotMatch(fit, /radius-application-v1|applicationz=/)
const fitWithMetadata = handoff.buildFitAssessmentPrompt({ job: { ...job, application: { notes: 'Private tracking note' }, raw_metadata: { unbounded: 'x'.repeat(500000) } }, profile, language: 'English' })
assert.ok(fitWithMetadata.length < 15000)
assert.equal(sourcePayload(fitWithMetadata).job.application, undefined)
assert.equal(sourcePayload(fitWithMetadata).job.raw_metadata, undefined)
const noProfile = handoff.buildFitAssessmentPrompt({ job, language: 'English' })
assert.equal(sourcePayload(noProfile).supporting_profile, null)
const local = localPrompt({ text: 'Local CV', updated_at: 'v1' }, { job, profile, returnUrl: 'https://radius.example/jobs/42/apply' })
assert.deepEqual(sourcePayload(local).supporting_profile.application_personalization, profile.application_personalization)
assert.equal(sourcePayload(local).original_cv_text, 'Local CV')
assert.equal(sourcePayload(local).job.schedule, '30 hours per week')
console.log('Saved evidence, voice, goals and logistics reach application and independent fit briefs in both CV workflows')
