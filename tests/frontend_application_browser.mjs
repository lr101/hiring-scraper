// Optional end-to-end regression: use a seeded, isolated preview and Playwright.
// APPLICATION_TEST_WEB_URL=... APPLICATION_TEST_API_URL=... node tests/frontend_application_browser.mjs
import assert from 'node:assert/strict'

const web = process.env.APPLICATION_TEST_WEB_URL
const api = process.env.APPLICATION_TEST_API_URL
assert.ok(web && api, 'Set application test URLs to an isolated preview with jobs')
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE ?? 'playwright')
const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] })
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } })
const errors = []
const profileIds = []
page.on('pageerror', error => errors.push(error.message))
let releaseSave
try {
  for (const name of ['Tracking test A', 'Tracking test B']) {
    const response = await page.request.post(`${api}/api/v1/profiles`, { data: { name } })
    assert.ok(response.ok())
    profileIds.push((await response.json()).id)
  }
  const [first, second] = profileIds
  const jobsResponse = await page.request.get(`${api}/api/v1/jobs?limit=7`)
  const jobs = (await jobsResponse.json()).items
  assert.equal(jobs.length, 7, 'Preview must contain at least seven jobs')
  for (const [index, status] of ['new', 'open', 'not_interested', 'waiting_for_reply', 'interview', 'rejected', 'accepted'].entries()) {
    const saved = await page.request.put(`${api}/api/v1/profiles/${first}/applications/${jobs[index].id}`, { data: { status } })
    assert.ok(saved.ok())
  }
  await page.goto(`${web}/applications?profile_id=${first}`)
  await page.getByRole('button', { name: 'Interview 1', exact: true }).waitFor()
  await page.getByRole('button', { name: 'All statuses', exact: true }).click()
  assert.equal(await page.locator('tbody tr').count(), 7, 'Clicking the active All statuses filter must preserve the overview')
  await page.getByRole('button', { name: 'Interview 1', exact: true }).click()
  await page.waitForFunction(() => document.querySelectorAll('tbody tr').length === 1)
  assert.equal(await page.locator('tbody tr').count(), 1)
  await page.getByRole('combobox', { name: /Application status for/ }).selectOption('waiting_for_reply')
  await page.getByRole('heading', { name: 'No applications with this status' }).waitFor()
  await page.getByRole('button', { name: 'Waiting for reply 2', exact: true }).waitFor()
  await page.getByRole('button', { name: 'All statuses', exact: true }).click()
  await page.waitForFunction(() => document.querySelectorAll('tbody tr').length === 7)
  assert.equal(await page.locator('tbody tr').count(), 7)
  await page.locator('tbody tr').first().getByRole('link').click()
  const statusControl = page.getByRole('combobox', { name: /Application status for/ })
  await statusControl.waitFor()
  assert.equal(await statusControl.inputValue(), 'waiting_for_reply')
  await statusControl.selectOption('accepted')
  await page.getByRole('status').filter({ hasText: 'Saved' }).waitFor()
  await page.reload()
  await statusControl.waitFor()
  assert.equal(await statusControl.inputValue(), 'accepted')

  // Hold A's save response until B's details have finished loading. The old
  // control must never overwrite B's untracked state when its save completes.
  let startedSave
  const started = new Promise(resolve => { startedSave = resolve })
  const gate = new Promise(resolve => { releaseSave = resolve })
  const saveUrl = `${api}/api/v1/profiles/${first}/applications/*`
  await page.route(saveUrl, async route => {
    const response = await route.fetch()
    startedSave()
    await gate
    await route.fulfill({ response })
  })
  await statusControl.selectOption('rejected')
  await started
  await page.getByRole('combobox', { name: 'Track application for profile' }).selectOption(String(second))
  await statusControl.waitFor()
  assert.equal(await statusControl.inputValue(), '')
  const delivered = page.waitForResponse(response => response.url().includes(`/profiles/${first}/applications/`))
  releaseSave()
  await delivered
  await page.waitForTimeout(100)
  assert.equal(await statusControl.inputValue(), '', 'A completed save must not replace the selected profile B status')
  await page.unroute(saveUrl)

  await page.goto(`${web}/?view=jobs&profile_id=${first}`)
  const listControl = page.getByRole('combobox', { name: /Application status for/ }).first()
  await listControl.waitFor()
  await listControl.selectOption('interview')
  await page.getByRole('status').filter({ hasText: 'Saved' }).waitFor()
  await page.reload()
  await listControl.waitFor()
  assert.equal(await listControl.inputValue(), 'interview')
  await page.goto(`${web}/applications?profile_id=${first}`)
  await page.locator('tbody tr').first().waitFor()
  for (const width of [320, 390, 768]) {
    await page.setViewportSize({ width, height: 844 })
    const layout = await page.evaluate(() => {
      const navigation = document.querySelector('[aria-label="Main navigation"]')
      const table = document.querySelector('.table-scroll')
      const control = document.querySelector('tbody select')
      return {
        width: innerWidth, scroll: document.documentElement.scrollWidth,
        tableWidth: table.clientWidth, tableScroll: table.scrollWidth,
        controlSize: control.getBoundingClientRect().height,
        controlFont: parseFloat(getComputedStyle(control).fontSize),
        navBottom: navigation.getBoundingClientRect().bottom, height: innerHeight,
        navTargets: [...navigation.querySelectorAll('a')].map(link => link.getBoundingClientRect().height),
      }
    })
    assert.ok(layout.scroll <= layout.width, `Mobile page overflows at ${width}px`)
    assert.ok(layout.tableScroll <= layout.tableWidth, `Application statuses must be reachable without sideways scrolling at ${width}px`)
    assert.ok(layout.controlSize >= 44 && layout.controlFont >= 16, 'Status selects must support touch and avoid mobile input zoom')
    assert.ok(layout.navBottom >= layout.height - 80, 'Mobile navigation must remain reachable at the bottom of the screen')
    assert.ok(layout.navTargets.every(height => height >= 44), 'Each mobile navigation target must be at least 44px tall')
  }
  const mobileControl = page.getByRole('combobox', { name: /Application status for/ }).first()
  await mobileControl.selectOption('open')
  await page.getByRole('status').filter({ hasText: /Moved .* to Open/ }).waitFor()
  await page.getByRole('button', { name: /Open \d/ }).waitFor()
  await page.goto(`${web}/?view=jobs&profile_id=${first}`)
  await listControl.waitFor()
  const mobileJobs = await page.locator('.table-scroll').evaluate(element => ({ width: element.clientWidth, scroll: element.scrollWidth }))
  assert.ok(mobileJobs.scroll <= mobileJobs.width, 'Mobile job listings must show status actions without sideways scrolling')
  await listControl.selectOption('accepted')
  await page.getByRole('status').filter({ hasText: 'Saved' }).waitFor()
  await page.reload()
  await listControl.waitFor()
  assert.equal(await listControl.inputValue(), 'accepted', 'Mobile job status changes must persist')
  await page.setViewportSize({ width: 320, height: 844 })
  await page.goto(`${web}/locations`)
  await page.getByRole('textbox', { name: 'CITY, STATE OR POSTCODE' }).fill('Karlsruhe')
  await page.getByRole('button', { name: 'Find location', exact: true }).click()
  await page.getByRole('combobox', { name: 'Select the matching German place' }).waitFor()
  const locationControls = await page.locator('.locations-page input, .locations-page select, .locations-page button').evaluateAll(elements =>
    elements.filter(element => element.getBoundingClientRect().height > 0).map(element => ({
      tag: element.tagName, label: element.textContent || element.getAttribute('placeholder'),
      height: element.getBoundingClientRect().height, font: parseFloat(getComputedStyle(element).fontSize),
    })))
  assert.ok(locationControls.every(control => control.height >= 44), `Search area actions must support touch: ${JSON.stringify(locationControls)}`)
  assert.ok(locationControls.filter(control => control.tag !== 'BUTTON').every(control => control.font >= 16), 'Search area inputs must avoid mobile focus zoom')
  assert.deepEqual(errors, [])
  console.log('Browser application checks passed, including delayed saves during profile switching')
} finally {
  releaseSave?.()
  for (const id of profileIds) await page.request.delete(`${api}/api/v1/profiles/${id}`)
  await browser.close()
}
