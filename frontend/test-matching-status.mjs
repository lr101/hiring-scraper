import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { stripTypeScriptTypes } from 'node:module'
import { createServer } from 'vite'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
const server = await createServer({ root: import.meta.dirname, server: { middlewareMode: true }, appType: 'custom' })
try {
  const { default: MatchingStatusPage, MatchingStatusDetails } = await server.ssrLoadModule('/src/MatchingStatusPage.tsx')
  assert.match(renderToStaticMarkup(React.createElement(MatchingStatusPage)), /Match all jobs with AI again/)
  const base = { state: 'pending', queued_pairs: 12, assessed_pairs: 5, last_assessed_at: null, stale_assessments_pending: false, usage: { allowed: true, used_percent: 20 }, checked_at: '2026-11-09T10:00:00Z' }
  const render = data => renderToStaticMarkup(React.createElement(MatchingStatusDetails, { data }))
  assert.match(render(base), /Waiting for assessments/)
  assert.match(render(base), /12/)
  assert.match(render({ ...base, queued_pairs: 0, indexing_pending: true }), /Preparing job matches/)
  assert.match(render(base), /No successful assessment recorded/)
  assert.match(render({ ...base, worker_health: 'running', queue_counts: { pending: 8, running: 4, retry: 2, done: 5, excluded: 1 }, concurrency: 4 }), /In progress/ )
  assert.match(render(base), /Every active job in your search area/)
  assert.match(render({ ...base, state: 'disconnected' }), /Connect ChatGPT/)
  assert.match(render({ ...base, state: 'paused', usage: { reason: 'usage_unavailable' } }), /Paused/)
  assert.match(render({ ...base, state: 'ready', queued_pairs: 0 }), /Up to date/)
} finally { await server.close() }


// Exercise the shipped rematch handler, including a worker-busy response.
const component = readFileSync(new URL('./src/MatchingStatusPage.tsx', import.meta.url), 'utf8')
const handler = component.slice(component.indexOf('  async function rematchAll()'), component.indexOf('  useEffect('))
let message, revision = 0
const busy = []
const calls = []
let response = new Response('{}', { status: 200 })
const rematch = new Function('fetch', 'API', 'setRematching', 'setMessage', 'setRevision',
  stripTypeScriptTypes(handler) + '; return rematchAll')(
  async (...args) => { calls.push(args); return response }, 'https://radius.test',
  value => busy.push(value), value => { message = value },
  update => { revision = update(revision) })
await rematch()
assert.equal(calls[0][0], 'https://radius.test/api/v1/profile-matching/rematch')
assert.equal(calls[0][1].method, 'POST')
assert.deepEqual(busy, [true, false])
assert.equal(revision, 1)
assert.match(message, /requested/)
response = new Response(JSON.stringify({ detail: 'An AI assessment is running. Try again shortly.' }), { status: 409 })
await rematch()
assert.match(message, /assessment is running/)
assert.equal(revision, 1)
response = new Response('Internal Server Error', { status: 500 })
await rematch()
assert.match(message, /Could not queue/)
