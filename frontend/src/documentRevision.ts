import type { Profile } from './profile'

export type RevisionTarget = 'cv' | 'cover_letter'
export type RevisionJob = {
  id: string
  state: 'running' | 'cancelling' | 'cancelled' | 'completed' | 'failed'
  stage: string
  error?: string
  request?: { target: RevisionTarget; current_document: string; instruction: string; language: 'en' | 'de'; cv_updated_at: string; profile_updated_at: string }
  result?: { target: RevisionTarget; document: string; review: string; changes: string }
}

type RevisionProfile = Pick<Profile, 'id' | 'cv' | 'updated_at'>
async function revisionRequest(url: string, options: RequestInit = {}): Promise<RevisionJob | null> {
  const response = await fetch(url, { ...options, cache: 'no-store' })
  const data = await response.json().catch(() => null)
  if (!response.ok) throw new Error(typeof data?.detail === 'string' ? data.detail : 'Could not revise this document. Your current document is unchanged.')
  return data?.job ?? null
}

export function getDocumentRevision(api: string, profileId: number, jobId: number, signal?: AbortSignal) {
  return revisionRequest(`${api}/api/v1/profiles/${profileId}/document-revision?job_id=${jobId}`, { signal })
}
export function cancelDocumentRevision(api: string, profileId: number, jobId: number, revisionId: string) {
  return revisionRequest(`${api}/api/v1/profiles/${profileId}/document-revision/cancel?job_id=${jobId}&revision_id=${encodeURIComponent(revisionId)}`, { method: 'POST' })
}
export async function discardDocumentRevision(api: string, profileId: number, jobId: number, revisionId: string) {
  const response = await fetch(`${api}/api/v1/profiles/${profileId}/document-revision?job_id=${jobId}&revision_id=${encodeURIComponent(revisionId)}`, { method: 'DELETE', cache: 'no-store' })
  if (!response.ok) throw new Error('Could not discard the suggestion. Your current document is unchanged.')
}
export async function verifyDocumentRevision(api: string, profileId: number, jobId: number, revisionId: string) {
  const response = await fetch(`${api}/api/v1/profiles/${profileId}/document-revision/check?job_id=${jobId}&revision_id=${encodeURIComponent(revisionId)}`, { method: 'POST', cache: 'no-store' })
  const data = await response.json().catch(() => null)
  if (!response.ok) throw new Error(typeof data?.detail === 'string' ? data.detail : 'Could not verify this suggestion. Your document is unchanged.')
}
export function startDocumentRevision({ api, jobId, profile, language, target, currentDocument, instruction }: {
  api: string; jobId: number; profile: RevisionProfile; language: 'en' | 'de'; target: RevisionTarget; currentDocument: string; instruction: string
}) {
  if (!profile.id || !profile.cv?.updated_at || !profile.updated_at) throw new Error('Reload this application to use the current saved profile and CV.')
  return revisionRequest(`${api}/api/v1/profiles/${profile.id}/document-revision`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ job_id: jobId, cv_updated_at: profile.cv.updated_at, profile_updated_at: profile.updated_at, language, target, current_document: currentDocument, instruction: instruction.trim() }),
  })
}

export function canApplyRevision(job: RevisionJob | null, target: RevisionTarget, current: string, profile: RevisionProfile, language: 'en' | 'de') {
  return !!job && job.state === 'completed' && job.request?.target === target && job.result?.target === target &&
    job.request.current_document === current && job.request.cv_updated_at === profile.cv?.updated_at &&
    job.request.profile_updated_at === profile.updated_at && job.request.language === language &&
    typeof job.result.document === 'string' && !!job.result.document.trim()
}

export function assertRevisionPackageFits(value: unknown) {
  if (JSON.stringify(value).length > 200000) throw new Error('This revision makes the application package too large to save. Keep the current version or request a shorter revision.')
}
export function assertRevisionDraftFits(value: unknown) {
  if (JSON.stringify({ version: 'x'.repeat(32), draft: value }).length > 450000) throw new Error('This application draft is too large to save. Clear the imported response or request a shorter revision.')
}
export function hasEmptyCVSource(value: { cv?: string } | undefined) {
  return !!value && Object.prototype.hasOwnProperty.call(value, 'cv') && !value.cv?.trim()
}

export function persistRevisionLocally(storage: Pick<Storage, 'setItem'>, key: string, draft: unknown) {
  try { storage.setItem(key, JSON.stringify(draft)) }
  catch { throw new Error('Could not save this version in the browser. Free storage or enable site storage, then try again; your current document is unchanged.') }
}
