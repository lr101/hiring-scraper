import type { CityElectionResult } from './profile'

export type ProfileMatch = {
  score_source?: 'local' | 'llm'
  mandatory_review?: boolean
  task_fit?: number
  task_alignment?: { score: number | null; requirements: {
    job_quote: string; status: 'supported' | 'transferable' | 'unknown'; explanation: string
    evidence_source: string; evidence_quote: string; evidence_context: string
  }[] }
  gates?: Record<string, { status: string; reason: string }>
  llm_match?: {
    weighted_task_fit?: number | null
    dimensions?: Record<string, { score: number | null; evidence: string; evidence_source?: string; evidence_quote?: string }>
    weights?: Record<string, number>
    qualification_fit: number | null; preference_fit: number | null; dimension_completeness: number
    role_evidence_groups?: { importance: string; priority: string; job_quote: string; requirement_count: number; credit: number; weight: number }[]
    role_evidence_score: number; requirement_coverage: number | null; requirement_completeness: number
    requirements: { requirement: string; priority: 'required' | 'preferred' | 'logistics'; importance: 'core' | 'supporting'
      evidence_distance?: 'close' | 'adjacent'
      status: 'supported' | 'transferable' | 'missing' | 'unknown'; explanation: string
      job_source: string; job_quote: string; evidence_quote: string; evidence_source: string }[]
  } | null
  fit_tier?: 'recommended' | 'possible' | 'unlikely'; requirement_gaps?: string[]
  score: number; eligible: boolean; uncertain: boolean; label: string
  custom_skill_evidence?: { name: string; evidence: string; source: string }[]
  local_context?: {
    city_election_results: CityElectionResult[]; average_afd_vote_share_percent: number | null
    score_adjustment: number; weight: number
  }
  matched_skills: string[]; missing_skills: string[]; reasons: string[]; conflicts: string[]; unknowns: string[]
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
  const ai = match.score_source === 'llm' ? match.llm_match : null
  const rows = ai ? [
    { label: 'Task assessment', value: ai.weighted_task_fit, tone: 'task' },
    { label: 'Role evidence ceiling', value: ai.role_evidence_score, tone: 'ceiling' },
    { label: 'Final qualification score', value: match.score, tone: 'final' },
    { label: 'Preference fit', value: ai.preference_fit, tone: 'preference' },
  ] : [
    { label: 'Task fit', value: match.task_fit, tone: 'task' },
    { label: 'Local shortlist score', value: match.score, tone: 'final' },
  ]
  return <figure className="score-graphic" aria-label="Job score breakdown">
    <figcaption>{ai ? 'From task evidence to qualification fit' : 'Your local match estimate'}</figcaption>
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
    <p>{ai ? 'The task assessment is limited by the role evidence ceiling. Unverified academic scope can lower it further. Preference fit is separate.' :
      'The shortlist score accounts for task fit, qualifications and preferences. Review the checks below for unresolved requirements.'}</p>
  </figure>
}

