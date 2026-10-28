import assert from 'node:assert/strict'
import { getDocumentRevision, startDocumentRevision, canApplyRevision } from '../frontend/src/documentRevision.ts'
const profile = {id:7,updated_at:'p1',cv:{updated_at:'c1'}}
let call
globalThis.fetch = async (url, options) => { call={url, options}; return {ok:true,json:async()=>({job:{id:'x',state:'running',stage:'revising'}})} }
const args = {api:'',jobId:42,profile,language:'en',target:'cover_letter',currentDocument:'Original',instruction:'Be concise'}
assert.equal((await startDocumentRevision(args)).stage,'revising')
assert.equal(call.url,'/api/v1/profiles/7/document-revision')
assert.equal(JSON.parse(call.options.body).instruction,'Be concise')
await getDocumentRevision('',7,42)
assert.match(call.url,/job_id=42/)
const job = {id:'x',state:'completed',request:{target:'cover_letter',current_document:'Original',cv_updated_at:'c1',profile_updated_at:'p1',language:'en'},result:{target:'cover_letter',document:'Revised'}}
assert.equal(canApplyRevision(job,'cover_letter','Original',profile,'en'),true)
assert.equal(canApplyRevision(job,'cover_letter','Edited while waiting',profile,'en'),false)
assert.equal(canApplyRevision(job,'cover_letter','Original',{...profile,updated_at:'p2'},'en'),false)
console.log('Document revision request and stale-draft guards passed.')
const { verifyDocumentRevision } = await import('../frontend/src/documentRevision.ts')
await verifyDocumentRevision('',7,42,'x')
assert.match(call.url,/document-revision\/check\?job_id=42&revision_id=x/)
const { assertRevisionPackageFits } = await import('../frontend/src/documentRevision.ts')
assert.doesNotThrow(() => assertRevisionPackageFits({cv:'safe'}))
assert.throws(() => assertRevisionPackageFits({cv:'x'.repeat(200001)}), /too large/i)
const { persistRevisionLocally } = await import('../frontend/src/documentRevision.ts')
let savedLocal = ''
persistRevisionLocally({setItem: (_key, value) => {savedLocal = value}}, 'draft', {package:{cv:'updated'}})
assert.match(savedLocal, /updated/)
assert.throws(() => persistRevisionLocally({setItem: () => {throw new Error('quota')}}, 'draft', {}), /save.*browser/i)
const { assertRevisionDraftFits } = await import('../frontend/src/documentRevision.ts')
assert.doesNotThrow(() => assertRevisionDraftFits({package:{cv:'ok'},response:''}))
assert.throws(() => assertRevisionDraftFits({package:{cv:'x'.repeat(199000)},response:'x'.repeat(252000)}), /too large/i)
const { hasEmptyCVSource } = await import('../frontend/src/documentRevision.ts')
assert.equal(hasEmptyCVSource({cv:''}),true)
assert.equal(hasEmptyCVSource({cover_letter:'letter'}),false)
assert.equal(hasEmptyCVSource(undefined),false)
