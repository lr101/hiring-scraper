import { useCallback, useEffect, useRef, useState } from 'react'
import SiteHeader from './SiteHeader'
import JobFeedCard from './JobFeedCard'
import JourneyConnections from './JourneyConnections'
import { applicationStatuses, saveApplication, trackJobInteraction } from './applications'
import type { Application, ApplicationStatus, ApplicationSwipeAction } from './applications'
import { getApplicationMomentum } from './applicationMomentum'
import type { ApplicationMomentum } from './applicationMomentum'
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

const journeyStages: ApplicationStatus[][] = [
  ['new'], ['open', 'in_gots_hand'], ['waiting_for_reply', 'automated_application'], ['interview'], ['accepted'],
]
const outcomeStages: ApplicationStatus[] = ['not_interested', 'rejected']
const applicationRewardCycle = [
  { reward: 'Kisses', icon: '💋' },
  { reward: 'Flowers', icon: '💐' },
  { reward: 'Jello', icon: '🍮' },
] as const
const applicationMilestones = Array.from({ length: 40 }, (_, index) => {
  const count = (index + 1) * 5
  const gift = applicationRewardCycle[index % applicationRewardCycle.length]
  return { count, step: `${count} applications`, ...gift }
})
const stagePalette: Record<ApplicationStatus, string> = {
  new: 'green', open: 'blue', in_gots_hand: 'sand', waiting_for_reply: 'amber', automated_application: 'teal', interview: 'violet',
  accepted: 'mint', not_interested: 'stone', rejected: 'rose',
}

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
  const pageRequestQueueRef = useRef<Promise<void>>(Promise.resolve())
  const rewardTimelineRef = useRef<HTMLOListElement>(null)
  const trailRef = useRef<HTMLDivElement>(null)
  const cacheGenerationRef = useRef(0)
  const [pageOffsets, setPageOffsets] = useState([0])
  const [pageIndex, setPageIndex] = useState(0)
  const offset = pageOffsets[pageIndex] ?? 0
  const pageIndexRef = useRef(pageIndex)
  pageIndexRef.current = pageIndex
  const [reloadKey, setReloadKey] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [noticeKind, setNoticeKind] = useState<'standard' | 'applied' | 'milestone'>('standard')
  const [momentum, setMomentum] = useState<ApplicationMomentum | null>(null)
  const momentumRef = useRef<ApplicationMomentum | null>(null)
  const [momentumRefreshKey, setMomentumRefreshKey] = useState(0)
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
  const resultStageHeading = !statuses.length ? 'All roles'
    : statuses.length === 1 ? applicationStatuses.find(item => item.value === statuses[0])?.label ?? 'Selected checkpoint'
      : 'Selected checkpoints'
  const weeklyProgress = momentum?.weekly_goal
    ? Math.min(100, Math.round(momentum.applied_this_week / momentum.weekly_goal * 100))
    : 0
  const rewardTimeline = momentum ? [
    ...applicationMilestones.map(milestone => ({
      id: `applications-${milestone.count}`,
      step: milestone.step,
      reward: milestone.reward,
      icon: milestone.icon,
      unlocked: momentum.lifetime_applied >= milestone.count,
      target: milestone.count,
      progress: Math.min(100, Math.round(momentum.lifetime_applied / milestone.count * 100)),
      progressLabel: `${Math.min(momentum.lifetime_applied, milestone.count)} / ${milestone.count} ${milestone.count === 1 ? 'application' : 'applications'}`,
    })),
    { id: 'interview', step: 'Interview', reward: 'Weekend trip', icon: '🧳', unlocked: momentum.interview_count > 0,
      target: null, progress: 0, progressLabel: 'Reach an interview' },
    { id: 'job', step: 'Job offer', reward: 'Trip to Mexico', icon: '🌴', unlocked: momentum.jobs_won > 0,
      target: null, progress: 0, progressLabel: 'Get a job offer' },
  ] : []
  const nextRewardIndex = rewardTimeline.findIndex(reward => !reward.unlocked)
  const visibleRewardTimeline = nextRewardIndex < 0 ? rewardTimeline : rewardTimeline.slice(0, nextRewardIndex + 1)
  const nextReward = nextRewardIndex < 0 ? null : rewardTimeline[nextRewardIndex]

  useEffect(() => {
    const timeline = rewardTimelineRef.current
    const currentReward = timeline?.querySelector<HTMLElement>('.journey-reward-step.is-current')
    if (!timeline || !currentReward || timeline.scrollWidth <= timeline.clientWidth) return

    const timelineLeft = timeline.getBoundingClientRect().left
    const currentLeft = currentReward.getBoundingClientRect().left - timelineLeft + timeline.scrollLeft
    const currentRight = currentLeft + currentReward.getBoundingClientRect().width
    const viewportRight = timeline.scrollLeft + timeline.clientWidth
    if (currentLeft < timeline.scrollLeft || currentRight > viewportRight) {
      const left = Math.max(0, currentRight - timeline.clientWidth + 16)
      const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
      timeline.scrollTo({ left, behavior: prefersReducedMotion ? 'auto' : 'smooth' })
    }
  }, [nextRewardIndex, profilesReady, profiles.length])

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
    request = pageRequestQueueRef.current.catch(() => undefined).then(async () => {
      const cachedAfterQueue = pageCacheRef.current.get(key)
      if (cachedAfterQueue) return cachedAfterQueue

      return requestApplicationPage(profileKey, selectedStatuses, sort, selectedOffset)
    }).then(page => {
      if (generation === cacheGenerationRef.current) {
        cacheApplicationResults(pageCacheRef.current, key, page)
      }
      return page
    }).finally(() => {
      if (pageRequestsRef.current.get(key) === request) pageRequestsRef.current.delete(key)
    })
    pageRequestsRef.current.set(key, request)
    pageRequestQueueRef.current = request.then(() => undefined, () => undefined)
    return request
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
    }).catch(reason => {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : 'Could not load matching jobs.')
    }).finally(() => { if (!controller.signal.aborted) setLoading(false) })

    return () => controller.abort()
  }, [profilesReady, activeProfileKey, activeProfileIds.length, reloadKey, profiles, statuses, sortBy, offset, queryKey])

  useEffect(() => {
    let active = true
    getApplicationMomentum().then(value => {
      if (!active) return
      const previous = momentumRef.current
      momentumRef.current = value
      setMomentum(value)

      if (momentumRefreshKey && previous) {
        const reachedJob = previous.jobs_won === 0 && value.jobs_won > 0
        const reachedInterview = previous.interview_count === 0 && value.interview_count > 0
        const reached = applicationMilestones.find(milestone =>
          previous.lifetime_applied < milestone.count && value.lifetime_applied >= milestone.count)
        const paceMet = previous.applied_this_week < previous.weekly_goal
          && value.applied_this_week >= value.weekly_goal
        if (reachedJob) {
          setNotice('Reward unlocked · Trip to Mexico')
          setNoticeKind('milestone')
        } else if (reachedInterview) {
          setNotice('Reward unlocked · Weekend trip')
          setNoticeKind('milestone')
        } else if (reached) {
          setNotice(`Reward unlocked · ${reached.reward}`)
          setNoticeKind('milestone')
        } else if (paceMet) {
          setNotice('Weekly pace met')
          setNoticeKind('milestone')
        }
      }
    }).catch(() => { /* Momentum is optional; application tracking remains available. */ })
    return () => { active = false }
  }, [momentumRefreshKey])

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
    const nextStatuses = value ? [value] : []
    if (statuses.length === nextStatuses.length && statuses.every((status, index) => status === nextStatuses[index])) return
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
    }
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
      const saved = await saveApplication(API, job.profileId, job.id, action.status, action.kind)
      applyLocalStatus(job, saved)
      const label = applicationStatuses.find(item => item.value === action.status)?.label
      const applied = action.kind === 'stage' && ['waiting_for_reply', 'automated_application'].includes(action.status)
      const milestoneAction = action.kind === 'stage'
        && ['waiting_for_reply', 'automated_application', 'interview', 'accepted'].includes(action.status)
      setNotice(applied ? `Application sent · ${job.title}` : action.kind === 'undo'
        ? `${job.title} · ${job.profileName} · restored to ${label}`
        : `${job.title} · ${job.profileName} · ${label}`)
      setNoticeKind(applied ? 'applied' : 'standard')
      if (milestoneAction) setMomentumRefreshKey(current => current + 1)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not save application status.')
    } finally {
      savingKeysRef.current.delete(key)
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
    if (savingKeysRef.current.has(key)) return
    invalidateInFlightPageRequests()
    savingKeysRef.current.add(key)
    setSavingKeys(current => new Set(current).add(key))
    try {
      const saved = await trackJobInteraction(API, job.profileId, job.id, currentApplication, 'open_post')
      if (saved) applyLocalStatus(job, saved)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not update application status.')
    } finally {
      savingKeysRef.current.delete(key)
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
    <main className="content applications-page journey-page">
      {profilesReady && !profiles.length && <section className="journey-first-profile">
        <h2>Start with a profile</h2>
        <a href="/profile/new">Create a profile <span aria-hidden="true">→</span></a>
      </section>}

      {profilesReady && profiles.length > 0 && momentum && <section className="journey-momentum" aria-label="Application momentum">
        <div className="journey-momentum-week">
          <div className="journey-momentum-title"><span aria-hidden="true">◷</span><span>This week</span></div>
          <strong className="journey-momentum-value">{momentum.applied_this_week}<span> / {momentum.weekly_goal}</span></strong>
          <div className="journey-momentum-track" role="progressbar" aria-label="Weekly application pace"
            aria-valuemin={0} aria-valuemax={momentum.weekly_goal}
            aria-valuenow={Math.min(momentum.applied_this_week, momentum.weekly_goal)}
            aria-valuetext={`${momentum.applied_this_week} of ${momentum.weekly_goal} applications sent this week`}>
            <span style={{ width: `${weeklyProgress}%` }} />
          </div>
          <small>{momentum.applied_this_week >= momentum.weekly_goal ? 'Pace met' : 'applications sent'}</small>
        </div>
        <span className="journey-momentum-divider" aria-hidden="true" />
        <div className="journey-momentum-streak">
          <div className="journey-momentum-title"><span aria-hidden="true">✦</span><span>Successful weeks</span></div>
          <strong className="journey-momentum-value">{momentum.successful_weeks}<span> {momentum.successful_weeks === 1 ? 'week' : 'weeks'}</span></strong>
          <small>{momentum.successful_weeks ? 'in a row' : 'Your first one starts here'}</small>
        </div>
      </section>}

      {profilesReady && profiles.length > 0 && momentum && <section className="journey-rewards" aria-label="Application rewards">
        <div className="journey-rewards-heading">
          <h2>Reward timeline</h2>
          <small>{nextReward ? `Up next · ${nextReward.step} · ${nextReward.reward}` : 'All rewards earned'}</small>
        </div>
        <ol ref={rewardTimelineRef} className="journey-reward-timeline" aria-label="Completed rewards and next milestone">
          {visibleRewardTimeline.map(reward => <li key={reward.id}
            aria-label={`${reward.step}: ${reward.reward}, ${reward.unlocked ? 'earned' : 'next reward'}`}
            className={`journey-reward-step ${reward.unlocked ? 'is-complete' : 'is-current'}`}>
            <span className="journey-reward-node" aria-hidden="true">{reward.unlocked ? '✓' : reward.icon}</span>
            <span className="journey-reward-copy"><small>{reward.step}</small><strong>{reward.reward}</strong></span>
            {!reward.unlocked && reward.target !== null && <>
              <div className="journey-reward-progress" role="progressbar" aria-label={`Progress to ${reward.reward}`}
                aria-valuemin={0} aria-valuemax={reward.target}
                aria-valuenow={Math.min(momentum.lifetime_applied, reward.target)}
                aria-valuetext={reward.progressLabel}>
                <span style={{ width: `${reward.progress}%` }} />
              </div>
              <small className="journey-reward-progress-label">{reward.progressLabel}</small>
            </>}
            {!reward.unlocked && reward.target === null && <small className="journey-reward-progress-label">{reward.progressLabel}</small>}
          </li>)}
        </ol>
      </section>}

      {profilesReady && profiles.length > 0 && <section id="quest-map" className="quest-map" aria-labelledby="quest-map-heading">
        <div className="quest-map-heading">
          <div><h2 id="quest-map-heading">Choose a checkpoint</h2></div>
          {profiles.length > 1 ? <details className="journey-profile-picker">
            <summary><span>Profile scope</span><strong>{allProfilesActive ? 'All profiles' : activeProfileIds.length
              ? `${activeProfileIds.length} selected` : 'None selected'}</strong><span className="journey-profile-adjust">Adjust</span></summary>
            <div className="journey-profile-options" role="group" aria-label="Choose profiles">
              <button type="button" disabled={hasSavingJob} className={`application-filter-chip ${allProfilesActive ? 'selected' : ''}`} aria-pressed={allProfilesActive}
                onClick={selectAllProfiles}>All profiles</button>
              {profiles.map(profile => <button type="button" key={profile.id} disabled={hasSavingJob}
                className={`application-filter-chip ${activeProfileIds.includes(profile.id) ? 'selected' : ''}`}
                aria-pressed={activeProfileIds.includes(profile.id)} onClick={() => toggleProfile(profile.id)}>{profile.name}</button>)}
            </div>
          </details> : <div className="journey-profile-single"><span>PROFILE</span><strong>{profiles[0].name}</strong>
            {!activeProfileIds.includes(profiles[0].id) && <button type="button" disabled={hasSavingJob} onClick={() => toggleProfile(profiles[0].id)}>Select</button>}
          </div>}
        </div>

        <div className="quest-trail" ref={trailRef} role="group" aria-label="Filter applications by checkpoint">
          <JourneyConnections containerRef={trailRef} />
          {journeyStages.map((stages, index) => <div className="quest-stage-group" key={stages[0]}>{stages.map(stage => {
            const item = applicationStatuses.find(option => option.value === stage)!
            return <button type="button" key={stage} disabled={hasSavingJob}
              data-journey-stage={stage}
              aria-pressed={statuses.includes(stage)}
              aria-label={`${item.label}, ${countsReady ? visibleCounts[stage] : 'loading'} roles`}
              className={`quest-stage quest-${stagePalette[stage]} ${visibleCounts[stage] ? 'has-roles' : 'quiet'} ${statuses.includes(stage) ? 'selected' : ''}`}
              onClick={() => filter(stage)}>
              <span className="quest-stage-top"><span className="quest-stage-number">{String(index + 1).padStart(2, '0')}</span>
                <span className="quest-stage-count">{countsReady ? visibleCounts[stage] : '—'}</span></span>
              <strong>{item.label}</strong>
              <span className="quest-stage-dot" aria-hidden="true" />
            </button>
          })}</div>)}
        </div>

        <div className="quest-map-footer">
          <details className={`quest-outcomes ${statuses.some(status => outcomeStages.includes(status)) ? 'has-selection' : ''}`}>
            <summary className="quest-outcomes-trigger">Past outcomes <span aria-hidden="true">⌄</span></summary>
            <div className="quest-outcome-options" role="group" aria-label="Filter applications by outcome">
              {outcomeStages.map(stage => {
                const item = applicationStatuses.find(option => option.value === stage)!
                return <button type="button" key={stage} disabled={hasSavingJob}
                  aria-label={`${item.label} applications`} aria-pressed={statuses.includes(stage)} className={`quest-outcome quest-${stagePalette[stage]} ${statuses.includes(stage) ? 'selected' : ''}`}
                  onClick={() => filter(stage)}>{item.label}</button>
              })}
            </div>
          </details>
          <button type="button" disabled={hasSavingJob} aria-pressed={statuses.length === 0}
            className={`quest-show-all ${statuses.length === 0 ? 'selected' : ''}`} onClick={() => filter('')}>
            All roles <span>{countsReady ? trackedCount : '—'}</span>
          </button>
        </div>
      </section>}

      {error && <div className="error-banner" role="alert">{error}</div>}
      {notice && <p className={`journey-notice ${noticeKind === 'standard' ? '' : `is-${noticeKind}`}`} role="status">
        <span aria-hidden="true">{noticeKind === 'milestone' ? '★' : '✓'}</span> {notice}</p>}
      {profilesReady && profiles.length > 0 && <section id="journey-roles" className="journey-results" aria-labelledby="journey-results-heading">
        <div className="journey-results-heading">
          <div><h2 id="journey-results-heading">{resultStageHeading}</h2></div>
          <div className="journey-results-tools"><span className="journey-result-count"><strong>{currentPage ? visibleTotal : countsReady ? visibleTotal : '—'}</strong> {visibleTotal === 1 ? 'role' : 'roles'}</span>
            <button type="button" className="application-sort-cycle" disabled={hasSavingJob} onClick={cycleSort}
              aria-label={`Sort by ${sortModes[sortIndex].label}. Tap to switch to ${nextSort.label}.`}
              title={`Tap to sort by ${nextSort.label}`}>Sort: {sortModes[sortIndex].label} <span aria-hidden="true">↻</span></button>
          </div>
        </div>
        {loadingCurrentPage ? <div className="loading-state"><span className="spinner" /> Loading your roles…</div>
          : error ? <div className="empty-state"><p>Matching jobs could not be loaded.</p><button type="button" className="broaden-button" onClick={() => {
            pageCacheRef.current.delete(queryKey)
            setLoading(true)
            setReloadKey(current => current + 1)
          }}>Try again</button></div>
            : !activeProfileIds.length ? <div className="journey-empty"><span className="journey-empty-icon" aria-hidden="true">◎</span><h3>Choose a profile</h3></div>
              : visibleJobs.length ? <div className="application-job-list">
                {visibleJobs.map(job => {
                  const key = profileJobKey(job)
                  return <JobFeedCard key={key} job={job} href={cardHref(job)} profileId={job.profileId} profileName={job.profileName}
                    updatedAt={jobUpdatedAt(job)} disabled={savingKeys.has(key)} saving={savingKeys.has(key)} showStartApplication={false}
                    pendingStatus={pendingStatuses[key]} onAction={onCardAction} onOpenOriginal={onOriginalPost} />
                })}
              </div> : <div className="journey-empty"><span className="journey-empty-icon" aria-hidden="true">✦</span>
                <h3>{statuses.length ? 'Nothing here yet' : 'Your next role is out there'}</h3>
                <a className="journey-empty-link" href="/discover?view=jobs">Find a role <span aria-hidden="true">↗</span></a>
              </div>}
        <div className="journey-results-footer">
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
