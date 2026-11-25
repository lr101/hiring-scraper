import type { ProfileMatch, ScoreContribution } from './JobEvidence'

// Two decimals, as stored: the rows then add up exactly to the points total.
const points = new Intl.NumberFormat('en', { maximumFractionDigits: 2 })
const signedFormat = new Intl.NumberFormat('en', { maximumFractionDigits: 2, signDisplay: 'exceptZero' })
const signed = { format: (value: number) => signedFormat.format(value).replace('-', '\u2212') }

const COMPONENT_LABELS: Record<string, string> = {
  duties: 'Main duties',
  must: 'Must-have requirements',
  nice: 'Nice-to-haves',
  role: 'Role fit',
  domain: 'Field fit',
}
const ADJUSTMENT_LABELS: Record<string, string> = {
  'penalty:work_style': 'Work style',
  'penalty:employment_type': 'Employment type',
  'penalty:formal_prerequisite': 'Formal qualification unverified',
  'penalty:language_visa_flag': 'Language or work permit to verify',
  'local_context:afd_vote_share': 'Local preference',
}
const COMPONENT_HINTS: Record<string, string> = {
  role: 'Job title against your desired and past roles',
  domain: 'Your background in this field and industry',
}

type Strength = 'direct' | 'partial' | 'none'
type ChecklistItem = { text: string; strength: Strength; quote: string; source: string }

function isComponent(row: ScoreContribution) {
  return row.signal in COMPONENT_LABELS
}

function componentHint(row: ScoreContribution) {
  if (row.total != null && row.supported != null) {
    return row.total === 0 ? 'None listed' : `${row.supported} of ${row.total} shown in your CV`
  }
  return COMPONENT_HINTS[row.signal] ?? ''
}

function StatusIcon({ strength }: { strength: Strength }) {
  const label = strength === 'direct' ? 'Shown in your CV' : strength === 'partial' ? 'Partly shown' : 'Not shown yet'
  return <svg className={`fit-icon fit-icon-${strength}`} viewBox="0 0 16 16" width="16" height="16" role="img" aria-label={label}>
    <circle cx="8" cy="8" r="6.25" />
    {strength === 'direct' && <path d="M5.2 8.3 7.1 10.1 10.9 6.1" />}
    {strength === 'partial' && <path d="M8 1.75a6.25 6.25 0 0 1 0 12.5Z" className="fit-icon-fill" />}
  </svg>
}

/** Earned against possible points per component, on one shared scale. */
function PointsTable({ rows, scale }: { rows: ScoreContribution[]; scale: number }) {
  return <table className="score-points">
    <caption className="visually-hidden">Points per part of the match</caption>
    <thead className="visually-hidden"><tr><th scope="col">Part</th><th scope="col">Bar</th><th scope="col">Points</th></tr></thead>
    <tbody>{rows.map((row, index) => {
      const component = isComponent(row)
      const max = component ? Math.max(row.max_points ?? row.contribution, row.contribution, 0) : Math.abs(row.contribution)
      const share = (value: number) => `${Math.max(0, Math.min(100, (value / scale) * 100))}%`
      const title = component
        ? `${COMPONENT_LABELS[row.signal]}: ${points.format(row.contribution)} of ${points.format(max)} points`
        : `${ADJUSTMENT_LABELS[row.signal] ?? row.detail}: ${signed.format(row.contribution)} points`
      return <tr key={`${row.signal}:${index}`} className={component ? 'score-points-part' : row.contribution < 0 ? 'score-points-penalty' : 'score-points-bonus'}>
        <th scope="row">
          <span>{component ? COMPONENT_LABELS[row.signal] : ADJUSTMENT_LABELS[row.signal] ?? row.detail}</span>
          {(component ? componentHint(row) : row.detail) && <small>{component ? componentHint(row) : row.detail}</small>}
        </th>
        <td className="score-points-bar" title={title} aria-hidden="true">
          <span className="score-points-track" style={{ width: share(max) }}>
            <span style={{ width: max > 0 ? `${(Math.max(0, Math.abs(row.contribution)) / max) * 100}%` : '0%' }} />
          </span>
        </td>
        <td className="score-points-value">
          {component ? <><strong>{points.format(row.contribution)}</strong><small> / {points.format(max)}</small></>
            : <strong>{signed.format(row.contribution)}</strong>}
        </td>
      </tr>
    })}</tbody>
  </table>
}

