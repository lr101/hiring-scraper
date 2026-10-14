import { useEffect, useState } from 'react'
import SiteHeader from './SiteHeader'
import JobFeedCard from './JobFeedCard'
import { applicationStatuses, saveApplication, trackJobInteraction } from './applications'
import type { Application, ApplicationStatus, ApplicationSwipeAction } from './applications'
import type { Enrichment, ProfileMatch } from './JobEvidence'
import type { Profile } from './profile'

const API = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '')
const PAGE_SIZE = 40
const sortModes = [
  { value: 'match', label: 'Best match' },
  { value: 'posted', label: 'Posted date' },
  { value: 'discovered', label: 'Discovered' },
] as const
type SortMode = typeof sortModes[number]['value']

type MatchingJob = {
  id: number; title: string; company_name: string; location_text: string | null
  url?: string | null
  salary?: string | null; employment_type?: string | null; schedule?: string | null; seniority?: string | null
  work_arrangement?: string | null; is_remote?: boolean
  is_active: boolean; expired: boolean; date_posted?: string | null; last_seen_at: string; first_seen_at: string
  application?: Application | null; enrichment?: Enrichment; profile_match?: ProfileMatch
}
type ProfiledJob = MatchingJob & { profileId: number; profileName: string }
type SavedProfile = Profile & { id: number }
type JobsPage = { items: Array<MatchingJob & { profile_id: number; profile_name: string }>; total: number; counts: Record<ApplicationStatus, number> }

function readStatusSelection(params: URLSearchParams): ApplicationStatus[] {
  if (params.has('statuses')) {
    const requested = params.get('statuses')
    if (!requested || requested === 'all') return []
    const values = new Set(requested.split(',').filter(value => applicationStatuses.some(item => item.value === value)))
    return applicationStatuses.filter(item => values.has(item.value)).map(item => item.value)
  }
  const legacyStatus = params.get('status')
  if (legacyStatus === 'all' || legacyStatus === '') return []
  const match = applicationStatuses.find(item => item.value === legacyStatus)
  return match ? [match.value] : ['new']
}

function jobUpdatedAt(job: MatchingJob) {
  return job.application?.updated_at ?? job.last_seen_at ?? job.first_seen_at
}

function profileJobKey(job: Pick<ProfiledJob, 'profileId' | 'id'>) {
  return `${job.profileId}:${job.id}`
}

