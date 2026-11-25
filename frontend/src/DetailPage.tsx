import JobPage, { type Job } from './JobPage'
import { useEffect, useState, type FormEvent } from 'react'
import SiteHeader from './SiteHeader'
import ApplicationSwipeCard, { ApplicationStateBadge } from './ApplicationSwipeCard'
import { applicationStatusLabel, saveApplication, trackJobInteraction } from './applications'
import type { Application, ApplicationSwipeAction } from './applications'
import JobOverviewTags from './JobOverviewTags'
import type { Profile } from './profile'

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '')
const apiUrl = (path: string) => `${API_BASE_URL}${path}`

type Feed = { id: number; provider: string; board_url: string | null; feed_url: string; status: string; enabled?: boolean; job_count: number | null; last_checked_at: string | null }
type Company = {
  crawl_paused: boolean; crawl_interval_days: number | null; crawl_reason: string | null; next_check_at: string | null; employee_count: number | null
  id: number; name: string; category: string | null; website_url: string | null; domain: string | null
  domain_evidence_url: string | null; career_url: string | null; career_status: string
  latitude: number | null; longitude: number | null; location_precision: string | null
  location_label: string | null; source_url: string | null; active_job_count: number
  feeds: Feed[]; jobs: Job[]
}

function ExternalLink({ href, children, onClick }: { href: string; children: React.ReactNode; onClick?: () => void }) {
  return <a className="page-link" href={href} target="_blank" rel="noreferrer" onClick={onClick}>{children}<span aria-hidden="true">↗</span></a>
}

