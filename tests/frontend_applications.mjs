import assert from 'node:assert/strict'
import { createServer } from 'node:http'

const helpers = await import('../frontend/src/applications.ts').catch(() => null)
assert.equal(typeof helpers?.saveApplication, 'function', 'Application controls must persist statuses through the profile API')
assert.equal(typeof helpers?.fetchApplicationResults, 'function', 'Journey reads must recover from a concurrent inventory update')

const readAttempts = new Map()

const server = createServer(async (request, response) => {
  let body = ''
  for await (const chunk of request) body += chunk
  if (request.url.startsWith('/api/v1/applications?') && request.method === 'GET') {
    const scenario = new URL(request.url, 'http://localhost').searchParams.get('scenario')
    const attempt = (readAttempts.get(scenario) ?? 0) + 1
    readAttempts.set(scenario, attempt)
    response.setHeader('Content-Type', 'application/json')
    if (scenario === 'always-conflict' || (scenario === 'inventory-update' && attempt === 1)) {
      response.writeHead(409)
      response.end(JSON.stringify({ detail: 'Job inventory changed; retry the request' }))
    } else if (scenario === 'invalid-profile') {
      response.writeHead(422)
      response.end(JSON.stringify({ detail: 'Invalid profile' }))
    } else response.end(JSON.stringify({ items: [{ id: 42 }], total: 1 }))
  } else if (request.url === '/api/v1/profiles/9/applications/42' && request.method === 'PUT'
      && request.headers['content-type'] === 'application/json') {
    const payload = JSON.parse(body)
    response.setHeader('Content-Type', 'application/json')
    if (payload.status === 'invalid') {
      response.writeHead(422)
      response.end(JSON.stringify({ detail: [{ msg: 'Invalid status' }] }))
    } else response.end(JSON.stringify({ profile_id: 9, job_id: 42, status: payload.status }))
  } else {
    response.writeHead(404)
    response.end(JSON.stringify({ detail: 'Job not found' }))
  }
})
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
try {
  const base = `http://127.0.0.1:${server.address().port}`
  const page = await helpers.fetchApplicationResults(base, new URLSearchParams({ scenario: 'inventory-update' }))
  assert.equal(page.status, 200)
  assert.deepEqual(await page.json(), { items: [{ id: 42 }], total: 1 })
  assert.equal(readAttempts.get('inventory-update'), 2)
  const conflict = await helpers.fetchApplicationResults(base, new URLSearchParams({ scenario: 'always-conflict' }))
  assert.equal(conflict.status, 409)
  assert.equal(readAttempts.get('always-conflict'), 2, 'Repeated conflicts must stop rather than loop')
  const invalid = await helpers.fetchApplicationResults(base, new URLSearchParams({ scenario: 'invalid-profile' }))
  assert.equal(invalid.status, 422)
  assert.equal(readAttempts.get('invalid-profile'), 1, 'Permanent errors must not retry')
  for (const status of ['new', 'open', 'not_interested', 'waiting_for_reply', 'interview', 'rejected', 'accepted']) {
    const saved = await helpers.saveApplication(base, 9, 42, status)
    assert.deepEqual(saved, { profile_id: 9, job_id: 42, status })
  }
  await assert.rejects(helpers.saveApplication(base, 9, 999, 'open'), /Job not found/)
  await assert.rejects(helpers.saveApplication(base, 9, 42, 'invalid'), /Could not save application status/)
  console.log('Application status requests and actionable API errors passed')
} finally {
  server.closeAllConnections()
  await new Promise(resolve => server.close(resolve))
}
