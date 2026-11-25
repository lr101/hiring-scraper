import { useState, type ReactNode } from 'react'
import JobEvidence, { JobPostingAnalysis, languageRequirementLabel } from './JobEvidence'
import type { AnalysisState, Enrichment, PostingAnalysis, ProfileMatch } from './JobEvidence'
import { FitChecklists, fitCounts } from './ScoreBreakdown'
import ApplicationSwipeCard from './ApplicationSwipeCard'
import { JobDescription, type DescriptionBlock } from './JobDescription'
import { applicationStatuses, nextApplicationStages, saveApplication, trackJobInteraction } from './applications'
import type { Application, ApplicationStatus } from './applications'
import type { CityElectionResult } from './profile'
import { applicationStartLink } from './applicationDocuments'

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '')

export type JobLocation = { label: string; precision: string | null; city_election_result?: CityElectionResult | null }
export type Job = {
  id: number; title: string; url: string; company_id: number; company_name: string; company_name_is_employer?: boolean
  employer_company_id?: number | null
  company_domain: string | null; company_website: string | null; company_source_url: string | null
  provider: string; board_url: string | null; location_text: string | null
  locations: JobLocation[]; is_remote: boolean; work_arrangement: string | null
  employment_type: string | null; schedule: string | null; department: string | null
  last_seen_at: string; valid_through?: string | null; expired?: boolean; remote_country_codes?: string[]
  seniority: string | null; date_posted: string | null; first_seen_at: string
  salary: string | null; description: string | null; description_blocks?: DescriptionBlock[]; is_active: boolean
  closed_at?: string | null
  enrichment: Enrichment; profile_match?: ProfileMatch
  analysis_state?: AnalysisState; analysis?: PostingAnalysis | null; analysis_refreshing?: boolean
  application?: Application | null
  raw_metadata: Record<string, unknown>
}

const dateFormat = new Intl.DateTimeFormat(undefined, { day: 'numeric', month: 'short', year: 'numeric' })

function formatDate(value: string | null | undefined) {
  if (!value) return ''
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : dateFormat.format(date)
}

function postedLabel(value: string | null) {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  const days = Math.floor((Date.now() - date.getTime()) / 86_400_000)
  const relative = days <= 0 ? 'today' : days === 1 ? 'yesterday' : days < 31 ? `${days} days ago` : ''
  return relative ? `${relative} · ${dateFormat.format(date)}` : dateFormat.format(date)
}

function sentenceCase(value: string | null | undefined) {
  const text = value?.trim().replaceAll('_', ' ') ?? ''
  return text ? text[0].toUpperCase() + text.slice(1) : ''
}

function workStyle(job: Job) {
  const key = (job.work_arrangement ?? '').toLowerCase().replace(/[\s-]+/g, '_')
  if (job.is_remote || key === 'remote') return 'Remote'
  if (key === 'hybrid') return 'Hybrid'
  if (key === 'onsite' || key === 'on_site') return 'On-site'
  return sentenceCase(job.work_arrangement)
}

function displayLeadingParty(party: string) {
  return party.split(' / ').map(name => name === 'GRÜNE' ? 'Grüne'
    : name === 'FREIE WÄHLER' ? 'Freie Wähler' : name).join(' / ')
}

type Concern = { tone: 'conflict' | 'check'; text: string }

/** Things to settle before applying, most serious first, without repeats. */
function concerns(match: ProfileMatch): Concern[] {
  const rows: Concern[] = [
    ...match.conflicts.map(text => ({ tone: 'conflict' as const, text })),
    ...(match.prerequisite_conditions ?? []).filter(row => row.status === 'fail').map(row => ({ tone: 'conflict' as const, text: row.reason })),
    ...Object.entries(match.gates ?? {}).filter(([, gate]) => gate.status !== 'pass' && gate.reason)
      .map(([, gate]) => ({ tone: gate.status === 'fail' ? 'conflict' as const : 'check' as const, text: gate.reason })),
    ...(match.mandatory_review ? [{ tone: 'check' as const, text: 'A mandatory eligibility requirement is not shown in your profile.' }] : []),
    ...(match.language_review ? [{ tone: 'check' as const, text: 'Check your language level against the stated requirement.' }] : []),
    ...match.unknowns.map(text => ({ tone: 'check' as const, text })),
    ...(match.soft_conflicts ?? []).map(text => ({ tone: 'check' as const, text })),
  ]
  const seen = new Set<string>()
  return rows.filter(row => {
    const key = row.text.trim().toLowerCase()
    if (!key || seen.has(key)) return false
    seen.add(key)
    return true
  })
}

