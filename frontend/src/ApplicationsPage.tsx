import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
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
type ApplicationResultsPage = { jobs: ProfiledJob[]; total: number; counts: Record<ApplicationStatus, number> }

const MAX_CACHED_RESULT_PAGES = 32

function applicationResultsKey(profileKey: string, statuses: ApplicationStatus[], sort: SortMode, offset: number) {
  const normalizedStatuses = applicationStatuses
    .filter(item => statuses.includes(item.value))
    .map(item => item.value)
  return JSON.stringify([profileKey, normalizedStatuses, sort, offset])
}

function cacheApplicationResults(cache: Map<string, ApplicationResultsPage>, key: string, page: ApplicationResultsPage) {
  cache.delete(key)
  cache.set(key, page)
  while (cache.size > MAX_CACHED_RESULT_PAGES) {
    const oldestKey = cache.keys().next().value
    if (oldestKey === undefined) break
    cache.delete(oldestKey)
  }
}

async function requestApplicationPage(
  profileKey: string,
  statuses: ApplicationStatus[],
  sort: SortMode,
  offset: number,
): Promise<ApplicationResultsPage> {
  const query = new URLSearchParams({
    profile_ids: profileKey,
    statuses: statuses.length ? statuses.join(',') : 'all',
    sort,
    offset: String(offset),
    limit: String(PAGE_SIZE),
  })
  const response = await fetch(`${API}/api/v1/applications?${query}`)
  const data = await response.json() as JobsPage & { detail?: unknown }
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Could not load matching jobs.')
  return {
    jobs: data.items.map(job => ({
      ...job,
      profileId: job.profile_id,
      profileName: job.profile_name,
    })),
    counts: data.counts,
    total: data.total,
  }
}

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

