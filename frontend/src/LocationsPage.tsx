import { useCallback, useEffect, useState, type FormEvent } from 'react'
import SiteHeader from './SiteHeader'
import { elapsedLabel, hasWaitedTooLong } from './progress'

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '')
const apiUrl = (path: string) => `${API_BASE_URL}${path}`

type ResolvedLocation = {
  label: string; city?: string | null; state?: string | null; postcode?: string | null
  latitude: number; longitude: number; precision?: string; type?: string
}
type ConfiguredLocation = ResolvedLocation & {
  id: number; radius_km: number; interval_days: number; enabled: boolean; next_run_at: string | null
}
type DiscoveryJob = {
  id: number; label: string; city: string | null; status: string; kind: string; stage: string
  radius_km: number; scheduled_for: string | null; created_at: string; started_at: string | null
  progress_message: string | null; progress_updated_at: string | null
  candidate_total: number; processed_count: number; succeeded_count: number
  failed_count: number; jobs_found: number; progress_percent: number
  companies_found: number; homepages_found: number; error?: string | null
}

function runStatus(status: string) {
  const labels: Record<string, string> = {
    scheduled: 'Scheduled', queued: 'Waiting', running: 'In progress',
    completed: 'Finished', partial: 'Finished with some issues', failed: 'Could not finish',
    cancelled: 'Cancelled',
  }
  return labels[status] ?? 'In progress'
}

function runStep(stage: string) {
  return stage === 'company_homepage_discovery' ? 'Finding companies and websites'
    : stage === 'career_page_discovery' ? 'Checking company websites for jobs'
      : 'Finished'
}

function dateTime(value: string | null) {
  if (!value) return 'Not scheduled'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat('en', {
    dateStyle: 'medium', timeStyle: 'short',
  }).format(date)
}

function errorMessage(payload: { detail?: string }, fallback: string) {
  return payload.detail || fallback
}

