import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import DetailPage from './DetailPage'
import FeedSourcesPage from './FeedSourcesPage'
import LocationsPage from './LocationsPage'
import SiteHeader from './SiteHeader'
import ProfilesPage from './ProfilesPage'
import ApplicationsPage from './ApplicationsPage'
import SettingsPage from './SettingsPage'
import ApplicationPreparationPage from './ApplicationPreparationPage'
import JobFeedCard from './JobFeedCard'
import { applicationStatusLabel, applicationStatuses, saveApplication, trackJobInteraction } from './applications'
import type { Application, ApplicationStatus, ApplicationSwipeAction } from './applications'
import type { Enrichment, ProfileMatch } from './JobEvidence'
import { profileBoardDefaults, boardLocationParams } from './profile'
import type { Profile } from './profile'

type Location = {
  search_scope?: 'radius' | 'state' | 'country'
  label: string
  city?: string | null
  state?: string | null
  postcode?: string | null
  latitude: number
  longitude: number
  precision: string
  country_code?: string | null
  type?: string
}

type Company = {
  state: string | null; crawl_paused: boolean; crawl_interval_days: number | null; crawl_reason: string | null; next_check_at: string | null; employee_count: number | null
  id: number
  name: string
  domain: string | null
  website_url: string | null
  domain_match_method: string | null
  domain_evidence_url: string | null
  category: string | null
  source: string
  source_url: string | null
  latitude: number | null
  longitude: number | null
  location_precision: string | null
  location_label: string | null
  career_url: string | null
  career_status: string
  distance_m: number | null
  feed_count: number
  active_job_count: number
}
type CompanyDiscoveryFilter = 'all' | 'domain' | 'career' | 'feed' | 'jobs'
type CompanySort = 'distance' | 'name'
type JobSort = 'posted' | 'discovered'
type JobWorkStyleFilter = 'all' | 'hybrid' | 'onsite'
type JobLocationScope = 'area' | 'remote' | 'area_remote'

type JobLocation = {
  label: string
  latitude: number | null
  longitude: number | null
  precision: string | null
  country_code: string | null
}

type Job = {
  id: number
  external_id: string
  title: string
  url: string
  company_id: number
  company_name: string
  company_domain: string | null
  company_website: string | null
  company_source_url: string | null
  provider: string
  board_url: string | null
  feed_status: string
  locations: JobLocation[]
  location_text: string | null
  is_remote: boolean
  work_arrangement: string | null
  employment_type: string | null
  schedule: string | null
  department: string | null
  seniority: string | null
  date_posted: string | null
  salary: string | null
  first_seen_at: string
  last_seen_at: string
  is_active: boolean
  description_preview?: string
  description?: string | null
  raw_metadata: Record<string, unknown>
  enrichment: Enrichment
  profile_match?: ProfileMatch
  application?: Application | null
  profile_id?: number
  profile_name?: string
  match_kind?: 'remote' | 'in_area' | 'work_location' | 'employer_in_state' | 'employer_in_country'
}

type JobsPage = { items: Job[]; total: number; total_is_upper_bound?: boolean; application_counts?: Record<ApplicationStatus, number>; snapshot_id?: string; snapshot_stale?: boolean; search_mode?: 'semantic' | 'keyword' | 'browse' | 'warming' }

type LocationOption = {
  key: string
  label: string
  location: Location
  radius_km: number
}

const NO_LOCATION: Location = {
  label: '', latitude: 0, longitude: 0, precision: 'unconfigured',
}
const PAGE_SIZE = 40
const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '')
const jobSortModes: { value: JobSort; label: string }[] = [
  { value: 'posted', label: 'Posted date' },
  { value: 'discovered', label: 'Recently found' },
]

function apiUrl(path: string) {
  return `${API_BASE_URL}${path}`
}

function Icon({ name, size = 18 }: { name: 'pin' | 'search' | 'arrow' | 'globe' | 'building' | 'briefcase' | 'chevron' | 'close' | 'external'; size?: number }) {
  const common = { width: size, height: size, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 1.7, strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const, 'aria-hidden': true as const }
  const paths: Record<string, React.ReactNode> = {
    pin: <><path d="M20 10c0 5-8 12-8 12S4 15 4 10a8 8 0 1 1 16 0Z"/><circle cx="12" cy="10" r="2.5"/></>,
    search: <><circle cx="10.8" cy="10.8" r="6.8"/><path d="m16 16 4.5 4.5"/></>,
    arrow: <><path d="M5 12h14M13 6l6 6-6 6"/></>,
    globe: <><circle cx="12" cy="12" r="9"/><path d="M3.5 9h17M3.5 15h17M12 3c2.4 2.5 3.6 5.5 3.6 9S14.4 18.5 12 21c-2.4-2.5-3.6-5.5-3.6-9S9.6 5.5 12 3Z"/></>,
    building: <><path d="M4 21V6.8L12 3l8 3.8V21M8 9h.01M12 9h.01M16 9h.01M8 13h.01M12 13h.01M16 13h.01M10 21v-4h4v4"/></>,
    briefcase: <><rect x="3" y="7" width="18" height="14" rx="2"/><path d="M8 7V4h8v3M3 12h18M10 12v2h4v-2"/></>,
    chevron: <path d="m9 18 6-6-6-6"/>,
    close: <><path d="m18 6-12 12M6 6l12 12"/></>,
    external: <><path d="M14 4h6v6M20 4l-9 9"/><path d="M18 13v5a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h5"/></>,
  }
  return <svg {...common}>{paths[name]}</svg>
}

function distanceLabel(distance: number | null) {
  if (distance === null) return 'Distance unavailable'
  return distance < 1000 ? `${Math.round(distance)} m` : `${(distance / 1000).toFixed(1)} km`
}

function statusLabel(status: string) {
  const labels: Record<string, string> = {
    jobs_feed_found: 'Job listings found', career_page_found: 'Hiring page found',
    jobs_extracted: 'Jobs found on company website',
    unresolved: 'Could not find a hiring page yet', not_checked: 'Not checked yet', provider_detected: 'Hiring page found',
    parsed: 'Job listings checked', complete_empty: 'Checked · no open jobs',
  }
  return labels[status] ?? 'Needs checking'
}

function domainMatchLabel(method: string | null) {
  const labels: Record<string, string> = {
    osm_website_tag: 'Listed with the company on the map',
    manual_group_match: 'Confirmed company website',
    osm_entity_wikidata_p856: 'Matched to the company',
  }
  return method ? labels[method] ?? 'Website source checked' : 'Website source not listed'
}

