import { parseApplicationPackage } from './applicationHandoff.ts'
import type { Profile } from './profile'
import type { ApplicationForm } from './applicationForm'

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