export default function LocationsPage() {
  const [locations, setLocations] = useState<ConfiguredLocation[]>([])
  const [jobs, setJobs] = useState<DiscoveryJob[]>([])
  const [query, setQuery] = useState('')
  const [radius, setRadius] = useState(15)
  const [intervalDays, setIntervalDays] = useState(7)
  const [choices, setChoices] = useState<ResolvedLocation[]>([])
  const [selectedIndex, setSelectedIndex] = useState(0)
  const [loading, setLoading] = useState(true)
  const [working, setWorking] = useState(false)
  const [cancellingJobId, setCancellingJobId] = useState<number | null>(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  const refresh = useCallback(async () => {
    const [locationResponse, jobResponse] = await Promise.all([
      fetch(apiUrl('/api/v1/locations')),
      fetch(apiUrl('/api/v1/discovery-jobs?limit=50')),
    ])
    if (!locationResponse.ok || !jobResponse.ok) throw new Error('Could not load saved locations and search progress.')
    const [locationData, jobData] = await Promise.all([locationResponse.json(), jobResponse.json()])
    setLocations(locationData.items)
    setJobs(jobData.items)
  }, [])

  useEffect(() => {
    let cancelled = false
    refresh().catch(reason => { if (!cancelled) setError(reason instanceof Error ? reason.message : 'Could not load locations.') })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [refresh])

  useEffect(() => {
    const timer = window.setInterval(() => {
      void refresh().catch(reason => setError(reason instanceof Error ? reason.message : 'Could not refresh search progress.'))
    }, 4000)
    return () => window.clearInterval(timer)
  }, [refresh])

  async function resolveLocation(event: FormEvent) {
    event.preventDefault()
    if (query.trim().length < 2) return
    setWorking(true)
    setError('')
    setNotice('')
    try {
      const response = await fetch(apiUrl('/api/v1/locations/resolve'), {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query: query.trim() }),
      })
      const payload = await response.json()
      if (!response.ok) throw new Error(errorMessage(payload, 'Could not resolve that location.'))
      const results: ResolvedLocation[] = payload.results
      if (!results.length) throw new Error('No German city or postcode matched that search.')
      setChoices(results)
      setSelectedIndex(0)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not resolve that location.')
    } finally {
      setWorking(false)
    }
  }

  async function saveLocation() {
    const selected = choices[selectedIndex]
    if (!selected) return
    setWorking(true)
    setError('')
    setNotice('')
    try {
      const response = await fetch(apiUrl('/api/v1/locations'), {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...selected, radius_km: radius, interval_days: intervalDays }),
      })
      const payload = await response.json()
      if (!response.ok) throw new Error(errorMessage(payload, 'Could not save this location.'))
      setChoices([])
      setQuery('')
      setNotice(`${payload.label} was added. We’re finding nearby companies and their websites now.`)
      await refresh()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not save this location.')
    } finally {
      setWorking(false)
    }
  }

  async function runNow(location: ConfiguredLocation) {
    setWorking(true)
    setError('')
    try {
      const response = await fetch(apiUrl('/api/v1/discovery-jobs'), {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ location_id: location.id }),
      })
      const payload = await response.json()
      if (!response.ok) throw new Error(errorMessage(payload, 'Could not start a search for this location.'))
      setNotice(`${payload.label}: ${runStatus(payload.status).toLowerCase()}.`)
      await refresh()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not start a search for this location.')
    } finally {
      setWorking(false)
    }
  }

  async function cancelSearch(job: DiscoveryJob) {
    setCancellingJobId(job.id)
    setError('')
    setNotice('')
    try {
      const response = await fetch(apiUrl(`/api/v1/discovery-jobs/${job.id}/cancel`), { method: 'POST' })
      if (!response.ok) throw new Error('Could not cancel this search. It may have already finished.')
      setNotice(`The search for ${job.label} was cancelled.`)
      await refresh()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not cancel this search.')
    } finally {
      setCancellingJobId(null)
    }
  }

  async function updateLocation(location: ConfiguredLocation, enabled: boolean) {
    setError('')
    try {
      const response = await fetch(apiUrl(`/api/v1/locations/${location.id}`), {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled }),
      })
      const payload = await response.json()
      if (!response.ok) throw new Error(errorMessage(payload, 'Could not update this schedule.'))
      await refresh()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not update this schedule.')
    }
  }

  async function deleteLocation(location: ConfiguredLocation) {
    setError('')
    try {
      const response = await fetch(apiUrl(`/api/v1/locations/${location.id}`), { method: 'DELETE' })
      if (!response.ok) throw new Error('Could not remove this saved location.')
      setNotice(`${location.label} was removed from your saved locations.`)
      await refresh()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not remove this location schedule.')
    }
  }

  const runningJobs = jobs.filter(job => ['queued', 'running'].includes(job.status))
  const scheduledJobs = jobs.filter(job => job.status === 'scheduled')
  const recentJobs = jobs.filter(job => ['completed', 'partial', 'failed', 'cancelled'].includes(job.status)).slice(0, 8)

  return <div className="app-shell">
    <SiteHeader active="locations" />
    <main className="content locations-page">
      <section className="intro-row">
        <div>
          <p className="eyebrow">YOUR SEARCH AREAS</p>
          <h1>Places to <em>search.</em></h1>
          <p className="intro-copy">Choose a place and distance. We’ll find nearby companies first, then check their websites for open jobs.</p>
        </div>
        <div className="snapshot-pill"><span>{locations.length.toString().padStart(2, '0')}</span> SAVED SEARCH AREAS</div>
      </section>

      {error && <div className="error-banner" role="alert">{error}<button type="button" onClick={() => setError('')}>Dismiss</button></div>}
      {notice && <div className="notice-banner" role="status">{notice}<button type="button" onClick={() => setNotice('')}>Dismiss</button></div>}

      <section className="location-setup-card">
        <div className="section-heading">
          <div><p className="eyebrow">START HERE</p><h2>Add a location</h2></div>
          <p>Choose a German city, state, or postcode and how far around it to look.</p>
        </div>
        <form className="location-setup-form" onSubmit={resolveLocation}>
          <label className="location-query-field">
            <span className="control-label">CITY, STATE OR POSTCODE</span>
            <input value={query} onChange={event => setQuery(event.target.value)} placeholder="e.g. Karlsruhe, Bavaria, or 76133" />
          </label>
          <label className="radius-control">
            <span className="control-label">DISTANCE AROUND IT</span>
            <select value={radius} onChange={event => setRadius(Number(event.target.value))}>
              {[5, 10, 15, 25, 50, 100, 200].map(value => <option key={value} value={value}>{value} km</option>)}
            </select>
          </label>
          <label className="interval-control">
            <span className="control-label">SEARCH AGAIN</span>
            <select value={intervalDays} onChange={event => setIntervalDays(Number(event.target.value))}>
              {[1, 7, 14, 30, 60, 90].map(value => <option key={value} value={value}>{value === 1 ? 'Daily' : `Every ${value} days`}</option>)}
            </select>
          </label>
          <button className="search-button" type="submit" disabled={working || query.trim().length < 2}>
            {working ? <span className="spinner small" /> : <span aria-hidden="true">⌕</span>}
            <span>{working ? 'Working…' : 'Find location'}</span>
          </button>
        </form>
        {choices.length > 0 && <div className="location-choice-panel">
          <label htmlFor="resolved-location">Select the matching German place</label>
          <select id="resolved-location" value={selectedIndex} onChange={event => setSelectedIndex(Number(event.target.value))}>
            {choices.map((choice, index) => <option value={index} key={`${choice.label}-${index}`}>{choice.label}</option>)}
          </select>
          <button className="secondary-button" type="button" disabled={working} onClick={() => void saveLocation()}>
            Add location and find companies
          </button>
        </div>}
        <p className="location-setup-note">After you add a place, we first find nearby companies and their websites. Then we look for hiring pages and open jobs.</p>
      </section>

      <section className="locations-list-section">
        <div className="section-heading">
          <div><p className="eyebrow">YOUR SEARCH AREAS</p><h2>Saved locations</h2></div>
          <span className="section-count">{locations.length} {locations.length === 1 ? 'place' : 'places'}</span>
        </div>
        {loading ? <div className="loading-state"><span className="spinner" /> Loading saved locations…</div>
          : locations.length ? <div className="configured-locations-grid">
            {locations.map(location => <article className="configured-location-card" key={location.id}>
              <div className="configured-location-top">
                <span className="location-card-pin" aria-hidden="true">⌖</span>
                <span className={`schedule-state ${location.enabled ? 'is-enabled' : ''}`}>{location.enabled ? 'REPEATS' : 'PAUSED'}</span>
              </div>
              <h3>{location.city || location.label.split(',')[0]}</h3>
              <p className="configured-location-label">{location.state || location.postcode || location.label}</p>
              <div className="location-card-meta"><span>Within {location.radius_km} km</span><span>Every {location.interval_days} days</span></div>
              <p className="next-run-label">Next search <strong>{location.enabled ? dateTime(location.next_run_at) : 'Paused'}</strong></p>
              <div className="location-card-actions">
                <button type="button" className="primary-small-button" disabled={working} onClick={() => void runNow(location)}>Search this place now</button>
                <button type="button" className="text-button" onClick={() => void updateLocation(location, !location.enabled)}>{location.enabled ? 'Pause' : 'Resume'}</button>
                <button type="button" className="text-button text-danger" onClick={() => void deleteLocation(location)}>Remove</button>
              </div>
            </article>)}
          </div> : <div className="empty-panel"><strong>No saved locations yet</strong><span>Add a city or postcode above to find nearby companies.</span></div>}
      </section>

      <section className="crawl-activity-section">
        <div className="section-heading">
          <div><p className="eyebrow">SEARCH ACTIVITY</p><h2>Current and upcoming searches</h2></div>
          <span className="refresh-indicator"><span className="status-dot" /> Updates every few seconds</span>
        </div>
        <div className="activity-grid">
          <article className="activity-column">
            <div className="activity-column-heading"><h3>Current searches</h3><span>{runningJobs.length}</span></div>
            {runningJobs.length ? runningJobs.map(job => <JobProgressCard key={job.id} job={job}
              cancellingJobId={cancellingJobId} onCancel={() => void cancelSearch(job)} />)
              : <div className="empty-activity">No searches are running. Start one from a saved location.</div>}
          </article>
          <article className="activity-column">
            <div className="activity-column-heading"><h3>Coming up</h3><span>{scheduledJobs.length + locations.filter(location => location.enabled).length}</span></div>
            {scheduledJobs.map(job => <ScheduledJobCard key={`job-${job.id}`} job={job}
              cancellingJobId={cancellingJobId} onCancel={() => void cancelSearch(job)} />)}
            {locations.filter(location => location.enabled).map(location => <div className="scheduled-location" key={`location-${location.id}`}>
              <span className="scheduled-icon" aria-hidden="true">◷</span>
              <span><strong>{location.city || location.label}</strong><small>Repeats · within {location.radius_km} km · every {location.interval_days} days</small></span>
              <time>{dateTime(location.next_run_at)}</time>
            </div>)}
            {!scheduledJobs.length && !locations.some(location => location.enabled) && <div className="empty-activity">Nothing is scheduled.</div>}
          </article>
        </div>
      </section>

      <section className="recent-crawls-section">
        <div className="section-heading"><div><p className="eyebrow">PAST SEARCHES</p><h2>Recent results</h2></div></div>
        {recentJobs.length ? <div className="recent-crawl-list">{recentJobs.map(job => <div className="recent-crawl-row" key={job.id}>
          <span><strong>{job.label}</strong><small>{runStep(job.stage)} · {dateTime(job.created_at)}</small></span>
          <span className={`history-status history-${job.status}`}>{runStatus(job.status)}</span>
          <span>{job.companies_found} companies · {job.homepages_found} websites</span>
          <span>{job.jobs_found} jobs found</span>
        </div>)}</div> : <div className="empty-activity">Finished searches will appear here.</div>}
      </section>
    </main>
  </div>
}

