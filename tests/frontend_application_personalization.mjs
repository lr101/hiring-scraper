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
assert.equal('buildFitAssessmentPrompt' in handoff, false)
const local = localPrompt({ text: 'Local CV', updated_at: 'v1' }, { job, profile, returnUrl: 'https://radius.example/jobs/42/apply' })
assert.deepEqual(sourcePayload(local).supporting_profile.application_personalization, profile.application_personalization)
assert.equal(sourcePayload(local).original_cv_text, 'Local CV')
assert.equal(sourcePayload(local).job.schedule, '30 hours per week')
console.log('Saved evidence, voice, goals and logistics reach application briefs in both CV workflows')
