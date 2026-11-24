import type { CityElectionResult } from './profile'
import ScoreBreakdown from './ScoreBreakdown'

export type ScoreContribution = {
  signal: string; contribution: number; detail: string
  max_points?: number | null; supported?: number | null; total?: number | null
  job_quote?: string; profile_source?: string; profile_quote?: string
}

export type ProfileMatch = {
  calibration?: { raw: number; calibrated: number; version: string }
  explanation?: ScoreContribution[]
  language_review?: boolean
  prerequisite_conditions?: {
    kind: string; status: string; reason: string
    job_quote?: string; profile_source?: string; profile_quote?: string
  }[]
  mandatory_review?: boolean
  task_fit?: number
  requirement_observations?: {
    kind: string; requirement: string; status: string; job_quote: string
    evidence_source: string; evidence_quote: string; modality?: string; strength?: 'direct' | 'partial' | 'none'
  }[]
  duty_evidence?: { score: number | null; coverage: number; duties: {
    job_quote: string; status: 'direct' | 'transferable' | 'unverified'; evidence_source: string
    evidence_quote: string; evidence_reference?: string; evidence_context?: string
  }[] }
  gates?: Record<string, { status: string; reason: string }>
  truncated?: boolean
  fit_tier?: 'recommended' | 'possible' | 'unlikely'; requirement_gaps?: string[]; refreshing?: boolean
  pending?: boolean; job_context?: { occupation: string; industry: string }
  score: number; eligible: boolean; uncertain: boolean; label: string
  custom_skill_evidence?: { name: string; evidence: string; source: string }[]
  local_context?: {
    city_election_results: CityElectionResult[]; average_afd_vote_share_percent: number | null
    score_adjustment: number; weight: number
  }
  matched_skills: string[]; missing_skills: string[]; reasons: string[]; conflicts: string[]; soft_conflicts?: string[]; unknowns: string[]
}
export type Enrichment = {
  version: string; quality: string; description_chars: number; warnings: string[]
  skills: { name: string; evidence: string; source: string }[]
  seniority: { value: string; evidence: string; source: string } | null
  experience_years: { value: number; evidence: string; source: string } | null
  languages: {
    value: string; evidence: string; source: string; level?: string | null; kind?: string
    level_range?: string[]; level_inferred?: boolean; modality?: 'written' | 'spoken' | 'written_and_spoken'
    alternative_group?: string
  }[]
}

export type AnalysisState = 'ready' | 'outdated' | 'pending' | 'failed' | 'unavailable'
type PostingQuote = { line: number; text: string }
type PostingRequirement = PostingQuote & { need: 'must' | 'nice'; kind: string }
export type PostingAnalysis = {
  occupation: string
  industry: string
  duties: PostingQuote[]
  requirements: PostingRequirement[]
}

const requirementKinds: Record<string, string> = {
  skill: 'Skill',
  knowledge: 'Knowledge',
  experience: 'Experience',
  education: 'Education',
  certification: 'Certification',
  language: 'Language',
  authorization: 'Work authorization',
  other: 'Other requirement',
}
const requirementGroups = [
  { need: 'must', label: 'Must-haves' },
  { need: 'nice', label: 'Nice-to-haves' },
] as const

export function JobPostingAnalysis({ state, analysis, refreshing }: {
  state?: AnalysisState; analysis?: PostingAnalysis | null; refreshing?: boolean
}) {
  return <section className="job-posting-analysis" aria-labelledby="job-posting-analysis-heading">
    <h2 id="job-posting-analysis-heading">What this job asks for</h2>
    {state === 'pending' ? <span className="application-match-refresh-tag" role="status">Reading this posting…</span>
      : state === 'failed' ? <p className="muted">Could not read this posting.</p>
      : analysis ? <>
        {state === 'outdated' && <p className="muted" role="status">
          {refreshing ? 'Earlier analysis — updating…' : 'Earlier analysis'}
        </p>}
        {(analysis.occupation || analysis.industry) && <div className="job-overview-tags">
          {analysis.occupation && <span className="job-overview-tag">Field · {analysis.occupation}</span>}
          {analysis.industry && <span className="job-overview-tag">Industry · {analysis.industry}</span>}
        </div>}
        {analysis.duties.length > 0 && <>
          <h3>Duties</h3>
          <ul>{analysis.duties.map(row => <li key={row.text}><q>{row.text}</q></li>)}</ul>
        </>}
        {requirementGroups.map(({ need, label }) => {
          const rows = analysis.requirements.filter(row => row.need === need)
          return rows.length > 0 && <div key={need}>
            <h3>{label}</h3>
            <ul>{rows.map(row => <li key={row.text}>
              <strong>{requirementKinds[row.kind] ?? requirementKinds.other}</strong>{' '}
              <q>{row.text}</q>
            </li>)}</ul>
          </div>
        })}
        {analysis.duties.length === 0 && analysis.requirements.length === 0 &&
          <p className="muted">No duties or requirements could be quoted from this posting.</p>}
      </> : <p className="muted">Posting analysis is unavailable.</p>}
  </section>
}