function JobProgressCard({ job, onCancel, cancellingJobId }: {
  job: DiscoveryJob; onCancel: () => void; cancellingJobId: number | null
}) {
  const waiting = job.status === 'queued'
  const findingCompanies = job.stage === 'company_homepage_discovery' && job.status === 'running'
  const firstStepFailed = job.stage === 'company_homepage_discovery' && job.status === 'failed'
  const foundNoHomepages = job.stage === 'complete' && job.candidate_total === 0
  const elapsed = elapsedLabel(waiting ? job.created_at : job.progress_updated_at || job.started_at)
  const progressMessage = waiting
    ? `Waiting for the search service${elapsed ? ` · ${elapsed} elapsed` : ''}`
    : `${job.progress_message || (findingCompanies ? 'Searching nearby map listings' : runStep(job.stage))}${elapsed ? ` · ${elapsed} in this step` : ''}`
  return <div className="job-progress-card">
    <div className="job-progress-top"><strong>{job.label}</strong><div className="job-progress-actions">
      <span className={`job-state job-state-${job.status}`}>{runStatus(job.status)}</span>
      <button type="button" className="cancel-search-button" disabled={cancellingJobId !== null}
        onClick={onCancel}>{cancellingJobId === job.id ? 'Cancelling…' : 'Cancel search'}</button>
    </div></div>
    <small>{runStep(job.stage)} · within {job.radius_km} km</small>
    <div className={`progress-track ${findingCompanies ? 'is-indeterminate' : ''} ${waiting ? 'is-waiting' : ''}`} role="progressbar" aria-label={runStep(job.stage)} aria-valuemin={0} aria-valuemax={100} {...(findingCompanies || waiting ? { 'aria-valuetext': progressMessage } : { 'aria-valuenow': job.progress_percent })}>
      <span style={findingCompanies || waiting ? undefined : { width: `${job.progress_percent}%` }} />
    </div>
    <div className="progress-caption">{findingCompanies || waiting
      ? <span>{progressMessage}</span>
      : firstStepFailed ? <span>The company and website search could not be completed.</span>
        : foundNoHomepages ? <span>Company and website search finished.</span>
          : <><span>{progressMessage}</span><strong>{job.processed_count} of {job.candidate_total} checked · {job.progress_percent}%</strong></>}</div>
    {waiting && hasWaitedTooLong(job.created_at) && <p className="progress-wait-note">This search has not started yet. It will begin when the search service is available.</p>}
    <div className="progress-results"><span>{job.companies_found} companies found</span><span>{job.homepages_found} websites found</span><span>{job.jobs_found} jobs found</span></div>
    {job.error && <p className="job-error">We couldn’t finish this search. Try again later.</p>}
  </div>
}

function ScheduledJobCard({ job, onCancel, cancellingJobId }: {
  job: DiscoveryJob; onCancel: () => void; cancellingJobId: number | null
}) {
  return <div className="scheduled-location">
    <span className="scheduled-icon" aria-hidden="true">◷</span>
    <span><strong>{job.label}</strong><small>One-time search · within {job.radius_km} km</small></span>
    <time>{dateTime(job.scheduled_for)}</time>
    <button type="button" className="cancel-search-button" disabled={cancellingJobId !== null}
      onClick={onCancel}>{cancellingJobId === job.id ? 'Cancelling…' : 'Cancel search'}</button>
  </div>
}
