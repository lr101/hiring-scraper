import { useEffect, useState } from 'react'
import SiteHeader from './SiteHeader'

const API = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '')
type MatchingStatus = {
  state: 'pending' | 'ready' | 'disconnected' | 'paused'
  indexing_pending?: boolean; selection_pending?: boolean
  queued_pairs: number; assessed_pairs: number; stale_assessments_pending: boolean
  last_assessed_at: string | null; checked_at: string
  model: string; usage: { allowed?: boolean; reason?: string; used_percent?: number | null; resets_at?: string | null }
}
function timestamp(value: string | null) {
  if (!value) return 'No successful assessment recorded'
  return new Date(value.endsWith('Z') || /[+-]\d\d:\d\d$/.test(value) ? value : `${value}Z`).toLocaleString()
}
export function MatchingStatusDetails({ data }: { data: MatchingStatus }) {
  const title = data.state === 'pending' && data.indexing_pending ? 'Preparing job matches' : { pending: 'Waiting for assessments', ready: 'Up to date', disconnected: 'Connect ChatGPT', paused: 'Paused' }[data.state]
  return <>
    <section className="detail-panel settings-card" aria-labelledby="matching-state">
      <h2 id="matching-state">{title}</h2>
      <p>{data.state === 'pending' && data.indexing_pending ? 'Local matches are still being rebuilt. The AI queue count is incomplete until this finishes.' : data.state === 'pending' ? 'A shortlist of eligible jobs with a local base score over 35/100 is selected for AI review. Local estimates stay visible until assessments are saved.'
        : data.state === 'ready' ? 'There are no pending assessments in the current queue.'
        : data.state === 'disconnected' ? 'Connect your ChatGPT subscription in Settings to enable AI job reviews.'
        : 'AI reviews are paused because subscription usage is unavailable or the usage limit has been reached.'}</p>
      {(data.state === 'disconnected' || data.state === 'paused') && <a href="/settings">Open connection settings</a>}
      <p>Queue status alone cannot confirm the worker is running. If queued jobs remain unchanged and no new assessments appear, the background worker may need attention.</p>
    </section>
    <section className="detail-panel settings-card" aria-labelledby="matching-activity">
      <h2 id="matching-activity">Assessment activity</h2>
      <p>AI review is only used when the local base score is strictly over 35/100 and the job is eligible. Connection and subscription usage limits also apply.</p>
      <dl className="matching-status-facts">
        <div><dt>Job/profile pairs awaiting first assessment</dt><dd>{data.queued_pairs}</dd></div>
        <div><dt>Saved assessments for active jobs</dt><dd>{data.assessed_pairs}</dd></div>
        <div><dt>Changed jobs awaiting reassessment</dt><dd>{data.stale_assessments_pending ? 'Yes' : 'No'}</dd></div>
        <div><dt>Last successful assessment</dt><dd>{timestamp(data.last_assessed_at)}</dd></div>
        <div><dt>Five-hour subscription usage</dt><dd>{data.usage.used_percent == null ? 'Unavailable' : `${data.usage.used_percent}%`} · pauses when exhausted</dd></div>
        <div><dt>Model</dt><dd>{data.model}</dd></div>
      </dl>
      {data.selection_pending && <p>The background shortlist needs refreshing. The queue count currently shows candidates before shortlist caps are applied.</p>}
      <p>Counts cover all profiles and active jobs. Saved assessments may need reassessment after source changes; queued candidates are checked for eligibility before scoring.</p>
      <p>Last checked: {timestamp(data.checked_at)}</p>
    </section>
  </>
}
export default function MatchingStatusPage() {
  const [data, setData] = useState<MatchingStatus | null>(null)
  const [error, setError] = useState('')
  const [revision, setRevision] = useState(0)
  const [rematching, setRematching] = useState(false)
  const [message, setMessage] = useState('')
  async function rematchAll() {
    setRematching(true); setMessage('')
    try {
      const response = await fetch(`${API}/api/v1/profile-matching/rematch`, { method: 'POST' })
      if (!response.ok) {
        const body = await response.json().catch(() => null)
        throw new Error(typeof body?.detail === 'string' ? body.detail : 'Could not queue AI rematching. Please try again.')
      }
      setMessage('AI rematching requested. The background worker will rebuild local matches and review eligible jobs.')
      setRevision(value => value + 1)
    } catch (error) { setMessage(error instanceof Error ? error.message : 'Could not queue AI rematching. Please try again.') }
    finally { setRematching(false) }
  }
  useEffect(() => {
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout>
    async function refresh() {
      try {
        const response = await fetch(`${API}/api/v1/profile-matching/status`, { cache: 'no-store', signal: controller.signal })
        if (!response.ok) throw new Error('Could not check AI matching status.')
        const value = await response.json() as MatchingStatus
        if (!controller.signal.aborted) { setData(value); setError('') }
      } catch { if (!controller.signal.aborted) setError('Could not check AI matching status. The information below may be out of date.') }
      finally { if (!controller.signal.aborted) timer = setTimeout(refresh, 30000) }
    }
    void refresh()
    return () => { controller.abort(); clearTimeout(timer) }
  }, [revision])
  return <div className="app-shell"><SiteHeader active="settings" />
    <main className="content settings-page">
      <section className="settings-page-heading"><h1>AI matching status</h1><p>Check whether AI job reviews are progressing. Updates every 30 seconds.</p>
        <button type="button" onClick={() => setRevision(value => value + 1)}>Refresh status</button>
        <button type="button" disabled={rematching} onClick={() => void rematchAll()}>{rematching ? 'Queuing…' : 'Match all jobs with AI again'}</button>
        {message && <p role="status">{message}</p>}</section>
      {error && <p role="alert">{error}</p>}
      {!data && !error && <p role="status">Checking AI matching…</p>}
      {data && <MatchingStatusDetails data={data} />}
      <a href="/">Back to Journey</a>
    </main></div>
}