export default function ApplicationsPage() {
  const [params] = useState(() => new URLSearchParams(window.location.search))
  const [profiles, setProfiles] = useState<SavedProfile[]>([])
  const [activeProfileIds, setActiveProfileIds] = useState<number[]>([])
  const [profilesReady, setProfilesReady] = useState(false)
  const [statuses, setStatuses] = useState<ApplicationStatus[]>(() => readStatusSelection(params))
  const [sortBy, setSortBy] = useState<SortMode>(() =>
    sortModes.find(item => item.value === params.get('sort'))?.value ?? 'match')
  const [jobs, setJobs] = useState<ProfiledJob[]>([])
  const [counts, setCounts] = useState<Record<ApplicationStatus, number>>(() =>
    Object.fromEntries(applicationStatuses.map(item => [item.value, 0])) as Record<ApplicationStatus, number>)
  const [totalVisible, setTotalVisible] = useState(0)
  const [offset, setOffset] = useState(0)
  const [reloadKey, setReloadKey] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [swipeSavingKey, setSwipeSavingKey] = useState<string | null>(null)
  const activeProfileKey = activeProfileIds.join(',')
  const allProfilesActive = profiles.length > 0 && activeProfileIds.length === profiles.length

  useEffect(() => {
    const controller = new AbortController()
    fetch(`${API}/api/v1/profiles`, { signal: controller.signal }).then(async response => {
      if (!response.ok) throw new Error('Could not load saved profiles.')
      const data = await response.json() as { items: SavedProfile[] }
      const availableProfiles = data.items
      setProfiles(availableProfiles)

      const requestedIds = params.get('profile_ids')
      const legacyId = params.get('profile_id')
      const requested = requestedIds !== null
        ? requestedIds.split(',').map(value => Number(value)).filter(Number.isSafeInteger)
        : legacyId ? [Number(legacyId)] : availableProfiles.map(profile => profile.id)
      const validIds = new Set(availableProfiles.map(profile => profile.id))
      setActiveProfileIds([...new Set(requested.filter(id => validIds.has(id)))])
      setProfilesReady(true)
    }).catch(reason => { if (!controller.signal.aborted) { setError(reason.message); setLoading(false) } })
    return () => controller.abort()
  }, [params])

  useEffect(() => {
    if (!profilesReady) return
    if (!activeProfileIds.length) {
      setJobs([])
      setCounts(Object.fromEntries(applicationStatuses.map(item => [item.value, 0])) as Record<ApplicationStatus, number>)
      setTotalVisible(0)
      setLoading(false)
      setError('')
      return
    }

    const controller = new AbortController()
    setLoading(true)
    setError('')
    const query = new URLSearchParams({
      profile_ids: activeProfileKey,
      statuses: statuses.length ? statuses.join(',') : 'all',
      sort: sortBy,
      offset: String(offset),
      limit: String(PAGE_SIZE),
    })
    fetch(`${API}/api/v1/applications?${query}`, { signal: controller.signal }).then(async response => {
      const data = await response.json() as JobsPage & { detail?: unknown }
      if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Could not load matching jobs.')
      setJobs(data.items.map(job => ({
        ...job,
        profileId: job.profile_id,
        profileName: job.profile_name,
      })))
      setCounts(data.counts)
      setTotalVisible(data.total)
    }).catch(reason => {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : 'Could not load matching jobs.')
    }).finally(() => { if (!controller.signal.aborted) setLoading(false) })

    return () => controller.abort()
  }, [profilesReady, activeProfileKey, activeProfileIds.length, reloadKey, profiles, statuses, sortBy, offset])

  useEffect(() => {
    if (!profilesReady) return
    const url = new URL(window.location.href)
    url.searchParams.delete('profile_id')
    url.searchParams.set('profile_ids', activeProfileKey)
    url.searchParams.delete('status')
    url.searchParams.set('statuses', statuses.length ? statuses.join(',') : 'all')
    if (sortBy === 'match') url.searchParams.delete('sort')
    else url.searchParams.set('sort', sortBy)
    window.history.replaceState({}, '', url)
  }, [profilesReady, activeProfileKey, statuses, sortBy])

  const filteredJobs = jobs
  const trackedCount = Object.values(counts).reduce((total, count) => total + count, 0)
  const sortIndex = sortModes.findIndex(item => item.value === sortBy)
  const nextSort = sortModes[(sortIndex + 1) % sortModes.length]

  function filter(value: ApplicationStatus | '') {
    if (!value) setStatuses([])
    else setStatuses(current => current.includes(value) ? current.filter(item => item !== value) : [...current, value])
    setOffset(0)
    setNotice('')
  }

  function cycleSort() {
    setSortBy(nextSort.value)
    setOffset(0)
  }

  function toggleProfile(profileId: number) {
    setActiveProfileIds(current => current.includes(profileId)
      ? current.filter(id => id !== profileId)
      : [...current, profileId].sort((a, b) => profiles.findIndex(profile => profile.id === a) - profiles.findIndex(profile => profile.id === b)))
    setOffset(0)
    setJobs([])
    setNotice('')
  }

  function selectAllProfiles() {
    setActiveProfileIds(profiles.map(profile => profile.id))
    setOffset(0)
    setJobs([])
    setNotice('')
  }

  function applyLocalStatus(profileId: number, jobId: number, application: Application) {
    const currentJob = jobs.find(job => job.profileId === profileId && job.id === jobId)
    const currentStatus = currentJob?.application?.status ?? 'new'
    const wasSelected = !statuses.length || statuses.includes(currentStatus)
    const isSelected = !statuses.length || statuses.includes(application.status)
    const leavesCurrentFilter = wasSelected && !isSelected
    if (leavesCurrentFilter && offset > 0 && totalVisible - 1 <= offset) {
      setOffset(Math.max(0, offset - PAGE_SIZE))
    }
    if (currentStatus !== application.status) {
      setCounts(current => ({
        ...current,
        [currentStatus]: Math.max(0, current[currentStatus] - 1),
        [application.status]: current[application.status] + 1,
      }))
      if (wasSelected !== isSelected) setTotalVisible(current => Math.max(0, current + (isSelected ? 1 : -1)))
      setReloadKey(value => value + 1)
    }
    setJobs(current => current.map(job => job.profileId === profileId && job.id === jobId ? { ...job, application } : job)
    )
  }

  async function updateJob(job: ProfiledJob, action: ApplicationSwipeAction) {
    const key = profileJobKey(job)
    if (swipeSavingKey !== null) return
    setError('')
    setNotice('')
    setSwipeSavingKey(key)
    try {
      const saved = await saveApplication(API, job.profileId, job.id, action.status)
      applyLocalStatus(job.profileId, job.id, saved)
      const label = applicationStatuses.find(item => item.value === action.status)?.label
      setNotice(action.kind === 'undo'
        ? `${job.title} · ${job.profileName} · restored to ${label}`
        : `${job.title} · ${job.profileName} · ${label}`)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not save application status.')
    } finally { setSwipeSavingKey(null) }
  }

  async function openOriginalPost(job: ProfiledJob) {
    try {
      const saved = await trackJobInteraction(API, job.profileId, job.id, job.application, 'open_post')
      if (saved) applyLocalStatus(job.profileId, job.id, saved)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not update application status.')
    }
  }

  function cardHref(job: ProfiledJob) {
    const query = new URLSearchParams({ profile_id: String(job.profileId), profile_ids: activeProfileKey, from: 'applications' })
    query.set('statuses', statuses.length ? statuses.join(',') : 'all')
    return `/jobs/${job.id}?${query}`
  }

  return <div className="app-shell"><SiteHeader active="applications" />
    <main className="content applications-page">
      <section className="intro-row"><h1>My applications</h1></section>
      {profilesReady && profiles.length > 0 && <section className="application-filters" aria-label="Filter applications">
        <div className="application-profile-filters" role="group" aria-label="Filter by profile">
          <button type="button" className={`application-filter-chip ${allProfilesActive ? 'selected' : ''}`} aria-pressed={allProfilesActive}
            onClick={selectAllProfiles}>All profiles</button>
          {profiles.map(profile => <button type="button" key={profile.id}
            className={`application-filter-chip ${activeProfileIds.includes(profile.id) ? 'selected' : ''}`}
            aria-pressed={activeProfileIds.includes(profile.id)} onClick={() => toggleProfile(profile.id)}>{profile.name}</button>)}
        </div>
        <div className="application-status-grid" role="group" aria-label="Filter by application stage">
          <button type="button" aria-pressed={statuses.length === 0} className={`application-filter-chip application-status-filter ${statuses.length === 0 ? 'selected' : ''}`}
            onClick={() => filter('')}>All <span>{loading ? '—' : trackedCount}</span></button>
          {applicationStatuses.map(item => <button type="button" key={item.value} aria-pressed={statuses.includes(item.value)}
            className={`application-filter-chip application-status-filter application-${item.value} ${statuses.includes(item.value) ? 'selected' : ''}`}
            onClick={() => filter(item.value)}>
            {item.label} <span>{loading ? '—' : counts[item.value]}</span>
          </button>)}
        </div>
      </section>}
      {error && <div className="error-banner" role="alert">{error}</div>}
      {notice && <p className="evidence-note" role="status">{notice}</p>}
      {profilesReady && !profiles.length ? <section className="detail-panel empty-state">
        <h2>Create a job profile to get started</h2><p>Save a profile to see matching jobs and track each opportunity here.</p>
        <a className="page-link" href="/profile">Create a profile →</a>
      </section> : profiles.length > 0 && <section className="directory-card applications-results">
        <div className="application-results-summary"><span>{totalVisible} {statuses.length ? statuses.length === 1 ? 'jobs in this stage' : 'jobs in selected stages' : 'jobs'}</span>
          <span>{trackedCount} tracked</span>
          <button type="button" className="application-sort-cycle" onClick={cycleSort}
            aria-label={`Sort by ${sortModes[sortIndex].label}. Tap to switch to ${nextSort.label}.`}
            title={`Tap to sort by ${nextSort.label}`}>Sort: {sortModes[sortIndex].label} <span aria-hidden="true">↻</span></button>
        </div>
        {loading ? <div className="loading-state"><span className="spinner" /> Loading matching jobs…</div>
          : error ? <div className="empty-state"><p>Matching jobs could not be loaded.</p><button type="button" className="broaden-button" onClick={() => setReloadKey(value => value + 1)}>Try again</button></div>
            : !activeProfileIds.length ? <div className="empty-state"><h2>Choose a profile</h2><p>Turn on one or more profiles above to see their matching jobs.</p></div>
              : filteredJobs.length ? <div className="application-job-list">
                {filteredJobs.map(job => {
                  const key = profileJobKey(job)
                  return <JobFeedCard key={key} job={job} href={cardHref(job)} profileId={job.profileId} profileName={job.profileName}
                    updatedAt={jobUpdatedAt(job)} disabled={swipeSavingKey !== null} saving={swipeSavingKey === key}
                    onAction={action => { void updateJob(job, action) }} onOpenOriginal={() => { void openOriginalPost(job) }} />
                })}
              </div> : <div className="empty-state"><h2>{statuses.length ? statuses.length === 1 ? 'No jobs in this stage' : 'No jobs in these stages' : 'No matching jobs yet'}</h2>
                <p>{statuses.length ? 'Choose another stage or clear the stage filters.' : 'The selected profiles do not have any matching or tracked jobs yet.'}</p>
              </div>}
        <div className="table-footer"><span>{totalVisible} {statuses.length ? statuses.length === 1 ? 'in this stage' : 'in selected stages' : 'jobs'} · {trackedCount} tracked</span>
          <div className="pagination"><button type="button" disabled={offset === 0 || loading} onClick={() => setOffset(value => Math.max(0, value - PAGE_SIZE))}>Previous</button>
            <span>{Math.floor(offset / PAGE_SIZE) + 1} / {Math.max(1, Math.ceil(totalVisible / PAGE_SIZE))}</span>
            <button type="button" disabled={loading || offset + PAGE_SIZE >= totalVisible} onClick={() => setOffset(value => value + PAGE_SIZE)}>Next</button></div>
        </div>
      </section>}
    </main>
  </div>
}
