import assert from 'node:assert/strict'
import { profileBoardDefaults, profileBoardLink } from '../frontend/src/profile.ts'
const profile = { id: 9, search_area: { city: 'Heidelberg', label: 'Heidelberg, Germany', latitude: 49.40936, longitude: 8.69472, radius_km: 35, country_code: 'DE' }, matching_defaults: { min_match_score: 30, include_unknown: true } }
assert.equal(profileBoardDefaults(profile, new URLSearchParams()).radius, 35)
assert.equal(profileBoardDefaults(profile, new URLSearchParams()).minimumScore, '30')
assert.equal(profileBoardDefaults(profile, new URLSearchParams('lat=50&lon=9&radius_km=10&min_match_score=0&include_unknown=false')).location, null)
assert.equal(profileBoardDefaults(profile, new URLSearchParams('lat=50&lon=9&radius_km=10&min_match_score=0&include_unknown=false')).minimumScore, '0')
assert.equal(profileBoardDefaults(profile, new URLSearchParams('include_unknown=false')).includeUnknown, false)
const link = new URL(profileBoardLink(profile), 'https://example.test')
assert.equal(link.searchParams.get('lat'), '49.40936')
assert.equal(link.searchParams.get('radius_km'), '35')
assert.equal(link.searchParams.get('min_match_score'), '30')
console.log('Profile board defaults and explicit URL overrides passed')

const helpers = await import('../frontend/src/profile.ts')
assert.equal(typeof helpers.selectedLanguageLevels, 'function', 'Language edits must prune removed proficiency')
const levels = { Spanish: 'native', English: 'C1', German: 'B2', French: 'A2' }
assert.deepEqual(helpers.selectedLanguageLevels(['Spanish', 'English', 'French'], levels), { Spanish: 'native', English: 'C1', French: 'A2' })
assert.deepEqual(helpers.selectedLanguageLevels([' german ', 'ENGLISH'], levels), { german: 'B2', ENGLISH: 'C1' })
assert.deepEqual(helpers.selectedLanguageLevels(['German'], { German: 'B2', german: 'C1' }), { German: 'B2' })
assert.equal(typeof helpers.skillEvidenceKey, 'function', 'Canonical skill controls must expose alias evidence')
const notes = { 'Stakeholder management': { context: 'professional', note: 'Coordinated partners and executive stakeholders.' } }
assert.equal(helpers.skillEvidenceKey('Stakeholder coordination', notes), 'Stakeholder management')
assert.equal(notes[helpers.skillEvidenceKey('Stakeholder coordination', notes)].note, 'Coordinated partners and executive stakeholders.')
console.log('Language removal/case edits and canonical evidence access passed')

// Exercise the saved frontend payload against the real matcher: removed German
// must no longer satisfy an explicit German B2 requirement.
const { spawnSync } = await import('node:child_process')
const languages = ['Spanish', 'English', 'French']
const saved = { languages, language_levels: helpers.selectedLanguageLevels(languages, levels) }
const result = spawnSync('.venv/bin/python', ['-c', `
import json, sys
from hiring_scraper.matching import match_job
profiles = json.loads(sys.stdin.read())
job = {'title': 'Project coordinator', 'description': 'German B2 required. Project management and coordination. ' * 12}
print(json.dumps([match_job(job, profile)['conflicts'] for profile in profiles]))
`], { input: JSON.stringify([{ languages: Object.keys(levels), language_levels: levels }, saved]), encoding: 'utf8' })
assert.equal(result.status, 0, result.stderr)
const [before, after] = JSON.parse(result.stdout)
assert.ok(!before.some(conflict => conflict.includes('German')))
assert.ok(after.includes('Language requested: German'))
console.log('Removed proficiency no longer satisfies real matcher language evidence')
