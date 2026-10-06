import { useCallback, useEffect, useMemo, useState } from 'react'
import DetailPage from './DetailPage'
import LocationsPage from './LocationsPage'
import SiteHeader from './SiteHeader'

type Location = {
  label: string
  city?: string | null
  state?: string | null
  postcode?: string | null
  latitude: number
  longitude: number
  precision: string
  type?: string
}

type Company = {
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
type JobSort = 'relevance' | 'newest' | 'title' | 'company'
type JobWorkStyleFilter = 'all' | 'remote' | 'hybrid' | 'onsite'

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
  match_kind?: 'remote' | 'in_area'
}

type DiscoveryJob = {
  id: number; label: string; city: string | null; state?: string | null; postcode?: string | null
  latitude: number; longitude: number; status: string; radius_km: number; started_at: string | null
}

type LocationOption = {
  key: string
  group: 'Available locations' | 'Previously searched' | 'Current selection'
  label: string
  location: Location
  radius_km: number
}

const DEFAULT_LOCATION: Location = {
  label: 'Karlsruhe, Baden-Württemberg, Deutschland', city: 'Karlsruhe', state: 'Baden-Württemberg',
  latitude: 49.0068705, longitude: 8.4034195, precision: 'city_centroid', type: 'city',
}
const DEFAULT_LOCATION_OPTION: LocationOption = {
  key: 'default', group: 'Available locations', label: 'Karlsruhe',
  location: DEFAULT_LOCATION, radius_km: 15,
}
const PAGE_SIZE = 40
const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '')

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

function hostLabel(value: string | null) {
  if (!value) return 'Company website'
  try { return new URL(value).hostname.replace(/^www\./, '') }
  catch { return value }
}