function locationIdentity(latitude: number, longitude: number, radiusKm: number, state?: string | null) {
  if (state) return `state:${state}`
  return `${latitude.toFixed(5)}:${longitude.toFixed(5)}:${radiusKm}`
}

function DirectoryPage() {
  const params = useMemo(() => new URLSearchParams(window.location.search), [])
  const companyFilters: CompanyDiscoveryFilter[] = ['all', 'domain', 'career', 'feed', 'jobs']
  const requestedCompanyFilter = params.get('company_filter') as CompanyDiscoveryFilter | null
  const initialCompanyFilter = requestedCompanyFilter && companyFilters.includes(requestedCompanyFilter)
    ? requestedCompanyFilter : 'all'
  const initialLocation: Location | null = params.has('country')
    ? { label: 'Germany', search_scope: 'country', latitude: 0, longitude: 0, precision: 'country_boundary', country_code: 'de' }
    : params.has('state')
    ? { label: params.get('state')!, state: params.get('state'), search_scope: 'state',
        latitude: 0, longitude: 0, precision: 'state_boundary', country_code: 'de' }
    : params.has('lat') && params.has('lon')
    ? { label: params.get('place') ?? '', city: params.get('place')?.split(',')[0] ?? null,
        latitude: Number(params.get('lat')), longitude: Number(params.get('lon')), precision: 'url_location' }
    : null
  const [location, setLocation] = useState<Location>(NO_LOCATION)
  const [radius, setRadius] = useState(Number(params.get('radius_km') ?? 15))
  const [selectedLocationKey, setSelectedLocationKey] = useState(params.get('location_key') ?? '')
  const [locationOptions, setLocationOptions] = useState<LocationOption[]>([])
  const [locationsReady, setLocationsReady] = useState(false)
  const [query, setQuery] = useState(params.get('q') ?? '')
  const [requestQuery, setRequestQuery] = useState(params.get('q') ?? '')
  const [companyFilter, setCompanyFilter] = useState<CompanyDiscoveryFilter>(initialCompanyFilter)
  const [companySort, setCompanySort] = useState<CompanySort>(params.get('company_sort') === 'name' ? 'name' : 'distance')
  const requestedJobSort = params.get('job_sort')
  const [jobSort, setJobSort] = useState<JobSort>(requestedJobSort === 'posted' || requestedJobSort === 'discovered'
    ? requestedJobSort : requestedJobSort === 'newest' ? 'posted' : 'discovered')
  const requestedLocationScope = params.get('location_scope')
  const [jobLocationScope, setJobLocationScope] = useState<JobLocationScope>(['area', 'remote', 'area_remote'].includes(requestedLocationScope ?? '')
    ? requestedLocationScope as JobLocationScope : params.get('job_work_style') === 'remote' ? 'remote' : 'area_remote')
  const [jobWorkStyle, setJobWorkStyle] = useState<JobWorkStyleFilter>(['hybrid', 'onsite'].includes(params.get('job_work_style') ?? '')
    ? params.get('job_work_style') as JobWorkStyleFilter : 'all')
  const [profiles, setProfiles] = useState<Profile[]>([])
  const [profilesReady, setProfilesReady] = useState(false)
  const requestedProfileIds = params.get('profile_ids')
  const [activeProfileIds, setActiveProfileIds] = useState<number[]>(() => {
    const requested = requestedProfileIds !== null ? requestedProfileIds.split(',')
      : params.has('profile_id') ? [params.get('profile_id') ?? ''] : []
    return [...new Set(requested.map(Number).filter(value => Number.isSafeInteger(value) && value > 0))].slice(0, 1)
  })
  const profileId = String(activeProfileIds[0] ?? '')
  const [jobApplicationCounts, setJobApplicationCounts] = useState<Record<ApplicationStatus, number>>(() =>
    Object.fromEntries(applicationStatuses.map(item => [item.value, 0])) as Record<ApplicationStatus, number>)
  const [jobStatusFilter, setJobStatusFilter] = useState<ApplicationStatus | ''>(() =>
    applicationStatuses.find(item => item.value === params.get('job_status'))?.value ?? '')
  const [tab, setTab] = useState<'companies' | 'jobs'>(params.get('view') === 'jobs' ? 'jobs' : 'companies')
  const [companies, setCompanies] = useState<Company[]>([])
  const [jobs, setJobs] = useState<Job[]>([])
  const [companyTotal, setCompanyTotal] = useState(0)
  const [jobTotal, setJobTotal] = useState(0)
  const [jobTotalIsUpperBound, setJobTotalIsUpperBound] = useState(false)
  const [summary, setSummary] = useState({ companies_in_radius: 0, companies_with_domain: 0, jobs_for_location: 0, remote_jobs_in_result: 0 })
  const [offset, setOffset] = useState(0)
  const [jobReloadKey, setJobReloadKey] = useState(0)
  const [snapshotStale, setSnapshotStale] = useState(false)
  const [snapshotRefreshTick, setSnapshotRefreshTick] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [trackingError, setTrackingError] = useState('')
  const [trackingNotice, setTrackingNotice] = useState('')
  const [semanticFallback, setSemanticFallback] = useState(false)
  const [swipeSavingJobId, setSwipeSavingJobId] = useState<number | null>(null)
  const swipeRequestToken = useRef(0)
  const profileSelectionToken = useRef(0)
  const jobSnapshotRef = useRef<{ criteria: string; id: string } | null>(null)
  const snapshotStaleRef = useRef(false)
  const snapshotRefreshPollRef = useRef(false)
  const loadedCompanyCriteriaRef = useRef<string | null>(null)
  const priorJobCriteriaRef = useRef<string | null>(null)
  snapshotStaleRef.current = snapshotStale
  const requestOffset = tab === 'companies' ? offset : 0

  useEffect(() => {
    const timer = window.setTimeout(() => setRequestQuery(query.trim()), 250)
    return () => window.clearTimeout(timer)
  }, [query])

  const selectedProfile = profiles.find(profile => String(profile.id) === profileId)
  const profileArea = selectedProfile?.search_area
  const profileLocationOption: LocationOption | null = selectedLocationKey.startsWith('profile:') && profileArea?.latitude != null && profileArea.longitude != null
    ? {
      key: `profile:${selectedProfile?.id}`,
      label: profileArea.city || profileArea.label.split(',')[0],
      location: { ...profileArea, latitude: profileArea.latitude, longitude: profileArea.longitude, precision: 'profile_search_area' },
      radius_km: profileArea.radius_km,
    }
    : null
  const selectableLocationOptions = profileLocationOption ? [...locationOptions, profileLocationOption] : locationOptions

  useEffect(() => {
    const controller = new AbortController()
    fetch(apiUrl('/api/v1/profiles'), { signal: controller.signal }).then(async response => {
      if (!response.ok) throw new Error('Could not load saved profiles.')
      const data = await response.json(); setProfiles(data.items)
      const allProfileIds: number[] = data.items.flatMap((item: Profile) => item.id == null ? [] : [item.id])
      const validIds = new Set<number>(allProfileIds)
      const requested = requestedProfileIds !== null ? requestedProfileIds.split(',').map(Number)
        : params.has('profile_id') ? [Number(params.get('profile_id'))] : null
      const selectedIds = requested !== null
        ? [...new Set(requested.filter((id: number) => Number.isSafeInteger(id) && id > 0 && validIds.has(id)))].slice(0, 1)
        : !(params.has('state') || params.has('country')) ? allProfileIds.slice(0, 1) : []
      const selected = data.items.find((item: Profile) => item.id === selectedIds[0])
      if (selected) {
        applyProfile(selected, params)
        setActiveProfileIds(selectedIds)
        if (!params.has('view')) setTab('jobs')
      } else {
        setActiveProfileIds([])
      }
      setProfilesReady(true)
    }).catch(reason => { if (!controller.signal.aborted) { setError(reason.message); setProfilesReady(true) } })
    return () => controller.abort()
  }, [])

  const locationParams = useMemo(() => selectedLocationKey
    ? boardLocationParams(location, radius, params.get('country_code'), selectedProfile?.search_area?.country_code)
    : new URLSearchParams(),
  [location, radius, params, selectedLocationKey, selectedProfile?.search_area?.country_code])
  const jobSnapshotCriteria = JSON.stringify([
    requestQuery, locationParams.toString(), jobLocationScope, jobWorkStyle, jobSort,
  ])
  const companyRefreshCriteria = JSON.stringify([
    locationParams.toString(), requestOffset,
    location.search_scope === 'country' || location.search_scope === 'state' ? 'name' : companySort,
    companyFilter, requestQuery,
  ])
  const jobCriteriaChanged = priorJobCriteriaRef.current !== null &&
    priorJobCriteriaRef.current !== jobSnapshotCriteria
  const jobRequestOffset = tab === 'jobs' && !jobCriteriaChanged ? offset : 0

  useEffect(() => {
    if (priorJobCriteriaRef.current !== jobSnapshotCriteria) {
      priorJobCriteriaRef.current = jobSnapshotCriteria
      jobSnapshotRef.current = null
      setSnapshotStale(false)
      setSnapshotRefreshTick(0)
      if (offset !== 0) setOffset(0)
    }
  }, [jobSnapshotCriteria, offset])

  useEffect(() => {
    if (!profilesReady) return
    let cancelled = false
    async function loadLocationOptions() {
      try {
        const locationResponse = await fetch(apiUrl('/api/v1/locations'))
        if (!locationResponse.ok) throw new Error('Could not load configured locations.')
        const locationData = await locationResponse.json()
        const available: LocationOption[] = locationData.items.map((item: {
          search_scope?: 'radius' | 'state' | 'country'; id: number; label: string; city: string | null; state: string | null; postcode: string | null
          latitude: number; longitude: number; radius_km: number
        }) => ({
          key: `saved:${item.id}`,
          label: item.city || item.label.split(',')[0],
          location: { ...item, precision: 'configured_location', type: 'configured_location' },
          radius_km: item.radius_km,
        }))
        const seenLocations = new Set<string>()
        const options = available.filter(option => {
          const identity = locationIdentity(option.location.latitude, option.location.longitude, option.radius_km, option.location.search_scope === 'country' ? 'Germany' : option.location.search_scope === 'state' ? option.location.state : null)
          if (seenLocations.has(identity)) return false
          seenLocations.add(identity)
          return true
        })
        if (cancelled) return
        setLocationOptions(options)
        const requestedKey = params.get('location_key')
        const profileSelection = selectedProfile && selectedLocationKey === `profile:${selectedProfile.id}`
          ? selectedProfile.search_area : null
        const configuredProfileLocation = profileSelection?.latitude != null && profileSelection.longitude != null
          ? options.find(option => locationIdentity(option.location.latitude, option.location.longitude, option.radius_km, option.location.search_scope === 'state' ? option.location.state : null) ===
            locationIdentity(profileSelection.latitude!, profileSelection.longitude!, profileSelection.radius_km))
          : undefined
        if (profileSelection && !configuredProfileLocation) {
          // A saved profile area is configured by the user and remains selectable for that profile.
        } else {
          const selected = configuredProfileLocation
            ?? options.find(option => option.key === requestedKey)
            ?? (initialLocation ? options.find(option => locationIdentity(option.location.latitude, option.location.longitude, option.radius_km, option.location.search_scope === 'country' ? 'Germany' : option.location.search_scope === 'state' ? option.location.state : null) ===
              locationIdentity(initialLocation.latitude, initialLocation.longitude, radius, initialLocation.search_scope === 'country' ? 'Germany' : initialLocation.search_scope === 'state' ? initialLocation.state : null)) : undefined)
            ?? options[0]
          if (selected) {
            setSelectedLocationKey(selected.key)
            setLocation(selected.location)
            if (!params.has('radius_km')) setRadius(selected.radius_km)
          } else {
            setSelectedLocationKey('')
            setLocation(NO_LOCATION)
          }
        }
      } catch (exception) {
        if (!cancelled) setError(exception instanceof Error ? exception.message : 'Could not load available locations.')
      } finally {
        if (!cancelled) setLocationsReady(true)
      }
    }
    void loadLocationOptions()
    return () => { cancelled = true }
  }, [profilesReady])

  const loadData = useCallback(async (signal: AbortSignal) => {
    if (!profilesReady || !locationsReady) {
      snapshotRefreshPollRef.current = false
      return
    }
    setError('')
    if (!selectedLocationKey) {
      setCompanies([]); setCompanyTotal(0); setJobs([]); setJobTotal(0)
      setJobTotalIsUpperBound(false)
      setSummary({ companies_in_radius: 0, companies_with_domain: 0, jobs_for_location: 0, remote_jobs_in_result: 0 })
      setJobApplicationCounts(Object.fromEntries(applicationStatuses.map(item => [item.value, 0])) as Record<ApplicationStatus, number>)
      setSnapshotStale(false)
      setSnapshotRefreshTick(0)
      snapshotRefreshPollRef.current = false
      setLoading(false)
      return
    }
    if (!snapshotStaleRef.current) setLoading(true)
    const isSnapshotRefreshPoll = snapshotRefreshPollRef.current
    snapshotRefreshPollRef.current = false
    const refreshOnly = isSnapshotRefreshPoll && snapshotStaleRef.current && !jobCriteriaChanged &&
      loadedCompanyCriteriaRef.current === companyRefreshCriteria
    const companyParams = new URLSearchParams(locationParams)
    companyParams.set('offset', String(requestOffset))
    companyParams.set('limit', String(PAGE_SIZE))
    companyParams.set('sort', location.search_scope === 'country' || location.search_scope === 'state' ? 'name' : companySort)
    if (companyFilter !== 'all') companyParams.set('discovery', companyFilter)
    if (requestQuery) companyParams.set('query', requestQuery)

    const jobParams = new URLSearchParams(locationParams)
    jobParams.set('sort', jobSort)
    jobParams.set('location_scope', jobLocationScope)
    jobParams.set('offset', String(tab === 'jobs' ? jobRequestOffset : 0))
    jobParams.set('limit', String(tab === 'jobs' ? PAGE_SIZE : 1))
    if (jobWorkStyle !== 'all') jobParams.set('work_style', jobWorkStyle)
    if (requestQuery) jobParams.set('query', requestQuery)
    if (profileId) jobParams.set('profile_id', profileId)
    if (profileId && jobStatusFilter) jobParams.set('job_status', jobStatusFilter)
    const cachedSnapshot = jobSnapshotRef.current
    if (cachedSnapshot?.criteria === jobSnapshotCriteria) jobParams.set('snapshot_id', cachedSnapshot.id)
    try {
      const [companyResponse, jobsResponse, summaryResponse] = await Promise.all([
        refreshOnly ? Promise.resolve(null) : fetch(apiUrl(`/api/v1/companies?${companyParams}`), { signal }),
        fetch(apiUrl(`/api/v1/jobs?${jobParams}`), { signal }),
        refreshOnly ? Promise.resolve(null) : fetch(apiUrl(`/api/v1/summary?${locationParams}`), { signal }),
      ])
      if (jobsResponse.status === 409 && jobParams.has('snapshot_id')) {
        jobSnapshotRef.current = null
        setSnapshotStale(false)
        setSnapshotRefreshTick(0)
        setOffset(0)
        setJobReloadKey(value => value + 1)
        return
      }
      const failedResponse = (companyResponse && !companyResponse.ok) ? companyResponse
        : !jobsResponse.ok ? jobsResponse
          : (summaryResponse && !summaryResponse.ok) ? summaryResponse : null
      if (failedResponse) {
        const errorBody = await failedResponse.json().catch(() => ({})) as { detail?: unknown }
        throw new Error(typeof errorBody.detail === 'string' ? errorBody.detail : `Could not load company and job results (error ${failedResponse.status}).`)
      }
      const jobPage = await jobsResponse.json() as JobsPage
      if (!refreshOnly) {
        const [companyPage, summaryData] = await Promise.all([
          companyResponse!.json(), summaryResponse!.json(),
        ])
        setCompanies(companyPage.items)
        setCompanyTotal(companyPage.total)
        setSummary(summaryData)
        loadedCompanyCriteriaRef.current = companyRefreshCriteria
        setSemanticFallback(Boolean(requestQuery &&
          (companyPage.search_mode === 'keyword' || jobPage.search_mode === 'keyword')))
      }
      setJobs(jobPage.items)
      setSnapshotStale(Boolean(jobPage.snapshot_stale))
      if (jobPage.snapshot_stale) {
        jobSnapshotRef.current = null
        setSnapshotRefreshTick(value => value + 1)
      } else {
        setSnapshotRefreshTick(0)
        jobSnapshotRef.current = jobPage.snapshot_id
          ? { criteria: jobSnapshotCriteria, id: jobPage.snapshot_id }
          : null
      }
      setJobTotal(jobPage.total)
      setJobTotalIsUpperBound(Boolean(jobPage.total_is_upper_bound))
      setJobApplicationCounts(jobPage.application_counts ?? Object.fromEntries(
        applicationStatuses.map(item => [item.value, 0])) as Record<ApplicationStatus, number>)
    } catch (exception) {
      if (!signal.aborted) {
        setError(exception instanceof Error ? exception.message : 'Could not load company and job results.')
        if (snapshotStaleRef.current) setSnapshotRefreshTick(value => value + 1)
      }
    } finally {
      if (!signal.aborted) setLoading(false)
    }
  }, [companyFilter, companySort, jobLocationScope, jobWorkStyle, jobSort, jobStatusFilter, locationParams,
    requestOffset, jobRequestOffset, requestQuery, jobSnapshotCriteria, tab, profileId,
    profilesReady, locationsReady, selectedLocationKey, jobReloadKey,
    companyRefreshCriteria])

  useEffect(() => {
    const controller = new AbortController()
    void loadData(controller.signal)
    return () => controller.abort()
  }, [loadData])

  useEffect(() => {
    if (!snapshotStale) return
    const delay = Math.min(1500 * (2 ** Math.min(snapshotRefreshTick, 4)), 15000)
    const timer = window.setTimeout(() => {
      snapshotRefreshPollRef.current = true
      setJobReloadKey(value => value + 1)
    }, delay)
    return () => window.clearTimeout(timer)
  }, [snapshotStale, snapshotRefreshTick, jobSnapshotCriteria])

  useEffect(() => {
    if (!profilesReady || !locationsReady) return
    const url = new URL(window.location.href)
    if (!selectedLocationKey) {
      for (const key of ['country', 'state', 'lat', 'lon', 'place', 'radius_km', 'country_code', 'location_key']) url.searchParams.delete(key)
    } else if (location.search_scope === 'country') {
      url.searchParams.set('country', 'DE')
      for (const key of ['state', 'lat', 'lon', 'place', 'radius_km']) url.searchParams.delete(key)
    } else if (location.search_scope === 'state' && location.state) {
      url.searchParams.delete('country')
      url.searchParams.set('state', location.state)
      for (const key of ['lat', 'lon', 'place', 'radius_km']) url.searchParams.delete(key)
    } else {
      url.searchParams.delete('country')
      url.searchParams.delete('state')
      url.searchParams.set('lat', String(location.latitude))
      url.searchParams.set('lon', String(location.longitude))
      url.searchParams.set('place', location.city ?? location.label.split(',')[0])
      url.searchParams.set('radius_km', String(radius))
    }
    const effectiveCountry = locationParams.get('country_code')
    if (effectiveCountry) url.searchParams.set('country_code', effectiveCountry)
    else url.searchParams.delete('country_code')
    if (selectedLocationKey) url.searchParams.set('location_key', selectedLocationKey)
    else url.searchParams.delete('location_key')
    if (query.trim()) url.searchParams.set('q', query.trim())
    else url.searchParams.delete('q')
    if (companyFilter !== 'all') url.searchParams.set('company_filter', companyFilter)
    else url.searchParams.delete('company_filter')
    if (companySort !== 'distance') url.searchParams.set('company_sort', companySort)
    else url.searchParams.delete('company_sort')
    if (jobSort !== 'discovered') url.searchParams.set('job_sort', jobSort)
    else url.searchParams.delete('job_sort')
    if (jobWorkStyle !== 'all') url.searchParams.set('job_work_style', jobWorkStyle)
    else url.searchParams.delete('job_work_style')
    if (jobLocationScope !== 'area') url.searchParams.set('location_scope', jobLocationScope)
    else url.searchParams.delete('location_scope')
    if (tab !== 'companies') url.searchParams.set('view', tab)
    else url.searchParams.delete('view')
    if (profileId) url.searchParams.set('profile_id', profileId)
    else url.searchParams.delete('profile_id')
    url.searchParams.delete('profile_ids')
    url.searchParams.delete('min_match_score')
    url.searchParams.delete('include_unknown')
    if (jobStatusFilter) url.searchParams.set('job_status', jobStatusFilter)
    else url.searchParams.delete('job_status')
    window.history.replaceState({}, '', url)
  }, [companyFilter, companySort, jobSort, jobLocationScope, jobWorkStyle, location, locationParams, radius, query, selectedLocationKey, tab, profileId, jobStatusFilter, profilesReady, locationsReady])

  function applyProfile(profile: Profile, overrides = new URLSearchParams()) {
    if (profile.id == null) return
    const profileLocationKey = `profile:${profile.id}`
    const defaults = profileBoardDefaults(
      profile,
      overrides.get('location_key') === profileLocationKey ? new URLSearchParams() : overrides,
    )
    swipeRequestToken.current += 1
    profileSelectionToken.current += 1
    setSwipeSavingJobId(null)
    setActiveProfileIds([profile.id])
    setJobStatusFilter('')
    setJobs([])
    if (defaults.location) { setLocation(defaults.location); setSelectedLocationKey(`profile:${profile.id}`) }
    else if (selectedLocationKey.startsWith('profile:')) {
      const configured = locationOptions[0]
      setSelectedLocationKey(configured?.key ?? '')
      setLocation(configured?.location ?? NO_LOCATION)
      if (configured) setRadius(configured.radius_km)
    }
    setRadius(defaults.radius)
    setOffset(0)
  }
  function invalidateProfileRequests() {
    swipeRequestToken.current += 1
    profileSelectionToken.current += 1
    setSwipeSavingJobId(null)
    setJobs([])
  }
  function selectSearchProfile(value: string) {
    const profile = profiles.find(item => String(item.id) === value)
    if (profile) {
      applyProfile(profile)
    } else {
      invalidateProfileRequests()
      setActiveProfileIds([])
      setJobStatusFilter('')
      if (selectedLocationKey.startsWith('profile:')) {
        const configured = locationOptions[0]
        setSelectedLocationKey(configured?.key ?? '')
        setLocation(configured?.location ?? NO_LOCATION)
        if (configured) setRadius(configured.radius_km)
      }
      setOffset(0)
    }
  }
  function cycleJobSort() {
    const index = jobSortModes.findIndex(item => item.value === jobSort)
    setJobSort(jobSortModes[(index + 1) % jobSortModes.length].value)
    setOffset(0)
  }
  function selectLocation(key: string) {
    const option = selectableLocationOptions.find(item => item.key === key)
    if (!option) return
    setSelectedLocationKey(key)
    setLocation(option.location)
    setRadius(option.radius_km)
    setJobLocationScope(option.location.search_scope === 'country' || option.location.search_scope === 'state' ? 'area_remote' : 'area')
    setOffset(0)
  }

  async function trackOpenedPost(job: Job) {
    if (!job.profile_id) return
    const trackingProfileId = job.profile_id
    const profileToken = profileSelectionToken.current
    setTrackingError('')
    try {
      const application = await trackJobInteraction(API_BASE_URL, trackingProfileId, job.id, job.application, 'open_post')
      if (profileSelectionToken.current !== profileToken) return
      if (application) setJobs(current => current.map(item => item.id === job.id && item.profile_id === trackingProfileId ? { ...item, application } : item))
    } catch {
      if (profileSelectionToken.current !== profileToken) return
      setTrackingError(`Could not update tracking for ${job.title}. Open the role details to try again.`)
    }
  }

  async function changeJobApplication(job: Job, action: ApplicationSwipeAction) {
    if (!job.profile_id || swipeSavingJobId !== null) return
    const trackingProfileId = job.profile_id
    const requestToken = ++swipeRequestToken.current
    const profileToken = profileSelectionToken.current
    setTrackingError('')
    setTrackingNotice('')
    setSwipeSavingJobId(job.id)
    try {
      const saved = await saveApplication(API_BASE_URL, trackingProfileId, job.id, action.status, action.kind)
      if (profileSelectionToken.current !== profileToken) return
      setJobs(current => current.map(item => item.id === job.id && item.profile_id === trackingProfileId ? { ...item, application: saved } : item))
      const previousStatus = job.application?.status ?? 'new'
      if (previousStatus !== saved.status) {
        setJobApplicationCounts(current => ({
          ...current,
          [previousStatus]: Math.max(0, current[previousStatus] - 1),
          [saved.status]: current[saved.status] + 1,
        }))
        setJobReloadKey(value => value + 1)
      }
      const label = applicationStatusLabel(action.status)
      setTrackingNotice(action.kind === 'undo' ? `${job.title} restored to ${label}.` : `${job.title} moved to ${label}.`)
    } catch (reason) {
      if (profileSelectionToken.current !== profileToken) return
      setTrackingError(reason instanceof Error ? reason.message : `Could not update tracking for ${job.title}.`)
    } finally {
      if (swipeRequestToken.current === requestToken) setSwipeSavingJobId(null)
    }
  }

  function companyHref(companyId: number) {
    const detailParams = new URLSearchParams(window.location.search)
    if (profileId) detailParams.set('profile_id', profileId)
    return `/companies/${companyId}?${detailParams}`
  }

  const visibleJobs = jobs
  const activeTotal = tab === 'companies' ? companyTotal : jobTotal
  const allMatchingStages = Object.values(jobApplicationCounts).reduce((total, count) => total + count, 0)
  const pageNumber = Math.floor(offset / PAGE_SIZE) + 1
  const pageCount = Math.max(1, Math.ceil(activeTotal / PAGE_SIZE))
  const wholeState = Boolean(selectedLocationKey) && (location.search_scope === 'state' || location.search_scope === 'country')
  const locality = selectedLocationKey ? location.city ?? location.label.split(',')[0] : 'a configured location'
  const selectedScopeLabel = location.search_scope === 'country' ? 'Germany' : locality

  return (
    <div className="app-shell">
      <SiteHeader active="directory" />

      <main className="content">
        <section className="intro-row">
          <div>
            <h1>Find employers and open roles.</h1>
            <p className="intro-copy">Search career pages and public listings by area, state, or across Germany.</p>
          </div>
          <div className="snapshot-pill">{selectedLocationKey
            ? wholeState ? `Across ${selectedScopeLabel}` : `${radius} km around ${locality}`
            : 'Choose a search area'}</div>
        </section>

        <section className="search-card" aria-label="Choose a location">
          <div className="search-form">
            <label className="search-location">
              <span className="control-label">Saved search area</span>
              <span className="input-wrap location-select-wrap"><Icon name="pin" size={19} />
                <select aria-label="Configured locations" value={selectedLocationKey} disabled={!locationsReady || selectableLocationOptions.length === 0}
                  onChange={event => selectLocation(event.target.value)}>
                  <option value="" disabled>{locationsReady ? selectableLocationOptions.length ? 'Choose a location' : 'No configured locations' : 'Loading locations…'}</option>
                  {selectableLocationOptions.map(option => <option key={option.key} value={option.key}>
                    {option.label} · {option.location.search_scope === 'country' ? 'All Germany' : option.location.search_scope === 'state' ? 'Whole state' : `${option.key === selectedLocationKey ? radius : option.radius_km} km`}
                  </option>)}
                </select>
              </span>
            </label>
            {selectedLocationKey && !wholeState && <label className="radius-control">
              <span className="control-label">Job distance</span>
              <select aria-label="Listing distance around this place" value={radius} onChange={event => { setRadius(Number(event.target.value)); setOffset(0) }}>
                {[...new Set([5, 10, 15, 25, 35, 50, 100, 200, radius])].sort((a,b) => a-b).map(value => <option key={value} value={value}>{value} km</option>)}
              </select>
            </label>}
            <a href="/locations" className="configure-locations-link">Configure locations <Icon name="arrow" size={15} /></a>
          </div>
          <div className="search-footnote"><span><Icon name="globe" size={14} />{selectedLocationKey ? selectedScopeLabel : 'No location selected'}</span><span>{selectedLocationKey ? 'Jobs use their listed work location when available; public sources may be incomplete.' : 'Configure a location to see nearby employers and jobs.'}</span></div>
        </section>

        {error && <div className="error-banner" role="alert">{error}<button type="button" onClick={() => setError('')}>Dismiss</button></div>}

        {tab === 'companies' && selectedLocationKey && <section className="metrics-grid" aria-label="Search overview">
          <article className="metric-card metric-primary">
            <span className="metric-icon"><Icon name="building" size={19} /></span>
            <span className="metric-label">Companies in this area</span>
            <strong>{loading ? '—' : summary.companies_in_radius.toLocaleString('en')}</strong>
            <span className="metric-note">{wholeState ? `Across ${locality}` : `within ${radius} km of ${locality}`}</span>
          </article>
          <article className="metric-card">
            <span className="metric-icon metric-icon-green"><Icon name="globe" size={19} /></span>
            <span className="metric-label">With a website</span>
            <strong>{loading ? '—' : summary.companies_with_domain.toLocaleString('en')}</strong>
            <span className="metric-note">{wholeState ? 'company websites found in this search area' : 'company websites found nearby'}</span>
          </article>
          <article className="metric-card">
            <span className="metric-icon metric-icon-lilac"><Icon name="briefcase" size={19} /></span>
            <span className="metric-label">{wholeState ? 'Jobs in selected area' : 'Jobs in this area'}</span>
            <strong>{loading ? '—' : summary.jobs_for_location.toLocaleString('en')}</strong>
            <span className="metric-note">{wholeState ? 'matched by listed work location when available' : `roles within ${radius} km`}</span>
          </article>
          <article className="metric-card metric-remote">
            <span className="metric-icon metric-icon-sand"><Icon name="globe" size={19} /></span>
            <span className="metric-label">Remote roles</span>
            <strong>{loading ? '—' : summary.remote_jobs_in_result.toLocaleString('en')}</strong>
            <span className="metric-note">select Remote in the job location filter</span>
          </article>
        </section>}

        <section className="directory-card">
          <div className="directory-header">
            <div>
              <h2>{!selectedLocationKey
                ? tab === 'jobs' ? 'Jobs' : 'Companies'
                : tab === 'jobs'
                  ? jobLocationScope === 'remote' ? 'Remote jobs'
                    : jobLocationScope === 'area_remote'
                      ? wholeState ? `Jobs in ${selectedScopeLabel} + remote` : `Jobs near ${locality} + remote`
                      : wholeState ? `Jobs in ${selectedScopeLabel}` : `Jobs near ${locality}`
                  : wholeState ? `Companies in ${selectedScopeLabel}` : `Companies near ${locality}`}</h2>
            </div>
            <label className="table-search"><Icon name="search" size={17} />
              <input aria-label="Search results" value={query} onChange={event => { setQuery(event.target.value); setOffset(0) }} placeholder="Search companies or job text" />
              {query && <button type="button" aria-label="Clear search" onClick={() => setQuery('')}><Icon name="close" size={15} /></button>}
            </label>
          </div>

          <div className="directory-toolbar">
            <div className="tabs" role="tablist" aria-label="Results type">
              <button role="tab" aria-selected={tab === 'companies'} className={tab === 'companies' ? 'active' : ''} onClick={() => { setTab('companies'); setOffset(0) }}>
                Companies <span>{companyTotal.toLocaleString('en')}</span>
              </button>
              <button role="tab" aria-selected={tab === 'jobs'} className={tab === 'jobs' ? 'active' : ''} onClick={() => { setTab('jobs'); setOffset(0) }}>
                Jobs <span>{jobTotal.toLocaleString('en')}</span>
              </button>
            </div>
            <div className="directory-tools">
              {tab === 'companies' ? <>
                <label className="discovery-filter"><span>Filter</span>
                <select aria-label="Filter companies by discovery" value={companyFilter}
                  onChange={event => { setCompanyFilter(event.target.value as CompanyDiscoveryFilter); setOffset(0) }}>
                  <option value="all">All companies</option>
                  <option value="domain">With a company website</option>
                  <option value="career">Hiring page found</option>
                  <option value="feed">Job listings found</option>
                  <option value="jobs">Open jobs found</option>
                </select>
                </label>
                {!requestQuery && <label className="sorting-control"><span>Sort</span>
                  <select aria-label="Sort companies" value={wholeState ? 'name' : companySort}
                    onChange={event => { setCompanySort(event.target.value as CompanySort); setOffset(0) }}>
                    {!wholeState && <option value="distance">Closest first</option>}
                    <option value="name">Name A–Z</option>
                  </select>
                </label>}
              </> : null}
              <div className="result-context"><span className="context-dot" /> {wholeState ? `${selectedScopeLabel} · ${tab === 'jobs' ? 'work location' : 'company location'}` : tab === 'jobs' ? jobLocationScope === 'remote' ? 'Remote roles only' : jobLocationScope === 'area_remote' ? `${locality} + remote` : `${locality} · ${radius} km` : `Company websites · ${radius} km`}</div>
            </div>
          </div>

          {tab === 'jobs' && <section className="job-feed-filters" aria-label="Filter job results">
            {profiles.length > 0 && <div className="job-feed-filter-row" role="group" aria-label="Choose application profile">
              <label className="job-feed-filter-label" htmlFor="search-profile">Profile context</label>
              <div className="job-feed-chip-list">
                <select id="search-profile" aria-label="Profile for saved area and application tracking" value={profileId}
                  className="application-profile-context"
                  onChange={event => selectSearchProfile(event.target.value)}>
                  <option value="">Location only</option>
                  {profiles.map(profile => {
                    const area = profile.search_area
                    const areaLabel = area?.city || area?.label || 'No saved area'
                    return <option key={profile.id} value={profile.id}>{profile.name} · {areaLabel}</option>
                  })}
                </select>
                {profileId && <a className="job-feed-inline-link" href="/profile">Edit profiles</a>}
              </div>
            </div>}
            {activeProfileIds.length > 0 && <div className="job-feed-filter-row" role="group" aria-label="Filter by application stage">
              <span className="job-feed-filter-label">Stage</span><div className="job-feed-chip-list">
                <button type="button" className={`application-filter-chip ${jobStatusFilter === '' ? 'selected' : ''}`}
                  aria-pressed={jobStatusFilter === ''} onClick={() => { setJobStatusFilter(''); setOffset(0) }}>All <span>{allMatchingStages}</span></button>
                {applicationStatuses.map(item => <button type="button" key={item.value}
                  className={`application-filter-chip application-status-filter application-${item.value} ${jobStatusFilter === item.value ? 'selected' : ''}`}
                  aria-pressed={jobStatusFilter === item.value} onClick={() => { setJobStatusFilter(current => current === item.value ? '' : item.value); setOffset(0) }}>
                  {item.label} <span>{jobApplicationCounts[item.value]}</span>
                </button>)}
              </div>
            </div>}
            <div className="job-feed-filter-row" role="group" aria-label="Filter by job location">
              <span className="job-feed-filter-label">Location</span><div className="job-feed-chip-list">
                {([
                  ['area', wholeState ? 'In selected scope' : 'Area'],
                  ['remote', 'Remote'],
                  ['area_remote', wholeState ? 'Scope + remote' : 'Area + remote'],
                ] as [JobLocationScope, string][]).map(([value, label]) => <button type="button" key={value}
                  className={`application-filter-chip ${jobLocationScope === value ? 'selected' : ''}`}
                  aria-pressed={jobLocationScope === value} onClick={() => { setJobLocationScope(value); setOffset(0) }}>{label}</button>)}
              </div>
            </div>
            <div className="job-feed-filter-row" role="group" aria-label="More job filters">
              <span className="job-feed-filter-label">More</span><div className="job-feed-chip-list">
                {([
                  ['all', 'Any work style'],
                  ['hybrid', 'Hybrid'],
                  ['onsite', 'On-site'],
                ] as [JobWorkStyleFilter, string][]).map(([value, label]) => <button type="button" key={value}
                  className={`application-filter-chip ${jobWorkStyle === value ? 'selected' : ''}`}
                  aria-pressed={jobWorkStyle === value} onClick={() => { setJobWorkStyle(value); setOffset(0) }}>{label}</button>)}
                {!requestQuery && <button type="button" className="application-sort-cycle job-feed-sort" onClick={cycleJobSort}
                  aria-label={`Sort by ${jobSortModes.find(item => item.value === jobSort)?.label}. Tap to switch to ${jobSortModes[(jobSortModes.findIndex(item => item.value === jobSort) + 1) % jobSortModes.length].label}.`}
                  title="Tap to change sort order">Sort: {jobSortModes.find(item => item.value === jobSort)?.label} <span aria-hidden="true">↻</span></button>}
              </div>
            </div>
          </section>}

          {trackingError && <div className="tracking-error-note" role="status">{trackingError}<button type="button" onClick={() => setTrackingError('')}>Dismiss</button></div>}
          {trackingNotice && <p className="evidence-note" role="status">{trackingNotice}</p>}
          {semanticFallback && requestQuery && <p className="evidence-note" role="status">Local semantic search is starting. Keyword results are shown until the embedding index is ready.</p>}
          {tab === 'jobs' && profiles.length > 0 && <p className="evidence-note">Profile context sets the saved area and where application stages are tracked. It does not filter jobs by match score.</p>}
          {snapshotStale && tab === 'jobs' && <p className="evidence-note snapshot-refresh-note" role="status">
            Updating job results in the background.
          </p>}
          {locationsReady && !selectedLocationKey ? <div className="location-empty-state">
            <EmptyState title="No configured locations yet" body="Add a search area to see nearby employers and jobs." />
            <a href="/locations" className="page-link">Configure locations <Icon name="arrow" size={15} /></a>
          </div> : loading ? <div className="loading-state"><span className="spinner" /> Gathering the latest results…</div> : tab === 'companies' ? (
            <div className="table-scroll mobile-results-scroll">
              <table className="companies-table mobile-results-table">
                <thead><tr><th>Company</th><th>Website</th><th>Hiring page</th><th>{wholeState ? 'State' : 'Distance'}</th><th /></tr></thead>
                <tbody>
                  {companies.map(company => <tr key={company.id} onClick={() => { window.location.href = companyHref(company.id) }} className="click-row">
                    <td className="result-primary"><div className="company-cell"><span className="company-monogram">{company.name.slice(0, 1).toUpperCase()}</span><span><strong><a href={companyHref(company.id)} onClick={event => event.stopPropagation()}>{company.name}</a></strong><small>{company.category?.replaceAll('_', ' ') || 'Business type not listed'}</small></span></div></td>
                    <td className="result-secondary"><span className="mobile-field-label">Website</span>{company.domain ? <span className="domain-cell"><a className="domain-link" href={company.website_url ?? '#'} target="_blank" rel="noreferrer" onClick={event => event.stopPropagation()}>{company.domain}<Icon name="external" size={12} /></a><small>{domainMatchLabel(company.domain_match_method)}</small></span> : <span className="muted">Website not found yet</span>}</td>
                    <td className="result-meta"><span className="mobile-field-label">Hiring status</span><span className={`status-badge ${['jobs_feed_found','jobs_extracted'].includes(company.career_status) ? 'status-success' : company.career_status === 'career_page_found' ? 'status-career' : 'status-muted'}`}><span />{statusLabel(company.career_status)}</span><small title={company.crawl_reason ?? undefined}>{company.crawl_paused ? 'Checks paused' : company.crawl_interval_days === 1 ? 'Checked daily' : `Checked every ${company.crawl_interval_days ?? 7} days`}</small></td>
                    <td className="result-meta"><span className="mobile-field-label">{wholeState ? 'State' : 'Distance'}</span><span className="distance-text"><Icon name="pin" size={14} />{wholeState ? company.state || locality : distanceLabel(company.distance_m)}</span></td>
                    <td className="row-arrow"><Icon name="chevron" size={17} /></td>
                  </tr>)}
                </tbody>
              </table>
              {companies.length === 0 && <EmptyState title="No companies listed in this area yet" body="Add this place on the search areas page to find nearby companies and their websites." />}
            </div>
          ) : (
            <div className="job-opportunity-list">
              {visibleJobs.map(job => {
                const jobHref = new URLSearchParams(window.location.search)
                if (job.profile_id) jobHref.set('profile_id', String(job.profile_id))
                return <JobFeedCard key={`${job.profile_id ?? 'all'}:${job.id}`} job={job} href={`/jobs/${job.id}?${jobHref}`}
                  profileId={job.profile_id} profileName={job.profile_name} updatedAt={job.application?.updated_at ?? job.last_seen_at}
                  disabled={!job.profile_id || swipeSavingJobId !== null} saving={swipeSavingJobId === job.id}
                  onAction={(_jobId, _profileId, action) => { void changeJobApplication(job, action) }}
                  onOpenOriginal={() => { void trackOpenedPost(job) }} />
              })}
              {jobTotal === 0 && <EmptyState
                title={snapshotStale ? 'Preparing your job search' : jobStatusFilter ? 'No jobs in this stage' : 'No matching jobs found yet'}
                body={snapshotStale ? 'The saved area search is being refreshed in the background. Results will appear here automatically.' : jobStatusFilter ? 'Choose another stage or view all jobs in this area.' : jobLocationScope === 'remote' ? 'Try adjusting the location or search text for remote roles.' : jobLocationScope === 'area_remote' ? 'Try adjusting the search text for jobs in this area or remote roles.' : 'Try adjusting the search text for jobs in this area.'} />}
            </div>
          )}

          <div className="table-footer">
            <span>Showing <strong>{activeTotal ? offset + 1 : 0}–{Math.min(offset + PAGE_SIZE, activeTotal)}</strong> of {tab === 'jobs' && jobTotalIsUpperBound ? 'up to ' : ''}<strong>{activeTotal.toLocaleString('en')}</strong></span>
            <div className="pagination"><button type="button" disabled={offset === 0 || loading} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>Previous</button><span>{pageNumber} / {pageCount}</span><button type="button" disabled={offset + PAGE_SIZE >= activeTotal || loading} onClick={() => setOffset(offset + PAGE_SIZE)}>Next</button></div>
          </div>
        </section>

        <footer className="page-footer"><span>Companies come from public listings; jobs come from public job listings and employer hiring pages.</span><span>Map listings © OpenStreetMap contributors <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">ODbL</a></span></footer>
      </main>

    </div>
  )
}

