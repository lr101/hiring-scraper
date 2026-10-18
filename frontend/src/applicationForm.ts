export type FormField = { id: string; label: string; type: string; required: boolean; max_length: number | null; options: { value: string; label: string }[]; accept: string; manual: boolean }
export type ApplicationForm = { format: 'radius-form-v1'; job_id: number; profile_id: number; form_id: string; url: string; captured_at: number; fields: FormField[] }
export type FormAnswers = { form_id: string; answers: { id: string; value: string }[] }
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
export function validateFormAnswers(value: unknown, form?: ApplicationForm): FormAnswers | undefined {
  if (value === undefined) return
  const input = value as FormAnswers
  if (!input || !/^[a-f0-9]{64}$/.test(input.form_id) || !Array.isArray(input.answers) || input.answers.length > 100 || (form && input.form_id !== form.form_id)) throw new Error('These answers belong to a different application form. Scan the current step and prepare its answers again.')
  const ids = new Set<string>()
  for (const answer of input.answers) {
    if (!answer || !/^q\d{1,2}$/.test(answer.id) || ids.has(answer.id) || typeof answer.value !== 'string' || !answer.value.trim() || answer.value.length > 10000) throw new Error('Invalid or duplicate form answer.')
    ids.add(answer.id)
    if (/[\u0000-\u0008\u000B\u000C\u000E-\u001F]|\[(?:CONFIRM\b|TODO\b|TBD\b|INSERT\b|YOUR\b)[^\]]*\]|(?:^|\n)\s*(?:TODO|TBD)\s*[:—-]|(?:please|can you) confirm (?:your|the following)|awaiting your (?:confirmation|approval)/i.test(answer.value)) throw new Error('Prepared form answers contain unfinished or invalid text.')
    const field = form?.fields.find(field => field.id === answer.id)
    if (form && (!field || field.manual || (field.max_length && answer.value.length > field.max_length) || (field.type === 'select' && !field.options.some(option => option.value === answer.value || option.label === answer.value)))) throw new Error('A form answer does not match the captured question or its constraints.')
  }
  return input
}