function verdict(match: ProfileMatch) {
  if (!match.eligible) return { tone: 'conflict', label: 'Conflicts with your profile' }
  if (match.mandatory_review) return { tone: 'check', label: 'Check eligibility first' }
  if (match.fit_tier === 'recommended') return { tone: 'strong', label: 'Strong match' }
  if (match.fit_tier === 'unlikely') return { tone: 'weak', label: 'Little overlap' }
  return { tone: 'possible', label: 'Possible match' }
}

const COUNT_LABELS: Record<string, string> = { duties: 'Duties', must: 'Must-haves', nice: 'Nice-to-haves' }

function FitSummary({ match, profileId }: { match?: ProfileMatch; profileId: number | null }) {
  if (profileId === null || !match) {
    return <aside className="job-fit job-fit-empty" aria-label="Fit with your profile">
      <p className="job-fit-verdict">See how this fits you</p>
      <p className="job-fit-note">Add a profile with your CV and Radius checks every duty and requirement against it.</p>
      <a className="job-fit-link" href="/profile">Create a profile</a>
    </aside>
  }
  if (match.pending) {
    return <aside className="job-fit job-fit-pending" aria-label="Fit with your profile" aria-busy="true">
      <p className="job-fit-verdict">{match.eligible ? 'Checking the fit…' : 'Conflicts with your profile'}</p>
      <p className="job-fit-note">{match.eligible
        ? 'The posting’s duties and requirements are being checked against your CV.'
        : 'A stated requirement or condition conflicts with your profile.'}</p>
      {!match.eligible && <FitConcerns items={concerns(match)} />}
    </aside>
  }
  const { tone, label } = verdict(match)
  const counts = fitCounts(match)
  const context = [match.job_context?.occupation, match.job_context?.industry].filter(Boolean).join(' · ')
  return <aside className={`job-fit job-fit-${tone}`} aria-label="Fit with your profile">
    <div className="job-fit-head">
      <p className="job-fit-score" aria-label={`Match score ${match.score} of 100`}><strong>{match.score}</strong><span aria-hidden="true">/100</span></p>
      <div>
        <p className="job-fit-verdict"><span className="job-fit-dot" aria-hidden="true" />{label}</p>
        {context && <p className="job-fit-context">{context}</p>}
      </div>
    </div>
    {counts.length > 0 && <ul className="job-fit-counts" aria-label="Shown in your CV">
      {counts.map(row => <li key={row.signal}>
        <span>{COUNT_LABELS[row.signal] ?? row.label}</span>
        <span className="job-fit-meter" aria-hidden="true"><span style={{ width: `${Math.round((row.supported / Math.max(1, row.total)) * 100)}%` }} /></span>
        <strong>{row.supported} of {row.total}</strong>
      </li>)}
    </ul>}
    <FitConcerns items={concerns(match)} />
    <a className="job-fit-link" href="#job-score-details">How this score was built</a>
  </aside>
}

function FitConcerns({ items }: { items: Concern[] }) {
  if (!items.length) return null
  const shown = items.slice(0, 4)
  return <div className="job-fit-concerns">
    <p>Check before applying</p>
    <ul>{shown.map(item => <li key={item.text} className={`job-fit-concern-${item.tone}`}>
      <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
        {item.tone === 'conflict'
          ? <path d="M4.5 4.5 11.5 11.5M11.5 4.5 4.5 11.5" />
          : <><circle cx="8" cy="8" r="6" /><path d="M8 4.8v3.6M8 10.9v.3" /></>}
      </svg>
      <span>{item.text}</span>
    </li>)}</ul>
    {items.length > shown.length && <p className="job-fit-more">{items.length - shown.length} more in the score details</p>}
  </div>
}

function Fact({ label, children }: { label: string; children: ReactNode }) {
  if (children === null || children === undefined || children === '') return null
  return <div className="job-fact"><dt>{label}</dt><dd>{children}</dd></div>
}

