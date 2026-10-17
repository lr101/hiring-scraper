import { useEffect, useMemo, useRef, useState } from 'react'
import SiteHeader from './SiteHeader'
import { buildApplicationPrompt, deleteCv, parseReturnedPackage, readCv, readDraft, removeDraft, saveCv, saveDraft, type ApplicationPackage, type StoredCv } from './applicationPreparation'
import './application-preparation.css'

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '')
const apiUrl = (path: string) => `${API_BASE_URL}${path}`
type Profile = { id: number; name: string }
type Job = { id: number; title: string; url: string; company_name: string; description: string | null; location_text: string | null; employment_type: string | null; work_arrangement: string | null }

export default function ApplicationPreparationPage({ jobId }: { jobId: number }) {
  const requestedProfileId = Number(new URLSearchParams(window.location.search).get('profile_id'))
  const [job, setJob] = useState<Job | null>(null)
  const [profile, setProfile] = useState<Profile | null>(null)
  const [cv, setCv] = useState<StoredCv | null>(null)
  const [cvText, setCvText] = useState('')
  const [returned, setReturned] = useState('')
  const [pendingHash, setPendingHash] = useState('')
  const [packageValue, setPackageValue] = useState<ApplicationPackage | null>(null)
  const [notice, setNotice] = useState('')
  const [error, setError] = useState('')
  const fileRef = useRef<HTMLInputElement>(null)
  const cvGeneration = useRef(0)
  const validRequestedProfile = Number.isSafeInteger(requestedProfileId) && requestedProfileId > 0

  useEffect(() => {
    if (!validRequestedProfile) { setError('Choose a valid application profile before preparing this application.'); return }
    const controller = new AbortController()
    Promise.all([fetch(apiUrl('/api/v1/profiles'), { signal: controller.signal }), fetch(apiUrl(`/api/v1/jobs/${jobId}?profile_id=${requestedProfileId}`), { signal: controller.signal })])
      .then(async ([profileResponse, jobResponse]) => {
        if (!profileResponse.ok) throw new Error('Could not load application profiles.')
        if (!jobResponse.ok) throw new Error('Could not load this job.')
        const profilePayload = await profileResponse.json(); const jobPayload = await jobResponse.json()
        const selected = (profilePayload.items as Profile[]).find(item => item.id === requestedProfileId)
        if (!selected) throw new Error('The selected application profile is unavailable. Return to the job and choose a profile.')
        if (controller.signal.aborted) return
        cvGeneration.current++; setProfile(selected); setJob(jobPayload); const localCv = readCv(localStorage, selected.id); setCv(localCv); setCvText(localCv?.text ?? '')
      }).catch(reason => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : 'Could not load preparation details.') })
    return () => controller.abort()
  }, [jobId, requestedProfileId, validRequestedProfile])

  useEffect(() => { if (job && profile && cv) setPackageValue(readDraft(localStorage, job.id, profile.id, cv.updated_at)) }, [job, profile, cv])
  useEffect(() => {
    function consumeHash() {
      if (!window.location.hash.startsWith('#application')) return
    const hash = window.location.hash
    window.history.replaceState({}, '', `${window.location.pathname}${window.location.search}`)
    setReturned(hash); setPendingHash(hash)
    }
    consumeHash(); window.addEventListener('hashchange', consumeHash)
    return () => window.removeEventListener('hashchange', consumeHash)
  }, [])
  useEffect(() => {
    if (!job || !profile || !cv || !pendingHash) return
    let cancelled = false
    const expected = cv
    void parseReturnedPackage(pendingHash, { jobId: job.id, profileId: profile.id, cvUpdatedAt: expected.updated_at }).then(parsed => {
      const latest = readCv(localStorage, profile.id)
      if (cancelled || latest?.updated_at !== expected.updated_at || latest.text !== expected.text) throw new Error('The saved CV changed while this package was being read. Paste a package for the current CV.')
      if (!saveDraft(localStorage, job.id, profile.id, parsed, { job_id: job.id, profile_id: profile.id, cv_updated_at: expected.updated_at })) throw new Error('Could not save this package.')
      if (!cancelled) { setPackageValue(parsed); setReturned(''); setNotice('Returned package validated and saved in this browser only.'); setError('') }
    }).catch(reason => { if (!cancelled) setError(reason instanceof Error ? reason.message : 'Could not read this returned package.') }).finally(() => { if (!cancelled) setPendingHash('') })
    return () => { cancelled = true }
  }, [cv, job, pendingHash, profile])
  useEffect(() => {
    function changed(event: StorageEvent) {
      if (!profile || !job || !event.key) return
      if (event.key === `radius:application-cv:v1:${profile.id}`) {
        cvGeneration.current++; const next = readCv(localStorage, profile.id); setCv(next); setCvText(next?.text ?? ''); setPackageValue(null)
      } else if (event.key === `radius:application-draft:v1:${job.id}:${profile.id}` && cv) setPackageValue(readDraft(localStorage, job.id, profile.id, cv.updated_at))
    }
    window.addEventListener('storage', changed); return () => window.removeEventListener('storage', changed)
  }, [cv, job, profile])

  const returnUrl = `${window.location.origin}${window.location.pathname}${window.location.search}`
  const prompt = useMemo(() => cv && job && profile ? buildApplicationPrompt(cv, { job, profile, returnUrl }) : '', [cv, job, profile, returnUrl])
  function saveLocalCv() {
    if (!profile) return
    try { cvGeneration.current++; const next = saveCv(localStorage, profile.id, cvText); setCv(next); setPackageValue(null); setNotice('Original CV saved in this browser only.'); setError('') }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not save the CV.') }
  }
  async function importText(file: File | undefined) {
    if (!file) return
    if (!file.name.toLowerCase().endsWith('.txt') || file.size > 100_000) { setError('Choose a plain .txt CV file under 100,000 bytes.'); return }
    const generation = cvGeneration.current; const profileId = profile?.id
    try {
      const text = await file.text()
      if (text.length > 100_000) throw new Error('Choose CV text under 100,000 characters.')
      if (generation !== cvGeneration.current || profileId !== profile?.id) return
      setCvText(text); setNotice('CV text imported. Select Save CV to keep it in this browser.'); setError('')
    } catch (reason) { if (generation === cvGeneration.current) setError(reason instanceof Error ? reason.message : 'Could not import this CV file.') }
  }
  async function acceptReturned() {
    if (!job || !profile || !cv) return
    try {
      const expected = cv
      const parsed = await parseReturnedPackage(returned, { jobId: job.id, profileId: profile.id, cvUpdatedAt: expected.updated_at })
      const latest = readCv(localStorage, profile.id)
      if (latest?.updated_at !== expected.updated_at || latest.text !== expected.text) throw new Error('The saved CV changed while this package was being read. Paste a package for the current CV.')
      if (!saveDraft(localStorage, job.id, profile.id, parsed, { job_id: job.id, profile_id: profile.id, cv_updated_at: expected.updated_at })) throw new Error('Could not save this package.')
      setPackageValue(parsed); setError(''); setNotice('Prepared package saved in this browser only.'); setReturned('')
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not read this returned package.') }
  }
  async function copyPrompt() { try { await navigator.clipboard.writeText(prompt); setNotice('Prompt copied. Paste it into ChatGPT when you choose.'); setError('') } catch { setError('Could not copy the prompt. Select and copy the text manually.') } }
  function download() {
    if (!packageValue) return
    downloadText(`application-${job?.id}.txt`, `Cover letter\n\n${packageValue.cover_letter}\n\nApplication answers\n\n${packageValue.application_answers}\n\nReview notes\n\n${packageValue.review_notes}`)
  }
  function downloadText(name: string, text: string) { const href = URL.createObjectURL(new Blob([text], { type: 'text/plain;charset=utf-8' })); const anchor = document.createElement('a'); anchor.href = href; anchor.download = name; anchor.click(); URL.revokeObjectURL(href) }
  if (error && !job && !profile) return <div className="app-shell"><SiteHeader active="applications" /><main className="content"><div className="error-banner" role="alert">{error}</div></main></div>
  return <div className="app-shell"><SiteHeader active="applications" /><main className="content application-preparation-page">
    <a className="back-link" href={job ? `/jobs/${job.id}?profile_id=${requestedProfileId}` : '/discover?view=jobs'}>← Back to job</a>
    <header className="application-preparation-hero"><p className="eyebrow">Local application preparation</p><h1>{job?.title ?? 'Prepare application'}</h1><p className="intro-copy">{job ? `${job.company_name} · ${profile?.name ?? 'loading profile'}` : 'Loading selected job and profile…'}</p></header>
    {error && <p className="error-banner" role="alert">{error}</p>}{notice && <p className="application-preparation-notice" role="status">{notice}</p>}
    {profile && <section className="application-preparation-card"><p className="eyebrow">1 · Original CV</p><h2>Keep the source text in this browser</h2><p>Radius never sends this CV to its API. Save it explicitly, replace it with pasted text or a plain text file, or delete it here.</p>
      <textarea value={cvText} onChange={event => setCvText(event.target.value)} aria-label="Original CV text" placeholder="Paste original CV text" />
      <div className="application-preparation-actions"><button className="search-button" type="button" onClick={saveLocalCv}>Save CV</button><button className="secondary-button" type="button" onClick={() => fileRef.current?.click()}>Import .txt</button><input ref={fileRef} type="file" accept="text/plain,.txt" hidden onChange={event => void importText(event.target.files?.[0])} />{cv && <><span className="application-cv-ready">Original CV saved locally</span><button className="text-button" type="button" onClick={() => { cvGeneration.current++; deleteCv(localStorage, profile.id); setCv(null); setPackageValue(null); setNotice('Saved CV and all its prepared drafts were removed from this browser.'); }}>Delete saved CV</button></>}</div>
    </section>}
    {cv && job && profile && <><section className="application-preparation-card"><p className="eyebrow">2 · Prompt</p><h2>Review and send only when ready</h2><p>Copy this prompt or open ChatGPT yourself. Generated content remains text for your review; Radius does not submit an application. If you use the companion extension, save a reusable extension profile once from this original CV, then keep this Radius tab open while it checks the CV version.</p><textarea aria-label="Prepared application prompt" value={prompt} readOnly />
      <div className="application-preparation-actions"><button className="search-button" type="button" onClick={() => void copyPrompt()}>Copy prompt</button><a className="secondary-button application-chatgpt-link" href="https://chatgpt.com" target="_blank" rel="noreferrer">Open ChatGPT ↗</a></div></section>
      <section className="application-preparation-card"><p className="eyebrow">3 · Returned package</p><h2>Paste the JSON or return link</h2><textarea aria-label="Returned application package" value={returned} onChange={event => setReturned(event.target.value)} placeholder="Paste the returned JSON or Radius return URL" /><div className="application-preparation-actions"><button className="search-button" type="button" onClick={() => void acceptReturned()}>Validate and save package</button>{packageValue && <button className="secondary-button" type="button" onClick={download}>Download letter and answers</button>}{packageValue && <button className="secondary-button" type="button" onClick={() => downloadText(`tailored-cv-${job.id}.txt`, typeof packageValue.cv === 'string' ? packageValue.cv : packageValue.cv.map(row => row.text).join('\n'))}>Download CV</button>}{packageValue && <button className="text-button" type="button" onClick={() => { removeDraft(localStorage, job.id, profile.id); setPackageValue(null); setNotice('Saved package removed from this browser.'); }}>Delete package</button>}</div>
      {packageValue && <div className="application-package-output"><h3>Tailored CV</h3><pre>{typeof packageValue.cv === 'string' ? packageValue.cv : packageValue.cv.map(row => row.text).join('\n')}</pre><h3>Cover letter</h3><pre>{packageValue.cover_letter}</pre><h3>Application answers</h3><pre>{packageValue.application_answers}</pre><h3>Review notes</h3><pre>{packageValue.review_notes}</pre>{packageValue.experience_bullets && <><h3>Experience bullets</h3><pre>{JSON.stringify(packageValue.experience_bullets, null, 2)}</pre></>}</div>}</section></>}
  </main></div>
}
