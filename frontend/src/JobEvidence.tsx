import type { CityElectionResult } from './profile'

export type ProfileMatch = {
  mandatory_review?: boolean
  task_fit?: number
  duty_evidence?: { score: number | null; coverage: number; duties: {
    job_quote: string; status: 'direct' | 'transferable' | 'unverified'; evidence_source: string
    evidence_quote: string; evidence_reference?: string; evidence_context?: string
  }[] }
  gates?: Record<string, { status: string; reason: string }>
  fit_tier?: 'recommended' | 'possible' | 'unlikely'; requirement_gaps?: string[]; refreshing?: boolean
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

function ScoreGraphic({ match }: { match: ProfileMatch }) {
  const rows = [
    { label: 'Task fit', value: match.task_fit, tone: 'task' },
    { label: 'Match score', value: match.score, tone: 'final' },
  ]
  return <figure className="score-graphic" aria-label="Job score breakdown">
    <figcaption>Your match breakdown</figcaption>
    <div className="score-scale" aria-hidden="true"><span>0</span><span>50</span><span>100</span></div>
    {rows.map(row => {
      const value = row.value != null && Number.isFinite(row.value) ? Math.max(0, Math.min(100, row.value)) : null
      return <div className={`score-row score-row-${row.tone}`} key={row.label}>
        <div className="score-row-label"><span>{row.label}</span><strong>{value == null ? 'Unknown' : `${value}/100`}</strong></div>
        {value == null ? <div className="score-track score-track-unknown" aria-hidden="true" /> :
          <div className="score-track" role="meter" aria-label={row.label} aria-valuenow={value} aria-valuemin={0} aria-valuemax={100}>
            <span style={{ width: `${value}%` }} />
          </div>}
      </div>
    })}
    <p>The match score accounts for task fit, qualifications and preferences. Review the checks below for unresolved requirements.</p>
  </figure>
}

export default function JobEvidence({ enrichment, match }: { enrichment: Enrichment; match?: ProfileMatch }) {
  const skillEvidence = [...enrichment.skills, ...(match?.custom_skill_evidence ?? [])]
  return <section className="detail-panel evidence-panel">
    <p className="eyebrow">How this job fits</p>
    {match ? <>
      <h2>{!match.eligible ? 'Requirement or preference conflict' : match.mandatory_review ? 'Verify mandatory eligibility' : match.fit_tier === 'recommended' ? 'Recommended match' : match.fit_tier === 'unlikely' ? 'Little match evidence' : 'Possible lead'} · {match.score}/100</h2>
      <p className="muted">Match score based on saved task, skill and requirement evidence. Shared tools may be incidental; verify the main work and mandatory requirements before applying. This is not a prediction of hiring success.</p>
      <ScoreGraphic match={match} />
      {match.mandatory_review && <p className="evidence-note">A mandatory eligibility requirement is unverified. The qualification score does not clear that requirement.</p>}
      {match.task_fit != null && <>
        <p><strong>Task fit before mandatory requirements and preferences:</strong> {match.task_fit}/100. The match score above also accounts for unresolved qualifications and constraints.</p>
      </>}
      {match.duty_evidence?.score != null && <details>
        <summary>Documented duty fit · {match.duty_evidence.score}/100</summary>
        <p>{Math.round(match.duty_evidence.coverage * 100)}% of extracted duties have some supporting evidence. This score compares work performed. Eligibility and preferences have separate checks.</p>
        {match.duty_evidence.duties.map((duty, index) => <div className="evidence-note" key={index}>
          <strong>{duty.status}{duty.evidence_context && duty.evidence_context !== 'unspecified' ? ' · ' + duty.evidence_context : ''}</strong>
          <p>Job <q>{duty.job_quote}</q></p>
          {duty.evidence_quote && <p>Saved evidence ({duty.evidence_reference || duty.evidence_source}) <q>{duty.evidence_quote}</q></p>}
        </div>)}
      </details>}
      {match.gates && <>
        <h3>Eligibility and location checks</h3>
        <dl>{Object.entries(match.gates).map(([name, gate]) => <div key={name}><dt>{name} · {gate.status}</dt><dd>{gate.reason}</dd></div>)}</dl>
      </>}
      {match.local_context && match.local_context.score_adjustment !== 0 && <p className="muted">Location preference changed this match by {match.local_context.score_adjustment} points.</p>}
      {match.reasons.length > 0 && <ul>{match.reasons.map(reason => <li key={reason}>{reason}</li>)}</ul>}
      {match.conflicts.length > 0 && <div className="error-banner"><strong>Requirements or preferences that conflict</strong><ul>{match.conflicts.map(reason => <li key={reason}>{reason}</li>)}</ul></div>}
      {(match.soft_conflicts?.length ?? 0) > 0 && <div className="evidence-note"><strong>Experience to verify</strong><ul>{match.soft_conflicts!.map(reason => <li key={reason}>{reason}</li>)}</ul></div>}
      {(match.requirement_gaps?.length ?? 0) > 0 && <div className="evidence-note"><strong>Qualification gaps to check</strong><ul>{match.requirement_gaps!.map(gap => <li key={gap}>{gap}</li>)}</ul></div>}
      {match.unknowns.length > 0 && <div className="evidence-note"><strong>Still needs checking</strong><ul>{match.unknowns.map(reason => <li key={reason}>{reason}</li>)}</ul></div>}
      {match.missing_skills.length > 0 && <p>Other skills mentioned in the post: {match.missing_skills.join(', ')}. Mentions can include optional skills.</p>}
    </> : <><h2>Skills and requirements found</h2><p className="muted"><a href="/profile">Create a profile</a> to rank jobs by your interests.</p></>}
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
    <h3>Evidence from the job post</h3>
    {skillEvidence.length ? <div className="skill-evidence-list">{skillEvidence.map(skill => <div key={skill.name}>
      <strong>{skill.name}</strong><p>{skill.evidence}</p><small>{(skill.source.startsWith('structured') || skill.source.startsWith('detail')) ? 'Employer’s structured job data' : skill.source === 'title' ? 'Job title' : 'Job description'}</small>
    </div>)}</div> : <p className="muted">No skills recognized yet. Our first version has a limited vocabulary; this does not mean the job has no requirements.</p>}
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
