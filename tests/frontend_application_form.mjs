import assert from 'node:assert/strict'
import { formStorageKey, readApplicationForm } from '../frontend/src/applicationForm.ts'

const form = {
  format: 'radius-form-v1', job_id: 42, profile_id: 9, form_id: 'a'.repeat(64),
  url: 'https://employer.example/apply?jobId=42', captured_at: Date.now(),
  fields: [{ id: 'q0', label: 'Why this role?', type: 'textarea', required: true,
    max_length: 180, options: [], accept: '', manual: false }],
}
const storage = new Map([[formStorageKey(42, 9), JSON.stringify(form)]])
globalThis.localStorage = { getItem: key => storage.get(key) }
assert.deepEqual(readApplicationForm(42, 9), form)
assert.equal(readApplicationForm(43, 9), undefined)
storage.set(formStorageKey(42, 9), JSON.stringify({ ...form, fields: [null] }))
assert.equal(readApplicationForm(42, 9), undefined, 'Corrupted captured fields must be ignored')
console.log('Captured application forms are validated and scoped to their job and profile')
