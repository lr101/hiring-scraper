import { useEffect, useState } from 'react'
import SiteHeader from './SiteHeader'
import ApplicationStatusControl from './ApplicationStatusControl'
import { applicationStatuses } from './applications'
import type { Application, ApplicationStatus } from './applications'
import { profileBoardLink } from './profile'
import type { Profile } from './profile'

const API = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '')
const PAGE_SIZE = 40
type TrackedJob = {
  id: number; title: string; company_name: string; location_text: string | null
  is_active: boolean; expired: boolean; application: Application
}
type Overview = { items: TrackedJob[]; total: number; counts: Record<ApplicationStatus, number> }

export default function ApplicationsPage() {
  const [params] = useState(() => new URLSearchParams(window.location.search))
  const [profiles, setProfiles] = useState<Profile[]>([])
  const [profileId, setProfileId] = useState(params.get('profile_id') ?? '')
  const [profilesReady, setProfilesReady] = useState(false)
  const [status, setStatus] = useState<ApplicationStatus | ''>(() =>
    applicationStatuses.find(item => item.value === params.get('status'))?.value ?? '')
  const [overview, setOverview] = useState<Overview | null>(null)
  const [offset, setOffset] = useState(0)
  const [revision, setRevision] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const selectedProfile = profiles.find(profile => String(profile.id) === profileId)

  useEffect(() => {
    const controller = new AbortController()
    fetch(`${API}/api/v1/profiles`, { signal: controller.signal }).then(async response => {
      if (!response.ok) throw new Error('Could not load saved profiles.')
      const data = await response.json()
      setProfiles(data.items)
      const selected = data.items.find((profile: Profile) => String(profile.id) === params.get('profile_id')) ?? data.items[0]
      setProfileId(selected ? String(selected.id) : '')
      setProfilesReady(true)
    }).catch(reason => { if (!controller.signal.aborted) { setError(reason.message); setLoading(false) } })
    return () => controller.abort()
  }, [params])

  useEffect(() => {
    if (!profilesReady) return
    if (!profileId) { setLoading(false); return }
    const controller = new AbortController()
    setLoading(true); setError('')
    const query = new URLSearchParams({ offset: String(offset), limit: String(PAGE_SIZE) })
    if (status) query.set('status', status)
    fetch(`${API}/api/v1/profiles/${profileId}/applications?${query}`, { signal: controller.signal })
      .then(async response => {
        const data = await response.json()
        if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Could not load applications.')
        return data
      }).then(data => setOverview(data))
      .catch(reason => { if (!controller.signal.aborted) setError(reason.message) })
      .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    const url = new URL(window.location.href)
    url.searchParams.set('profile_id', profileId)
    if (status) url.searchParams.set('status', status)
    else url.searchParams.delete('status')
    window.history.replaceState({}, '', url)
    return () => controller.abort()
  }, [profilesReady, profileId, status, offset, revision])

  function filter(value: ApplicationStatus | '') {
    setStatus(value); setOffset(0); setNotice('')
  }

  return <div className="app-shell"><SiteHeader active="applications" />
    <main className="content applications-page">
      <section className="intro-row">
        <div><p className="eyebrow">YOUR JOB PROFILE OVERVIEW</p><h1>My applications</h1>
          <p className="intro-copy">Keep track of opportunities, replies, interviews, and offers.</p></div>
        {selectedProfile && <a className="page-link" href={profileBoardLink(selectedProfile)}>Find matching jobs →</a>}
      </section>
      {error && <div className="error-banner" role="alert">{error}</div>}
      {notice && <p className="evidence-note" role="status">{notice}</p>}
      {profilesReady && !profiles.length ? <section className="detail-panel empty-state">
        <h2>Create a job profile to get started</h2><p>Save a profile, then choose a status on any job to track it here.</p>
        <a className="page-link" href="/profile">Create a profile →</a>
      </section> : <>
        <section className="application-profile-bar" aria-label="Application profile">
          <label>Job profile <select aria-label="Application profile" value={profileId} disabled={!profilesReady}
            onChange={event => { setProfileId(event.target.value); setOffset(0); setOverview(null); setNotice('') }}>
            {!profilesReady && <option value="">Loading profiles…</option>}
            {profiles.map(profile => <option value={profile.id} key={profile.id}>{profile.name}</option>)}
          </select></label>
          <a className="page-link" href="/profile">Manage profiles →</a>
        </section>
        <div className="application-status-grid" aria-label="Filter applications by status">
          {applicationStatuses.map(item => <button type="button" key={item.value} aria-pressed={status === item.value}
            className={`application-status-card application-${item.value} ${status === item.value ? 'selected' : ''}`}
            onClick={() => filter(status === item.value ? '' : item.value)}>
            <span>{item.label}</span><strong>{overview?.counts[item.value] ?? '—'}</strong>
          </button>)}
        </div>
        <section className="directory-card">
          <div className="directory-header"><div><p className="eyebrow">TRACKED JOB POSTINGS</p>
            <h2>{status ? applicationStatuses.find(item => item.value === status)?.label : 'All applications'}</h2></div>
            <button className="broaden-button" type="button" aria-pressed={status === ''} onClick={() => filter('')}>All statuses</button>
          </div>
          <p className="application-help">Choose a status on a job to add it here. Closed and expired postings stay visible while you track them.</p>
          {loading ? <div className="loading-state"><span className="spinner" /> Loading applications…</div>
            : error ? <div className="empty-state"><p>Applications could not be loaded.</p><button type="button" className="broaden-button" onClick={() => setRevision(value => value + 1)}>Try again</button></div>
            : overview?.items.length ? <div className="table-scroll mobile-results-scroll"><table className="applications-table mobile-results-table">
              <thead><tr><th>JOB / COMPANY</th><th>WORK LOCATION</th><th>APPLICATION STATUS</th><th>UPDATED</th></tr></thead>
              <tbody>{overview.items.map(job => <tr key={`${profileId}:${job.id}`}>
                <td className="result-primary"><div className="application-role"><a href={`/jobs/${job.id}?profile_id=${profileId}&from=applications`}>{job.title}</a>
                  <small>{job.company_name}</small>{(!job.is_active || job.expired) && <small className="application-closed">{job.expired ? 'Posting expired' : 'Posting closed'}</small>}</div></td>
                <td className="result-meta"><span className="mobile-field-label">Work location</span>{job.location_text || 'Location not listed'}</td>
                <td className="result-status"><span className="mobile-field-label">Application status</span><ApplicationStatusControl profileId={Number(profileId)} jobId={job.id} title={job.title} application={job.application}
                  onSaved={saved => {
                    setNotice(`Moved ${job.title} to ${applicationStatuses.find(item => item.value === saved.status)?.label}.`)
                    setOffset(0); setRevision(value => value + 1)
                  }} /></td>
                <td className="result-meta result-date"><span className="mobile-field-label">Updated</span>{new Date(job.application.updated_at).toLocaleDateString('en', { day: 'numeric', month: 'short', year: 'numeric' })}</td>
              </tr>)}</tbody>
            </table></div> : <div className="empty-state"><h2>{status ? 'No applications with this status' : 'No tracked jobs yet'}</h2>
              <p>{status ? 'Choose another status or view all applications.' : 'Find an opportunity and choose New, Open, or another status to start tracking it.'}</p>
              {selectedProfile && <a className="page-link" href={profileBoardLink(selectedProfile)}>Browse matching jobs →</a>}
            </div>}
          <div className="table-footer"><span>{overview?.total ?? 0} {status ? 'in this status' : 'tracked jobs'}</span>
            <div className="pagination"><button type="button" disabled={offset === 0 || loading} onClick={() => setOffset(value => Math.max(0, value - PAGE_SIZE))}>Previous</button>
              <span>{Math.floor(offset / PAGE_SIZE) + 1} / {Math.max(1, Math.ceil((overview?.total ?? 0) / PAGE_SIZE))}</span>
              <button type="button" disabled={loading || offset + PAGE_SIZE >= (overview?.total ?? 0)} onClick={() => setOffset(value => value + PAGE_SIZE)}>Next</button></div>
          </div>
        </section>
      </>}
    </main>
  </div>
}