export default function DetailPage({ kind, id }: { kind: 'company' | 'job'; id: number }) {
  const [record, setRecord] = useState<Company | Job | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [profileId, setProfileId] = useState(() => new URLSearchParams(window.location.search).get('profile_id') ?? '')
  const [profiles, setProfiles] = useState<Profile[]>([])
  const [profilesReady, setProfilesReady] = useState(false)
  const [profileSelectionRequired, setProfileSelectionRequired] = useState(false)
  const [profileError, setProfileError] = useState('')
  const [trackingError, setTrackingError] = useState('')

  useEffect(() => {
    const controller = new AbortController()
    fetch(apiUrl('/api/v1/profiles'), { signal: controller.signal }).then(async response => {
      if (!response.ok) throw new Error('Could not load application profiles.')
      const data = await response.json()
      const requested = new URLSearchParams(window.location.search).get('profile_id')
      setProfiles(data.items)
      if (requested) {
        const selected = data.items.find((profile: Profile) => String(profile.id) === requested)
        if (selected) {
          setProfileId(String(selected.id))
        } else {
          setProfileId('')
          setProfileSelectionRequired(data.items.length > 0)
          setProfileError(data.items.length > 0
            ? 'The selected application profile is unavailable. Choose a profile to continue.'
            : 'The selected application profile is unavailable. Details will be shown without application tracking.')
          if (data.items.length > 0) setLoading(false)
        }
      } else {
        const selected = data.items[0]
        setProfileId(selected ? String(selected.id) : '')
      }
      setProfilesReady(true)
    }).catch(reason => { if (!controller.signal.aborted) { setProfileError(reason.message); setProfilesReady(true) } })
    return () => controller.abort()
  }, [])

  useEffect(() => {
    if (!profilesReady || profileSelectionRequired) return
    let cancelled = false
    setLoading(true)
    setError('')
    setTrackingError('')
    const boardParams = new URLSearchParams(window.location.search)
    const detailParams = new URLSearchParams()
    if (kind === 'job') {
      for (const [from, to] of [['lat','latitude'],['lon','longitude'],['radius_km','radius_km'],['place','place'],['country_code','country_code'],['state','state'],['country','country']]) {
        const value = boardParams.get(from)
        if (value) detailParams.set(to, value)
      }
    }
    if (profileId) detailParams.set('profile_id', profileId)
    fetch(apiUrl(`/api/v1/${kind === 'company' ? 'companies' : 'jobs'}/${id}${detailParams.size ? `?${detailParams}` : ''}`))
      .then(async response => {
        const payload = await response.json()
        if (!response.ok) throw new Error(payload.detail ?? `Could not load ${kind} details.`)
        return payload
      })
      .then(async payload => {
        if (cancelled) return
        if (kind === 'job' && profileId && !payload.application) {
          try {
            const application = await trackJobInteraction(API_BASE_URL, Number(profileId), id, payload.application, 'view')
            payload = { ...payload, application }
          } catch {
            if (!cancelled) setTrackingError('This role could not be added to your applications yet. You can still set its stage below.')
          }
        }
        if (!cancelled) setRecord(payload)
      })
      .catch(reason => { if (!cancelled) setError(reason instanceof Error ? reason.message : 'Could not load this record.') })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [id, kind, profileId, profileSelectionRequired, profilesReady])

  useEffect(() => {
    if (!profilesReady || profileSelectionRequired) return
    const url = new URL(window.location.href)
    if (profileId) url.searchParams.set('profile_id', profileId)
    else url.searchParams.delete('profile_id')
    window.history.replaceState({}, '', url)
  }, [profileId, profileSelectionRequired, profilesReady])

  const fromApplications = kind === 'job' && new URLSearchParams(window.location.search).get('from') === 'applications'
  const applicationReturnParams = new URLSearchParams(window.location.search)
  const applicationReturnQuery = new URLSearchParams()
  if (applicationReturnParams.has('profile_ids')) applicationReturnQuery.set('profile_ids', applicationReturnParams.get('profile_ids') ?? '')
  else if (profileId) applicationReturnQuery.set('profile_id', profileId)
  const returnStatus = applicationReturnParams.get('status')
  if (applicationReturnParams.has('statuses')) applicationReturnQuery.set('statuses', applicationReturnParams.get('statuses') ?? '')
  else if (returnStatus) applicationReturnQuery.set('status', returnStatus)
  const applicationsBackHref = `/applications${applicationReturnQuery.size ? `?${applicationReturnQuery}` : ''}`

  return <div className="app-shell">
    <SiteHeader active={fromApplications ? 'applications' : 'directory'} />
    <main className="content detail-page">
      <a className="back-link" href={fromApplications ? applicationsBackHref : kind === 'company' ? '/discover' : `/discover?view=jobs${window.location.search ? '&' + window.location.search.slice(1) : ''}`}>← {fromApplications ? 'Back to my applications' : 'Back to role discovery'}</a>
      {profiles.length > 0 && <div className="detail-profile-context">
        <span>Tracking with</span>
        {profiles.length > 1 || profileSelectionRequired ? <select aria-label="Application profile" value={profileId}
          onChange={event => {
            setProfileId(event.target.value)
            setProfileSelectionRequired(false)
            setProfileError('')
          }}>
          {profileSelectionRequired && <option value="">Choose a profile</option>}
          {profiles.map(profile => <option key={profile.id} value={profile.id}>{profile.name}</option>)}
        </select> : <strong>{profiles[0].name}</strong>}
      </div>}
      {profileError && <div className="error-banner" role="alert">{profileError}</div>}
      {trackingError && <p className="tracking-error-note" role="status">{trackingError}</p>}
      {loading ? <div className="loading-state"><span className="spinner" /> Loading details…</div>
          : error ? <div className="error-banner" role="alert">{error}<a href="/discover">Return to role discovery</a></div>
          : kind === 'company' && record ? <CompanyPage company={record as Company} profileId={profileId ? Number(profileId) : null}
              onApplicationSaved={(jobId, application) => setRecord(current => current && 'jobs' in current
                ? { ...current, jobs: current.jobs.map(job => job.id === jobId ? { ...job, application } : job) } : current)} />
            : record ? <JobPage job={record as Job} profileId={profileId ? Number(profileId) : null}
              onApplicationSaved={application => setRecord(current => current ? { ...current, application } : current)} /> : null}
    </main>
  </div>
}

function CompanyPage({ company, profileId, onApplicationSaved }: {
  company: Company
  profileId: number | null
  onApplicationSaved: (jobId: number, application: Application) => void
}) {
  const [companyDomain, setCompanyDomain] = useState(company.domain ?? '')
  const [domainSaving, setDomainSaving] = useState(false)
  const [domainError, setDomainError] = useState('')
  const [domainNotice, setDomainNotice] = useState('')
  const [savingJobId, setSavingJobId] = useState<number | null>(null)
  const [applicationError, setApplicationError] = useState('')
  const [applicationNotice, setApplicationNotice] = useState('')
  const statusLabels: Record<string, string> = {
    jobs_feed_found: 'Job listings found', career_page_found: 'Hiring page found',
    jobs_extracted: 'Jobs found on company website', unresolved: 'Hiring page not found yet',
    not_checked: 'Not checked yet', provider_detected: 'Hiring page found',
  }
  const precisionLabels: Record<string, string> = {
    point: 'Map point', feature_center: 'Area center', city_centroid: 'City center',
    address: 'Street address', place_point: 'Place marker',
  }
  const providerLabels: Record<string, string> = {
    greenhouse: 'Company hiring page', lever: 'Company hiring page', personio: 'Company hiring page', ashby: 'Company hiring page',
    schema_org: 'Company job page', html_jobs: 'Company job page', external_html: 'Public job page',
    browser_html: 'JavaScript job page', rss: 'RSS / Atom job feed',
    arbeitsagentur: 'Bundesagentur für Arbeit job listing',
  }
  const feedStatusLabels: Record<string, string> = {
    parsed: 'checked', complete_empty: 'checked · no jobs', incomplete: 'partly checked',
    windowed: 'scanned · rolling feed', windowed_empty: 'checked · no items in feed window',
    blocked: 'site access blocked', bot_challenge: 'bot challenge', paused: 'paused',
    throttled: 'try again later', schema_error: 'could not read listings',
    failed: 'could not check', not_modified: 'checked recently', unsupported: 'not supported yet',
  }

  async function updateJobApplication(job: Job, action: ApplicationSwipeAction) {
    if (profileId === null || savingJobId !== null) return
    setSavingJobId(job.id)
    setApplicationError('')
    setApplicationNotice('')
    try {
      const application = await saveApplication(API_BASE_URL, profileId, job.id, action.status, action.kind)
      onApplicationSaved(job.id, application)
      const stage = applicationStatusLabel(application.status)
      setApplicationNotice(action.kind === 'undo' ? `${job.title} restored to ${stage}.` : `${job.title} moved to ${stage}.`)
    } catch (reason) {
      setApplicationError(reason instanceof Error ? reason.message : 'Could not update this application stage.')
    } finally {
      setSavingJobId(null)
    }
  }

  async function saveCompanyDomain(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setDomainSaving(true)
    setDomainError('')
    setDomainNotice('')
    try {
      const response = await fetch(apiUrl(`/api/v1/companies/${company.id}`), {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ domain: companyDomain.trim() || null }),
      })
      const payload = await response.json().catch(() => ({}))
      if (!response.ok) throw new Error(payload.detail ?? 'Could not save the company domain.')
      setCompanyDomain(payload.domain ?? '')
      setDomainNotice('Company domain saved.')
    } catch (reason) {
      setDomainError(reason instanceof Error ? reason.message : 'Could not save the company domain.')
    } finally {
      setDomainSaving(false)
    }
  }

  return <>
    <section className="detail-hero">
      <p className="eyebrow">Company · {company.category?.replaceAll('_', ' ') || 'business'}</p>
      <h1>{company.name}</h1>
      <p className="intro-copy">Company website, hiring page, and the open jobs listed there.</p>
      <div className="detail-links">
        {company.website_url && <ExternalLink href={company.website_url}>Company website</ExternalLink>}
        {company.career_url && <ExternalLink href={company.career_url}>Hiring page</ExternalLink>}
        {company.domain_evidence_url && <ExternalLink href={company.domain_evidence_url}>Website listing</ExternalLink>}
        {company.source_url && <ExternalLink href={company.source_url}>OpenStreetMap record</ExternalLink>}
      </div>
    </section>
    <section className="detail-layout">
      <article className="detail-panel">
        <p className="eyebrow">Hiring status</p>
        <h2>{statusLabels[company.career_status] || 'Not checked yet'}</h2>
        <div className="metadata-grid">
          <form className="company-domain-editor meta-item" onSubmit={saveCompanyDomain}>
            <label htmlFor="company-domain"><span>Company domain</span></label>
            <div className="company-domain-input">
              <input id="company-domain" type="text" maxLength={255} autoCapitalize="none" autoCorrect="off"
                spellCheck={false} value={companyDomain} onChange={event => setCompanyDomain(event.target.value)}
                placeholder="e.g. company.de" />
              <button className="secondary-button" type="submit" disabled={domainSaving}>{domainSaving ? 'Saving…' : 'Save'}</button>
            </div>
            {domainError && <small className="company-domain-error" role="alert">{domainError}</small>}
            {domainNotice && <small className="company-domain-notice" role="status">{domainNotice}</small>}
          </form>
          <Meta label="Hiring page" value={company.career_url || 'Not found yet'} />
          <Meta label="Business type" value={(company.category || company.location_label)?.replaceAll('_', ' ') || 'Not listed'} />
          <Meta label="Location detail" value={precisionLabels[company.location_precision || ''] || company.location_precision || 'Not provided'} />
          <Meta label="Check schedule" value={company.crawl_paused ? 'Paused' : company.crawl_interval_days === 1 ? 'Daily' : `Every ${company.crawl_interval_days ?? 7} days`} />
          <Meta label="Why this schedule" value={company.crawl_reason || 'Waiting for employer evidence'} />
          <Meta label="Next check" value={company.crawl_paused ? 'Paused until fresh evidence or a manual search' : company.next_check_at ? new Date(company.next_check_at).toLocaleString() : 'Pending'} />
          <Meta label="Active jobs" value={String(company.active_job_count ?? company.jobs.filter(job => job.is_active !== false).length)} />
        </div>
        <div className="feed-section-heading"><h3 className="panel-subheading">Where job listings were found</h3>
          <a className="page-link" href={`/feeds?company_id=${company.id}`}>Manage feeds</a></div>
        {company.feeds.length ? company.feeds.map(feed => <div className="feed-record" key={feed.id}>
          <div><strong>{providerLabels[feed.provider] || 'Company job page'}</strong><span>{feed.job_count ?? 0} jobs · {feedStatusLabels[feed.status] || 'needs checking'}</span></div>
          <ExternalLink href={feed.board_url || feed.feed_url}>View listings</ExternalLink>
        </div>) : <p className="muted">No job listings found on this company’s website yet.</p>}
      </article>
      <article className="detail-panel company-jobs-panel">
        <p className="eyebrow">Open jobs</p>
        <h2>{company.jobs.length} {company.jobs.length === 1 ? 'job' : 'jobs'}</h2>
        {applicationError && <p className="error-banner" role="alert">{applicationError}</p>}
        {applicationNotice && <p className="evidence-note" role="status">{applicationNotice}</p>}
        {company.jobs.length ? company.jobs.map(job => {
          const previousStatus = job.application?.previous_status && job.application.previous_status !== job.application.status
            ? job.application.previous_status : null
          const href = `/jobs/${job.id}${profileId ? `?profile_id=${profileId}` : ''}`
          return <ApplicationSwipeCard key={job.id} application={job.application} disabled={profileId === null || savingJobId !== null}
            className={`job-result-card company-job-card status-${job.application?.status ?? 'untracked'}`}
            onAction={action => { void updateJobApplication(job, action) }}>
            <a className="company-job-link" href={href}>
              <span><strong>{job.title}</strong><small>{job.location_text || 'Work location not listed'} · {providerLabels[job.provider] || 'Company job page'}</small></span>
              <span aria-hidden="true">→</span>
            </a>
            <JobOverviewTags job={job} />
            <footer className="application-card-footer company-job-footer">
              <ApplicationStateBadge application={job.application} />
              {previousStatus && <button type="button" data-swipe-undo className="application-undo-button application-card-undo"
                disabled={savingJobId !== null} onClick={() => void updateJobApplication(job, { kind: 'undo', status: previousStatus })}>
                {savingJobId === job.id ? 'Undoing…' : `↶ Undo to ${applicationStatusLabel(previousStatus)}`}
              </button>}
            </footer>
          </ApplicationSwipeCard>
        }) : <p className="muted">No open jobs were found on this company’s website yet.</p>}
      </article>
    </section>
  </>
}

function Meta({ label, value, wide = false }: { label: string; value?: string | null; wide?: boolean }) {
  const displayValue = value?.trim()
  if (!displayValue || /^(?:not listed|not found|not provided|not specified|unknown|n\/a|none)$/i.test(displayValue)) return null
  return <div className={`meta-item${wide ? ' meta-item-wide' : ''}`}><span>{label}</span><strong>{displayValue}</strong></div>

}
