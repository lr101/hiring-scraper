export const applicationStatuses = [
  { value: 'new', label: 'New' },
  { value: 'open', label: 'Interested' },
  { value: 'in_gots_hand', label: 'In gots hand' },
  { value: 'waiting_for_reply', label: 'Waiting for reply' },
  { value: 'automated_application', label: 'Automated application' },
  { value: 'interview', label: 'Interview' },
  { value: 'not_interested', label: 'Not interested' },
  { value: 'rejected', label: 'Rejected' },
  { value: 'accepted', label: 'Accepted' },
] as const

export type ApplicationStatus = typeof applicationStatuses[number]['value']
export type SwipeDirection = 'left' | 'right'
export type ApplicationSwipeAction = { kind: 'stage' | 'undo'; status: ApplicationStatus }
export const nextApplicationStages: Record<ApplicationStatus, ApplicationStatus[]> = {
  new: ['open', 'in_gots_hand', 'not_interested'],
  open: ['waiting_for_reply', 'in_gots_hand', 'not_interested'],
  in_gots_hand: ['waiting_for_reply', 'not_interested'],
  waiting_for_reply: ['interview', 'rejected'],
  automated_application: ['interview', 'rejected'],
  interview: ['accepted', 'rejected'],
  accepted: [],
  rejected: [],
  not_interested: [],
}
export const swipeApplicationStages: Record<ApplicationStatus, Partial<Record<SwipeDirection, ApplicationStatus>>> = {
  new: { left: 'not_interested', right: 'open' },
  open: { left: 'not_interested', right: 'waiting_for_reply' },
  in_gots_hand: { left: 'not_interested', right: 'waiting_for_reply' },
  waiting_for_reply: { left: 'rejected', right: 'interview' },
  automated_application: { left: 'rejected', right: 'interview' },
  interview: { left: 'rejected', right: 'accepted' },
  accepted: {},
  rejected: {},
  not_interested: {},
}

export function applicationStatusLabel(status?: ApplicationStatus | null) {
  return status ? applicationStatuses.find(item => item.value === status)?.label ?? 'Tracked' : 'Not tracked'
}

/** Resolve each horizontal gesture from the same stage map on every job surface. */
export function applicationSwipeAction(
  application: Pick<Application, 'status' | 'previous_status'> | null | undefined,
  direction: SwipeDirection,
): ApplicationSwipeAction | null {
  const current = application?.status ?? 'new'
  const next = swipeApplicationStages[current][direction]
  if (next) return { kind: 'stage', status: next }
  const previous = application?.previous_status
  return previous && previous !== current ? { kind: 'undo', status: previous } : null
}

export type Application = {
  profile_id: number; job_id: number; status: ApplicationStatus; previous_status?: ApplicationStatus | null
  created_at: string; updated_at: string
}

export async function saveApplication(
  base: string,
  profileId: number,
  jobId: number,
  status: ApplicationStatus,
  action: ApplicationSwipeAction['kind'] = 'stage',
): Promise<Application> {
  const response = await fetch(`${base}/api/v1/profiles/${profileId}/applications/${jobId}`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status, action }),
  })
  const data = await response.json()
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Could not save application status. Please try again.')
  return data
}

export async function trackJobInteraction(
  base: string,
  profileId: number,
  jobId: number,
  current: Application | null | undefined,
  interaction: 'view' | 'open_post',
): Promise<Application | null> {
  let latest = current ?? null
  if (interaction === 'open_post') {
    const response = await fetch(`${base}/api/v1/jobs/${jobId}?profile_id=${profileId}`)
    const payload = await response.json()
    if (!response.ok) throw new Error(typeof payload.detail === 'string' ? payload.detail : 'Could not update application stage.')
    return payload.application ?? null
  }
  if (latest) return latest
  return saveApplication(base, profileId, jobId, 'new')
}