/** Items the CV shows, out of all items: the same numbers wherever they appear. */
function checklistCount(items: ChecklistItem[], total?: number | null, supported?: number | null) {
  // Long postings are stored with a shortened item list; the counts come from the full score.
  const complete = total == null || total <= items.length
  return complete
    ? { complete, shown: items.filter(item => item.strength !== 'none').length, count: items.length }
    : { complete, shown: supported ?? 0, count: total }
}

function Checklist({ title, items, total, supported }: {
  title: string; items: ChecklistItem[]; total?: number | null; supported?: number | null
}) {
  if (!items.length) return null
  const { complete, shown, count } = checklistCount(items, total, supported)
  const first = items.slice(0, 6)
  const rest = items.slice(6)
  const renderItem = (item: ChecklistItem, index: number) => <li key={`${item.text}:${index}`} className={`fit-item fit-item-${item.strength}`}>
    <StatusIcon strength={item.strength} />
    <div>
      <p>{item.text}</p>
      {item.quote && <p className="fit-item-quote"><span>Your CV</span> <q>{item.quote}</q></p>}
    </div>
  </li>
  return <section className="fit-checklist" aria-label={title}>
    <h4>{title} <span>{shown} of {count}</span></h4>
    <ul>{first.map(renderItem)}</ul>
    {rest.length > 0 && <details><summary>Show {rest.length} more</summary><ul>{rest.map(renderItem)}</ul></details>}
    {!complete && <p className="fit-checklist-note">Showing {items.length} of {count}.</p>}
  </section>
}

function dutyItems(match: ProfileMatch): ChecklistItem[] {
  return (match.duty_evidence?.duties ?? []).map(duty => ({
    text: duty.job_quote,
    strength: duty.status === 'direct' ? 'direct' : duty.status === 'transferable' ? 'partial' : 'none',
    quote: duty.evidence_quote, source: duty.evidence_source,
  }))
}

function requirementItems(match: ProfileMatch, modality: 'must' | 'nice'): ChecklistItem[] {
  return (match.requirement_observations ?? [])
    .filter(row => row.strength && (row.modality ?? 'must') === modality)
    .map(row => ({ text: row.job_quote || row.requirement, strength: row.strength!, quote: row.evidence_quote, source: row.evidence_source }))
}

/** The posting's duties and requirements, each marked against the CV. */
export function FitChecklists({ match }: { match: ProfileMatch }) {
  const parts = (match.explanation ?? []).filter(isComponent)
  const part = (signal: string) => parts.find(row => row.signal === signal)
  const duties = dutyItems(match)
  const must = requirementItems(match, 'must')
  const nice = requirementItems(match, 'nice')
  if (!duties.length && !must.length && !nice.length && !match.soft_requirements?.length) return null
  return <div className="fit-checklists">
    <Checklist title="Duties" items={duties} total={part('duties')?.total} supported={part('duties')?.supported} />
    <Checklist title="Must-haves" items={must} total={part('must')?.total} supported={part('must')?.supported} />
    <Checklist title="Nice-to-haves" items={nice} total={part('nice')?.total} supported={part('nice')?.supported} />
    {!!match.soft_requirements?.length && <section className="fit-checklist fit-checklist-soft" aria-label="Personal qualities">
      <h4>Personal qualities <span>not scored</span></h4>
      <ul>{match.soft_requirements.map((row, index) => <li key={`${row.text}:${index}`} className="fit-item">
        <p>{row.text}</p>
      </li>)}</ul>
      <p className="fit-checklist-note">A CV cannot prove these. Show them with an example in your cover letter.</p>
    </section>}
  </div>
}

/** Supported and total items per scored part, for a one-line summary. */
export function fitCounts(match: ProfileMatch) {
  const parts = (match.explanation ?? []).filter(isComponent)
  const groups: [string, ChecklistItem[]][] = [
    ['duties', dutyItems(match)], ['must', requirementItems(match, 'must')], ['nice', requirementItems(match, 'nice')],
  ]
  return groups.flatMap(([signal, items]) => {
    const part = parts.find(row => row.signal === signal)
    if (items.length) {
      const { shown, count } = checklistCount(items, part?.total, part?.supported)
      return [{ signal, label: COMPONENT_LABELS[signal], supported: shown, total: count }]
    }
    return part?.total ? [{ signal, label: COMPONENT_LABELS[signal], supported: part.supported ?? 0, total: part.total }] : []
  })
}

