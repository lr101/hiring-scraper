export type FormField = { id: string; label: string; type: string; required: boolean; max_length: number | null; options: { value: string; label: string }[]; accept: string; manual: boolean }
export type ApplicationForm = { format: 'radius-form-v1'; job_id: number; profile_id: number; form_id: string; url: string; captured_at: number; fields: FormField[] }
export const formStorageKey = (jobId: number, profileId: number) => `radius:application-form:v1:${jobId}:${profileId}`
export function readApplicationForm(jobId: number, profileId: number): ApplicationForm | undefined {
  try {
    const raw = localStorage.getItem(formStorageKey(jobId, profileId))
    if (!raw || raw.length > 32000) return
    const form = JSON.parse(raw)
    if (form.format !== 'radius-form-v1' || form.job_id !== jobId || form.profile_id !== profileId || !/^[a-f0-9]{64}$/.test(form.form_id) || !Array.isArray(form.fields) || form.fields.length > 100 || !Number.isFinite(form.captured_at) || Date.now() - form.captured_at > 7 * 86400000 || form.captured_at > Date.now()) return
    const url = new URL(form.url)
    if (!['https:', 'http:'].includes(url.protocol) || url.username || url.password) return
    const ids = new Set<string>()
    for (const field of form.fields) {
      if (!field || !/^q\d{1,2}$/.test(field.id) || ids.has(field.id) || typeof field.label !== 'string' || typeof field.type !== 'string' || typeof field.required !== 'boolean' || typeof field.manual !== 'boolean' || typeof field.accept !== 'string' || !(field.max_length === null || Number.isSafeInteger(field.max_length) && field.max_length > 0) || !Array.isArray(field.options) || field.options.some((option: { value: unknown; label: unknown }) => !option || typeof option.value !== 'string' || typeof option.label !== 'string')) return
      ids.add(field.id)
    }
    return form
  } catch { return }
}