function EmptyState({ title, body }: { title: string; body: string }) {
  return <div className="empty-state"><span className="empty-icon"><Icon name="search" size={20} /></span><strong>{title}</strong><p>{body}</p></div>
}

function App() {
  const path = window.location.pathname
  if (path === '/profile' || path.startsWith('/profile/')) return <ProfilesPage />
  if (path === '/applications' || path === '/applications/') return <ApplicationsPage />
  if (path === '/discover' || path === '/discover/') return <DirectoryPage />
  if (path === '/settings' || path === '/settings/') return <SettingsPage />
  if (path === '/locations' || path === '/locations/') return <LocationsPage />
  if (path === '/feeds' || path === '/feeds/') return <FeedSourcesPage />
  const applicationPreparationMatch = path.match(/^\/jobs\/(\d+)\/apply\/?$/)
  if (applicationPreparationMatch) return <ApplicationPreparationPage jobId={Number(applicationPreparationMatch[1])} />
  const companyMatch = path.match(/^\/companies\/(\d+)\/?$/)
  if (companyMatch) return <DetailPage kind="company" id={Number(companyMatch[1])} />
  const jobMatch = path.match(/^\/jobs\/(\d+)\/?$/)
  if (jobMatch) return <DetailPage kind="job" id={Number(jobMatch[1])} />
  if (path === '/' || path === '') return <ApplicationsPage />
  return <DirectoryPage />
}

export default App
