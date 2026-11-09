import assert from 'node:assert/strict'
import { createServer } from 'vite'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
const server = await createServer({ root: import.meta.dirname, server: { middlewareMode: true }, appType: 'custom' })
try {
  const { MatchingStatusDetails } = await server.ssrLoadModule('/src/MatchingStatusPage.tsx')
  const base = { state: 'pending', queued_pairs: 12, assessed_pairs: 5, last_assessed_at: null, stale_assessments_pending: false, usage: { allowed: true, used_percent: 20 }, checked_at: '2026-11-09T10:00:00Z' }
  const render = data => renderToStaticMarkup(React.createElement(MatchingStatusDetails, { data }))
  assert.match(render(base), /Waiting for assessments/)
  assert.match(render(base), /12/)
  assert.match(render(base), /No successful assessment recorded/)
  assert.match(render(base), /worker is running/)
  assert.match(render({ ...base, state: 'disconnected' }), /Connect ChatGPT/)
  assert.match(render({ ...base, state: 'paused', usage: { reason: 'usage_unavailable' } }), /Paused/)
  assert.match(render({ ...base, state: 'ready', queued_pairs: 0 }), /Up to date/)
} finally { await server.close() }
