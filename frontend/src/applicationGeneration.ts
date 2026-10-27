import { parseApplicationPackage } from './applicationHandoff.ts'
import type { Profile } from './profile'
import type { ApplicationForm } from './applicationForm'

export type PreparationJob = {
  id: string; state: 'running' | 'cancelling' | 'cancelled' | 'completed' | 'failed'; stage: string; error?: string; started_at?: number
  request?: { cv_updated_at: string; profile_updated_at: string; language: 'en' | 'de'; notes?: string; form?: ApplicationForm | null }
  result?: { package?: unknown }
}

export function cancelPreparationJob(api: string, profileId: number, jobId: number, expectedId: string) {
  const query = new URLSearchParams({ job_id: String(jobId), expected_id: expectedId })
  return jobRequest(`${api}/api/v1/profiles/${profileId}/preparation-job/cancel?${query}`, { method: 'POST' })
}

export type SavedApplicationDraft = { version: string; draft: Record<string, unknown> }
export function matchesDraftContext(draft: { context?: unknown; profile_updated_at?: unknown }, context: unknown, profileUpdatedAt: string | undefined) {
  return !!profileUpdatedAt && draft.profile_updated_at === profileUpdatedAt && JSON.stringify(draft.context) === JSON.stringify(context)
}
export function matchesLegacyDraftContext(draft: { context?: unknown; profile_updated_at?: unknown }, context: unknown) {
  return draft.profile_updated_at == null && JSON.stringify(draft.context) === JSON.stringify(context)
}
export function applicationDraftSnapshot(value: Record<string, unknown>) {
  return JSON.stringify([value.context, value.profile_updated_at, value.package, value.step, value.language, value.notes, value.response])
}
async function draftRequest(url: string, options: RequestInit = {}): Promise<SavedApplicationDraft | null> {
  const result = await fetch(url, { ...options, cache: 'no-store' })
  const data = await result.json().catch(() => null)
  if (!result.ok) throw new Error(typeof data?.detail === 'string' ? data.detail : 'Could not sync your draft. Your local copy is unchanged.')
  return data?.saved ?? null
}
export function getApplicationDraft(api: string, profileId: number, jobId: number, signal?: AbortSignal) {
  return draftRequest(`${api}/api/v1/profiles/${profileId}/application-draft?job_id=${jobId}`, { signal })
}
export function saveApplicationDraft(api: string, profileId: number, jobId: number, draft: Record<string, unknown>, version: string | null) {
  return draftRequest(`${api}/api/v1/profiles/${profileId}/application-draft?job_id=${jobId}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ version, draft }) })
}
export async function deleteApplicationDraft(api: string, profileId: number, jobId: number) {
  const result = await fetch(`${api}/api/v1/profiles/${profileId}/application-draft?job_id=${jobId}`, { method: 'DELETE', cache: 'no-store' })
  if (!result.ok) throw new Error('Could not delete the saved application. Please try again.')
}

async function jobRequest(url: string, options: RequestInit = {}): Promise<PreparationJob | null> {
  const result = await fetch(url, { ...options, cache: 'no-store' })
  const data = await result.json().catch(() => null)
  if (!result.ok) throw new Error(typeof data?.detail === 'string' ? data.detail : 'Could not check preparation. Retrying may help.')
  return data?.job ?? null
}

export function getPreparationJob(api: string, profileId: number, jobId: number, signal?: AbortSignal) {
  return jobRequest(`${api}/api/v1/profiles/${profileId}/preparation-job?job_id=${jobId}`, { signal })
}

export function startPreparationJob({ api, jobId, profile, language, notes, form, signal }: {
  api: string; jobId: number; profile: Pick<Profile, 'id' | 'cv' | 'updated_at'>
  language: 'en' | 'de'; notes: string; form?: ApplicationForm; signal?: AbortSignal
}) {
  if (!profile.id || !profile.cv?.updated_at || !profile.updated_at) throw new Error('Reload this application to use the current saved profile and CV.')
  return jobRequest(`${api}/api/v1/profiles/${profile.id}/preparation-job`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, signal,
    body: JSON.stringify({ job_id: jobId, cv_updated_at: profile.cv.updated_at, profile_updated_at: profile.updated_at, language, notes, ...(form ? { form } : {}) }),
  })
}

export async function prepareInsideRadius({ api, jobId, profile, language, notes, cvText, form, signal }: {
  api: string; jobId: number; profile: Pick<Profile, 'id' | 'cv' | 'updated_at'>
  language: 'en' | 'de'; notes: string; cvText: string; form?: ApplicationForm; signal?: AbortSignal
}) {
  if (!profile.id || !profile.cv?.updated_at || !profile.updated_at) throw new Error('Reload this application to use the current saved profile and CV.')
  const context = { job_id: jobId, profile_id: profile.id, cv_updated_at: profile.cv.updated_at }
  const result = await fetch(`${api}/api/v1/profiles/${profile.id}/prepare-application`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, signal,
    body: JSON.stringify({ job_id: jobId, cv_updated_at: profile.cv.updated_at, profile_updated_at: profile.updated_at, language, notes, ...(form ? { form } : {}) }),
  })
  const data = await result.json().catch(() => null)
  if (!result.ok) throw new Error(typeof data?.detail === 'string' ? data.detail : 'Could not prepare this application. Try again or use ChatGPT.')
  return parseApplicationPackage(JSON.stringify(data?.package ?? null), context, undefined, form, cvText)
}