export default function JobEvidence({ enrichment, match }: { enrichment: Enrichment; match?: ProfileMatch }) {
  return <section className="detail-panel evidence-panel">
    <p className="eyebrow">How this job fits</p>
    {match?.pending ? <>
      <h2>{match.eligible ? 'Analysing this job' : 'Requirement or preference conflict'}</h2>
      <p className="muted">{match.eligible
        ? 'A local model is reading the posting’s duties and requirements and checking them against your CV. The match appears here and in Journey once that finishes.'
        : 'This posting states a requirement or condition that conflicts with your profile, so it stays out of Journey. The local model has not read the rest of it yet.'}</p>
      {match.conflicts.length > 0 && <div className="error-banner"><strong>Requirements or preferences that conflict</strong><ul>{match.conflicts.map(reason => <li key={reason}>{reason}</li>)}</ul></div>}
      {match.prerequisite_conditions?.filter(row => row.status === 'fail' && row.job_quote).map((row, index) =>
        <div className="evidence-note" key={`${row.kind}:${index}`}>
          <strong>{row.reason}</strong>
          <p>Job <q>{row.job_quote}</q></p>
          {row.profile_quote && <p>Saved evidence <q>{row.profile_quote}</q></p>}
        </div>)}
    </> : match ? <>
      {match.job_context && (match.job_context.occupation || match.job_context.industry) && <p className="eyebrow">
        {[match.job_context.occupation, match.job_context.industry].filter(Boolean).join(' · ')}</p>}
      <h2>{!match.eligible ? 'Requirement or preference conflict' : match.mandatory_review ? 'Verify mandatory eligibility' : match.fit_tier === 'recommended' ? 'Recommended match' : match.fit_tier === 'unlikely' ? 'Little match evidence' : 'Possible lead'}</h2>
      <ScoreBreakdown match={match} />
      {match.language_review && <p className="evidence-note">Verify language proficiency against the stated requirement.</p>}
      {match.mandatory_review && <p className="evidence-note">A mandatory eligibility requirement is unverified. The match score does not clear that requirement.</p>}
      {match.gates && <>
        <h3>Eligibility and location checks</h3>
        <dl>{Object.entries(match.gates).map(([name, gate]) => <div key={name}><dt>{name} · {gate.status}</dt><dd>{gate.reason}</dd></div>)}</dl>
      </>}
      {match.prerequisite_conditions?.filter(row => row.status !== 'pass' && row.job_quote).map((row, index) =>
        <div className="evidence-note" key={`${row.kind}:${index}`}>
          <strong>{row.reason}</strong>
          <p>Job <q>{row.job_quote}</q></p>
          {row.profile_quote && <p>Saved evidence <q>{row.profile_quote}</q></p>}
        </div>)}
      {!match.calibration && match.local_context && match.local_context.score_adjustment !== 0 && <p className="muted">Location preference changed this match by {match.local_context.score_adjustment} points.</p>}
      {!match.calibration && match.reasons.length > 0 && <ul>{match.reasons.map(reason => <li key={reason}>{reason}</li>)}</ul>}
      {match.conflicts.length > 0 && <div className="error-banner"><strong>Requirements or preferences that conflict</strong><ul>{match.conflicts.map(reason => <li key={reason}>{reason}</li>)}</ul></div>}
      {(match.soft_conflicts?.length ?? 0) > 0 && <div className="evidence-note"><strong>Experience to verify</strong><ul>{match.soft_conflicts!.map(reason => <li key={reason}>{reason}</li>)}</ul></div>}
      {(match.requirement_gaps?.length ?? 0) > 0 && !match.requirement_observations?.some(row => row.strength) && <div className="evidence-note"><strong>Qualification gaps to check</strong><ul>{match.requirement_gaps!.map(gap => <li key={gap}>{gap}</li>)}</ul></div>}
      {match.unknowns.length > 0 && <div className="evidence-note"><strong>Still needs checking</strong><ul>{match.unknowns.map(reason => <li key={reason}>{reason}</li>)}</ul></div>}
      {match.missing_skills.length > 0 && <p>Other skills mentioned in the post: {match.missing_skills.join(', ')}. Mentions can include optional skills.</p>}
    </> : <><h2>Profile matching</h2><p className="muted"><a href="/profile">Create a profile</a> to rank jobs by your interests.</p></>}
    {enrichment.seniority && <p><strong>Experience level:</strong> {enrichment.seniority.value} · {enrichment.seniority.evidence}</p>}
    {enrichment.experience_years && <p><strong>Minimum experience:</strong> {enrichment.experience_years.value} years · {enrichment.experience_years.evidence}</p>}
    {enrichment.languages.length > 0 && <>
      <h3>Language requirements</h3>
      <div className="skill-evidence-list">{enrichment.languages.map(row => <div key={`${row.value}:${row.alternative_group ?? ''}`}>
        <strong>{row.value}{languageRequirementLabel(row)}</strong>
        <p>{row.evidence}</p>
        {row.level_inferred && <small>CEFR range estimated from wording</small>}
        {row.modality && <small> · {languageModalityLabel(row.modality)}</small>}
        <small>{(row.source.startsWith('structured') || row.source.startsWith('detail')) ? 'Employer’s structured job data' : 'Job description'}</small>
      </div>)}</div>
    </>}
    {enrichment.warnings.length > 0 && <div className="evidence-note"><strong>Information gaps</strong><ul>{enrichment.warnings.map(warning => <li key={warning}>{warning}</li>)}</ul></div>}
  </section>
}

export function languageRequirementLabel(row: Enrichment['languages'][number]) {
  const level = row.level_inferred && row.level_range?.length === 2
    ? `${row.level_range[0]}–${row.level_range[1]} estimated`
    : row.level ? (row.level === 'NATIVE' ? 'native' : row.level)
      : row.kind === 'fluency' ? 'fluency' : ''
  return `${level ? ` · ${level}` : ''}${row.modality ? ` · ${languageModalityLabel(row.modality)}` : ''}`
}

function languageModalityLabel(modality: NonNullable<Enrichment['languages'][number]['modality']>) {
  return modality === 'written_and_spoken' ? 'written and spoken' : modality
}
