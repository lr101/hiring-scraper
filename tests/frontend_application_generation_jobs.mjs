import assert from 'node:assert/strict'
import { getPreparationJob, startPreparationJob } from '../frontend/src/applicationGeneration.ts'
let request
const profile = {id: 7, updated_at: 'p1', cv: {updated_at: 'c1'}}
globalThis.fetch = async (url, options) => { request = {url, options}; return {ok:true, json:async()=>({job:{id:'job1',state:'running',stage:'drafting'}})} }
assert.equal((await getPreparationJob('', 7, 42)).stage, 'drafting')
assert.equal(request.url, '/api/v1/profiles/7/preparation-job?job_id=42')
await startPreparationJob({api:'', jobId:42, profile, language:'en', notes:''})
assert.equal(request.options.method, 'POST')
assert.equal(JSON.parse(request.options.body).profile_updated_at, 'p1')
console.log('Preparation job status and start requests passed.')
const { saveApplicationDraft, getApplicationDraft } = await import('../frontend/src/applicationGeneration.ts')
globalThis.fetch = async (url, options) => { request={url,options}; return {ok:true,json:async()=>({saved:{version:'v1',draft:{notes:'saved'}}})} }
assert.equal((await getApplicationDraft('',7,42)).version,'v1')
await saveApplicationDraft('',7,42,{notes:'edited'},'v1')
assert.deepEqual(JSON.parse(request.options.body),{version:'v1',draft:{notes:'edited'}})
const { matchesDraftContext } = await import('../frontend/src/applicationGeneration.ts')
assert.equal(matchesDraftContext({context:{job_id:42,profile_id:7,cv_updated_at:'c1'},profile_updated_at:'p1'},{job_id:42,profile_id:7,cv_updated_at:'c1'},'p1'),true)
assert.equal(matchesDraftContext({context:{job_id:42,profile_id:7,cv_updated_at:'c1'},profile_updated_at:'p0'},{job_id:42,profile_id:7,cv_updated_at:'c1'},'p1'),false)