function toggleStatusSelection(current: ApplicationStatus[], value: ApplicationStatus) {
  const selected = new Set(current)
  if (selected.has(value)) selected.delete(value)
  else selected.add(value)
  return applicationStatuses.filter(item => selected.has(item.value)).map(item => item.value)
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
  const jobsRef = useRef(jobs)
  const statusesRef = useRef(statuses)
  statusesRef.current = statuses
  const activeProfileIdsRef = useRef(activeProfileIds)
  activeProfileIdsRef.current = activeProfileIds
  const [counts, setCounts] = useState<Record<ApplicationStatus, number>>(() =>
    Object.fromEntries(applicationStatuses.map(item => [item.value, 0])) as Record<ApplicationStatus, number>)
  const [countsProfileKey, setCountsProfileKey] = useState('')
  const [totalVisible, setTotalVisible] = useState(0)
  const [resultQueryKey, setResultQueryKey] = useState('')
  const pageCacheRef = useRef(new Map<string, ApplicationResultsPage>())
  const pageRequestsRef = useRef(new Map<string, Promise<ApplicationResultsPage>>())
  const cacheGenerationRef = useRef(0)
  const prefetchFailuresRef = useRef(new Set<string>())
  const [cacheRevision, setCacheRevision] = useState(0)
  const [pageOffsets, setPageOffsets] = useState([0])
  const [pageIndex, setPageIndex] = useState(0)
  const offset = pageOffsets[pageIndex] ?? 0
  const pageIndexRef = useRef(pageIndex)
  pageIndexRef.current = pageIndex
  const [reloadKey, setReloadKey] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [savingKeys, setSavingKeys] = useState<Set<string>>(() => new Set())
  const savingKeysRef = useRef(new Set<string>())
  const [pendingStatuses, setPendingStatuses] = useState<Record<string, ApplicationStatus>>({})
  const activeProfileKey = activeProfileIds.join(',')
  const allProfilesActive = profiles.length > 0 && activeProfileIds.length === profiles.length
  const hasSavingJob = savingKeys.size > 0
  const queryKey = applicationResultsKey(activeProfileKey, statuses, sortBy, offset)
  const cachedPage = pageCacheRef.current.get(queryKey)
  const currentPage = cachedPage ?? (resultQueryKey === queryKey
    ? { jobs, counts, total: totalVisible }
    : null)
  const visibleJobs = currentPage?.jobs ?? []
  const currentCounts = currentPage?.counts ?? (countsProfileKey === activeProfileKey ? counts : null)
  const visibleCounts = currentCounts ?? counts
  const countsReady = currentCounts !== null
  const trackedCount = countsReady ? Object.values(visibleCounts).reduce((total, count) => total + count, 0) : 0
  const visibleTotal = currentPage?.total ?? (countsReady
    ? statuses.length
      ? statuses.reduce((total, status) => total + visibleCounts[status], 0)
      : trackedCount
    : 0)
  const loadingCurrentPage = !currentPage && loading

  const readySingleFilterKeys = useMemo(() => {
    const ready = new Set<string>()
    const filters: ApplicationStatus[][] = [[], ...applicationStatuses.map(item => [item.value])]
    for (const selectedStatuses of filters) {
      const key = applicationResultsKey(activeProfileKey, selectedStatuses, sortBy, 0)
      if (pageCacheRef.current.has(key) || prefetchFailuresRef.current.has(key)) {
        ready.add(JSON.stringify(selectedStatuses))
      }
    }
    return ready
  }, [activeProfileKey, sortBy, cacheRevision])
  const preparingSingleFilters = profilesReady && activeProfileIds.length > 0
    && readySingleFilterKeys.size < applicationStatuses.length + 1 && !error

  if (currentPage) jobsRef.current = currentPage.jobs

  function resetPagination() {
    setPageOffsets([0])
    setPageIndex(0)
  }

  function activateCachedPage(profileKey: string, selectedStatuses: ApplicationStatus[], sort: SortMode, selectedOffset: number) {
    const cached = pageCacheRef.current.get(applicationResultsKey(profileKey, selectedStatuses, sort, selectedOffset))
    if (!cached) {
      jobsRef.current = []
      setJobs([])
      setLoading(true)
      setError('')
      return
    }
    jobsRef.current = cached.jobs
    setJobs(cached.jobs)
    setCounts(cached.counts)
    setCountsProfileKey(profileKey)
    setTotalVisible(cached.total)
    setResultQueryKey(applicationResultsKey(profileKey, selectedStatuses, sort, selectedOffset))
    setLoading(false)
    setError('')
  }

  function loadApplicationPage(profileKey: string, selectedStatuses: ApplicationStatus[], sort: SortMode, selectedOffset: number) {
    const key = applicationResultsKey(profileKey, selectedStatuses, sort, selectedOffset)
    const cached = pageCacheRef.current.get(key)
    if (cached) return Promise.resolve(cached)
    const pending = pageRequestsRef.current.get(key)
    if (pending) return pending

    const generation = cacheGenerationRef.current
    let request: Promise<ApplicationResultsPage>
    request = requestApplicationPage(profileKey, selectedStatuses, sort, selectedOffset).then(page => {
      if (generation === cacheGenerationRef.current) {
        cacheApplicationResults(pageCacheRef.current, key, page)
        prefetchFailuresRef.current.delete(key)
        setCacheRevision(revision => revision + 1)
      }
      return page
    }).finally(() => {
      if (pageRequestsRef.current.get(key) === request) pageRequestsRef.current.delete(key)
    })
    pageRequestsRef.current.set(key, request)
    return request
  }

  function prefetchFilterPages(profileKey: string, sort: SortMode) {
    const filters: ApplicationStatus[][] = [[], ...applicationStatuses.map(item => [item.value])]
    const missingFilters = filters.filter(selectedStatuses =>
      !pageCacheRef.current.has(applicationResultsKey(profileKey, selectedStatuses, sort, 0)))
    void Promise.allSettled(missingFilters.map(async selectedStatuses => {
      const key = applicationResultsKey(profileKey, selectedStatuses, sort, 0)
      try {
        await loadApplicationPage(profileKey, selectedStatuses, sort, 0)
      } catch {
        prefetchFailuresRef.current.add(key)
        setCacheRevision(revision => revision + 1)
      }
    }))
  }

  function canOpenFilter(selectedStatuses: ApplicationStatus[]) {
    const key = applicationResultsKey(activeProfileKey, selectedStatuses, sortBy, 0)
    return pageCacheRef.current.has(key)
      || (selectedStatuses.length <= 1 && readySingleFilterKeys.has(JSON.stringify(selectedStatuses)))
      || selectedStatuses.length > 1
  }

  function invalidateInFlightPageRequests() {
    cacheGenerationRef.current += 1
    pageRequestsRef.current = new Map()
  }

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
      jobsRef.current = []
      setJobs([])
      setCounts(Object.fromEntries(applicationStatuses.map(item => [item.value, 0])) as Record<ApplicationStatus, number>)
      setCountsProfileKey('')
      setTotalVisible(0)
      setResultQueryKey(queryKey)
      setLoading(false)
      setError('')
      return
    }

    const cached = pageCacheRef.current.get(queryKey)
    if (cached) {
      jobsRef.current = cached.jobs
      setJobs(cached.jobs)
      setCounts(cached.counts)
      setCountsProfileKey(activeProfileKey)
      setTotalVisible(cached.total)
      setResultQueryKey(queryKey)
      setLoading(false)
      setError('')
      prefetchFilterPages(activeProfileKey, sortBy)
      return
    }

    const controller = new AbortController()
    setLoading(true)
    setError('')
    loadApplicationPage(activeProfileKey, statuses, sortBy, offset).then(resultPage => {
      if (controller.signal.aborted) return
      jobsRef.current = resultPage.jobs
      setJobs(resultPage.jobs)
      setCounts(resultPage.counts)
      setCountsProfileKey(activeProfileKey)
      setTotalVisible(resultPage.total)
      setResultQueryKey(queryKey)
      prefetchFilterPages(activeProfileKey, sortBy)
    }).catch(reason => {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : 'Could not load matching jobs.')
    }).finally(() => { if (!controller.signal.aborted) setLoading(false) })

    return () => controller.abort()
  }, [profilesReady, activeProfileKey, activeProfileIds.length, reloadKey, profiles, statuses, sortBy, offset, queryKey])

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

  const sortIndex = sortModes.findIndex(item => item.value === sortBy)
  const nextSort = sortModes[(sortIndex + 1) % sortModes.length]

  function filter(value: ApplicationStatus | '') {
    const nextStatuses = !value ? [] : toggleStatusSelection(statuses, value)
    setStatuses(nextStatuses)
    resetPagination()
    activateCachedPage(activeProfileKey, nextStatuses, sortBy, 0)
    setNotice('')
  }

  function cycleSort() {
    const nextSortBy = nextSort.value
    setSortBy(nextSortBy)
    resetPagination()
    activateCachedPage(activeProfileKey, statuses, nextSortBy, 0)
  }

  function toggleProfile(profileId: number) {
    const nextProfileIds = activeProfileIds.includes(profileId)
      ? activeProfileIds.filter(id => id !== profileId)
      : [...activeProfileIds, profileId].sort((a, b) => profiles.findIndex(profile => profile.id === a) - profiles.findIndex(profile => profile.id === b))
    setActiveProfileIds(nextProfileIds)
    resetPagination()
    activateCachedPage(nextProfileIds.join(','), statuses, sortBy, 0)
    setNotice('')
  }

  function selectAllProfiles() {
    const nextProfileIds = profiles.map(profile => profile.id)
    setActiveProfileIds(nextProfileIds)
    resetPagination()
    activateCachedPage(nextProfileIds.join(','), statuses, sortBy, 0)
    setNotice('')
  }

  function updateCachedApplicationPages(job: ProfiledJob, previousStatus: ApplicationStatus, application: Application) {
    const statusChanged = previousStatus !== application.status

    let changed = false
    for (const [cacheKey, cachedPage] of pageCacheRef.current) {
      const [profileKey, selectedStatuses] = JSON.parse(cacheKey) as [string, ApplicationStatus[], SortMode, number]
      if (!profileKey.split(',').includes(String(job.profileId))) continue

      const previouslySelected = !selectedStatuses.length || selectedStatuses.includes(previousStatus)
      const nowSelected = !selectedStatuses.length || selectedStatuses.includes(application.status)
      const nextCounts = statusChanged ? {
        ...cachedPage.counts,
        [previousStatus]: Math.max(0, cachedPage.counts[previousStatus] - 1),
        [application.status]: cachedPage.counts[application.status] + 1,
      } : cachedPage.counts

      if (!previouslySelected && nowSelected) {
        pageCacheRef.current.delete(cacheKey)
        prefetchFailuresRef.current.delete(cacheKey)
        changed = true
        continue
      }

      const nextJobs = cachedPage.jobs.flatMap(item => {
        if (item.profileId !== job.profileId || item.id !== job.id) return [item]
        if (previouslySelected && !nowSelected) return []
        if (previouslySelected && nowSelected) return [{ ...item, application }]
        return [item]
      })
      const total = cachedPage.total + (previouslySelected === nowSelected
        ? 0 : nowSelected ? 1 : -1)
      pageCacheRef.current.set(cacheKey, {
        jobs: nextJobs,
        counts: nextCounts,
        total: Math.max(0, total),
      })
      changed = true
    }
    if (changed) setCacheRevision(revision => revision + 1)
  }

  function applyLocalStatus(job: ProfiledJob, application: Application) {
    const currentJobs = jobsRef.current
    const currentIndex = currentJobs.findIndex(item => item.profileId === job.profileId && item.id === job.id)
    const currentJob = currentIndex >= 0 ? currentJobs[currentIndex] : job
    const currentStatus = currentJob?.application?.status ?? 'new'
    const currentStatuses = statusesRef.current
    const wasSelected = !currentStatuses.length || currentStatuses.includes(currentStatus)
    const isSelected = !currentStatuses.length || currentStatuses.includes(application.status)
    const leavesCurrentFilter = wasSelected && !isSelected
    const profileIsActive = activeProfileIdsRef.current.includes(job.profileId)

    if (currentStatus !== application.status) {
      setResultQueryKey(queryKey)
      if (profileIsActive) {
        setCountsProfileKey(activeProfileKey)
        setCounts(current => ({
          ...current,
          [currentStatus]: Math.max(0, current[currentStatus] - 1),
          [application.status]: current[application.status] + 1,
        }))
        if (wasSelected !== isSelected) setTotalVisible(current => Math.max(0, current + (isSelected ? 1 : -1)))
      }
    }
    updateCachedApplicationPages(job, currentStatus, application)

    if (leavesCurrentFilter && currentIndex >= 0 && currentJobs.length === 1 && pageIndexRef.current > 0) {
      setPageOffsets(current => current.slice(0, -1))
      setPageIndex(current => Math.max(0, current - 1))
      setLoading(true)
    }
    if (currentIndex < 0) return
    const nextJobs = !isSelected
      ? currentJobs.filter((_, itemIndex) => itemIndex !== currentIndex)
      : currentJobs.map((item, itemIndex) => itemIndex === currentIndex ? { ...item, application } : item)
    jobsRef.current = nextJobs
    setJobs(nextJobs)
  }

  async function updateJob(job: ProfiledJob, action: ApplicationSwipeAction) {
    const key = profileJobKey(job)
    if (savingKeysRef.current.has(key)) return
    invalidateInFlightPageRequests()
    savingKeysRef.current.add(key)
    setSavingKeys(current => new Set(current).add(key))
    setPendingStatuses(current => ({ ...current, [key]: action.status }))
    setError('')
    setNotice('')
    try {
      const saved = await saveApplication(API, job.profileId, job.id, action.status)
      applyLocalStatus(job, saved)
      const label = applicationStatuses.find(item => item.value === action.status)?.label
      setNotice(action.kind === 'undo'
        ? `${job.title} · ${job.profileName} · restored to ${label}`
        : `${job.title} · ${job.profileName} · ${label}`)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not save application status.')
    } finally {
      savingKeysRef.current.delete(key)
      if (!savingKeysRef.current.size) prefetchFilterPages(activeProfileKey, sortBy)
      setSavingKeys(current => {
        const next = new Set(current)
        next.delete(key)
        return next
      })
      setPendingStatuses(current => {
        const next = { ...current }
        delete next[key]
        return next
      })
    }
  }

  async function openOriginalPost(job: ProfiledJob) {
    const key = profileJobKey(job)
    const currentApplication = jobsRef.current.find(item => item.profileId === job.profileId && item.id === job.id)?.application ?? job.application
    const willMoveToInterested = !currentApplication || currentApplication.status === 'new'
    if (savingKeysRef.current.has(key)) return
    invalidateInFlightPageRequests()
    savingKeysRef.current.add(key)
    setSavingKeys(current => new Set(current).add(key))
    if (willMoveToInterested) setPendingStatuses(current => ({ ...current, [key]: 'open' }))
    try {
      const saved = await trackJobInteraction(API, job.profileId, job.id, currentApplication, 'open_post')
      if (saved) applyLocalStatus(job, saved)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not update application status.')
    } finally {
      savingKeysRef.current.delete(key)
      if (!savingKeysRef.current.size) prefetchFilterPages(activeProfileKey, sortBy)
      setSavingKeys(current => {
        const next = new Set(current)
        next.delete(key)
        return next
      })
      setPendingStatuses(current => {
        const next = { ...current }
        delete next[key]
        return next
      })
    }
  }

  const updateJobRef = useRef(updateJob)
  updateJobRef.current = updateJob
  const openOriginalPostRef = useRef(openOriginalPost)
  openOriginalPostRef.current = openOriginalPost
  const onCardAction = useCallback((jobId: number, profileId: number | undefined, action: ApplicationSwipeAction) => {
    if (profileId === undefined) return
    const job = jobsRef.current.find(item => item.profileId === profileId && item.id === jobId)
    if (job) void updateJobRef.current(job, action)
  }, [])
  const onOriginalPost = useCallback((jobId: number, profileId: number | undefined) => {
    if (profileId === undefined) return
    const job = jobsRef.current.find(item => item.profileId === profileId && item.id === jobId)
    if (job) void openOriginalPostRef.current(job)
  }, [])

  function goToNextPage() {
    const nextOffset = offset + visibleJobs.length
    setPageOffsets(current => [...current.slice(0, pageIndex + 1), nextOffset])
    setPageIndex(current => current + 1)
    activateCachedPage(activeProfileKey, statuses, sortBy, nextOffset)
  }

  function goToPreviousPage() {
    const previousOffset = pageOffsets[Math.max(0, pageIndex - 1)] ?? 0
    setPageOffsets(current => current.slice(0, pageIndex))
    setPageIndex(current => Math.max(0, current - 1))
    activateCachedPage(activeProfileKey, statuses, sortBy, previousOffset)
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
          <button type="button" disabled={hasSavingJob} className={`application-filter-chip ${allProfilesActive ? 'selected' : ''}`} aria-pressed={allProfilesActive}
            onClick={selectAllProfiles}>All profiles</button>
          {profiles.map(profile => <button type="button" key={profile.id}
            disabled={hasSavingJob}
            className={`application-filter-chip ${activeProfileIds.includes(profile.id) ? 'selected' : ''}`}
            aria-pressed={activeProfileIds.includes(profile.id)} onClick={() => toggleProfile(profile.id)}>{profile.name}</button>)}
        </div>
        <div className="application-status-grid" role="group" aria-label="Filter by application stage">
          <button type="button" disabled={hasSavingJob || !canOpenFilter([])} aria-pressed={statuses.length === 0} className={`application-filter-chip application-status-filter ${statuses.length === 0 ? 'selected' : ''}`}
          onClick={() => filter('')}>All <span>{countsReady ? trackedCount : '—'}</span></button>
          {applicationStatuses.map(item => {
            const nextStatuses = toggleStatusSelection(statuses, item.value)
            return <button type="button" key={item.value} disabled={hasSavingJob || !canOpenFilter(nextStatuses)}
              aria-pressed={statuses.includes(item.value)}
              className={`application-filter-chip application-status-filter application-${item.value} ${statuses.includes(item.value) ? 'selected' : ''}`}
              onClick={() => filter(item.value)}>
              {item.label} <span>{countsReady ? visibleCounts[item.value] : '—'}</span>
            </button>
          })}
        </div>
        {preparingSingleFilters && <p className="evidence-note" role="status">Preparing stage filters…</p>}
      </section>}
      {error && <div className="error-banner" role="alert">{error}</div>}
      {notice && <p className="evidence-note" role="status">{notice}</p>}
      {profilesReady && !profiles.length ? <section className="detail-panel empty-state">
        <h2>Create a job profile to get started</h2><p>Save a profile to see matching jobs and track each opportunity here.</p>
        <a className="page-link" href="/profile">Create a profile →</a>
      </section> : profiles.length > 0 && <section className="directory-card applications-results">
        <div className="application-results-summary"><span>{visibleTotal} {statuses.length ? statuses.length === 1 ? 'jobs in this stage' : 'jobs in selected stages' : 'jobs'}</span>
          <span>{countsReady ? trackedCount : '—'} tracked</span>
          <button type="button" className="application-sort-cycle" disabled={hasSavingJob} onClick={cycleSort}
            aria-label={`Sort by ${sortModes[sortIndex].label}. Tap to switch to ${nextSort.label}.`}
            title={`Tap to sort by ${nextSort.label}`}>Sort: {sortModes[sortIndex].label} <span aria-hidden="true">↻</span></button>
        </div>
        {loadingCurrentPage ? <div className="loading-state"><span className="spinner" /> Loading matching jobs…</div>
          : error ? <div className="empty-state"><p>Matching jobs could not be loaded.</p><button type="button" className="broaden-button" onClick={() => {
            pageCacheRef.current.delete(queryKey)
            setLoading(true)
            setReloadKey(current => current + 1)
          }}>Try again</button></div>
            : !activeProfileIds.length ? <div className="empty-state"><h2>Choose a profile</h2><p>Turn on one or more profiles above to see their matching jobs.</p></div>
              : visibleJobs.length ? <div className="application-job-list">
                {visibleJobs.map(job => {
                  const key = profileJobKey(job)
                  return <JobFeedCard key={key} job={job} href={cardHref(job)} profileId={job.profileId} profileName={job.profileName}
                    updatedAt={jobUpdatedAt(job)} disabled={savingKeys.has(key)} saving={savingKeys.has(key)}
                    pendingStatus={pendingStatuses[key]} onAction={onCardAction} onOpenOriginal={onOriginalPost} />
                })}
              </div> : <div className="empty-state"><h2>{statuses.length ? statuses.length === 1 ? 'No jobs in this stage' : 'No jobs in these stages' : 'No matching jobs yet'}</h2>
                <p>{statuses.length ? 'Choose another stage or clear the stage filters.' : 'The selected profiles do not have any matching or tracked jobs yet.'}</p>
              </div>}
        <div className="table-footer"><span>{visibleTotal} {statuses.length ? statuses.length === 1 ? 'in this stage' : 'in selected stages' : 'jobs'} · {countsReady ? trackedCount : '—'} tracked</span>
          <div className="pagination"><button type="button" disabled={!currentPage || pageIndex === 0 || hasSavingJob} onClick={goToPreviousPage}>Previous</button>
            <span>{currentPage
              ? `${pageIndex + 1} / ${pageIndex + 1 + Math.ceil(Math.max(0, visibleTotal - offset - visibleJobs.length) / PAGE_SIZE)}`
              : '— / —'}</span>
            <button type="button" disabled={!currentPage || hasSavingJob || offset + visibleJobs.length >= visibleTotal} onClick={goToNextPage}>Next</button></div>
        </div>
      </section>}
    </main>
  </div>
}
