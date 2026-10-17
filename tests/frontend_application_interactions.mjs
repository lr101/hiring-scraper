import assert from 'node:assert/strict'
import { createServer } from 'node:http'
import { applicationSwipeAction, nextApplicationStages, trackJobInteraction } from '../frontend/src/applications.ts'

let application = null
let writes = 0
let decideAfterRead = false
const server = createServer(async (request, response) => {
  response.setHeader('Content-Type', 'application/json')
  if (request.method === 'GET' && request.url === '/api/v1/jobs/42?profile_id=9') {
    response.end(JSON.stringify({ id: 42, application }))
    if (decideAfterRead) application = { profile_id: 9, job_id: 42, status: 'open' }
  } else if (request.method === 'PUT' && request.url === '/api/v1/profiles/9/applications/42') {
    let body = ''
    for await (const chunk of request) body += chunk
    application = { profile_id: 9, job_id: 42, status: JSON.parse(body).status }
    writes++
    response.end(JSON.stringify(application))
  } else {
    response.writeHead(404)
    response.end(JSON.stringify({ detail: 'Not found' }))
  }
})
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
try {
  const base = `http://127.0.0.1:${server.address().port}`
  decideAfterRead = true
  await trackJobInteraction(base, 9, 42, null, 'open_post')
  assert.equal(application.status, 'open', 'Opening must not overwrite an explicit decision made after its read')
  decideAfterRead = false
  for (const interaction of ['view', 'open_post']) {
    application = null
    writes = 0
    const tracked = await trackJobInteraction(base, 9, 42, null, interaction)
    assert.equal(tracked?.status ?? null, interaction === 'view' ? 'new' : null,
      `${interaction} must keep a new job undecided`)
    assert.equal(writes, interaction === 'view' ? 1 : 0)
    writes = 0
    const revisited = await trackJobInteraction(base, 9, 42, tracked, interaction)
    assert.equal(revisited?.status ?? null, interaction === 'view' ? 'new' : null,
      `${interaction} must not imply interest on repeated clicks`)
    assert.equal(writes, 0, 'Revisiting must not save another stage')
  }
  for (const status of ['open', 'in_gots_hand', 'automated_application', 'not_interested', 'waiting_for_reply', 'interview', 'rejected', 'accepted']) {
    application = { profile_id: 9, job_id: 42, status }
    writes = 0
    const opened = await trackJobInteraction(base, 9, 42, application, 'open_post')
    assert.equal(opened.status, status, 'Opening must preserve a deliberate stage')
    assert.equal(writes, 0)
  }
  const stale = { profile_id: 9, job_id: 42, status: 'new' }
  application = { ...stale, status: 'not_interested' }
  assert.equal((await trackJobInteraction(base, 9, 42, stale, 'open_post')).status, 'not_interested',
    'Opening with stale card data must preserve the latest saved decision')
  assert.ok(nextApplicationStages.open.includes('not_interested'), 'Interested jobs must offer a Not interested action')
  for (const stage of ['new', 'open']) {
    assert.ok(nextApplicationStages[stage].includes('in_gots_hand'), `${stage} jobs can be set aside as stretch roles`)
  }
  for (const stage of ['new', 'open', 'in_gots_hand']) {
    assert.ok(!nextApplicationStages[stage].includes('automated_application'), 'Only plugin submission callbacks assign the automated lane')
  }
  assert.deepEqual(applicationSwipeAction({ status: 'in_gots_hand' }, 'right'),
    { kind: 'stage', status: 'waiting_for_reply' }, 'Manual swipes mark a sent application without claiming autofill')
  assert.deepEqual(applicationSwipeAction({ status: 'automated_application', previous_status: 'in_gots_hand' }, 'right'),
    { kind: 'stage', status: 'interview' }, 'Automated applications can progress to interview')
  assert.deepEqual(applicationSwipeAction({ status: 'automated_application' }, 'left'),
    { kind: 'stage', status: 'rejected' }, 'Automated applications can record rejection')
  assert.deepEqual(applicationSwipeAction({ status: 'open', previous_status: 'new' }, 'left'),
    { kind: 'stage', status: 'not_interested' }, 'Swiping left on an interested job must skip it')
  assert.deepEqual(applicationSwipeAction({ status: 'open' }, 'right'),
    { kind: 'stage', status: 'waiting_for_reply' }, 'Interested jobs must still support marking applied')
  console.log('Job opening and interested-to-not-interested checks passed')
} finally {
  server.closeAllConnections()
  await new Promise(resolve => server.close(resolve))
}
