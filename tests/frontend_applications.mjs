import assert from 'node:assert/strict'
import { createServer } from 'node:http'

const helpers = await import('../frontend/src/applications.ts').catch(() => null)
assert.equal(typeof helpers?.saveApplication, 'function', 'Application controls must persist statuses through the profile API')

const server = createServer(async (request, response) => {
  let body = ''
  for await (const chunk of request) body += chunk
  if (request.url === '/api/v1/profiles/9/applications/42' && request.method === 'PUT'
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
