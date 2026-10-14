import ApplicationSwipeCard, { ApplicationStateBadge } from './ApplicationSwipeCard'
import JobOverviewTags from './JobOverviewTags'
import { applicationStatuses } from './applications'
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

export default function JobFeedCard({
  job, href, profileId, profileName, updatedAt, disabled = false, saving = false, onAction, onOpenOriginal,
}: {
  job: JobCardData
  href: string
  profileId?: number
  profileName?: string
  updatedAt?: string | null
  disabled?: boolean
  saving?: boolean
  onAction: (action: ApplicationSwipeAction) => void
  onOpenOriginal?: () => void
}) {
  const status = job.application?.status ?? (profileId ? 'new' : null)
  const previousStatus: ApplicationStatus | null = job.application?.previous_status && job.application.previous_status !== job.application.status
    ? job.application.previous_status : null
  const matchingSkills = job.profile_match?.matched_skills ?? []
  const languageRequirements = job.enrichment?.languages.map(language =>
    `${language.value}${languageRequirementLabel(language)}`,
  ).join(' · ')
  const matchTier = job.profile_match?.fit_tier ?? (job.profile_match && job.profile_match.score < 30
    ? 'unlikely' : job.profile_match && job.profile_match.score >= 65 ? 'recommended' : 'possible')
  const lastUpdated = updatedAt ?? job.application?.updated_at ?? job.last_seen_at ?? job.first_seen_at

  return <ApplicationSwipeCard application={job.application} disabled={disabled}
    className={`application-job-card status-${status ?? 'untracked'} ${saving ? 'swipe-saving' : ''}`} onAction={onAction}>
    <header className="application-job-card-header">
      <div className="application-role">
        <a className="application-job-title" href={href}>{job.title}</a>
        <small className="application-company-name">{job.company_name}{job.department ? ` · ${job.department}` : ''}</small>
        {(job.expired || job.is_active === false) && <small className="application-closed">{job.expired ? 'Posting expired' : 'Posting closed'}</small>}
      </div>
      <div className="application-card-state">
        <ApplicationStateBadge application={job.application} fallbackStatus={profileId ? 'new' : undefined} />
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
    {profileId !== undefined && <p className="application-matching-skills"><strong>Matching skills:</strong> {matchingSkills.length ? matchingSkills.join(' · ') : 'None identified yet'}</p>}
    {languageRequirements && <p className="application-language-requirements"><strong>Language requirements:</strong> {languageRequirements}</p>}
    {(previousStatus || (job.url && onOpenOriginal)) && <footer className="application-card-footer application-job-card-footer">
      {previousStatus && <>
        <button type="button" data-swipe-undo className="application-undo-button application-card-undo"
          disabled={disabled} onClick={() => onAction({ kind: 'undo', status: previousStatus })}>
          {saving ? 'Undoing…' : `↶ Undo to ${applicationStatuses.find(item => item.value === previousStatus)?.label}`}
        </button>
        <span className="application-undo-hint">Swipe this control to undo</span>
      </>}
      {job.url && onOpenOriginal && <a className="job-original-link" href={job.url} target="_blank" rel="noreferrer" onClick={onOpenOriginal}>
        Open original post <span aria-hidden="true">↗</span>
      </a>}
    </footer>}
  </ApplicationSwipeCard>
}
