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
