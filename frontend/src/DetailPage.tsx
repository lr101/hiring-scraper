import { useEffect, useState } from 'react'
import SiteHeader from './SiteHeader'
import JobEvidence from './JobEvidence'
import type { Enrichment, ProfileMatch } from './JobEvidence'

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '')
const apiUrl = (path: string) => `${API_BASE_URL}${path}`

type Feed = { id: number; provider: string; board_url: string | null; feed_url: string; status: string; job_count: number | null; last_checked_at: string | null }
type Company = {
  id: number; name: string; category: string | null; website_url: string | null; domain: string | null
  domain_evidence_url: string | null; career_url: string | null; career_status: string
  latitude: number | null; longitude: number | null; location_precision: string | null
  location_label: string | null; source_url: string | null; active_job_count: number
  feeds: Feed[]; jobs: Job[]
}
type JobLocation = { label: string; precision: string | null }
type Job = {
  id: number; title: string; url: string; company_id: number; company_name: string
  company_domain: string | null; company_website: string | null; company_source_url: string | null
  provider: string; board_url: string | null; location_text: string | null
  locations: JobLocation[]; is_remote: boolean; work_arrangement: string | null
  employment_type: string | null; schedule: string | null; department: string | null
  seniority: string | null; date_posted: string | null; first_seen_at: string
  salary: string | null; description: string | null; is_active: boolean
  enrichment: Enrichment; profile_match?: ProfileMatch
  raw_metadata: Record<string, unknown>
}

function formatDate(value: string | null) {
  if (!value) return 'Not listed'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat('en', { day: 'numeric', month: 'short', year: 'numeric' }).format(date)
}

function ExternalLink({ href, children }: { href: string; children: React.ReactNode }) {
  return <a className="page-link" href={href} target="_blank" rel="noreferrer">{children}<span aria-hidden="true">↗</span></a>
}

export default function DetailPage({ kind, id }: { kind: 'company' | 'job'; id: number }) {
  const [record, setRecord] = useState<Company | Job | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError('')
    const profileId = new URLSearchParams(window.location.search).get('profile_id')
    fetch(apiUrl(`/api/v1/${kind === 'company' ? 'companies' : 'jobs'}/${id}${kind === 'job' && profileId ? `?profile_id=${encodeURIComponent(profileId)}` : ''}`))
      .then(async response => {
        const payload = await response.json()
        if (!response.ok) throw new Error(payload.detail ?? `Could not load ${kind} details.`)
        return payload
      })
      .then(payload => { if (!cancelled) setRecord(payload) })
      .catch(reason => { if (!cancelled) setError(reason instanceof Error ? reason.message : 'Could not load this record.') })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [id, kind])

  return <div className="app-shell">
    <SiteHeader active="directory" />
    <main className="content detail-page">
      <a className="back-link" href={kind === 'company' ? '/' : `/?view=jobs${window.location.search ? '&' + window.location.search.slice(1) : ''}`}>← Back to companies and jobs</a>
      {loading ? <div className="loading-state"><span className="spinner" /> Loading details…</div>
          : error ? <div className="error-banner" role="alert">{error}<a href="/">Return to companies and jobs</a></div>
          : kind === 'company' && record ? <CompanyPage company={record as Company} />
            : record ? <JobPage job={record as Job} /> : null}
    </main>
  </div>
}

