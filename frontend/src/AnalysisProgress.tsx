import { useEffect, useState } from 'react'

export type ProgressCounts = { done: number; queued: number; retrying: number; failed?: number; skipped?: number; last_hour: number }
export type AnalysisProgressData = { postings: ProgressCounts; matches: ProgressCounts; checked_at: string }

const count = new Intl.NumberFormat('en')
const POLL_MS = 20_000

export async function loadAnalysisProgress(base: string, profileIds: number[], signal?: AbortSignal): Promise<AnalysisProgressData> {
  const response = await fetch(`${base}/api/v1/analysis-progress?profile_ids=${profileIds.join(',')}`, { signal })
  if (!response.ok) throw new Error('Could not load matching progress.')
  return response.json()
}

function waiting(row: ProgressCounts) {
  return row.queued + row.retrying
}

/** Rough time left at the last hour's pace, or null when there is no pace yet. */
export function remainingLabel(row: ProgressCounts): string | null {
  const left = waiting(row)
  if (!left) return null
  if (!row.last_hour) return 'starting…'
  const minutes = Math.ceil((left / row.last_hour) * 60)
  if (minutes < 60) return `about ${minutes} min left`
  const hours = minutes / 60
  return hours < 48 ? `about ${Math.round(hours)} h left` : `about ${Math.round(hours / 24)} days left`
}

function ProgressRow({ label, row, unit }: { label: string; row: ProgressCounts; unit: string }) {
  const failed = row.failed ?? 0
  const skipped = row.skipped ?? 0
  const total = row.done + row.queued + row.retrying + failed
  const share = (value: number) => (total ? `${(value / total) * 100}%` : '0%')
  const percent = total ? Math.floor((row.done / total) * 100) : 100
  const remaining = remainingLabel(row)
  const parts = [`${count.format(row.done)} done`, `${count.format(row.queued)} queued`]
  if (row.retrying) parts.push(`${count.format(row.retrying)} retrying later`)
  if (failed) parts.push(`${count.format(failed)} failed`)
  return <div className="analysis-progress-row">
    <div className="analysis-progress-label">
      <strong>{label}</strong>
      <span><b>{count.format(row.done)}</b> of {count.format(total)} {unit}</span>
    </div>
    <div className="analysis-progress-bar" role="progressbar" aria-label={label}
      aria-valuemin={0} aria-valuemax={total} aria-valuenow={row.done} aria-valuetext={parts.join(', ')}>
      <span className="is-done" style={{ width: share(row.done) }} />
      <span className="is-queued" style={{ width: share(row.queued) }} />
      {row.retrying > 0 && <span className="is-retrying" style={{ width: share(row.retrying) }} />}
      {failed > 0 && <span className="is-failed" style={{ width: share(failed) }} />}
    </div>
    <p className="analysis-progress-meta">
      <span>{percent}%</span>
      <span>{parts.slice(1).join(' · ')}</span>
      {row.last_hour > 0 && <span>{count.format(row.last_hour)} in the last hour</span>}
      {remaining && <span>{remaining}</span>}
    </p>
    {skipped > 0 && <p className="analysis-progress-skipped">
      {count.format(skipped)} skipped (older or low similarity)
    </p>}
  </div>
}

export function AnalysisProgressView({ data }: { data: AnalysisProgressData }) {
  const busy = waiting(data.postings) + waiting(data.matches) > 0
  return <section className="analysis-progress" aria-label="Local matching progress">
    <div className="analysis-progress-head">
      <h2>Local matching</h2>
      <span className={busy ? 'is-busy' : ''}>{busy ? 'Working through the queue' : 'Up to date'}</span>
    </div>
    <ProgressRow label="Postings read" row={data.postings} unit="open postings" />
    <ProgressRow label="Matches scored" row={data.matches} unit="profile matches" />
    <ul className="analysis-progress-legend" aria-hidden="true">
      <li className="is-done">Done</li><li className="is-queued">Queued</li><li className="is-retrying">Retrying later</li><li className="is-failed">Failed</li>
    </ul>
  </section>
}

/** Polls while the local models still have work, then stops. */
export default function AnalysisProgress({ base, profileIds, onProgress }: {
  base: string; profileIds: number[]; onProgress?: (data: AnalysisProgressData) => void
}) {
  const [data, setData] = useState<AnalysisProgressData | null>(null)
  const key = profileIds.join(',')
  useEffect(() => {
    if (!key) return
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout> | undefined
    const tick = () => loadAnalysisProgress(base, key.split(',').map(Number), controller.signal).then(next => {
      setData(next)
      onProgress?.(next)
      if (waiting(next.postings) + waiting(next.matches) > 0) timer = setTimeout(tick, POLL_MS)
    }).catch(() => {
      if (!controller.signal.aborted) timer = setTimeout(tick, POLL_MS * 3)
    })
    tick()
    return () => { controller.abort(); if (timer) clearTimeout(timer) }
  // onProgress is a notification hook; re-polling when its identity changes is not wanted.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [base, key])
  if (!data) return null
  const total = (row: ProgressCounts) => row.done + row.queued + row.retrying + (row.failed ?? 0)
  if (!total(data.postings) && !(data.postings.skipped ?? 0) && !total(data.matches)) return null
  return <AnalysisProgressView data={data} />
}
