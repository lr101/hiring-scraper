import { memo } from 'react'
import ApplicationSwipeCard, { ApplicationStateBadge } from './ApplicationSwipeCard'
import JobOverviewTags from './JobOverviewTags'
import { applicationStartLink } from './applicationHandoff'
import { applicationStatuses, nextApplicationStages } from './applications'
import type { Application, ApplicationSwipeAction, ApplicationStatus } from './applications'
import { languageRequirementLabel } from './JobEvidence'
import type { Enrichment, ProfileMatch } from './JobEvidence'

type JobCardData = {
  id: number
  title: string
  company_name: string
  department?: string | null
  location_text: string | null
  salary?: string | null
  employment_type?: string | null
  schedule?: string | null
  seniority?: string | null
  work_arrangement?: string | null
  is_remote?: boolean
  url?: string | null
  is_active?: boolean
  closed_at?: string | null
  expired?: boolean
  last_seen_at?: string | null
  first_seen_at?: string | null
  application?: Application | null
  enrichment?: Pick<Enrichment, 'languages' | 'experience_years'>
  profile_match?: Pick<ProfileMatch, 'matched_skills' | 'score' | 'fit_tier'>
}

function formatDate(value?: string | null) {
  if (!value) return 'Unknown'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? 'Unknown' : date.toLocaleDateString('en', { day: 'numeric', month: 'short', year: 'numeric' })
}

const stageActionCopy: Partial<Record<ApplicationStatus, string>> = {
  open: 'Mark interested',
  in_gots_hand: 'In GOD\'s hand',
  not_interested: 'Skip role',
  waiting_for_reply: 'Mark applied',
  interview: 'Log interview',
  rejected: 'Log rejection',
  accepted: 'Log offer',
}

function JobFeedCard({
  job, href, profileId, profileName, updatedAt, disabled = false, saving = false, pendingStatus, onAction, onOpenOriginal, showStartApplication = true,
}: {
  job: JobCardData
  href: string
  profileId?: number
  profileName?: string
  updatedAt?: string | null
  disabled?: boolean
  saving?: boolean
  pendingStatus?: ApplicationStatus
  onAction: (jobId: number, profileId: number | undefined, action: ApplicationSwipeAction) => void
  onOpenOriginal?: (jobId: number, profileId: number | undefined) => void
  showStartApplication?: boolean
}) {
  const application = pendingStatus && job.application
    ? { ...job.application, previous_status: job.application.status, status: pendingStatus }
    : job.application
  const status = application?.status ?? (profileId ? 'new' : null)
  const nextMoves = status ? nextApplicationStages[status] : []
  const previousStatus: ApplicationStatus | null = application?.previous_status && application.previous_status !== application.status
    ? application.previous_status : null
  const matchingSkills = job.profile_match?.matched_skills ?? []
  const languageRequirements = job.enrichment?.languages.map(language =>
    `${language.value}${languageRequirementLabel(language)}`,
  ).join(' · ')
  const matchTier = job.profile_match?.fit_tier ?? (job.profile_match && job.profile_match.score < 30
    ? 'unlikely' : job.profile_match && job.profile_match.score >= 65 ? 'recommended' : 'possible')
  const lastUpdated = updatedAt ?? job.application?.updated_at ?? job.last_seen_at ?? job.first_seen_at

  return <ApplicationSwipeCard application={application} disabled={disabled}
    className={`application-job-card status-${status ?? 'untracked'} ${saving ? 'swipe-saving' : ''}`}
    onAction={action => onAction(job.id, profileId, action)}>
    <header className="application-job-card-header">
      <div className="application-role">
        <a className="application-job-title" href={href}>{job.title}</a>
        <small className="application-company-name">{job.company_name}{job.department ? ` · ${job.department}` : ''}</small>
        {(job.expired || job.is_active === false) && <small className="application-closed">{job.is_active === false
          ? `Posting closed${job.closed_at ? ` · ${formatDate(job.closed_at)}` : ''}` : 'Posting expired'}</small>}
      </div>
      <div className="application-card-state">
        <ApplicationStateBadge application={application} fallbackStatus={profileId ? 'new' : undefined} />
        {job.profile_match && <span className={`application-card-match-score match-${matchTier}`}
          aria-label={`${job.profile_match.score} percent match${matchTier === 'recommended' ? ', strong' : matchTier === 'unlikely' ? ', low' : ', possible'}`}>
          {job.profile_match.score}% match
        </span>}
        {profileName && <span className="application-card-profile-name">{profileName}</span>}
      </div>
    </header>
    <div className="application-job-facts">
      <span><small>Work location</small>{job.location_text || 'Location not listed'}</span>
      <span><small>Last updated</small>{formatDate(lastUpdated)}</span>
    </div>
    <JobOverviewTags job={job} />
    {showStartApplication && <a className="job-prepare-link" href={applicationStartLink(job.id, profileId)}>Start application <span aria-hidden="true">↗</span></a>}
    {profileId !== undefined && <p className="application-matching-skills"><strong>Matching skills:</strong> {matchingSkills.length ? matchingSkills.join(' · ') : 'None identified yet'}</p>}
    {languageRequirements && <p className="application-language-requirements"><strong>Language requirements:</strong> {languageRequirements}</p>}
    {nextMoves.length > 0 && profileId !== undefined && <div className="application-quick-moves" aria-label="Move this application to its next state">
      <span className="application-quick-moves-label">Next move</span>
      {nextMoves.map(nextStatus => <button type="button" key={nextStatus}
        className={`application-quick-move move-${nextStatus}`} disabled={disabled}
        onClick={() => onAction(job.id, profileId, { kind: 'stage', status: nextStatus })}>
        <span aria-hidden="true">{nextStatus === 'not_interested' || nextStatus === 'rejected' ? '↘' : '↗'}</span>
        {stageActionCopy[nextStatus] ?? `Move to ${applicationStatuses.find(item => item.value === nextStatus)?.label}`}
      </button>)}
    </div>}
    {status === 'accepted' && <div className="application-outcome-banner"><span aria-hidden="true">★</span> Offer logged · checkpoint complete</div>}
    {(previousStatus || (job.url && onOpenOriginal)) && <footer className="application-card-footer application-job-card-footer">
      {previousStatus && <>
        <button type="button" data-swipe-undo className="application-undo-button application-card-undo"
          disabled={disabled} onClick={() => onAction(job.id, profileId, { kind: 'undo', status: previousStatus })}>
          {saving ? 'Undoing…' : `↶ Undo to ${applicationStatuses.find(item => item.value === previousStatus)?.label}`}
        </button>
        <span className="application-undo-hint">Swipe this control to undo</span>
      </>}
      {job.url && onOpenOriginal && <a className="job-original-link" href={job.url} target="_blank" rel="noreferrer"
        onClick={() => onOpenOriginal(job.id, profileId)}>
        Open original post <span aria-hidden="true">↗</span>
      </a>}
    </footer>}
  </ApplicationSwipeCard>
}

export default memo(JobFeedCard)
