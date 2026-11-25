import assert from 'node:assert/strict'
import { focusHeadline, focusHref, getTodaysFocus } from '../frontend/src/todaysFocus.ts'

const item = { job_id: 7, title: 'Developer', company: 'Employer', status: 'new', match_score: 80,
  documents_ready: false, reason: 'Strong match (80)' }
assert.equal(focusHref(item, 3), '/jobs/7?profile_id=3&from=applications')
assert.equal(focusHref({ ...item, documents_ready: true }, 3), '/jobs/7/apply?profile_id=3',
  'Ready documents open the review page directly')

const focus = { items: [item], sent_today: 1, daily_target: 3, completed: false }
assert.equal(focusHeadline(focus), '2 applications to finish today')
assert.equal(focusHeadline({ ...focus, sent_today: 2 }), '1 application to finish today')
assert.equal(focusHeadline({ ...focus, sent_today: 3, completed: true }), 'Today is done. Anything more is a bonus.')
assert.equal(focusHeadline({ ...focus, items: [] }), 'Nothing waiting. New matches will appear here.')

let requested
globalThis.fetch = async url => { requested = url; return { ok: true, json: async () => focus } }
assert.deepEqual(await getTodaysFocus('', 3), focus)
assert.equal(requested, '/api/v1/profiles/3/focus')
globalThis.fetch = async () => ({ ok: false, json: async () => ({ detail: 'Profile not found' }) })
await assert.rejects(getTodaysFocus('', 4), /Profile not found/)
console.log('Today’s focus links, headlines and API errors passed.')
