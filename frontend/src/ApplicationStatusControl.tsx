import { useEffect, useRef, useState } from 'react'
import { applicationStatuses, saveApplication } from './applications'
import type { Application, ApplicationStatus } from './applications'

const API = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '')

export default function ApplicationStatusControl({ profileId, jobId, title, application, onSaved }: {
  profileId: number; jobId: number; title: string; application?: Application | null
  onSaved: (application: Application) => void
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const mounted = useRef(true)
  useEffect(() => {
    mounted.current = true
    return () => { mounted.current = false }
  }, [])

  async function change(status: ApplicationStatus) {
    setBusy(true); setError(''); setNotice('')
    try {
      const saved = await saveApplication(API, profileId, jobId, status)
      // A save belongs to this job/profile control. It may finish after a
      // profile switch has unmounted us and loaded a different application.
      if (!mounted.current) return
      onSaved(saved)
      setNotice('Saved')
    } catch (reason) {
      if (mounted.current) setError(reason instanceof Error ? reason.message : 'Could not save application status.')
    } finally { if (mounted.current) setBusy(false) }
  }

  return <div className="application-control" onClick={event => event.stopPropagation()}>
    <select aria-label={`Application status for ${title}`} value={application?.status ?? ''}
      disabled={busy} onChange={event => void change(event.target.value as ApplicationStatus)}>
      <option value="" disabled>Track this job…</option>
      {applicationStatuses.map(status => <option value={status.value} key={status.value}>{status.label}</option>)}
    </select>
    <span className="application-save-state" role="status">{busy ? 'Saving…' : notice}</span>
    {error && <span className="application-error" role="alert">{error}</span>}
  </div>
}
