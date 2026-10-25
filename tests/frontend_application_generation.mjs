import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
const generation = await import('../frontend/src/applicationGeneration.ts').catch(() => null)
assert.equal(typeof generation?.prepareInsideRadius, 'function', 'Direct preparation must return validated documents to the editor')
const { prepareInsideRadius } = generation
const template = JSON.parse(await readFile(new URL('../hiring_scraper/app/latex_template.json', import.meta.url), 'utf8'))
const cv = template.preamble + 'Alex Example\\par\n' + template.ending
const context = { job_id: 42, profile_id: 7, cv_updated_at: '2026-10-25T10:00:00' }
const packageValue = { format: 'radius-application-v1', ...context, cv: 'Alex Example', cover_letter: 'Dear team, I coordinate products.', application_answers: 'None needed.', review_notes: 'Emphasized coordination.', latex_documents: {cv, cover_letter: cv} }
const args = { api: '', jobId: 42, profile: { id: 7, updated_at: '2026-10-25T09:00:00', cv: {updated_at: context.cv_updated_at} }, language: 'en', notes: 'Keep it concise.', cvText: cv }
let calls = 0
globalThis.fetch = async (url, options) => {
  calls++
  assert.equal(url, '/api/v1/profiles/7/prepare-application')
  assert.deepEqual(JSON.parse(options.body), { job_id: 42, cv_updated_at: context.cv_updated_at, profile_updated_at: '2026-10-25T09:00:00', language: 'en', notes: 'Keep it concise.' })
  assert.equal(options.method, 'POST')
  return { ok: true, json: async () => ({package: packageValue, provider:'OpenAI', model:'test-model'}) }
}
const result = await prepareInsideRadius(args)
assert.equal(calls, 1)
assert.equal(result.documents.coverLetter, 'Dear team, I coordinate products.')
assert.equal(result.latexDocuments.cv, cv)
globalThis.fetch = async () => ({ok:true,json:async()=>({package:{...packageValue,job_id:43}})})
await assert.rejects(prepareInsideRadius(args), /different job/i)
globalThis.fetch = async () => ({ok:false,json:async()=>({detail:'Your saved CV changed. Reload this application.'})})
await assert.rejects(prepareInsideRadius(args), /CV changed/)
globalThis.fetch = async () => ({ok:false,json:async()=>{throw new SyntaxError('proxy HTML')}})
await assert.rejects(prepareInsideRadius(args), /prepare/i)
globalThis.fetch = async () => { throw new Error('Must not spend without a saved profile revision') }
await assert.rejects(prepareInsideRadius({...args,profile:{...args.profile,updated_at:undefined}}), /reload/i)
console.log('Direct application preparation: validated round trip, stale-context rejection and recoverable errors passed.')