function KeyFacts({ job }: { job: Job }) {
  const languages = job.enrichment.languages.map(row => `${row.value}${languageRequirementLabel(row)}`).join(' · ')
  const years = job.enrichment.experience_years?.value
  const experience = [years != null ? `${years}+ ${years === 1 ? 'year' : 'years'}` : '', sentenceCase(job.seniority)]
    .filter(Boolean).join(' · ')
  const employment = [sentenceCase(job.employment_type), sentenceCase(job.schedule)].filter(Boolean).join(' · ')
  const employerType = job.raw_metadata.employer_type === 'agency' ? 'Recruitment agency' : ''
  const votes = [...new Map(job.locations.map(location => location.city_election_result)
    .filter((result): result is CityElectionResult => result != null && result.postal_ballots_included)
    .map(result => [result.municipality_code, result])).values()]
    .map(result => `${displayLeadingParty(result.leading_party)} ${result.leading_party_vote_share_percent.toFixed(1)}% (${result.municipality.split(',', 1)[0].trim()})`)
    .join('; ')
  const locations = job.locations.length ? job.locations.map(item => item.label) : job.location_text ? [job.location_text] : []
  const deadline = job.valid_through ? `${formatDate(job.valid_through)}${job.expired ? ' · passed' : ''}` : ''
  return <dl className="job-facts">
    <Fact label="Location">{locations.length ? locations.join(' · ') : null}</Fact>
    <Fact label="Work style">{workStyle(job)}</Fact>
    <Fact label="Salary">{job.salary?.trim()}</Fact>
    <Fact label="Employment">{employment}</Fact>
    <Fact label="Experience">{experience}</Fact>
    <Fact label="Languages">{languages}</Fact>
    <Fact label="Remote from">{job.remote_country_codes?.length ? job.remote_country_codes.join(', ') : ''}</Fact>
    <Fact label="Team">{job.department?.trim()}</Fact>
    <Fact label="Posted">{postedLabel(job.date_posted)}</Fact>
    <Fact label="Apply by">{deadline}</Fact>
    <Fact label="Employer">{employerType}</Fact>
    <Fact label="Local vote">{votes}</Fact>
  </dl>
}

export default function JobPage({ job, profileId, onApplicationSaved }: {
  job: Job
  profileId: number | null
  onApplicationSaved: (application: Application) => void
}) {
  const [trackingError, setTrackingError] = useState('')
  const linkCompany = !job.company_name_is_employer || job.employer_company_id != null
  const match = job.profile_match
  const placeLine = [
    job.locations[0]?.label ?? job.location_text ?? '',
    job.locations.length > 1 ? `+${job.locations.length - 1} more` : '',
  ].filter(Boolean).join(' ')
  const style = workStyle(job)
  const showChecklists = !!match && !match.pending && (
    (match.duty_evidence?.duties.length ?? 0) > 0 || (match.requirement_observations ?? []).some(row => row.strength))
  async function markOriginalPostOpened() {
    if (profileId === null) return
    setTrackingError('')
    try {
      const application = await trackJobInteraction(API_BASE_URL, profileId, job.id, job.application, 'open_post')
      if (application) onApplicationSaved(application)
    } catch {
      setTrackingError('Could not update this role’s stage. You can set it below.')
    }
  }
  return <article className="job-page">
    <header className="job-page-header">
      <div className="job-page-title">
        <h1>{job.title}</h1>
        <p className="job-page-org">
          {linkCompany ? <a href={`/companies/${job.company_id}${profileId !== null ? `?profile_id=${profileId}` : ''}`}>{job.company_name}</a>
            : <span>{job.company_name}</span>}
          {placeLine && <span>{placeLine}</span>}
          {style && <span>{style}</span>}
        </p>
        <div className="job-page-actions">
          <a className="job-page-primary" href={applicationStartLink(job.id, profileId)}>Start application</a>
          <a className="job-page-secondary" href={job.url} target="_blank" rel="noreferrer" onClick={() => { void markOriginalPostOpened() }}>
            Original post
            <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true"><path d="M6 3.5H3.5v9h9V10M9 3.5h3.5V7M12.5 3.5 7 9" /></svg>
            <span className="visually-hidden"> (opens in a new tab)</span>
          </a>
        </div>
        {trackingError && <p className="tracking-error-note" role="status">{trackingError}</p>}
      </div>
      <FitSummary match={match} profileId={profileId} />
    </header>
    {!job.is_active && <p className="job-page-closed" role="status">
      <strong>Closed</strong> This posting closed {job.closed_at ? `on ${formatDate(job.closed_at)}` : '(date unknown)'}. Details stay here for reference.
    </p>}
    <KeyFacts job={job} />
    <div className="job-page-body">
      <div className="job-page-main">
        {showChecklists ? <section className="job-page-section" aria-labelledby="job-fit-checklists">
          <h2 id="job-fit-checklists">Requirements against your CV</h2>
          <FitChecklists match={match!} />
        </section> : <JobPostingAnalysis state={job.analysis_state} analysis={job.analysis} refreshing={job.analysis_refreshing} />}
        {job.description && <JobDescription blocks={job.description_blocks} text={job.description} />}
        {match && !match.pending && <details className="job-score-details" id="job-score-details">
          <summary>How this score was built</summary>
          <JobEvidence enrichment={job.enrichment} match={match} embedded />
        </details>}
      </div>
      <div className="job-page-aside">
        <ApplicationStageActions jobId={job.id} profileId={profileId} application={job.application} onSaved={onApplicationSaved} />
      </div>
    </div>
  </article>
}

