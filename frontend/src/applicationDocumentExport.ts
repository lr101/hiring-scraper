import type { ApplicationPackage } from './applicationPreparation'
const API = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '')
export async function downloadApplicationDocument(pack: Pick<ApplicationPackage, 'job_id' | 'profile_id' | 'cv_updated_at' | 'cv' | 'cover_letter'>, kind: 'cv' | 'cover_letter', format: 'pdf' | 'docx', filename: string) {
  const response = await fetch(`${API}/api/v1/profiles/${pack.profile_id}/application-document?kind=${kind}&format=${format}`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, cache: 'no-store', signal: AbortSignal.timeout(30000),
    body: JSON.stringify({ job_id: pack.job_id, cv_updated_at: pack.cv_updated_at, cv: pack.cv, cover_letter: pack.cover_letter }),
  })
  if (!response.ok) { const result = await response.json(); throw new Error(typeof result.detail === 'string' ? result.detail : 'Could not export this document.') }
  const blob = await response.blob()
  if (!blob.size || blob.size > 5242880) throw new Error('The exported document is empty or too large.')
  const url = URL.createObjectURL(blob), link = document.createElement('a')
  link.href = url; link.download = filename; link.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