export default function ScoreBreakdown({ match, checklists = true }: { match: ProfileMatch; checklists?: boolean }) {
  const ledger = match.explanation ?? []
  const parts = ledger.filter(isComponent)
  const part = (signal: string) => parts.find(row => row.signal === signal)
  const adjustments = ledger.filter(row => !isComponent(row) && row.contribution !== 0)
  const earned = parts.reduce((total, row) => total + Math.max(0, row.contribution), 0)
  const deducted = adjustments.reduce((total, row) => total + Math.min(0, row.contribution), 0)
  const bonus = adjustments.reduce((total, row) => total + Math.max(0, row.contribution), 0)
  const raw = match.calibration?.raw ?? earned + bonus + deducted
  const scale = Math.max(1, ...parts.map(row => row.max_points ?? row.contribution), ...adjustments.map(row => Math.abs(row.contribution)))
  const duties = dutyItems(match)
  const must = requirementItems(match, 'must')
  const nice = requirementItems(match, 'nice')
  const kept = Math.max(0, Math.min(100, earned + bonus + deducted))
  const lost = Math.max(0, Math.min(100, earned + bonus) - kept)

  return <figure className="score-breakdown" aria-label="How the match score was built">
    <div className="score-breakdown-head">
      <p className="score-breakdown-number"><strong>{match.score}</strong><span>/100</span></p>
      <div>
        <figcaption>How this score was built</figcaption>
        <p>Points from how well your CV shows this posting’s work and requirements{match.job_context?.occupation ? ` in ${match.job_context.occupation}` : ''}.</p>
      </div>
    </div>
    {parts.length > 0 && <>
      <div className="score-strip" role="img"
        aria-label={`${points.format(raw)} of 100 points before calibration${deducted ? `, ${points.format(-deducted)} deducted` : ''}`}>
        {parts.filter(row => row.contribution > 0).map(row =>
          <span key={row.signal} className="score-strip-part" style={{ flexBasis: `${row.contribution}%` }}
            title={`${COMPONENT_LABELS[row.signal]}: ${points.format(row.contribution)} points`} />)}
        {bonus > 0 && <span className="score-strip-part" style={{ flexBasis: `${bonus}%` }} title={`Adjustments: +${points.format(bonus)} points`} />}
        {lost > 0 && <span className="score-strip-lost" style={{ flexBasis: `${lost}%`, marginLeft: `-${lost}%` }}
          title={`Deductions: ${signed.format(deducted)} points`} />}
      </div>
      <div className="score-strip-scale" aria-hidden="true"><span>0</span><span>50</span><span>100 points</span></div>
      <PointsTable rows={[...parts, ...adjustments]} scale={scale} />
      {match.calibration && <p className="score-breakdown-total">
        <span>Points <strong>{points.format(raw)}</strong></span>
        <svg viewBox="0 0 24 12" width="24" height="12" aria-hidden="true"><path d="M2 6h18M15 1.5 20 6l-5 4.5" /></svg>
        <span>Match score <strong>{match.calibration.calibrated}</strong></span>
        <small>Calibrated so 80+ means strong overlap and under 30 means little. Not a prediction of hiring success.</small>
      </p>}
    </>}
    {checklists && (duties.length > 0 || must.length > 0 || nice.length > 0 || !!match.soft_requirements?.length) && <div className="fit-checklists">
      <Checklist title="Duties" items={duties} total={part('duties')?.total} supported={part('duties')?.supported} />
      <Checklist title="Must-haves" items={must} total={part('must')?.total} supported={part('must')?.supported} />
      <Checklist title="Nice-to-haves" items={nice} total={part('nice')?.total} supported={part('nice')?.supported} />
      {!!match.soft_requirements?.length && <section className="fit-checklist fit-checklist-soft" aria-label="Personal qualities">
        <h4>Personal qualities <span>not scored</span></h4>
        <ul>{match.soft_requirements.map((row, index) => <li key={`${row.text}:${index}`} className="fit-item">
          <p>{row.text}</p>
        </li>)}</ul>
        <p className="fit-checklist-note">A CV cannot prove these. Show them with an example in your cover letter.</p>
      </section>}
    </div>}
  </figure>
}
