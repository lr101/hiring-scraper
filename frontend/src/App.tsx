import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import DetailPage from './DetailPage'
import FeedSourcesPage from './FeedSourcesPage'
import LocationsPage from './LocationsPage'
import SiteHeader from './SiteHeader'
import ProfilesPage from './ProfilesPage'
import ApplicationsPage from './ApplicationsPage'
import SettingsPage from './SettingsPage'
import JobOverviewTags from './JobOverviewTags'
import ApplicationSwipeCard, { ApplicationStateBadge } from './ApplicationSwipeCard'
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
type JobSort = 'match' | 'posted' | 'discovered'
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
  match_kind?: 'remote' | 'in_area' | 'employer_in_state' | 'employer_in_country'
}

type JobCounts = { source: number; scoped: number; duplicates_removed: number; recommended: number; possible: number; unlikely?: number; filtered: Record<string, number> }
type JobCoverage = { source_count: number; note: string }
type JobsPage = { items: Job[]; total: number; counts?: JobCounts; coverage?: JobCoverage }

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
const JOB_REQUEST_SIZE = 100
const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '')
const jobSortModes: { value: JobSort; label: string }[] = [
  { value: 'match', label: 'Best match' },
  { value: 'posted', label: 'Posted date' },
  { value: 'discovered', label: 'Discovered' },
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

function dateLabel(value: string | null | undefined) {
  if (!value) return 'Date not listed'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('en', { day: 'numeric', month: 'short', year: 'numeric' }).format(date)
}

function dateRank(value?: string | null) {
  const timestamp = value ? Date.parse(value) : Number.NaN
  return Number.isFinite(timestamp) ? timestamp : Number.NEGATIVE_INFINITY
}

async function fetchAllJobs(query: URLSearchParams, signal: AbortSignal): Promise<JobsPage> {
  const items: Job[] = []
  let total = Number.POSITIVE_INFINITY
  let counts: JobCounts | undefined
  let coverage: JobCoverage | undefined
  while (items.length < total) {
    const pageQuery = new URLSearchParams(query)
    pageQuery.set('offset', String(items.length))
    pageQuery.set('limit', String(JOB_REQUEST_SIZE))
    const response = await fetch(apiUrl(`/api/v1/jobs?${pageQuery}`), { signal })
    const page = await response.json() as JobsPage & { detail?: unknown }
    if (!response.ok) throw new Error(typeof page.detail === 'string' ? page.detail : 'Could not load job results.')
    items.push(...page.items)
    total = page.total
    counts = page.counts ?? counts
    coverage = page.coverage ?? coverage
    if (!page.items.length) break
  }
  return { items, total, counts, coverage }
}

function compareJobRows(sort: JobSort, a: Job, b: Job) {
  const scoreDifference = (b.profile_match?.score ?? -1) - (a.profile_match?.score ?? -1)
  const discoveredDifference = dateRank(b.first_seen_at) - dateRank(a.first_seen_at)
  const postedDifference = dateRank(b.date_posted) - dateRank(a.date_posted)
  if (sort === 'posted') return postedDifference || scoreDifference || discoveredDifference
  if (sort === 'discovered') return discoveredDifference || scoreDifference || postedDifference
  return scoreDifference || discoveredDifference || postedDifference
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
  const [companyFilter, setCompanyFilter] = useState<CompanyDiscoveryFilter>(initialCompanyFilter)
  const [companySort, setCompanySort] = useState<CompanySort>(params.get('company_sort') === 'name' ? 'name' : 'distance')
  const requestedJobSort = params.get('job_sort')
  const [jobSort, setJobSort] = useState<JobSort>(requestedJobSort === 'posted' || requestedJobSort === 'discovered'
    ? requestedJobSort : requestedJobSort === 'newest' ? 'posted' : 'match')
  const requestedLocationScope = params.get('location_scope')
  const [jobLocationScope, setJobLocationScope] = useState<JobLocationScope>(['area', 'remote', 'area_remote'].includes(requestedLocationScope ?? '')
    ? requestedLocationScope as JobLocationScope : params.get('job_work_style') === 'remote' ? 'remote' : (params.has('state') || params.has('country')) ? 'area_remote' : 'area')
  const [jobWorkStyle, setJobWorkStyle] = useState<JobWorkStyleFilter>(['hybrid', 'onsite'].includes(params.get('job_work_style') ?? '')
    ? params.get('job_work_style') as JobWorkStyleFilter : 'all')
  const [profiles, setProfiles] = useState<Profile[]>([])
  const [profilesReady, setProfilesReady] = useState(false)
  const requestedProfileIds = params.get('profile_ids')
  const [activeProfileIds, setActiveProfileIds] = useState<number[]>(() => {
    const requested = requestedProfileIds !== null ? requestedProfileIds.split(',')
      : params.has('profile_id') ? [params.get('profile_id') ?? ''] : []
    return [...new Set(requested.map(Number).filter(value => Number.isSafeInteger(value) && value > 0))]
  })
  const profileId = String(activeProfileIds[0] ?? '')
  const activeProfileKey = activeProfileIds.join(',')
  const allProfilesActive = profiles.length > 0 && activeProfileIds.length === profiles.length
  const [coverage, setCoverage] = useState<JobCoverage | null>(null)
  const [counts, setCounts] = useState<JobCounts | null>(null)
  const [minimumScore, setMinimumScore] = useState(params.get('min_match_score') ?? '0')
  const [includeUnknown, setIncludeUnknown] = useState(params.get('include_unknown') !== 'false')
  const [jobStatusFilter, setJobStatusFilter] = useState<ApplicationStatus | ''>(() =>
    applicationStatuses.find(item => item.value === params.get('job_status'))?.value ?? '')
  const [tab, setTab] = useState<'companies' | 'jobs'>(params.get('view') === 'jobs' ? 'jobs' : 'companies')
  const [companies, setCompanies] = useState<Company[]>([])
  const [jobs, setJobs] = useState<Job[]>([])
  const [companyTotal, setCompanyTotal] = useState(0)
  const [jobTotal, setJobTotal] = useState(0)
  const [summary, setSummary] = useState({ companies_in_radius: 0, companies_with_domain: 0, jobs_for_location: 0, remote_jobs_in_result: 0 })
  const [offset, setOffset] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [trackingError, setTrackingError] = useState('')
  const [trackingNotice, setTrackingNotice] = useState('')
  const [swipeSavingJobId, setSwipeSavingJobId] = useState<number | null>(null)
  const swipeRequestToken = useRef(0)
  const profileSelectionToken = useRef(0)
  const requestOffset = tab === 'companies' ? offset : 0

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
        ? [...new Set(requested.filter((id: number) => Number.isSafeInteger(id) && id > 0 && validIds.has(id)))]
        : !(params.has('state') || params.has('country')) ? allProfileIds : []
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
    if (!profilesReady || !locationsReady) return
    setError('')
    if (!selectedLocationKey) {
      setCompanies([]); setCompanyTotal(0); setJobs([]); setJobTotal(0)
      setSummary({ companies_in_radius: 0, companies_with_domain: 0, jobs_for_location: 0, remote_jobs_in_result: 0 })
      setCounts(null); setCoverage(null); setLoading(false)
      return
    }
    setLoading(true)
    const companyParams = new URLSearchParams(locationParams)
    companyParams.set('offset', String(requestOffset))
    companyParams.set('limit', String(PAGE_SIZE))
    companyParams.set('sort', location.search_scope === 'country' || location.search_scope === 'state' ? 'name' : companySort)
    if (companyFilter !== 'all') companyParams.set('discovery', companyFilter)
    const jobParams = new URLSearchParams(locationParams)
    jobParams.set('sort', 'relevance')
    jobParams.set('location_scope', jobLocationScope)
    if (jobWorkStyle !== 'all') jobParams.set('work_style', jobWorkStyle)
    if (query.trim()) {
      companyParams.set('query', query.trim())
      jobParams.set('query', query.trim())
    }
    const selectedProfiles = profiles.filter(profile => activeProfileIds.includes(profile.id!))
    const profileQueries = selectedProfiles.length ? selectedProfiles.map(profile => {
      const profileQuery = new URLSearchParams(jobParams)
      profileQuery.set('profile_id', String(profile.id))
      profileQuery.set('min_match_score', minimumScore)
      profileQuery.set('include_unknown', String(includeUnknown))
      return { profile, query: profileQuery }
    }) : [{ profile: null, query: jobParams }]
    try {
      const [companyResponse, profilePages, summaryResponse] = await Promise.all([
        fetch(apiUrl(`/api/v1/companies?${companyParams}`), { signal }),
        Promise.all(profileQueries.map(async ({ profile, query: profileQuery }) => {
          if (tab === 'jobs') {
            const page = await fetchAllJobs(profileQuery, signal)
            return { ...page, items: page.items.map(job => profile ? { ...job, profile_id: profile.id, profile_name: profile.name } : job) }
          }
          profileQuery.set('limit', '1')
          profileQuery.set('offset', '0')
          const response = await fetch(apiUrl(`/api/v1/jobs?${profileQuery}`), { signal })
          const page = await response.json() as JobsPage & { detail?: unknown }
          if (!response.ok) throw new Error(typeof page.detail === 'string' ? page.detail : 'Could not load job results.')
          return { ...page, items: page.items.map(job => profile ? { ...job, profile_id: profile.id, profile_name: profile.name } : job) }
        })),
        fetch(apiUrl(`/api/v1/summary?${locationParams}`), { signal }),
      ])
      if (!companyResponse.ok || !summaryResponse.ok) throw new Error(`Could not load company and job results (error ${!companyResponse.ok ? companyResponse.status : summaryResponse.status}).`)
      const [companyPage, summaryData] = await Promise.all([companyResponse.json(), summaryResponse.json()])
      const jobPage = {
        items: profilePages.flatMap(page => page.items),
        total: profilePages.reduce((total, page) => total + page.total, 0),
        counts: profilePages.some(page => page.counts) ? profilePages.reduce<JobCounts>((total, page) => {
          const next = page.counts
          if (!next) return total
          total.source += next.source
          total.scoped += next.scoped
          total.duplicates_removed += next.duplicates_removed
          total.recommended += next.recommended
          total.possible += next.possible
          total.unlikely = (total.unlikely ?? 0) + (next.unlikely ?? 0)
          for (const [key, value] of Object.entries(next.filtered)) total.filtered[key] = (total.filtered[key] ?? 0) + value
          return total
        }, { source: 0, scoped: 0, duplicates_removed: 0, recommended: 0, possible: 0, unlikely: 0, filtered: {} }) : null,
        coverage: profilePages.find(page => page.coverage)?.coverage ?? null,
      }
      setCompanies(companyPage.items)
      setCompanyTotal(companyPage.total)
      setJobs(jobPage.items)
      setJobTotal(jobPage.total)
      setSummary(summaryData)
      setCounts(jobPage.counts); setCoverage(jobPage.coverage)
    } catch (exception) {
      if (!signal.aborted) setError(exception instanceof Error ? exception.message : 'Could not load company and job results.')
    } finally {
      if (!signal.aborted) setLoading(false)
    }
  }, [companyFilter, companySort, jobLocationScope, jobWorkStyle, locationParams, requestOffset, query, tab, activeProfileKey, activeProfileIds, profiles, minimumScore, includeUnknown, profilesReady, locationsReady, selectedLocationKey])

  useEffect(() => {
    const controller = new AbortController()
    void loadData(controller.signal)
    return () => controller.abort()
  }, [loadData])

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
    if (jobSort !== 'match') url.searchParams.set('job_sort', jobSort)
    else url.searchParams.delete('job_sort')
    if (jobWorkStyle !== 'all') url.searchParams.set('job_work_style', jobWorkStyle)
    else url.searchParams.delete('job_work_style')
    if (jobLocationScope !== 'area') url.searchParams.set('location_scope', jobLocationScope)
    else url.searchParams.delete('location_scope')
    if (tab !== 'companies') url.searchParams.set('view', tab)
    else url.searchParams.delete('view')
    if (activeProfileIds.length) {
      url.searchParams.set('profile_ids', activeProfileKey)
      url.searchParams.set('profile_id', profileId)
      url.searchParams.set('min_match_score', minimumScore)
      url.searchParams.set('include_unknown', String(includeUnknown))
    } else {
      url.searchParams.delete('profile_id'); url.searchParams.delete('profile_ids')
      url.searchParams.delete('min_match_score'); url.searchParams.delete('include_unknown')
    }
    if (jobStatusFilter) url.searchParams.set('job_status', jobStatusFilter)
    else url.searchParams.delete('job_status')
    window.history.replaceState({}, '', url)
  }, [companyFilter, companySort, jobSort, jobLocationScope, jobWorkStyle, location, locationParams, radius, query, selectedLocationKey, tab, profileId, activeProfileIds, activeProfileKey, minimumScore, includeUnknown, jobStatusFilter, profilesReady, locationsReady])

  function applyProfile(profile: Profile, overrides = new URLSearchParams()) {
    if (profile.id == null) return
    const defaults = profileBoardDefaults(profile, overrides)
    swipeRequestToken.current += 1
    profileSelectionToken.current += 1
    setSwipeSavingJobId(null)
    setActiveProfileIds([profile.id]); setMinimumScore(defaults.minimumScore); setIncludeUnknown(defaults.includeUnknown)
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
  function profileScopeOverrides() {
    return location.search_scope === 'country' ? new URLSearchParams({ country: 'DE' })
      : location.search_scope === 'state' && location.state ? new URLSearchParams({ state: location.state })
        : new URLSearchParams()
  }
  function invalidateProfileRequests() {
    swipeRequestToken.current += 1
    profileSelectionToken.current += 1
    setSwipeSavingJobId(null)
    setJobs([])
  }
  function syncProfileLocation(nextIds: number[]) {
    if (!selectedLocationKey.startsWith('profile:')) return
    const locationOwner = Number(selectedLocationKey.slice('profile:'.length))
    if (nextIds.includes(locationOwner)) return
    const nextProfile = profiles.find(profile => profile.id === nextIds[0])
    const area = nextProfile?.search_area
    if (nextProfile && area?.latitude != null && area.longitude != null) {
      setSelectedLocationKey(`profile:${nextProfile.id}`)
      setLocation({ ...area, latitude: area.latitude, longitude: area.longitude, precision: 'profile_search_area' })
      setRadius(area.radius_km)
      return
    }
    const configured = locationOptions[0]
    setSelectedLocationKey(configured?.key ?? '')
    setLocation(configured?.location ?? NO_LOCATION)
    if (configured) setRadius(configured.radius_km)
  }
  function clearProfileFilters() {
    invalidateProfileRequests()
    setActiveProfileIds([])
    setJobStatusFilter('')
    syncProfileLocation([])
    setOffset(0)
  }
  function selectAllProfiles() {
    if (!profiles.length) return
    const selected = profiles.find(profile => profile.id === activeProfileIds[0]) ?? profiles[0]
    if (!activeProfileIds.length) applyProfile(selected, profileScopeOverrides())
    else invalidateProfileRequests()
    setActiveProfileIds(profiles.flatMap(profile => profile.id == null ? [] : [profile.id]))
    setJobStatusFilter('')
    setOffset(0)
  }
  function toggleProfileFilter(id: number) {
    const nextIds = activeProfileIds.includes(id)
      ? activeProfileIds.filter(profileId => profileId !== id)
      : [...activeProfileIds, id].sort((a, b) => profiles.findIndex(profile => profile.id === a) - profiles.findIndex(profile => profile.id === b))
    const profile = profiles.find(item => item.id === id)
    if (!activeProfileIds.length && profile) applyProfile(profile, profileScopeOverrides())
    else invalidateProfileRequests()
    setActiveProfileIds(nextIds)
    setJobStatusFilter('')
    syncProfileLocation(nextIds)
    setOffset(0)
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
      const saved = await saveApplication(API_BASE_URL, trackingProfileId, job.id, action.status)
      if (profileSelectionToken.current !== profileToken) return
      setJobs(current => current.map(item => item.id === job.id && item.profile_id === trackingProfileId ? { ...item, application: saved } : item))
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

  const jobApplicationCounts = useMemo(() => {
    const result = Object.fromEntries(applicationStatuses.map(item => [item.value, 0])) as Record<ApplicationStatus, number>
    for (const job of jobs) if (job.application) result[job.application.status] += 1
    return result
  }, [jobs])
  const filteredJobs = useMemo(() => jobs
    .filter(job => !jobStatusFilter || job.application?.status === jobStatusFilter)
    .sort((a, b) => compareJobRows(jobSort, a, b)), [jobs, jobSort, jobStatusFilter])
  const visibleJobs = filteredJobs.slice(offset, offset + PAGE_SIZE)
  const activeTotal = tab === 'companies' ? companyTotal : filteredJobs.length
  const pageNumber = Math.floor(offset / PAGE_SIZE) + 1
  const pageCount = Math.max(1, Math.ceil(activeTotal / PAGE_SIZE))
  const wholeState = Boolean(selectedLocationKey) && (location.search_scope === 'state' || location.search_scope === 'country')
  const locality = selectedLocationKey ? location.city ?? location.label.split(',')[0] : 'a configured location'

  return (
    <div className="app-shell">
      <SiteHeader active="directory" />

      <main className="content">
        <section className="intro-row">
          <div>
            <p className="eyebrow">COMPANIES, JOBS, AND PLACES</p>
            <h1>Opportunity, <em>around you.</em></h1>
            <p className="intro-copy">Find nearby opportunities from employer hiring pages and selected public job listings.</p>
          </div>
          <div className="snapshot-pill">{selectedLocationKey ? <><span>{radius}</span> KM SEARCH AREA</> : 'LOCATION REQUIRED'}</div>
        </section>

        <section className="search-card" aria-label="Choose a location">
          <div className="search-form">
            <label className="search-location">
              <span className="control-label">LOCATION</span>
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
              <span className="control-label">LISTING DISTANCE</span>
              <select aria-label="Listing distance around this place" value={radius} onChange={event => { setRadius(Number(event.target.value)); setOffset(0) }}>
                {[...new Set([5, 10, 15, 25, 35, 50, 100, 200, radius])].sort((a,b) => a-b).map(value => <option key={value} value={value}>{value} km</option>)}
              </select>
            </label>}
            <a href="/locations" className="configure-locations-link">Configure locations <Icon name="arrow" size={15} /></a>
          </div>
          <div className="search-footnote"><span><Icon name="globe" size={14} />{selectedLocationKey ? wholeState ? 'Germany' : locality : 'No location selected'}</span><span>{selectedLocationKey ? wholeState ? 'All work locations from employers discovered in this search area; public sources may be incomplete.' : 'Jobs can be local or remote' : 'Configure a location to see nearby employers and jobs.'}</span></div>
        </section>

        {error && <div className="error-banner" role="alert">{error}<button type="button" onClick={() => setError('')}>Dismiss</button></div>}

        {tab === 'companies' && selectedLocationKey && <section className="metrics-grid" aria-label="Search overview">
          <article className="metric-card metric-primary">
            <span className="metric-icon"><Icon name="building" size={19} /></span>
            <span className="metric-label">COMPANIES IN THIS AREA</span>
            <strong>{loading ? '—' : summary.companies_in_radius.toLocaleString('en')}</strong>
            <span className="metric-note">{wholeState ? `Across ${locality}` : `within ${radius} km of ${locality}`}</span>
          </article>
          <article className="metric-card">
            <span className="metric-icon metric-icon-green"><Icon name="globe" size={19} /></span>
            <span className="metric-label">WITH A WEBSITE</span>
            <strong>{loading ? '—' : summary.companies_with_domain.toLocaleString('en')}</strong>
            <span className="metric-note">{wholeState ? 'company websites found in this search area' : 'company websites found nearby'}</span>
          </article>
          <article className="metric-card">
            <span className="metric-icon metric-icon-lilac"><Icon name="briefcase" size={19} /></span>
            <span className="metric-label">{wholeState ? 'JOBS AT AREA EMPLOYERS' : 'JOBS IN AREA'}</span>
            <strong>{loading ? '—' : summary.jobs_for_location.toLocaleString('en')}</strong>
            <span className="metric-note">{wholeState ? 'all work locations, including remote' : `roles within ${radius} km`}</span>
          </article>
          <article className="metric-card metric-remote">
            <span className="metric-icon metric-icon-sand"><Icon name="globe" size={19} /></span>
            <span className="metric-label">REMOTE ROLES</span>
            <strong>{loading ? '—' : summary.remote_jobs_in_result.toLocaleString('en')}</strong>
            <span className="metric-note">select Remote in the job location filter</span>
          </article>
        </section>}

        <section className="directory-card">
          <div className="directory-header">
            <div>
              <p className="eyebrow">YOUR RESULTS</p>
              <h2>{tab === 'jobs' ? profileId ? 'Best matches near you' : 'Jobs around you' : 'What’s nearby'}</h2>
            </div>
            <label className="table-search"><Icon name="search" size={17} />
              <input aria-label="Filter results" value={query} onChange={event => { setQuery(event.target.value); setOffset(0) }} placeholder="Search name or role" />
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
                <label className="sorting-control"><span>Sort</span>
                  <select aria-label="Sort companies" value={wholeState ? 'name' : companySort}
                    onChange={event => { setCompanySort(event.target.value as CompanySort); setOffset(0) }}>
                    {!wholeState && <option value="distance">Closest first</option>}
                    <option value="name">Name A–Z</option>
                  </select>
                </label>
              </> : null}
              <div className="result-context"><span className="context-dot" /> {wholeState ? `${locality} · employer sources` : tab === 'jobs' ? jobLocationScope === 'remote' ? 'Remote roles only' : jobLocationScope === 'area_remote' ? `${locality} + remote` : `${locality} · ${radius} km` : `Company websites · ${radius} km`}</div>
            </div>
          </div>

          {tab === 'jobs' && <section className="job-feed-filters" aria-label="Filter job results">
            {profiles.length > 0 && <div className="job-feed-filter-row" role="group" aria-label="Filter by profile">
              <span className="job-feed-filter-label">Profiles</span><div className="job-feed-chip-list">
                <button type="button" className={`application-filter-chip ${activeProfileIds.length === 0 ? 'selected' : ''}`}
                  aria-pressed={activeProfileIds.length === 0} onClick={clearProfileFilters}>All jobs</button>
                <button type="button" className={`application-filter-chip ${allProfilesActive ? 'selected' : ''}`}
                  aria-pressed={allProfilesActive} onClick={selectAllProfiles}>All profiles</button>
                {profiles.map(profile => <button type="button" key={profile.id}
                  className={`application-filter-chip ${activeProfileIds.includes(profile.id!) ? 'selected' : ''}`}
                  aria-pressed={activeProfileIds.includes(profile.id!)} onClick={() => toggleProfileFilter(profile.id!)}>{profile.name}</button>)}
              </div>
            </div>}
            {activeProfileIds.length > 0 && <div className="job-feed-filter-row" role="group" aria-label="Filter by application stage">
              <span className="job-feed-filter-label">Stage</span><div className="job-feed-chip-list">
                <button type="button" className={`application-filter-chip ${jobStatusFilter === '' ? 'selected' : ''}`}
                  aria-pressed={jobStatusFilter === ''} onClick={() => { setJobStatusFilter(''); setOffset(0) }}>All <span>{jobs.length}</span></button>
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
                  ['area', wholeState ? 'Local employers' : 'Area'],
                  ['remote', 'Remote'],
                  ['area_remote', wholeState ? 'All locations' : 'Area + remote'],
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
                {activeProfileIds.length > 0 && <>
                  <span className="job-feed-filter-label job-feed-inline-label">Overlap</span>
                  {(['0', '30', '65'] as const).map(value => <button type="button" key={value}
                    className={`application-filter-chip ${minimumScore === value ? 'selected' : ''}`}
                    aria-pressed={minimumScore === value} onClick={() => { setMinimumScore(value); setOffset(0) }}>
                    {value === '0' ? 'Any match' : value === '30' ? 'Some overlap' : 'Strong overlap'}
                  </button>)}
                  {!['0', '30', '65'].includes(minimumScore) && <button type="button" className="application-filter-chip selected"
                    aria-pressed="true" onClick={() => { setMinimumScore('0'); setOffset(0) }}>{minimumScore}+ overlap</button>}
                  <button type="button" className={`application-filter-chip ${includeUnknown ? 'selected' : ''}`}
                    aria-pressed={includeUnknown} onClick={() => { setIncludeUnknown(value => !value); setOffset(0) }}>Include unknown</button>
                </>}
                <button type="button" className="application-sort-cycle job-feed-sort" onClick={cycleJobSort}
                  aria-label={`Sort by ${jobSortModes.find(item => item.value === jobSort)?.label}. Tap to switch to ${jobSortModes[(jobSortModes.findIndex(item => item.value === jobSort) + 1) % jobSortModes.length].label}.`}
                  title="Tap to change sort order">Sort: {jobSortModes.find(item => item.value === jobSort)?.label} <span aria-hidden="true">↻</span></button>
                {activeProfileIds.length > 0 && <a className="job-feed-inline-link" href="/profile">Edit profiles</a>}
              </div>
            </div>
          </section>}

          {trackingError && <div className="tracking-error-note" role="status">{trackingError}<button type="button" onClick={() => setTrackingError('')}>Dismiss</button></div>}
          {trackingNotice && <p className="evidence-note" role="status">{trackingNotice}</p>}
          {tab === 'jobs' && activeProfileIds.length > 0 && <div className="board-coverage" aria-live="polite">
            <div className="board-tiers"><strong>{counts?.recommended ?? '—'} recommended matches</strong><span>{counts?.possible ?? '—'} possible leads</span><span>{counts?.unlikely ?? '—'} with little match evidence</span></div>
            <details className="coverage-more">
              <summary>How matches work</summary>
              <div>
                <p>Scores measure overlap with selected profiles, never a hiring probability. Recommended matches have stronger evidence; possible leads may need a closer look at qualifications, location, or missing details.</p>
                {counts && <p>{counts.source} source records · {counts.scoped} in scope · {counts.duplicates_removed} duplicates removed · {counts.filtered.below_score} below your overlap filter · {counts.filtered.expired} expired · {counts.filtered.remote_country} incompatible remote country</p>}
                {coverage && <p>{coverage.source_count} observed job feeds. {coverage.note}</p>}
                <button type="button" className="broaden-button" onClick={() => { setMinimumScore('0'); setIncludeUnknown(true); setOffset(0) }}>Broaden to all eligible leads</button>
              </div>
            </details>
          </div>}
          {locationsReady && !selectedLocationKey ? <div className="location-empty-state">
            <EmptyState title="No configured locations yet" body="Add a search area to see nearby employers and jobs." />
            <a href="/locations" className="page-link">Configure locations <Icon name="arrow" size={15} /></a>
          </div> : loading ? <div className="loading-state"><span className="spinner" /> Gathering the latest results…</div> : tab === 'companies' ? (
            <div className="table-scroll mobile-results-scroll">
              <table className="companies-table mobile-results-table">
                <thead><tr><th>COMPANY</th><th>WEBSITE</th><th>HIRING PAGE</th><th>{wholeState ? 'STATE' : 'DISTANCE'}</th><th /></tr></thead>
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
                const previousStatus = job.application?.previous_status && job.application.previous_status !== job.application.status
                  ? job.application.previous_status : null
                const cardStatus = job.profile_id ? job.application?.status ?? 'new' : null
                const jobHref = new URLSearchParams(window.location.search)
                if (job.profile_id) jobHref.set('profile_id', String(job.profile_id))
                return <ApplicationSwipeCard key={`${job.profile_id ?? 'all'}:${job.id}`} application={job.application} disabled={!job.profile_id || swipeSavingJobId !== null}
                  className={`job-opportunity-card ${cardStatus ? `status-${cardStatus}` : ''}`}
                  onAction={action => { void changeJobApplication(job, action) }}>
                <div className="job-opportunity-main">
                  <div className="job-opportunity-heading">
                    <div className="job-opportunity-identity">
                      <a className="job-opportunity-title" href={`/jobs/${job.id}?${jobHref}`}>{job.title}</a>
                      <p className="job-opportunity-company">{job.company_name}{job.department ? ` · ${job.department}` : ''}</p>
                      {job.profile_name && <small className="job-profile-context">Matched for {job.profile_name}</small>}
                    </div>
                    <div className="job-opportunity-card-badges">
                      {job.profile_id && <ApplicationStateBadge application={job.application} fallbackStatus="new" />}
                      {job.profile_id && job.profile_match && <div className="job-match-score" aria-label={`Profile match ${job.profile_match.score} out of 100`}>
                        <strong>{job.profile_match.score}</strong><span>match</span>
                      </div>}
                    </div>
                  </div>
                  <div className="job-opportunity-facts">
                    <span><Icon name="pin" size={14} />{job.location_text || 'Location not listed'}</span>
                    <span>Posted {dateLabel(job.date_posted)}</span>
                  </div>
                  <JobOverviewTags job={job} />
                  {job.profile_id && job.profile_match && <div className="job-opportunity-match-details">
                    <span className={`fit-tier fit-${job.profile_match.fit_tier ?? 'possible'}`}>{job.profile_match.fit_tier === 'recommended' ? 'Strong match' : job.profile_match.fit_tier === 'unlikely' ? 'Limited overlap' : 'Possible match'}</span>
                    <span>{job.profile_match.matched_skills.slice(0, 3).join(' · ') || 'No matching skills found yet'}</span>
                    {job.profile_match.unknowns.slice(0, 1).map(item => <span className="job-match-unknown" key={item}>Check: {item}</span>)}
                  </div>}
                  {previousStatus && <footer className="application-card-footer job-opportunity-card-footer">
                    <button type="button" data-swipe-undo className="application-undo-button application-card-undo"
                      disabled={swipeSavingJobId !== null} onClick={() => void changeJobApplication(job, { kind: 'undo', status: previousStatus })}>
                      {swipeSavingJobId === job.id ? 'Undoing…' : `↶ Undo to ${applicationStatusLabel(previousStatus)}`}
                    </button>
                    <span className="application-undo-hint">Swipe this control to undo</span>
                  </footer>}
                </div>
                <div className="job-opportunity-actions">
                  <a className="job-original-link" href={job.url} target="_blank" rel="noreferrer"
                    onClick={() => { void trackOpenedPost(job) }}>Open original post <Icon name="external" size={14} /></a>
                </div>
              </ApplicationSwipeCard>})}
              {filteredJobs.length === 0 && <EmptyState title={jobStatusFilter ? 'No jobs in this stage' : 'No matching jobs found yet'} body={jobStatusFilter ? 'Choose another stage or view all matching jobs.' : jobLocationScope === 'remote' ? 'Try adjusting your profile or search terms for remote roles.' : jobLocationScope === 'area_remote' ? 'Try adjusting your profile or search terms for roles in the selected area or remote roles.' : 'Try adjusting your profile or search terms for roles in the selected area.'} />}
            </div>
          )}

          <div className="table-footer">
            <span>Showing <strong>{activeTotal ? offset + 1 : 0}–{Math.min(offset + PAGE_SIZE, activeTotal)}</strong> of <strong>{activeTotal.toLocaleString('en')}</strong></span>
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
  if (path === '/settings' || path === '/settings/') return <SettingsPage />
  if (path === '/locations' || path === '/locations/') return <LocationsPage />
  if (path === '/feeds' || path === '/feeds/') return <FeedSourcesPage />
  const companyMatch = path.match(/^\/companies\/(\d+)\/?$/)
  if (companyMatch) return <DetailPage kind="company" id={Number(companyMatch[1])} />
  const jobMatch = path.match(/^\/jobs\/(\d+)\/?$/)
  if (jobMatch) return <DetailPage kind="job" id={Number(jobMatch[1])} />
  return <DirectoryPage />
}

export default App
