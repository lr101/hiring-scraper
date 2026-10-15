export type ProfileMatch = {
  fit_tier?: 'recommended' | 'possible' | 'unlikely'; requirement_gaps?: string[]
  score: number; eligible: boolean; uncertain: boolean; label: string
  custom_skill_evidence?: { name: string; evidence: string; source: string }[]
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

export default function JobEvidence({ enrichment, match }: { enrichment: Enrichment; match?: ProfileMatch }) {
  const skillEvidence = [...enrichment.skills, ...(match?.custom_skill_evidence ?? [])]
  return <section className="detail-panel evidence-panel">
    <p className="eyebrow">How this job fits</p>
    {match ? <>
      <h2>{match.fit_tier === 'recommended' ? 'Recommended match' : match.fit_tier === 'unlikely' ? 'Little match evidence' : 'Possible lead'} · {match.score}/100</h2>
      <p className="muted">This heuristic score measures overlap with your interests and skills. It is not a prediction of hiring success.</p>
      {match.reasons.length > 0 && <ul>{match.reasons.map(reason => <li key={reason}>{reason}</li>)}</ul>}
      {match.conflicts.length > 0 && <div className="error-banner"><strong>Outside your preferences</strong><ul>{match.conflicts.map(reason => <li key={reason}>{reason}</li>)}</ul></div>}
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