function ApplicationStageActions({ jobId, profileId, application, onSaved }: {
  jobId: number; profileId: number | null; application?: Application | null
  onSaved: (application: Application) => void
}) {
  const [savingStatus, setSavingStatus] = useState<ApplicationStatus | null>(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const currentStatus = application?.status ?? 'new'
  const options = nextApplicationStages[currentStatus]
  const previousStatus = application?.previous_status && application.previous_status !== currentStatus
    ? application.previous_status : null
  const previousStatusLabel = previousStatus
    ? applicationStatuses.find(item => item.value === previousStatus)?.label : null

  async function choose(status: ApplicationStatus, undo = false) {
    if (profileId === null || savingStatus !== null) return
    setSavingStatus(status); setError(''); setNotice('')
    try {
      const saved = await saveApplication(API_BASE_URL, profileId, jobId, status)
      onSaved(saved)
      const label = applicationStatuses.find(item => item.value === status)?.label
      setNotice(undo ? `Undid the last change · back to ${label}.` : `Moved to ${label}.`)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not update this application stage.')
    } finally { setSavingStatus(null) }
  }

  return <ApplicationSwipeCard as="section" className="application-stage-actions" application={application}
    disabled={profileId === null || savingStatus !== null}
    onAction={action => { void choose(action.status, action.kind === 'undo') }}>
    {profileId === null ? <>
      <div><h2>Track this role</h2><p className="muted">Create a profile to move this role through your journey.</p></div>
      <a className="page-link" href="/profile">Create a profile →</a>
    </> : <>
      <div className="application-stage-heading">
        <h2><span className="application-stage-label">Your stage</span> {applicationStatuses.find(item => item.value === currentStatus)?.label ?? 'New'}</h2>
      </div>
      {options.length > 0 && <div className="application-stage-buttons" role="group" aria-label="Next application stage">
        {options.map(status => <button key={status} type="button" className={`application-stage-button stage-${status}`}
          disabled={savingStatus !== null} onClick={() => void choose(status)}>
          {savingStatus === status ? 'Saving…' : applicationStatuses.find(item => item.value === status)?.label}
        </button>)}
      </div>}
      {previousStatus && <footer className="application-stage-footer">
        <button type="button" data-swipe-undo className="application-undo-button" disabled={savingStatus !== null}
          onClick={() => void choose(previousStatus, true)}>
          {savingStatus === previousStatus ? 'Undoing…' : `↶ Undo to ${previousStatusLabel}`}
        </button>
        <span className="application-undo-hint">Swipe this control to undo</span>
      </footer>}
      {!options.length && !previousStatus && <p className="application-stage-complete">This application is at its final stage.</p>}
      {notice && <p className="application-save-state" role="status">{notice}</p>}
      {error && <p className="application-error" role="alert">{error}</p>}
    </>}
  </ApplicationSwipeCard>
}
