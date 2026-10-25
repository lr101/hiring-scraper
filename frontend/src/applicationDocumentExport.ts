import { localCVSource } from './applicationLatex.ts'
import type { ApplicationPackage } from './applicationPreparation'
const API = (import.meta.env?.VITE_API_BASE_URL ?? '').replace(/\/$/, '')
export type DocumentFormat = 'pdf' | 'docx' | 'tex' | 'latex_pdf' | 'tex_bundle'
export async function downloadApplicationDocument(pack: Pick<ApplicationPackage, 'job_id' | 'profile_id' | 'cv_updated_at' | 'cv' | 'cover_letter' | 'latex_documents'>, kind: 'cv' | 'cover_letter', format: DocumentFormat, filename: string, originalCV?: string, photoDataURL?: string) {
  const source = kind === 'cv' && originalCV ? localCVSource(originalCV, pack.latex_documents) : null
  if (source && format === 'tex') {
    downloadBlob(new Blob([source], { type: 'text/plain;charset=utf-8' }), filename)
    return
  }
  const response = await fetch(`${API}/api/v1/profiles/${pack.profile_id}/application-document?kind=${kind}&format=${format}`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, cache: 'no-store', signal: AbortSignal.timeout(45000),
    body: JSON.stringify({ job_id: pack.job_id, cv_updated_at: pack.cv_updated_at, cv: pack.cv, cover_letter: pack.cover_letter, ...(pack.latex_documents ? { latex_documents: pack.latex_documents } : {}), ...(kind === 'cv' && photoDataURL && ['latex_pdf', 'tex_bundle'].includes(format) ? { photo_data_url: photoDataURL } : {}) }),
  })
  if (!response.ok) { const result = await response.json(); throw new Error(typeof result.detail === 'string' ? result.detail : 'Could not export this document.') }
  const blob = await response.blob()
  if (!blob.size || blob.size > 5242880) throw new Error('The exported document is empty or too large.')
  downloadBlob(blob, filename)
}

function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob), link = document.createElement('a')
  link.href = url; link.download = filename; link.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

// Photos stay in this page's memory; they are never included in prompts or drafts.
export async function readCVPhoto(file: File): Promise<string> {
  if (file.type !== 'image/png' || file.size > 2 * 1024 * 1024) throw new Error('Choose a PNG photo under 2 MiB.')
  const bitmap = await createImageBitmap(file).catch(() => { throw new Error('Could not read this PNG photo.') })
  const valid = bitmap.width <= 4096 && bitmap.height <= 4096
  bitmap.close()
  if (!valid) throw new Error('Use a PNG photo no larger than 4096 pixels per side.')
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onerror = () => reject(new Error('Could not read this PNG photo.'))
    reader.onload = () => resolve(reader.result as string)
    reader.readAsDataURL(file)
  })
}
