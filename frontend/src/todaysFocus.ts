export type FocusItem = {
  job_id: number
  title: string
  company: string | null
  status: 'new' | 'open'
  match_score: number | null
  documents_ready: boolean
  reason: string
}

export type TodaysFocus = {
  items: FocusItem[]
  sent_today: number
  daily_target: number
  completed: boolean
}

export async function getTodaysFocus(api: string, profileId: number): Promise<TodaysFocus> {
  const response = await fetch(`${api}/api/v1/profiles/${profileId}/focus`)
  const data = await response.json() as TodaysFocus & { detail?: unknown }
  if (!response.ok) {
    throw new Error(typeof data.detail === 'string' ? data.detail : 'Could not load today’s focus.')
  }
  return data
}

/** Ready documents go straight to review; everything else opens the role. */
export function focusHref(item: FocusItem, profileId: number): string {
  const query = new URLSearchParams({ profile_id: String(profileId) })
  if (item.documents_ready) return `/jobs/${item.job_id}/apply?${query}`
  query.set('from', 'applications')
  return `/jobs/${item.job_id}?${query}`
}

export function focusHeadline(focus: TodaysFocus): string {
  if (focus.completed) return 'Today is done. Anything more is a bonus.'
  if (!focus.items.length) return 'Nothing waiting. New matches will appear here.'
  const remaining = Math.max(0, focus.daily_target - focus.sent_today)
  return `${remaining} ${remaining === 1 ? 'application' : 'applications'} to finish today`
}
