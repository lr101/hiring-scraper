import assert from 'node:assert/strict'
import { deflateSync } from 'node:zlib'
import { readPackage as readExtensionPackage } from '../extensions/application-companion/sources.js'
import {
  buildApplicationPrompt,
  cvStorageKey,
  deleteCv,
  draftStorageKey,
  parseReturnedPackage,
  readCv,
  readDraft,
  removeDraft,
  saveCv,
  saveDraft,
} from '../frontend/src/applicationPreparation.ts'

class MemoryStorage {
  values = new Map()
  get length() { return this.values.size }
  key(index) { return [...this.values.keys()][index] ?? null }
  getItem(key) { return this.values.get(key) ?? null }
  setItem(key, value) { this.values.set(key, value) }
  removeItem(key) { this.values.delete(key) }
}

const storage = new MemoryStorage()
assert.equal(cvStorageKey(3), 'radius:application-cv:v1:3')
assert.equal(draftStorageKey(42, 3), 'radius:application-draft:v1:42:3')
assert.equal(readCv(storage, 3), null)
const first = saveCv(storage, 3, 'Known original CV', () => 'v1')
assert.deepEqual(first, { text: 'Known original CV', updated_at: 'v1' })
assert.equal(saveCv(storage, 3, 'Known original CV', () => 'v2').updated_at, 'v1', 'unchanged CV keeps its version')
assert.notEqual(saveCv(storage, 3, 'Changed original CV', () => 'v1').updated_at, 'v1', 'changed CV gets a new version even when the clock repeats')
assert.throws(() => saveCv(storage, 3, '   '), /CV text/i, 'empty CV text is rejected')
assert.equal(readCv(storage, 4), null, 'CVs are isolated by profile')
deleteCv(storage, 3)
assert.equal(readCv(storage, 3), null, 'deleting a CV removes only the selected profile CV')
saveCv(storage, 3, 'Known original CV', () => 'v1')
saveCv(storage, 4, 'Other profile CV', () => 'v1')

