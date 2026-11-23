import assert from 'node:assert/strict'
import { test } from 'node:test'

import {
  applicationPostingURL, incomingDocument, pollDocumentPdf, preparationStages, taskErrorMessage,
} from '../frontend/src/applicationDocuments.ts'

test('an incoming version preserves unsaved text and ignores older poll responses', () => {
  assert.equal(incomingDocument(3, 4, true), 'conflict')
  assert.equal(incomingDocument(3, 4, false), 'replace')
  assert.equal(incomingDocument(3, 2, false), 'ignore')
  assert.equal(incomingDocument(3, 3, true), 'metadata')
})

test('default preparation stages include review while Fast skips it', () => {
  assert.deepEqual(preparationStages(), ['cv', 'letter', 'review'])
  assert.deepEqual(preparationStages('fast'), ['cv', 'letter'])
})

test('PDF export waits for a queued render and sends the retry flag only once', async () => {
  const requests = []
  const request = async url => {
    requests.push(url)
    return requests.length === 1 ? new Response(null, { status: 202 })
      : new Response('%PDF-1.7', { headers: { 'Content-Type': 'application/pdf' } })
  }
  const pdf = await pollDocumentPdf('/letter/pdf?job_id=2&version=3',
    new AbortController().signal, true, { request, interval: 0 })
  assert.equal(await pdf.text(), '%PDF-1.7')
  assert.deepEqual(requests, ['/letter/pdf?job_id=2&version=3&retry=true', '/letter/pdf?job_id=2&version=3'])
})

test('the original posting link only accepts a web URL without credentials', () => {
  assert.equal(applicationPostingURL('https://jobs.example/role'), 'https://jobs.example/role')
  assert.equal(applicationPostingURL('javascript:alert(1)'), null)
  assert.equal(applicationPostingURL('https://user@jobs.example/role'), null)
})

test('connection and quota errors describe actions even without a server message', () => {
  assert.match(taskErrorMessage({ error_code: 'not_connected' }), /Reconnect ChatGPT/)
  assert.match(taskErrorMessage({ error_code: 'usage_limit' }), /limit resets/)
})