export default function JobEvidence({ enrichment, match }: { enrichment: Enrichment; match?: ProfileMatch }) {
  const skillEvidence = [...enrichment.skills, ...(match?.custom_skill_evidence ?? [])]
  return <section className="detail-panel evidence-panel">
    <p className="eyebrow">How this job fits</p>
    {match ? <>
      <h2>{!match.eligible ? 'Requirement or preference conflict' : match.mandatory_review ? 'Verify mandatory eligibility' : match.fit_tier === 'recommended' ? 'Recommended match' : match.fit_tier === 'unlikely' ? 'Little match evidence' : 'Possible lead'} · {match.score}/100</h2>
      <p className="muted">{match.score_source === 'llm'
        ? 'AI qualification index based on your saved career evidence. Preferences and eligibility are assessed separately. It replaces the local estimate and is not a probability of getting hired.'
        : 'Provisional local shortlist estimate based on saved task and skill evidence. Shared tools may be incidental; verify the main work and mandatory requirements before applying. This is not a prediction of hiring success.'}</p>
      <ScoreGraphic match={match} />
      {match.mandatory_review && <p className="evidence-note">A mandatory eligibility requirement is unverified. The qualification score does not clear that requirement.</p>}
      {match.score_source !== 'llm' && match.task_fit != null && <>
        <p><strong>Task fit before mandatory requirements and preferences:</strong> {match.task_fit}/100. The shortlist score above also accounts for unresolved qualifications and constraints.</p>
      </>}
      {match.score_source !== 'llm' && (match.task_alignment?.requirements.length ?? 0) > 0 && <details>
        <summary>Task evidence behind the local estimate</summary>
        <p className="muted">These comparisons use saved actions and work products. Academic and adjacent work can show relevant capability without proving professional tenure or independent responsibility.</p>
        {match.task_alignment!.requirements.map((row, index) => <div className="evidence-note" key={index}>
          <strong>{row.status} · {row.evidence_context}</strong>
          <p>Job: <q>{row.job_quote}</q></p>
          <p>{row.explanation}</p>
          {row.evidence_quote && <p>Saved evidence ({row.evidence_source}): <q>{row.evidence_quote}</q></p>}
        </div>)}
      </details>}
      {match.score_source === 'llm' && match.llm_match && <>
        <h3>How your score was calculated</h3>
        {match.llm_match.weighted_task_fit != null && <p>
          Weighted skills and experience: <strong>{match.llm_match.weighted_task_fit}/100</strong>.
          {' '}Limited by role evidence: <strong>{match.llm_match.role_evidence_score}/100</strong>.
          {' '}Final score: <strong>{match.score}/100</strong>.
        </p>}
        {match.llm_match.dimensions && <dl>{Object.entries(match.llm_match.dimensions).map(([name, dimension]) => <div key={name}>
          <dt>{name.replaceAll('_', ' ')} · {dimension.score == null ? 'Unknown' : `${dimension.score}/100`}</dt>
          <dd>{dimension.evidence}{dimension.evidence_quote && <p>Saved evidence ({dimension.evidence_source}): <q>{dimension.evidence_quote}</q></p>}</dd>
        </div>)}</dl>}
        {match.llm_match.dimensions && match.llm_match.weights && <p className="muted">
          Task calculation: {['skills', 'experience'].filter(name => match.llm_match!.dimensions![name]?.score != null).map(name =>
            `${match.llm_match!.dimensions![name].score} × ${Math.round(match.llm_match!.weights![name] * 100)}%`).join(' + ') || 'No scored task dimensions'}.
          {' '}Divide by the total weight of the known task dimensions, then round. The role evidence ceiling can lower that result.
          {' '}Preferences are shown separately; language and eligibility checks may still require review.
        </p>}
        <h3>Match breakdown</h3>
        <dl>
          <dt>Qualification fit · role-relevant evidence</dt><dd>{match.llm_match.qualification_fit == null ? 'Unknown' : `${match.llm_match.qualification_fit}/100`}</dd>
          <dt>Preference fit · career direction and working style</dt><dd>{match.llm_match.preference_fit == null ? 'Unknown' : `${match.llm_match.preference_fit}/100`}</dd>
          <dt>Core-role evidence ceiling</dt><dd>{match.llm_match.role_evidence_score}/100</dd>
          <dt>Assessment detail completeness</dt><dd>{match.llm_match.dimension_completeness}% of the weighted assessment dimensions have evidence</dd>
          <dt>Requirement evidence completeness</dt><dd>{match.llm_match.requirement_completeness}% of assessed requirement weight has evidence</dd>
          <dt>Requirement coverage · known evidence only</dt><dd>{match.llm_match.requirement_coverage == null ? 'Unknown' : `${match.llm_match.requirement_coverage}/100`}</dd>
        </dl>
        <p className="muted">Core work controls 85% of the evidence ceiling (100% when there are no supporting requirements); supporting skills control at most 15%. Unknown requirements are excluded from coverage, so high coverage with low completeness needs checking. Preferred requirements have one quarter of the weight; close transfer earns 80% credit and adjacent transfer earns 50%. Quotes establish the source, not the correctness of the AI interpretation.</p>
        {match.llm_match.role_evidence_groups && <details>
          <summary>Role evidence calculation</summary>
          <p>Requirements quoted from the same employer clause share one weight within each priority and importance group. Splitting a duty into several rows does not multiply its credit or penalty.</p>
          {match.llm_match.role_evidence_groups.map((group, index) => <div className="evidence-note" key={index}>
            <q>{group.job_quote}</q>
            <p>{group.importance} · {group.priority} · {group.requirement_count} requirement rows · {(100 * group.credit).toFixed(0)}% evidence credit · weight {group.weight}</p>
          </div>)}
        </details>}
        <details>
          <summary>Requirements and saved evidence</summary>
          {match.llm_match.requirements.map((row, index) => <div className="evidence-note" key={index}>
            <strong>{row.requirement} · {row.priority} · {row.importance} · {row.status}{row.status === 'transferable' && row.evidence_distance === 'close' ? ' (close transfer)' : ''}</strong>
            <p>{row.explanation}</p>
            <p>Job ({row.job_source}): <q>{row.job_quote}</q></p>
            {row.evidence_quote && <p>Saved evidence ({row.evidence_source}): <q>{row.evidence_quote}</q></p>}
          </div>)}
        </details>
      </>}
      {match.gates && <>
        <h3>Eligibility and location checks</h3>
        <dl>{Object.entries(match.gates).map(([name, gate]) => <div key={name}><dt>{name} · {gate.status}</dt><dd>{gate.reason}</dd></div>)}</dl>
      </>}
      {match.score_source !== 'llm' && match.local_context && match.local_context.score_adjustment !== 0 && <p className="muted">Location preference changed this match by {match.local_context.score_adjustment} points.</p>}
      {match.reasons.length > 0 && <ul>{match.reasons.map(reason => <li key={reason}>{reason}</li>)}</ul>}
      {match.conflicts.length > 0 && <div className="error-banner"><strong>Requirements or preferences that conflict</strong><ul>{match.conflicts.map(reason => <li key={reason}>{reason}</li>)}</ul></div>}
      {(match.requirement_gaps?.length ?? 0) > 0 && <div className="evidence-note"><strong>Qualification gaps to check</strong><ul>{match.requirement_gaps!.map(gap => <li key={gap}>{gap}</li>)}</ul></div>}
      {match.unknowns.length > 0 && <div className="evidence-note"><strong>Still needs checking</strong><ul>{match.unknowns.map(reason => <li key={reason}>{reason}</li>)}</ul></div>}
      {match.score_source !== 'llm' && match.missing_skills.length > 0 && <p>Other skills mentioned in the post: {match.missing_skills.join(', ')}. Mentions can include optional skills.</p>}
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