function CompanyPage({ company }: { company: Company }) {
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
    schema_org: 'Company job page', html_jobs: 'Company job page',
  }
  const feedStatusLabels: Record<string, string> = {
    parsed: 'checked', complete_empty: 'checked · no jobs', incomplete: 'partly checked',
    blocked: 'site access blocked', throttled: 'try again later', schema_error: 'could not read listings',
    failed: 'could not check', not_modified: 'checked recently', unsupported: 'not supported yet',
  }
  return <>
    <section className="detail-hero">
      <p className="eyebrow">COMPANY · {company.category?.replaceAll('_', ' ') || 'BUSINESS'}</p>
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
        <p className="eyebrow">HIRING STATUS</p>
        <h2>{statusLabels[company.career_status] || 'Not checked yet'}</h2>
        <div className="metadata-grid">
          <Meta label="WEBSITE" value={company.domain || 'Not found'} />
          <Meta label="HIRING PAGE" value={company.career_url || 'Not found yet'} />
          <Meta label="BUSINESS TYPE" value={(company.category || company.location_label)?.replaceAll('_', ' ') || 'Not listed'} />
          <Meta label="LOCATION DETAIL" value={precisionLabels[company.location_precision || ''] || company.location_precision || 'Not provided'} />
          <Meta label="ACTIVE JOBS" value={String(company.active_job_count ?? company.jobs.filter(job => job.is_active !== false).length)} />
        </div>
        <h3 className="panel-subheading">Where job listings were found</h3>
        {company.feeds.length ? company.feeds.map(feed => <div className="feed-record" key={feed.id}>
          <div><strong>{providerLabels[feed.provider] || 'Company job page'}</strong><span>{feed.job_count ?? 0} jobs · {feedStatusLabels[feed.status] || 'needs checking'}</span></div>
          <ExternalLink href={feed.board_url || feed.feed_url}>View listings</ExternalLink>
        </div>) : <p className="muted">No job listings found on this company’s website yet.</p>}
      </article>
      <article className="detail-panel company-jobs-panel">
        <p className="eyebrow">OPEN JOBS</p>
        <h2>{company.jobs.length} {company.jobs.length === 1 ? 'job' : 'jobs'}</h2>
        {company.jobs.length ? company.jobs.map(job => <a className="job-result-card" href={`/jobs/${job.id}`} key={job.id}>
          <span><strong>{job.title}</strong><small>{job.location_text || 'Work location not listed'} · {providerLabels[job.provider] || 'Company job page'}</small></span>
          <span aria-hidden="true">→</span>
        </a>) : <p className="muted">No open jobs were found on this company’s website yet.</p>}
      </article>
    </section>
  </>
}

function JobPage({ job }: { job: Job }) {
  const arrangement = job.work_arrangement || (job.is_remote ? 'remote' : 'not specified')
  const providerLabels: Record<string, string> = {
    greenhouse: 'Company hiring page', lever: 'Company hiring page', personio: 'Company hiring page', ashby: 'Company hiring page',
    schema_org: 'Company job page', html_jobs: 'Company job page',
  }
  const precisionLabels: Record<string, string> = {
    point: 'Map point', feature_center: 'Area center', city_centroid: 'City center',
    address: 'Street address', place_point: 'Place marker',
  }
  return <>
    <section className="detail-hero">
      <p className="eyebrow">JOB POSTING · {providerLabels[job.provider] || 'Company job page'}</p>
      <h1>{job.title}</h1>
      <p className="intro-copy"><a href={`/companies/${job.company_id}`}>{job.company_name}</a>{job.company_domain ? ` · ${job.company_domain}` : ''}</p>
      <div className="detail-links">
        <ExternalLink href={job.url}>Original job post</ExternalLink>
        {job.company_website && <ExternalLink href={job.company_website}>Company website</ExternalLink>}
        {job.company_source_url && <ExternalLink href={job.company_source_url}>Company listing</ExternalLink>}
      </div>
    </section>
    <section className="detail-panel job-detail-panel">
        <p className="eyebrow">JOB DETAILS</p>
      <div className="metadata-grid">
        <Meta label="WORK LOCATION" value={job.location_text || job.locations.map(item => item.label).join(' · ') || 'Not listed'} />
        <Meta label="WORK STYLE" value={arrangement} />
        <Meta label="EMPLOYMENT TYPE" value={job.employment_type || 'Not listed'} />
        <Meta label="HOURS" value={job.schedule || 'Not listed'} />
        <Meta label="TEAM" value={job.department || 'Not listed'} />
        <Meta label="EXPERIENCE LEVEL" value={job.seniority || 'Not listed'} />
        <Meta label="POSTED" value={formatDate(job.date_posted)} />
        <Meta label="FIRST SEEN" value={formatDate(job.first_seen_at)} />
        <Meta label="SALARY" value={job.salary || 'Not listed'} />
        <Meta label="LOCATION DETAIL" value={[...new Set(job.locations.map(item => item.precision).filter(Boolean))].map(value => precisionLabels[value || ''] || value).join(' · ') || 'Not provided'} />
      </div>
      {job.description && <section className="job-description"><h2>Role description</h2><p>{job.description}</p></section>}
    </section>
    {job.enrichment && <JobEvidence enrichment={job.enrichment} match={job.profile_match} />}
  </>
}

function Meta({ label, value }: { label: string; value: string }) {
  return <div className="meta-item"><span>{label}</span><strong>{value}</strong></div>
}
