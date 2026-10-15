export type ApplicationMomentum = {
  weekly_goal: number
  applied_this_week: number
  lifetime_applied: number
  successful_weeks: number
  interview_count: number
  jobs_won: number
}

const API = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '')

async function readMomentum(response: Response): Promise<ApplicationMomentum> {
  const data = await response.json() as ApplicationMomentum & { detail?: unknown }
  if (!response.ok) {
    throw new Error(typeof data.detail === 'string' ? data.detail : 'Could not load application progress.')
  }
  return data
}

export async function getApplicationMomentum(): Promise<ApplicationMomentum> {
  return readMomentum(await fetch(`${API}/api/v1/application-momentum`))
}
