import { useEffect, useRef, useState } from 'react'
import ChatGPTConnection from './ChatGPTConnection'
import PDFPreview from './PDFPreview'
import { documentHTML } from './applicationHandoff'
import { canApplyRevision, cancelDocumentRevision, discardDocumentRevision, getDocumentRevision, startDocumentRevision, verifyDocumentRevision } from './documentRevision'
import type { RevisionJob, RevisionTarget } from './documentRevision'
import type { Profile } from './profile'
import type { LatexDocuments } from './applicationLatex'

type Props = {
  api: string; jobId: number; profile: Profile; language: 'en' | 'de'; target: RevisionTarget
  currentDocument: string; cv: string; coverLetter: string; latexDocuments?: LatexDocuments
  useProfilePhoto: boolean; photoDataURL: string; onApply: (document: string, review: string) => Promise<boolean>; onSwitchTarget: (target: RevisionTarget) => void
}

export default function DocumentRevisionPanel({ api, jobId, profile, language, target, currentDocument, cv, coverLetter, latexDocuments, useProfilePhoto, photoDataURL, onApply, onSwitchTarget }: Props) {
  const [instruction, setInstruction] = useState('')
  const [job, setJob] = useState<RevisionJob | null>(null)
  const [connected, setConnected] = useState<boolean | null>(null)
  const [starting, setStarting] = useState(false)
  const [error, setError] = useState('')
  const [refresh, setRefresh] = useState(0)
  const [preview, setPreview] = useState('')
  const [previewError, setPreviewError] = useState('')
  const [applying, setApplying] = useState(false)
  const mounted = useRef(false)
  const latest = useRef({ job, target, currentDocument, profile, language, onApply })
  latest.current = { job, target, currentDocument, profile, language, onApply }
  useEffect(() => { mounted.current = true; return () => { mounted.current = false } }, [])
  const running = applying || starting || job?.state === 'running' || job?.state === 'cancelling'
  const result = job?.state === 'completed' && job.result?.target === target ? job.result : null
  const otherAlreadyApplied = job?.state === 'completed' && !!job.result &&
    (job.result.target === 'cv' ? latexDocuments?.cv === job.result.document : coverLetter === job.result.document)
  const otherTarget = job?.request?.target && job.request.target !== target && ['running', 'cancelling', 'completed'].includes(job.state) && !otherAlreadyApplied ? job.request.target : null
  const valid = canApplyRevision(job, target, currentDocument, profile, language)
  const matchesCurrent = !!result && result.document === currentDocument && job?.request?.cv_updated_at === profile.cv?.updated_at && job?.request?.profile_updated_at === profile.updated_at && job?.request?.language === language
  const name = target === 'cv' ? 'CV' : 'cover letter'

  useEffect(() => {
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout> | undefined
    async function poll() {
      try {
        const current = await getDocumentRevision(api, profile.id!, jobId, controller.signal)
        if (controller.signal.aborted) return
        setJob(current); setError('')
        if (current?.request?.target === target && current.request.instruction) setInstruction(value => value || current.request!.instruction)
        if (current?.state === 'running' || current?.state === 'cancelling') timer = setTimeout(() => void poll(), 2000)
      } catch {
        if (!controller.signal.aborted) { setError('Could not check the suggestion. Reconnecting…'); timer = setTimeout(() => void poll(), 5000) }
      }
    }
    void poll()
    return () => { controller.abort(); clearTimeout(timer) }
  }, [api, profile.id, jobId, refresh, target])

  useEffect(() => {
    setPreview(''); setPreviewError('')
    if (!result || target !== 'cv') return
    const controller = new AbortController()
    let url = ''
    void fetch(`${api}/api/v1/profiles/${profile.id}/application-document?kind=cv&format=latex_pdf`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, signal: controller.signal,
      body: JSON.stringify({ job_id: jobId, cv_updated_at: profile.cv?.updated_at, cv, cover_letter: coverLetter,
        latex_documents: { ...latexDocuments, cv: result.document }, use_profile_photo: useProfilePhoto,
        ...(photoDataURL ? { photo_data_url: photoDataURL } : {}) }),
    }).then(async response => {
      if (!response.ok) { const value = await response.json().catch(() => null); throw new Error(value?.detail || 'Could not preview this CV.') }
      const blob = await response.blob()
      if (!controller.signal.aborted) { url = URL.createObjectURL(blob); setPreview(url) }
    }).catch(reason => { if (!controller.signal.aborted) setPreviewError(reason instanceof Error ? reason.message : 'Could not preview this CV.') })
    return () => { controller.abort(); if (url) URL.revokeObjectURL(url) }
  }, [api, jobId, profile.id, profile.cv?.updated_at, result, target, cv, coverLetter, latexDocuments, useProfilePhoto, photoDataURL])

  async function submit() {
    if (!instruction.trim() || !currentDocument.trim() || starting) return
    setStarting(true); setError('')
    try {
      const next = await startDocumentRevision({ api, jobId, profile, language, target, currentDocument, instruction })
      setJob(next); setRefresh(value => value + 1)
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not revise this document.') }
    finally { setStarting(false) }
  }
  async function stop() {
    if (!job) return
    try { setJob(await cancelDocumentRevision(api, profile.id!, jobId, job.id)); setRefresh(value => value + 1) }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not stop the revision.') }
  }
  async function discard(revisionId = job?.id) {
    if (!revisionId) return
    setStarting(true)
    try { await discardDocumentRevision(api, profile.id!, jobId, revisionId); setJob(null); setRefresh(value => value + 1) }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not discard the suggestion.') }
    finally { setStarting(false) }
  }
  async function apply() {
    if (!job || !result || !valid || applying) return
    setApplying(true); setError('')
    try {
      await verifyDocumentRevision(api, profile.id!, jobId, job.id)
      if (!mounted.current) return
      const current = latest.current
      if (current.job?.id !== job.id || !canApplyRevision(current.job, current.target, current.currentDocument, current.profile, current.language)) {
        setError('The document changed while checking this suggestion. Request a new one.'); return
      }
      if (!await current.onApply(result.document, result.review)) { setError('Could not apply the suggestion. Your current document is unchanged; check the message below.'); return }
      setInstruction('')
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not verify this suggestion.') }
    finally { setApplying(false) }
  }

  return <section className="document-revision" aria-label={`Review and revise ${name}`}>
    <div className="document-revision-heading"><div><h3>Refine your {name}</h3><p>Ask for a change, then review the suggested version before using it.</p></div><span>Review before applying</span></div>
    {!currentDocument && <p className="finish-help">This CV has no editable LaTeX source. Prepare a current CV before requesting a revision.</p>}
    <label htmlFor={`revision-instruction-${target}`}>What would you like to change?
      <textarea id={`revision-instruction-${target}`} value={instruction} maxLength={5000} rows={3} disabled={running} onChange={event => setInstruction(event.target.value)} placeholder={target === 'cv' ? 'For example: Make the profile more concise and emphasize my coordination experience.' : 'For example: Make the opening more direct and connect my experience to this role.'} />
    </label>
    <div className="document-revision-actions"><button className="primary-button" disabled={running || !!otherTarget || !connected || !instruction.trim() || !currentDocument.trim()} onClick={() => void submit()}>Review and suggest changes</button>
      {running && job?.state === 'running' && <button onClick={() => void stop()}>Cancel revision</button>}</div>
    {otherTarget && <p className="finish-help">A {otherTarget === 'cv' ? 'CV' : 'cover letter'} suggestion {job?.state === 'completed' ? 'is ready for review' : 'is in progress'}. <button className="finish-text-button" onClick={() => onSwitchTarget(otherTarget)}>Open it</button></p>}
    <details className="document-revision-connection" open={connected === false}><summary>ChatGPT connection</summary><ChatGPTConnection onChange={setConnected} preparing={!!running} /></details>
    {running && !applying && <p className="finish-help" role="status">{job?.stage === 'waiting' ? 'Waiting for the ChatGPT connection' : job?.state === 'cancelling' ? 'Stopping revision' : 'Reviewing and preparing a suggestion'}… You can reload this page; your current document is unchanged.</p>}
    {job?.state === 'failed' && <p className="preparation-error" role="alert">{job.error || 'Could not prepare a suggestion. Try again.'}</p>}
    {job?.state === 'cancelled' && <p className="finish-help" role="status">Revision cancelled. Your current document is unchanged.</p>}
    {error && <p className="preparation-error" role="alert">{error}</p>}
    {result && <div className="revision-suggestion">
      <div className="revision-suggestion-head"><div><h4>Suggested version</h4><p>Review the content and any gaps before replacing your current {name}.</p></div><span>{matchesCurrent ? 'Applied' : 'Not applied'}</span></div>
      <p className="finish-help">Your request: {job?.request?.instruction}</p>
      <div className="revision-notes"><div><strong>Review</strong><p>{result.review}</p></div><div><strong>Changes</strong><p>{result.changes}</p></div></div>
      {!valid && !matchesCurrent && <p className="preparation-warning" role="alert">The document, language or profile changed since this suggestion was requested. Request a new suggestion to avoid overwriting newer work.</p>}
      {target === 'cover_letter' ? <div className="revision-comparison"><div><h5>Current</h5><iframe title="Current cover letter" sandbox="" srcDoc={documentHTML(currentDocument, 'Current cover letter')} /></div><div><h5>Suggested</h5><iframe title="Suggested cover letter" sandbox="" srcDoc={documentHTML(result.document, 'Suggested cover letter')} /></div></div>
        : <div className="revision-cv-preview">{preview ? <PDFPreview url={preview} /> : <p className="finish-help">{previewError || 'Preparing the suggested CV preview…'}</p>}<p className="finish-help">The PDF and LaTeX source are the complete CV. Readable CV text is not automatically regenerated.</p><details><summary>Compare LaTeX sources</summary><div className="revision-comparison"><div><h5>Current</h5><pre>{currentDocument}</pre></div><div><h5>Suggested</h5><pre>{result.document}</pre></div></div></details></div>}
      <div className="document-revision-actions">{!matchesCurrent && <button className="primary-button" disabled={!valid || applying || starting} onClick={() => void apply()}>{applying ? 'Checking…' : 'Use this version'}</button>}<button disabled={applying || starting} onClick={() => void discard()}>{matchesCurrent ? 'Dismiss suggestion' : 'Keep current version'}</button></div>
    </div>}
    <p className="finish-help">Suggestions use your saved profile and CV. Check every claim; nothing is sent to an employer.</p>
  </section>
}
