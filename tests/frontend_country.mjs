import assert from 'node:assert/strict'
import { boardLocationParams, profileBoardDefaults } from '../frontend/src/profile.ts'
const country = { search_scope: 'country', label: 'Germany', latitude: 0, longitude: 0, country_code: 'de' }
assert.equal(boardLocationParams(country, 15, 'US').toString(), 'country=DE&country_code=US')
assert.equal(profileBoardDefaults({search_area:{latitude:49,longitude:8,radius_km:15},matching_defaults:{}}, new URLSearchParams('country=DE')).location, null)
console.log('Germany requests preserve employer scope independently of remote eligibility and profile radius')