const pack = {
  format: 'radius-application-v1', job_id: 42, profile_id: 3, cv_updated_at: 'v1',
  cv: 'Known original CV', cover_letter: 'A factual letter.', application_answers: 'Availability: two weeks.',
  review_notes: 'Check dates.', experience_bullets: [{ experience_id: 'work-1', bullets: ['Coordinated delivery.'] }],
}
await assert.rejects(() => parseReturnedPackage(JSON.stringify({ ...pack, cover_letter: String.raw`Dear \textbf{team}` }), { jobId: 42, profileId: 3, cvUpdatedAt: 'v1' }), /cover letter.*latex/i)
await assert.rejects(() => parseReturnedPackage(JSON.stringify({ ...pack, cover_letter: String.raw`\centering Dear team` }), { jobId: 42, profileId: 3, cvUpdatedAt: 'v1' }), /cover letter.*latex/i)
await assert.rejects(() => readExtensionPackage(JSON.stringify({ ...pack, cover_letter: String.raw`\newline Dear team` })), /cover letter.*latex/i)
for (const letter of [String.raw`\(x+y\)`, String.raw`\[x^2\]`, String.raw`\"o`, 'Price is $x^2$ per unit.', '$x + y$', '$E = mc^2$', '$$x+y$$', '```latex\nDear team\n```']) {
  await assert.rejects(() => parseReturnedPackage(JSON.stringify({ ...pack, cover_letter: letter }), { jobId: 42, profileId: 3, cvUpdatedAt: 'v1' }), /cover letter.*latex/i)
  await assert.rejects(() => readExtensionPackage(JSON.stringify({ ...pack, cover_letter: letter })), /cover letter.*latex/i)
}
assert.equal((await parseReturnedPackage(JSON.stringify({ ...pack, cover_letter: 'The budget was $5 to $10.' }), { jobId: 42, profileId: 3, cvUpdatedAt: 'v1' })).cover_letter, 'The budget was $5 to $10.')
const prompt = buildApplicationPrompt({ text: 'Known original CV', updated_at: 'v1' }, { job: { id: 42, title: 'Role', company_name: 'Company', description: 'x'.repeat(60_000), location_text: null, employment_type: null, work_arrangement: null, url: 'https://employer.example/job' }, profile: { id: 3, name: 'Profile' }, returnUrl: 'https://radius.example/jobs/42/apply?profile_id=3' })
const promptPayload = JSON.parse(prompt.slice(prompt.indexOf('SOURCE PAYLOAD (quoted data):\n') + 'SOURCE PAYLOAD (quoted data):\n'.length))
assert.equal(promptPayload.job.description.length, 50_000, 'prompt bounds untrusted job descriptions')
const { experience_bullets: ignoredBullets, ...basePackage } = pack
assert.deepEqual(await readExtensionPackage(JSON.stringify(basePackage)), basePackage, 'the real extension accepts the base package without fake bullet groups')
assert.deepEqual(await parseReturnedPackage(JSON.stringify(pack), { jobId: 42, profileId: 3, cvUpdatedAt: 'v1' }), pack)
assert.deepEqual(await parseReturnedPackage(`https://radius.example/jobs/42/apply?profile_id=3#application=${encodeURIComponent(JSON.stringify(pack))}`, { jobId: 42, profileId: 3, cvUpdatedAt: 'v1' }), pack)
const compressed = '#applicationz=' + deflateSync(JSON.stringify(pack)).toString('base64url')
assert.deepEqual(await parseReturnedPackage(compressed, { jobId: 42, profileId: 3, cvUpdatedAt: 'v1' }), pack)
await assert.rejects(() => parseReturnedPackage(JSON.stringify({ ...pack, job_id: 43 }), { jobId: 42, profileId: 3, cvUpdatedAt: 'v1' }), /does not match/i)
await assert.rejects(() => parseReturnedPackage(JSON.stringify({ ...pack, cv_updated_at: 'old' }), { jobId: 42, profileId: 3, cvUpdatedAt: 'v1' }), /does not match/i)
await assert.rejects(() => parseReturnedPackage(JSON.stringify({ ...pack, review_notes: 7 }), { jobId: 42, profileId: 3, cvUpdatedAt: 'v1' }), /review_notes/i)
const savedAt = Date.parse('2026-10-17T12:00:00.000Z')
assert.equal(saveDraft(storage, 42, 3, pack, { job_id: 42, profile_id: 3, cv_updated_at: 'v1' }, () => savedAt), true)
assert.equal(saveDraft(storage, 43, 3, { ...pack, job_id: 43 }, { job_id: 43, profile_id: 3, cv_updated_at: 'v1' }, () => savedAt), true)
assert.equal(saveDraft(storage, 42, 4, { ...pack, profile_id: 4 }, { job_id: 42, profile_id: 4, cv_updated_at: 'v1' }, () => savedAt), true)
assert.equal(saveDraft(storage, 42, 3, pack, { job_id: 42, profile_id: 3, cv_updated_at: 'old' }, () => savedAt), false, 'invalid context never overwrites a valid draft')
assert.deepEqual(readDraft(storage, 42, 3, 'v1', savedAt + 1000), pack)
assert.equal(readDraft(storage, 42, 3, 'changed-version'), null, 'a changed CV invalidates its draft')
assert.equal(readDraft(storage, 42, 3, 'v1', savedAt + 8 * 24 * 60 * 60 * 1000), null, 'stale drafts are rejected')
assert.equal(readDraft(storage, 42, 3, 'v1', savedAt - 1000), null, 'future draft timestamps are rejected')
deleteCv(storage, 3)
assert.equal(storage.getItem(draftStorageKey(42, 3)), null, 'CV deletion clears the first job draft for its profile')
assert.equal(storage.getItem(draftStorageKey(43, 3)), null, 'CV deletion clears every job draft for its profile')
assert.notEqual(storage.getItem(draftStorageKey(42, 4)), null, 'CV deletion preserves another profile draft')
assert.notEqual(storage.getItem(cvStorageKey(4)), null, 'CV deletion preserves another profile CV')
removeDraft(storage, 42, 3)
assert.equal(readDraft(storage, 42, 3, 'v1'), null, 'draft deletion is scoped to the selected job/profile')
for (const invalidBullets of [
  [{ experience_id: '', bullets: ['Fact'] }],
  [{ experience_id: 'role', bullets: [] }],
  [{ experience_id: 'role', bullets: ['Fact'] }, { experience_id: 'role', bullets: ['Again'] }],
  [{ experience_id: 7, bullets: ['Fact'] }],
]) await assert.rejects(() => parseReturnedPackage(JSON.stringify({ ...pack, experience_bullets: invalidBullets }), { jobId: 42, profileId: 3, cvUpdatedAt: 'v1' }), /experience_bullets/i)
const minimalPrompt = buildApplicationPrompt({text:'Original CV',updated_at:'v1'}, {
  job: {id:42,title:'Research role',company_name:'Labs',description:'Known description',location_text:'Karlsruhe',employment_type:null,work_arrangement:null,url:'https://employer.example/42',raw_metadata:{huge:'x'.repeat(400000)},application:{notes:'Unrelated private tracking notes'}},
  profile:{id:3,name:'Research',excluded_terms:['Not part of CV preparation'],search_area:{city:'Private search area'}}, returnUrl:'https://radius.example/jobs/42/apply?profile_id=3',
})
const minimalPayload = JSON.parse(minimalPrompt.split('SOURCE PAYLOAD (quoted data):').at(-1))
assert.deepEqual(Object.keys(minimalPayload.job).sort(), ['company_name','description','employment_type','id','location_text','title','url','work_arrangement'])
assert.deepEqual(minimalPayload.profile,{id:3,name:'Research'})
assert.ok(!minimalPrompt.includes('Unrelated private tracking notes'), 'Tracking notes must stay outside application prompts')
assert.ok(!minimalPrompt.includes('x'.repeat(400000)), 'Large raw API metadata must stay outside application prompts')
const boundedPrompt = buildApplicationPrompt({text:'Original CV',updated_at:'v1'}, {job:{id:42,title:'Research role',company_name:'Labs',description:'z'.repeat(50001),location_text:null,employment_type:null,work_arrangement:null,url:'https://employer.example/42'},profile:{id:3,name:'Research'},returnUrl:'https://radius.example/jobs/42/apply?profile_id=3'})
const boundedPayload = JSON.parse(boundedPrompt.split('SOURCE PAYLOAD (quoted data):').at(-1))
assert.equal(boundedPayload.job.description.length, 50000, 'Job source text keeps its input bound independently of instruction length')
console.log('Application preparation storage, bounded return decoding, and context validation passed')
