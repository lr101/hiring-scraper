export type ProfileMatch = {
  score: number; eligible: boolean; uncertain: boolean; label: string
  custom_skill_evidence?: { name: string; evidence: string; source: string }[]
  matched_skills: string[]; missing_skills: string[]; reasons: string[]; conflicts: string[]; unknowns: string[]
}
export type Enrichment = {
  version: string; quality: string; description_chars: number; warnings: string[]
  skills: { name: string; evidence: string; source: string }[]
  seniority: { value: string; evidence: string; source: string } | null
  experience_years: { value: number; evidence: string; source: string } | null
  languages: { value: string; evidence: string; source: string }[]
}

export default function JobEvidence({ enrichment, match }: { enrichment: Enrichment; match?: ProfileMatch }) {
  const skillEvidence = [...enrichment.skills, ...(match?.custom_skill_evidence ?? [])]
  return <section className="detail-panel evidence-panel">
    <p className="eyebrow">HOW THIS JOB FITS</p>
    {match ? <>
      <h2>{match.score}/100 · {match.label}</h2>
      <p className="muted">This score measures overlap with your interests and skills. It is not a prediction of hiring success.</p>
      {match.reasons.length > 0 && <ul>{match.reasons.map(reason => <li key={reason}>{reason}</li>)}</ul>}
      {match.conflicts.length > 0 && <div className="error-banner"><strong>Outside your preferences</strong><ul>{match.conflicts.map(reason => <li key={reason}>{reason}</li>)}</ul></div>}
      {match.unknowns.length > 0 && <div className="evidence-note"><strong>Still needs checking</strong><ul>{match.unknowns.map(reason => <li key={reason}>{reason}</li>)}</ul></div>}
      {match.missing_skills.length > 0 && <p>Other skills mentioned in the post: {match.missing_skills.join(', ')}. Mentions can include optional skills.</p>}
    </> : <><h2>Skills and requirements found</h2><p className="muted"><a href="/profile">Create a profile</a> to rank jobs by your interests.</p></>}
    {enrichment.seniority && <p><strong>Experience level:</strong> {enrichment.seniority.value} · {enrichment.seniority.evidence}</p>}
    {enrichment.experience_years && <p><strong>Minimum experience:</strong> {enrichment.experience_years.value} years · {enrichment.experience_years.evidence}</p>}
    {enrichment.languages.map(row => <p key={row.value}><strong>{row.value} requested:</strong> {row.evidence}</p>)}
    <h3>Evidence from the job post</h3>
    {skillEvidence.length ? <div className="skill-evidence-list">{skillEvidence.map(skill => <div key={skill.name}>
      <strong>{skill.name}</strong><p>{skill.evidence}</p><small>{(skill.source.startsWith('structured') || skill.source.startsWith('detail')) ? 'Employer’s structured job data' : skill.source === 'title' ? 'Job title' : 'Job description'}</small>
    </div>)}</div> : <p className="muted">No skills recognized yet. Our first version has a limited vocabulary; this does not mean the job has no requirements.</p>}
    {enrichment.warnings.length > 0 && <div className="evidence-note"><strong>Information gaps</strong><ul>{enrichment.warnings.map(warning => <li key={warning}>{warning}</li>)}</ul></div>}
  </section>
}
