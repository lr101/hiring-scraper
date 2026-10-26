import assert from 'node:assert/strict'
import { downloadApplicationDocument } from '../frontend/src/applicationDocumentExport.ts'

const requests = []
globalThis.fetch = async (url, options) => {
  requests.push({ url, body: JSON.parse(options.body) })
  return { ok: true, blob: async () => new Blob(['document']) }
}
globalThis.document = { createElement: () => ({ click() {} }) }
const pack = { profile_id: 7, job_id: 8, cv_updated_at: 'r1', cv: 'CV', cover_letter: 'Letter' }
await downloadApplicationDocument(pack, 'cv', 'latex_pdf', 'cv.pdf', undefined, undefined, false)
assert.equal(requests.at(-1).body.use_profile_photo, false, 'Excluding the saved photo must reach the renderer')
await downloadApplicationDocument(pack, 'cv', 'latex_pdf', 'cv.pdf')
assert.equal(requests.at(-1).body.use_profile_photo, true, 'Saved profile photos are used by default')
await downloadApplicationDocument(pack, 'cv', 'tex_bundle', 'cv.zip', undefined, 'data:image/png;base64,override')
assert.equal(requests.at(-1).body.photo_data_url, 'data:image/png;base64,override')
await downloadApplicationDocument(pack, 'cover_letter', 'latex_pdf', 'letter.pdf', undefined, 'data:image/png;base64,override')
assert.equal(requests.at(-1).body.photo_data_url, undefined, 'Photos must not be sent for letters')
console.log('Saved photo defaults, per-application exclusion and temporary overrides passed.')