function providerLabel(provider: string) {
  const labels: Record<string, string> = {
    greenhouse: 'Company hiring page', lever: 'Company hiring page', personio: 'Company hiring page', ashby: 'Company hiring page',
    schema_org: 'Company job page', html_jobs: 'Company job page',
  }
  return labels[provider] ?? 'Company job page'
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

function locationIdentity(latitude: number, longitude: number, radiusKm: number) {
  return `${latitude.toFixed(5)}:${longitude.toFixed(5)}:${radiusKm}`
}

function DirectoryPage() {
  const params = new URLSearchParams(window.location.search)
  const companyFilters: CompanyDiscoveryFilter[] = ['all', 'domain', 'career', 'feed', 'jobs']
  const requestedCompanyFilter = params.get('company_filter') as CompanyDiscoveryFilter | null
  const initialCompanyFilter = requestedCompanyFilter && companyFilters.includes(requestedCompanyFilter)
    ? requestedCompanyFilter : 'all'
  const initialLocation = params.has('lat') && params.has('lon')
    ? { ...DEFAULT_LOCATION, label: params.get('place') ?? DEFAULT_LOCATION.label,
        city: params.get('place')?.split(',')[0] ?? DEFAULT_LOCATION.city,
        latitude: Number(params.get('lat')), longitude: Number(params.get('lon')) }
    : DEFAULT_LOCATION
  const [location, setLocation] = useState<Location>(initialLocation)
  const [radius, setRadius] = useState(Number(params.get('radius_km') ?? 15))
  const [selectedLocationKey, setSelectedLocationKey] = useState(params.get('location_key') ?? 'default')
  const [locationOptions, setLocationOptions] = useState<LocationOption[]>([DEFAULT_LOCATION_OPTION])
  const [query, setQuery] = useState(params.get('q') ?? '')
  const [companyFilter, setCompanyFilter] = useState<CompanyDiscoveryFilter>(initialCompanyFilter)
  const [companySort, setCompanySort] = useState<CompanySort>(params.get('company_sort') === 'name' ? 'name' : 'distance')
  const [jobSort, setJobSort] = useState<JobSort>(['newest', 'title', 'company'].includes(params.get('job_sort') ?? '')
    ? params.get('job_sort') as JobSort : 'relevance')
  const [jobWorkStyle, setJobWorkStyle] = useState<JobWorkStyleFilter>(['remote', 'hybrid', 'onsite'].includes(params.get('job_work_style') ?? '')
    ? params.get('job_work_style') as JobWorkStyleFilter : 'all')
  const [tab, setTab] = useState<'companies' | 'jobs'>(params.get('view') === 'jobs' ? 'jobs' : 'companies')
  const [companies, setCompanies] = useState<Company[]>([])
  const [jobs, setJobs] = useState<Job[]>([])
  const [companyTotal, setCompanyTotal] = useState(0)
  const [jobTotal, setJobTotal] = useState(0)
  const [summary, setSummary] = useState({ companies_in_radius: 0, companies_with_domain: 0, jobs_for_location: 0, remote_jobs_in_result: 0 })
  const [offset, setOffset] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const locationParams = useMemo(() => new URLSearchParams({
    latitude: String(location.latitude), longitude: String(location.longitude), radius_km: String(radius),
    place: location.city ?? '',
  }), [location, radius])

  useEffect(() => {
    let cancelled = false
    async function loadLocationOptions() {
      try {
        const [locationResponse, searchesResponse] = await Promise.all([
          fetch(apiUrl('/api/v1/locations')),
          fetch(apiUrl('/api/v1/discovery-jobs?limit=50')),
        ])
        if (!locationResponse.ok || !searchesResponse.ok) throw new Error('Could not load available locations.')
        const [locationData, searchesData] = await Promise.all([
          locationResponse.json(), searchesResponse.json(),
        ])
        const available: LocationOption[] = locationData.items.map((item: {
          id: number; label: string; city: string | null; state: string | null; postcode: string | null
          latitude: number; longitude: number; radius_km: number
        }) => ({
          key: `saved:${item.id}`,
          group: 'Available locations',
          label: item.city || item.label.split(',')[0],
          location: { ...item, precision: 'configured_location', type: 'configured_location' },
          radius_km: item.radius_km,
        }))
        const searched: LocationOption[] = (searchesData.items as DiscoveryJob[])
          .filter(job => Boolean(job.started_at) || ['running', 'completed', 'partial', 'failed'].includes(job.status))
          .map(job => ({
            key: `searched:${job.id}`,
            group: 'Previously searched',
            label: job.city || job.label.split(',')[0],
            location: {
              label: job.label, city: job.city, state: job.state, postcode: job.postcode,
              latitude: job.latitude, longitude: job.longitude,
              precision: 'previously_searched', type: 'previously_searched',
            },
            radius_km: job.radius_km,
          }))
        const currentLocationOption: LocationOption = {
          key: 'current', group: 'Current selection',
          label: initialLocation.city || initialLocation.label.split(',')[0],
          location: initialLocation, radius_km: radius,
        }
        const defaultOption: LocationOption = {
          ...DEFAULT_LOCATION_OPTION,
        }
        const candidates = [
          ...available,
          ...searched,
          ...(params.has('lat') && params.has('lon') ? [currentLocationOption] : []),
          defaultOption,
        ]
        const seenLocations = new Set<string>()
        const options = candidates.filter(option => {
          const identity = locationIdentity(option.location.latitude, option.location.longitude, option.radius_km)
          if (seenLocations.has(identity)) return false
          seenLocations.add(identity)
          return true
        })
        if (cancelled) return
        setLocationOptions(options)
        const requestedKey = params.get('location_key')
        const selected = options.find(option => option.key === requestedKey)
          ?? options.find(option => locationIdentity(option.location.latitude, option.location.longitude, option.radius_km) ===
            locationIdentity(initialLocation.latitude, initialLocation.longitude, radius))
          ?? options.find(option => option.key === 'default')
          ?? options[0]
        if (selected) {
          setSelectedLocationKey(selected.key)
          if (!params.has('lat') || !params.has('lon') || selected.key === requestedKey) {
            setLocation(selected.location)
            if (!params.has('radius_km')) setRadius(selected.radius_km)
          }
        }
      } catch (exception) {
        if (!cancelled) setError(exception instanceof Error ? exception.message : 'Could not load available locations.')
      }
    }
    void loadLocationOptions()
    return () => { cancelled = true }
  }, [])

  const loadData = useCallback(async (signal: AbortSignal) => {
    setLoading(true)
    setError('')
    const companyParams = new URLSearchParams(locationParams)
    companyParams.set('offset', String(tab === 'companies' ? offset : 0))
    companyParams.set('limit', String(PAGE_SIZE))
    companyParams.set('sort', companySort)
    if (companyFilter !== 'all') companyParams.set('discovery', companyFilter)
    const jobParams = new URLSearchParams(locationParams)
    jobParams.set('offset', String(tab === 'jobs' ? offset : 0))
    jobParams.set('limit', String(PAGE_SIZE))
    jobParams.set('sort', jobSort)
    if (jobWorkStyle !== 'all') jobParams.set('work_style', jobWorkStyle)
    if (query.trim()) {
      companyParams.set('query', query.trim())
      jobParams.set('query', query.trim())
    }
    try {
      const [companyResponse, jobResponse, summaryResponse] = await Promise.all([
        fetch(apiUrl(`/api/v1/companies?${companyParams}`), { signal }),
        fetch(apiUrl(`/api/v1/jobs?${jobParams}`), { signal }),
        fetch(apiUrl(`/api/v1/summary?${locationParams}`), { signal }),
      ])
      for (const response of [companyResponse, jobResponse, summaryResponse]) {
        if (!response.ok) throw new Error(`Could not load company and job results (error ${response.status}).`)
      }
      const [companyPage, jobPage, summaryData] = await Promise.all([
        companyResponse.json(), jobResponse.json(), summaryResponse.json(),
      ])
      setCompanies(companyPage.items)
      setCompanyTotal(companyPage.total)
      setJobs(jobPage.items)
      setJobTotal(jobPage.total)
      setSummary(summaryData)
    } catch (exception) {
      if (!signal.aborted) setError(exception instanceof Error ? exception.message : 'Could not load company and job results.')
    } finally {
      if (!signal.aborted) setLoading(false)
    }
  }, [companyFilter, companySort, jobSort, jobWorkStyle, locationParams, offset, query, tab])

  useEffect(() => {
    const controller = new AbortController()
    void loadData(controller.signal)
    return () => controller.abort()
  }, [loadData])

  useEffect(() => {
    const url = new URL(window.location.href)
    url.searchParams.set('lat', String(location.latitude))
    url.searchParams.set('lon', String(location.longitude))
    url.searchParams.set('place', location.city ?? location.label.split(',')[0])
    url.searchParams.set('radius_km', String(radius))
    if (selectedLocationKey) url.searchParams.set('location_key', selectedLocationKey)
    else url.searchParams.delete('location_key')
    if (query.trim()) url.searchParams.set('q', query.trim())
    else url.searchParams.delete('q')
    if (companyFilter !== 'all') url.searchParams.set('company_filter', companyFilter)
    else url.searchParams.delete('company_filter')
    if (companySort !== 'distance') url.searchParams.set('company_sort', companySort)
    else url.searchParams.delete('company_sort')
    if (jobSort !== 'relevance') url.searchParams.set('job_sort', jobSort)
    else url.searchParams.delete('job_sort')
    if (jobWorkStyle !== 'all') url.searchParams.set('job_work_style', jobWorkStyle)
    else url.searchParams.delete('job_work_style')
    if (tab !== 'companies') url.searchParams.set('view', tab)
    else url.searchParams.delete('view')
    window.history.replaceState({}, '', url)
  }, [companyFilter, companySort, jobSort, jobWorkStyle, location, radius, query, selectedLocationKey, tab])

  function selectLocation(key: string) {
    const option = locationOptions.find(item => item.key === key)
    if (!option) return
    setSelectedLocationKey(key)
    setLocation(option.location)
    setRadius(option.radius_km)
    setOffset(0)
  }

  const activeTotal = tab === 'companies' ? companyTotal : jobTotal
  const pageNumber = Math.floor(offset / PAGE_SIZE) + 1
  const pageCount = Math.max(1, Math.ceil(activeTotal / PAGE_SIZE))
  const locality = location.city ?? location.label.split(',')[0]

  return (
    <div className="app-shell">
      <SiteHeader active="directory" />

      <main className="content">
        <section className="intro-row">
          <div>
            <p className="eyebrow">COMPANIES, JOBS, AND PLACES</p>
            <h1>Opportunity, <em>around you.</em></h1>
            <p className="intro-copy">Find nearby companies and see the open jobs listed on their own websites.</p>
          </div>
          <div className="snapshot-pill"><span>{radius}</span> KM SEARCH AREA</div>
        </section>

        <section className="search-card" aria-label="Choose a location">
          <div className="search-form">
            <label className="search-location">
              <span className="control-label">LOCATION</span>
              <span className="input-wrap location-select-wrap"><Icon name="pin" size={19} />
                <select aria-label="Available and searched locations" value={selectedLocationKey}
                  onChange={event => selectLocation(event.target.value)}>
                  {!locationOptions.some(option => option.key === selectedLocationKey) &&
                    <option value={selectedLocationKey}>{locality} · {radius} km</option>}
                  {(['Available locations', 'Previously searched', 'Current selection'] as const).map(group => {
                    const options = locationOptions.filter(option => option.group === group)
                    return options.length ? <optgroup key={group} label={group}>
                      {options.map(option => <option key={option.key} value={option.key}>
                        {option.label} · {option.key === selectedLocationKey ? radius : option.radius_km} km
                      </option>)}
                    </optgroup> : null
                  })}
                </select>
              </span>
            </label>
            <label className="radius-control">
              <span className="control-label">LISTING DISTANCE</span>
              <select aria-label="Listing distance around this place" value={radius} onChange={event => { setRadius(Number(event.target.value)); setOffset(0) }}>
                {[5, 10, 15, 25, 50, 100, 200].map(value => <option key={value} value={value}>{value} km</option>)}
              </select>
            </label>
            <a href="/locations" className="configure-locations-link">Configure locations <Icon name="arrow" size={15} /></a>
          </div>
          <div className="search-footnote"><span><Icon name="globe" size={14} /> Germany</span><span>Jobs can be local or remote</span></div>
        </section>

        {error && <div className="error-banner" role="alert">{error}<button type="button" onClick={() => setError('')}>Dismiss</button></div>}

        <section className="metrics-grid" aria-label="Search overview">
          <article className="metric-card metric-primary">
            <span className="metric-icon"><Icon name="building" size={19} /></span>
            <span className="metric-label">COMPANIES IN THIS AREA</span>
            <strong>{loading ? '—' : summary.companies_in_radius.toLocaleString('en')}</strong>
            <span className="metric-note">within {radius} km of {locality}</span>
          </article>
          <article className="metric-card">
            <span className="metric-icon metric-icon-green"><Icon name="globe" size={19} /></span>
            <span className="metric-label">WITH A WEBSITE</span>
            <strong>{loading ? '—' : summary.companies_with_domain.toLocaleString('en')}</strong>
            <span className="metric-note">company websites found nearby</span>
          </article>
          <article className="metric-card">
            <span className="metric-icon metric-icon-lilac"><Icon name="briefcase" size={19} /></span>
            <span className="metric-label">JOBS IN AREA</span>
            <strong>{loading ? '—' : summary.jobs_for_location.toLocaleString('en')}</strong>
            <span className="metric-note">local roles plus remote</span>
          </article>
          <article className="metric-card metric-remote">
            <span className="metric-icon metric-icon-sand"><Icon name="globe" size={19} /></span>
            <span className="metric-label">REMOTE ROLES</span>
            <strong>{loading ? '—' : summary.remote_jobs_in_result.toLocaleString('en')}</strong>
            <span className="metric-note">included in the jobs view</span>
          </article>
        </section>

        <section className="directory-card">
          <div className="directory-header">
            <div>
              <p className="eyebrow">YOUR RESULTS</p>
              <h2>What’s nearby</h2>
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
                  <select aria-label="Sort companies" value={companySort}
                    onChange={event => { setCompanySort(event.target.value as CompanySort); setOffset(0) }}>
                    <option value="distance">Closest first</option>
                    <option value="name">Name A–Z</option>
                  </select>
                </label>
              </> : <>
                <label className="discovery-filter"><span>Work style</span>
                  <select aria-label="Filter jobs by work style" value={jobWorkStyle}
                    onChange={event => { setJobWorkStyle(event.target.value as JobWorkStyleFilter); setOffset(0) }}>
                    <option value="all">All work styles</option>
                    <option value="remote">Remote</option>
                    <option value="hybrid">Hybrid</option>
                    <option value="onsite">On-site</option>
                  </select>
                </label>
                <label className="sorting-control"><span>Sort</span>
                  <select aria-label="Sort jobs" value={jobSort}
                    onChange={event => { setJobSort(event.target.value as JobSort); setOffset(0) }}>
                    <option value="relevance">Best match</option>
                    <option value="newest">Newest first</option>
                    <option value="title">Role A–Z</option>
                    <option value="company">Company A–Z</option>
                  </select>
                </label>
              </>}
              <div className="result-context"><span className="context-dot" /> {tab === 'jobs' ? `${locality} + remote` : `Company websites · ${radius} km`}</div>
            </div>
          </div>

          {loading ? <div className="loading-state"><span className="spinner" /> Gathering the latest results…</div> : tab === 'companies' ? (
            <div className="table-scroll">
              <table>
                <thead><tr><th>COMPANY</th><th>WEBSITE</th><th>HIRING PAGE</th><th>DISTANCE</th><th /></tr></thead>
                <tbody>
                  {companies.map(company => <tr key={company.id} onClick={() => { window.location.href = `/companies/${company.id}` }} className="click-row">
                    <td><div className="company-cell"><span className="company-monogram">{company.name.slice(0, 1).toUpperCase()}</span><span><strong>{company.name}</strong><small>{company.category?.replaceAll('_', ' ') || 'Business type not listed'}</small></span></div></td>
                    <td>{company.domain ? <span className="domain-cell"><a className="domain-link" href={company.website_url ?? '#'} target="_blank" rel="noreferrer" onClick={event => event.stopPropagation()}>{company.domain}<Icon name="external" size={12} /></a><small>{domainMatchLabel(company.domain_match_method)}</small></span> : <span className="muted">Website not found yet</span>}</td>
                    <td><span className={`status-badge ${['jobs_feed_found','jobs_extracted'].includes(company.career_status) ? 'status-success' : company.career_status === 'career_page_found' ? 'status-career' : 'status-muted'}`}><span />{statusLabel(company.career_status)}</span></td>
                    <td><span className="distance-text"><Icon name="pin" size={14} />{distanceLabel(company.distance_m)}</span></td>
                    <td className="row-arrow"><Icon name="chevron" size={17} /></td>
                  </tr>)}
                </tbody>
              </table>
              {companies.length === 0 && <EmptyState title="No companies listed in this area yet" body="Add this place on the search areas page to find nearby companies and their websites." />}
            </div>
          ) : (
            <div className="table-scroll">
              <table className="jobs-table">
                <thead><tr><th>JOB</th><th>COMPANY / JOB PAGE</th><th>WORK LOCATION</th><th>WORK STYLE</th><th>POSTED</th><th /></tr></thead>
                <tbody>
                  {jobs.map(job => <tr key={job.id} onClick={() => { window.location.href = `/jobs/${job.id}` }} className="click-row">
                    <td><div className="role-cell"><strong>{job.title}</strong><small>{job.department || providerLabel(job.provider)}</small></div></td>
                    <td><span className="job-company">{job.company_name}</span>
                      {job.company_website ? <a className="job-domain" href={job.company_website} target="_blank" rel="noreferrer" onClick={event => event.stopPropagation()}>{job.company_domain || hostLabel(job.company_website)}</a> : <small className="job-domain">Company website not listed</small>}
                      <small className="job-source">Job page: <a href={job.board_url || job.url} target="_blank" rel="noreferrer" onClick={event => event.stopPropagation()}>{hostLabel(job.board_url || job.url)}</a></small>
                    </td>
                    <td><span className="location-chip"><Icon name="pin" size={13} />{job.location_text || 'Location not listed'}</span></td>
                    <td><span className={`arrangement-chip ${job.is_remote ? 'arrangement-remote' : ''}`}>{job.is_remote ? 'Remote' : job.work_arrangement === 'hybrid' ? 'Hybrid' : job.work_arrangement === 'onsite' ? 'On-site' : 'In area'}</span></td>
                    <td><span className="posted-date">{dateLabel(job.date_posted)}</span></td>
                    <td className="row-arrow"><Icon name="chevron" size={17} /></td>
                  </tr>)}
                </tbody>
              </table>
              {jobs.length === 0 && <EmptyState title="No matching jobs found yet" body="This list includes jobs at the selected location and jobs marked remote." />}
            </div>
          )}

          <div className="table-footer">
            <span>Showing <strong>{activeTotal ? offset + 1 : 0}–{Math.min(offset + PAGE_SIZE, activeTotal)}</strong> of <strong>{activeTotal.toLocaleString('en')}</strong></span>
            <div className="pagination"><button type="button" disabled={offset === 0 || loading} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>Previous</button><span>{pageNumber} / {pageCount}</span><button type="button" disabled={offset + PAGE_SIZE >= activeTotal || loading} onClick={() => setOffset(offset + PAGE_SIZE)}>Next</button></div>
          </div>
        </section>

      <footer className="page-footer"><span>Nearby companies and websites are found from public listings; open jobs are checked on company hiring pages.</span><span>Map listings © OpenStreetMap contributors <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">ODbL</a></span></footer>
      </main>

    </div>
  )
}

function EmptyState({ title, body }: { title: string; body: string }) {
  return <div className="empty-state"><span className="empty-icon"><Icon name="search" size={20} /></span><strong>{title}</strong><p>{body}</p></div>
}

function App() {
  const path = window.location.pathname
  if (path === '/locations' || path === '/locations/') return <LocationsPage />
  const companyMatch = path.match(/^\/companies\/(\d+)\/?$/)
  if (companyMatch) return <DetailPage kind="company" id={Number(companyMatch[1])} />
  const jobMatch = path.match(/^\/jobs\/(\d+)\/?$/)
  if (jobMatch) return <DetailPage kind="job" id={Number(jobMatch[1])} />
  return <DirectoryPage />
}

export default App
