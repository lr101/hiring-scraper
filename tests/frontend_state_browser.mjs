// Optional browser regression against a seeded, isolated preview.
// STATE_TEST_WEB_URL=... STATE_TEST_API_URL=... PLAYWRIGHT_MODULE=... node tests/frontend_state_browser.mjs
import assert from 'node:assert/strict'
const web = process.env.STATE_TEST_WEB_URL
const api = process.env.STATE_TEST_API_URL
assert.ok(web && api, 'Set state test URLs to an isolated preview')
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE ?? 'playwright')
const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] })
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } })
const errors = []
const savedIds = []
const profileIds = []
page.on('pageerror', error => errors.push(error.message))
// Save through the real API, but cancel the initial crawl immediately so this
// browser check does not launch a live whole-state OSM scrape.
await page.route('**/api/v1/locations', async route => {
  if (route.request().method() !== 'POST') return route.continue()
  const response = await route.fetch()
  const saved = await response.json()
  assert.equal(response.status(), 201)
  assert.equal(saved.search_scope, 'state')
  assert.equal(saved.state, 'Baden-Württemberg')
  savedIds.push(saved.id)
  const cancelled = await page.request.post(`${api}/api/v1/discovery-jobs/${saved.first_discovery_job.id}/cancel`)
  assert.ok(cancelled.ok())
  await route.fulfill({ response })
})
try {
  const profileResponse = await page.request.post(`${api}/api/v1/profiles`, { data: {
    name: 'State filtering regression', search_area: { label: 'Karlsruhe', city: 'Karlsruhe',
      latitude: 49.0068705, longitude: 8.4034195, radius_km: 15, country_code: 'DE' },
    matching_defaults: { min_match_score: 0, include_unknown: true },
  } })
  assert.ok(profileResponse.ok())
  profileIds.push((await profileResponse.json()).id)
  await page.goto(`${web}/locations`)
  await page.getByRole('combobox', { name: 'Search coverage' }).selectOption('state')
  const stateSelect = page.getByRole('combobox', { name: 'German state' })
  await stateSelect.locator('option').last().waitFor({ state: 'attached' })
  assert.equal(await stateSelect.locator('option').count(), 16)
  await stateSelect.selectOption('Baden-Württemberg')
  assert.equal(await page.getByRole('combobox', { name: 'DISTANCE AROUND IT' }).count(), 0)
  await page.getByRole('button', { name: 'Add state and find companies' }).click()
  const savedCard = page.locator('.configured-location-card').filter({ hasText: 'Baden-Württemberg' }).last()
  await savedCard.waitFor()
  assert.match(await savedCard.textContent(), /Whole state/)
  await savedCard.getByRole('link', { name: 'View companies and jobs' }).click()
  await page.getByRole('combobox', { name: 'Available and searched locations' }).waitFor()
  await page.waitForFunction(() => !document.querySelector('.loading-state'))
  assert.equal(new URL(page.url()).searchParams.get('state'), 'Baden-Württemberg')
  assert.equal(new URL(page.url()).searchParams.has('lat'), false)
  assert.equal(await page.getByRole('combobox', { name: 'Listing distance around this place' }).count(), 0)
  assert.match(await page.locator('.search-footnote').textContent(), /All work locations/)
  assert.equal(await page.getByRole('combobox', { name: 'Sort companies' }).inputValue(), 'name')
  await page.getByRole('tab', { name: /^Jobs / }).click()
  await page.getByRole('combobox', { name: 'Filter jobs by location' }).waitFor()
  assert.equal(await page.getByRole('combobox', { name: 'Filter jobs by location' }).inputValue(), 'area_remote')
  assert.equal(await page.getByRole('combobox', { name: 'Match jobs to a profile' }).inputValue(), '')
  await Promise.all([
    page.waitForResponse(response => response.url().includes('/api/v1/jobs?')
      && new URL(response.url()).searchParams.get('profile_id') === String(profileIds[0])
      && new URL(response.url()).searchParams.get('state') === 'Baden-Württemberg', { timeout: 5000 }),
    page.getByRole('combobox', { name: 'Match jobs to a profile' }).selectOption(String(profileIds[0])),
  ])
  assert.equal(new URL(page.url()).searchParams.get('state'), 'Baden-Württemberg')
  assert.equal(await page.getByRole('combobox', { name: 'Listing distance around this place' }).count(), 0)
  await page.reload()
  await page.getByRole('combobox', { name: 'Filter jobs by location' }).waitFor()
  assert.equal(await page.getByRole('combobox', { name: 'Filter jobs by location' }).inputValue(), 'area_remote')
  await page.getByRole('combobox', { name: 'Available and searched locations' }).selectOption('default')
  await page.getByRole('combobox', { name: 'Listing distance around this place' }).waitFor()
  await page.waitForFunction(() => !new URL(location.href).searchParams.has('state'))
  assert.deepEqual(errors, [])
  console.log('State selection, real save, browsing, reload, and return to radius passed')
} finally {
  for (const id of savedIds) await page.request.delete(`${api}/api/v1/locations/${id}`)
  for (const id of profileIds) await page.request.delete(`${api}/api/v1/profiles/${id}`)
  await browser.close()
}
