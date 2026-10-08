export const applicationStatuses = [
  { value: 'new', label: 'New' },
  { value: 'open', label: 'Open' },
  { value: 'not_interested', label: 'Not interested' },
  { value: 'waiting_for_reply', label: 'Waiting for reply' },
  { value: 'interview', label: 'Interview' },
  { value: 'rejected', label: 'Rejected' },
  { value: 'accepted', label: 'Accepted' },
] as const

export type ApplicationStatus = typeof applicationStatuses[number]['value']
export type Application = {
  profile_id: number; job_id: number; status: ApplicationStatus; created_at: string; updated_at: string
}

export async function saveApplication(base: string, profileId: number, jobId: number, status: ApplicationStatus): Promise<Application> {
  const response = await fetch(`${base}/api/v1/profiles/${profileId}/applications/${jobId}`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status }),
  })
  const data = await response.json()
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Could not save application status. Please try again.')
  return data
}
