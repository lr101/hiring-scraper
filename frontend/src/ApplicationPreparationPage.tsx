import { selectCVLanguage } from '../../extensions/application-companion/tex-cv.js'
import { downloadApplicationDocument } from './applicationDocumentExport'
import type { DocumentFormat } from './applicationDocumentExport'
import type { LatexDocuments } from './applicationLatex'
import { useEffect, useMemo, useRef, useState } from 'react'
import PDFPreview from './PDFPreview'
import type { Profile } from './profile'
import { applicationDocumentFilename, applicationStartLink, readApplicationReturnInput, assertFinishedText, buildApplicationPrompt, buildFitAssessmentPrompt, readApplicationReturn, documentHTML, documentPlainText, documentSections, parseApplicationPackage, parseApplicationResponse, parseTemplateChanges } from './applicationHandoff'
import type { ApplicationDocuments, ApplicationPackage, HandoffJob, TemplateParagraph } from './applicationHandoff'
import { readApplicationForm } from './applicationForm'
import type { FormAnswers } from './applicationForm'

const API = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '')
const CHATGPT = 'https://chatgpt.com/'

export default function ApplicationPreparationPage({ jobId }: { jobId: number }) {
  const [job, setJob] = useState<HandoffJob | null>(null)
  const [profiles, setProfiles] = useState<Profile[]>([])
  const [profileId, setProfileId] = useState(new URLSearchParams(location.search).get('profile_id') ?? '')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [incoming, setIncoming] = useState(() => ({ hash: /^#applicationz?=/.test(location.hash) ? location.hash : '' }))
  useEffect(() => {
    function receive() {
      if (!/^#applicationz?=/.test(location.hash)) return
      setIncoming({ hash: location.hash })
      // Remove private document data before rendering previews or following links.
      history.replaceState(history.state, '', location.pathname + location.search)
    }
    receive()
    window.addEventListener('hashchange', receive)
    return () => window.removeEventListener('hashchange', receive)
  }, [])
  useEffect(() => {
    const controller = new AbortController()
    async function load() {
      try {
        const [jobResponse, profilesResponse] = await Promise.all([
          fetch(`${API}/api/v1/jobs/${jobId}`, { signal: controller.signal }),
          fetch(`${API}/api/v1/profiles`, { signal: controller.signal }),
        ])
        if (!jobResponse.ok || !profilesResponse.ok) throw new Error('Could not load this application. Go back to the job and try again.')
        const [jobData, profileData] = await Promise.all([jobResponse.json(), profilesResponse.json()])
        setJob(jobData); setProfiles(profileData.items)
        if (!new URLSearchParams(location.search).has('profile_id') && profileData.items.length === 1) setProfileId(String(profileData.items[0].id))
      } catch (reason) { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : 'Could not load this application.') }
      finally { if (!controller.signal.aborted) setLoading(false) }
    }
    void load()
    return () => controller.abort()
  }, [jobId])
  const profile = profiles.find(item => String(item.id) === profileId)
  const href = applicationStartLink(jobId, profile?.id)
  const editHref = profile ? `/profile/${profile.id}/edit?return_to=${encodeURIComponent(href)}#profile-cv` : '/profile/new'
  return <div className="app-shell application-workspace"><main className="content application-finish-page">
    <a className="back-link" href={`/jobs/${jobId}${profileId ? `?profile_id=${profileId}` : ''}`}>← Back to job</a>
    {loading ? <div className="loading-state">Preparing your application…</div> : error ? <p className="error-banner" role="alert">{error}</p> : job && <>
      <header className="application-finish-heading"><h1>{job.company_name}</h1><p>{job.title}</p></header>
      {!profile ? <section className="application-finish-card"><h2>{profiles.length ? 'Choose your profile' : 'Add a profile to get started'}</h2>
        {profiles.length ? <label>Application profile<select value={profileId} onChange={event => {
          setProfileId(event.target.value)
          history.replaceState({}, '', applicationStartLink(jobId, Number(event.target.value)))
        }}><option value="">Choose a profile</option>{profiles.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
          : <a className="primary-button" href={`/profile/new?return_to=${encodeURIComponent(applicationStartLink(jobId))}`}>Create profile</a>}</section>
        : !profile.cv ? <section className="application-finish-card"><h2>Attach your CV once</h2><p>Add your CV to {profile.name}. It will be ready for this application and every one after it.</p>
          <a className="primary-button" href={editHref}>Attach CV to profile →</a></section>
          : <ApplicationFinish key={profile.id} job={job} profile={profile} editHref={editHref} incoming={incoming} />}
    </>}
  </main></div>
}

function ApplicationFinish({ job, profile, editHref, incoming }: { job: HandoffJob; profile: Profile; editHref: string; incoming: { hash: string } }) {
  const [cvText, setCVText] = useState('')
  const [loading, setLoading] = useState(true)
  const [step, setStep] = useState<'handoff' | 'response' | 'edit'>('handoff')
  const [language, setLanguage] = useState('the language of the job posting')
  const [briefType, setBriefType] = useState<'application' | 'fit'>('application')
  const [documentLayout, setDocumentLayout] = useState<'original' | 'latex'>('latex')
  const [latexDocuments, setLatexDocuments] = useState<LatexDocuments>()
  const [notes, setNotes] = useState('')
  const [response, setResponse] = useState('')
  const [documents, setDocuments] = useState<ApplicationDocuments>({ cv: '', coverLetter: '', answers: '', reviewNotes: '' })
  const [form, setForm] = useState(() => readApplicationForm(job.id, profile.id!))
  const [formAnswers, setFormAnswers] = useState<FormAnswers>()
  useEffect(() => {
    const update = () => setForm(readApplicationForm(job.id, profile.id!))
    document.addEventListener('radius-form-updated', update); window.addEventListener('storage', update)
    // A capture may arrive after the initial render but before we subscribe.
    update()
    return () => { document.removeEventListener('radius-form-updated', update); window.removeEventListener('storage', update) }
  }, [job.id, profile.id])
  const [selected, setSelected] = useState<keyof ApplicationDocuments>('cv')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [manual, setManual] = useState(false)
  const [manualResult, setManualResult] = useState(false)
  const [optionsOpen, setOptionsOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [template, setTemplate] = useState<{ source_sha256: string; paragraphs: TemplateParagraph[] } | null>(null)
  const [changes, setChanges] = useState<TemplateParagraph[]>([])
  const [pdf, setPDF] = useState<{ url: string; body: string } | null>(null)
  const [previewBusy, setPreviewBusy] = useState(false)
  const [previewError, setPreviewError] = useState('')
  const [draftReady, setDraftReady] = useState(false)
  const [storageError, setStorageError] = useState('')
  const [draftExpiresAt, setDraftExpiresAt] = useState(0)
  const restored = useRef(false)
  const skipHydrationSave = useRef(false)
  const lastReturn = useRef<object | null>(null)
  const importVersion = useRef(0)
  const pdfURL = useRef('')
  const promptRef = useRef<HTMLTextAreaElement>(null)
  useEffect(() => {
    const controller = new AbortController()
    setLoading(true); setCVText(''); setError('')
    async function get(path: string) {
      const result = await fetch(`${API}/api/v1/profiles/${profile.id}/cv/${path}`, { signal: controller.signal, cache: 'no-store' })
      const data = await result.json()
      if (!result.ok) throw new Error(data.detail ?? 'Could not read the saved CV.')
      return data
    }
    Promise.all([get(`text?language=${selectCVLanguage(language, job.description ?? '')}`), Promise.resolve(null)])
      .then(([data, layout]) => { setCVText(data.text); setTemplate(layout) })
      .catch(reason => { if (!controller.signal.aborted) setError(reason.message) })
      .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [profile.id, language, job.description])
  const context = useMemo(() => ({ job_id: job.id, profile_id: profile.id!, cv_updated_at: profile.cv!.updated_at }), [job.id, profile.id, profile.cv?.updated_at])
  const applicationLanguage = selectCVLanguage(language, job.description ?? '') === 'de' ? 'German' : 'English'
  const returnURL = new URL(applicationStartLink(job.id, profile.id), location.origin).href
  const draftKey = `radius:application-draft:v1:${job.id}:${profile.id}`
  const prompt = useMemo(() => briefType === 'fit' ? buildFitAssessmentPrompt({ job, profile, language: applicationLanguage, cvText })
    : buildApplicationPrompt({ job, profile, language: applicationLanguage, cvText, notes, templateParagraphs: template?.paragraphs,
      handoff: { ...context, return_url: returnURL }, form }), [briefType, job, profile, applicationLanguage, cvText, notes, template, context, returnURL, form])
  useEffect(() => {
    if (loading || !cvText || restored.current) return
    restored.current = true
    try {
      const raw = localStorage.getItem(draftKey)
      const saved = raw && raw.length <= 450000 ? JSON.parse(raw) : null
      if (saved && Date.now() - saved.saved_at < 7 * 86400000 && saved.saved_at <= Date.now() && JSON.stringify(saved.context) === JSON.stringify(context)) {
        if (saved.package) {
          const imported = parseApplicationPackage(JSON.stringify(saved.package), context, template?.paragraphs, undefined, cvText)
          setDocuments(imported.documents); setChanges(imported.changes)
          setLatexDocuments(imported.latexDocuments)
          if (imported.latexDocuments) setDocumentLayout('latex')
          setFormAnswers(imported.formAnswers)
        }
        if (['handoff', 'response', 'edit'].includes(saved.step) && (saved.step !== 'edit' || saved.package)) setStep(saved.step)
        if (typeof saved.response === 'string' && saved.response.length <= 200000) setResponse(saved.response)
        if (typeof saved.language === 'string' && saved.language.length < 100) setLanguage(saved.language)
        if (typeof saved.notes === 'string' && saved.notes.length <= 5000) setNotes(saved.notes)
        setDraftExpiresAt(saved.saved_at + 7 * 86400000)
        skipHydrationSave.current = true
      } else if (raw) localStorage.removeItem(draftKey)
    } catch {
      // Discard corrupt drafts as well; never retain an unusable document silently.
      try { localStorage.removeItem(draftKey) } catch { /* Storage may be disabled. */ }
    }
    setDraftReady(true)
  }, [loading, cvText, draftKey, context, template])
  useEffect(() => {
    if (!draftReady || !incoming.hash || lastReturn.current === incoming) return
    let cancelled = false
    const version = ++importVersion.current
    void readApplicationReturn(incoming.hash).then(text => {
      if (cancelled || version !== importVersion.current) return
      lastReturn.current = incoming
      const imported = parseApplicationPackage(text ?? '', context, template?.paragraphs, form, cvText)
      setDocuments(imported.documents); setChanges(imported.changes); setSelected('cv'); setStep('edit'); setError('')
      setLatexDocuments(imported.latexDocuments)
      if (imported.latexDocuments) setDocumentLayout('latex')
      setFormAnswers(imported.formAnswers)
      setNotice(''); setOptionsOpen(false)
    }).catch(reason => {
      if (cancelled || version !== importVersion.current) return
      lastReturn.current = incoming
      setError(reason instanceof Error ? reason.message : 'Could not import this return link. Import the application package file instead.')
    })
    return () => { cancelled = true }
  }, [draftReady, incoming, context, template, form])
  useEffect(() => {
    if (!draftReady) return
    // Restoring a draft is not an edit and must not extend its expiry window.
    if (skipHydrationSave.current) { skipHydrationSave.current = false; return }
    const result: ApplicationPackage | null = documents.cv ? { format: 'radius-application-v1', ...context,
      cv: template ? changes : documents.cv, cover_letter: documents.coverLetter, application_answers: documents.answers, review_notes: documents.reviewNotes, ...(formAnswers ? {form_answers:formAnswers} : {}), ...(latexDocuments ? {latex_documents:latexDocuments} : {}) } : null
    // No original CV bytes are copied into browser storage. Drafts expire in a week.
    if (step === 'handoff' && !result && !response && !notes) return
    try {
      const savedAt = Date.now()
      localStorage.setItem(draftKey, JSON.stringify({ context, package: result, step, language, notes, response, saved_at: savedAt }))
      setDraftExpiresAt(savedAt + 7 * 86400000)
      setStorageError('')
    } catch { setStorageError('This browser cannot save your draft. Export your documents before closing the page.') }
  }, [draftReady, draftKey, context, documents, changes, template, step, language, notes, response, formAnswers, latexDocuments])
  useEffect(() => {
    if (!draftExpiresAt) return
    function expire() {
      if (Date.now() < draftExpiresAt) return
      clearDraft()
      setNotice('Your browser draft expired after 7 days without changes. Your original CV is still attached.')
    }
    const timer = setTimeout(expire, Math.max(0, draftExpiresAt - Date.now()))
    window.addEventListener('focus', expire)
    return () => { clearTimeout(timer); window.removeEventListener('focus', expire) }
  }, [draftExpiresAt])
  const exportBody = JSON.stringify(template ? { source_sha256: template.source_sha256, changes } : { job_id: context.job_id, cv_updated_at: context.cv_updated_at, cv: documents.cv, cover_letter: documents.coverLetter, latex_documents: latexDocuments })
  useEffect(() => {
    if (step !== 'edit' || selected !== 'cv' || (!template && !latexDocuments?.cv)) return
    const controller = new AbortController()
    setPreviewBusy(true); setPreviewError('')
    const timer = setTimeout(() => {
      fetch(`${API}/api/v1/profiles/${profile.id}/${template ? 'cv/export?format=pdf&preview=true' : 'application-document?kind=cv&format=latex_pdf'}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: exportBody, signal: controller.signal,
      }).then(async result => {
        if (!result.ok) { const data = await result.json(); throw new Error(data.detail ?? 'Could not preview the template.') }
        const blob = await result.blob()
        if (controller.signal.aborted) return
        if (pdfURL.current) URL.revokeObjectURL(pdfURL.current)
        pdfURL.current = URL.createObjectURL(blob)
        setPDF({ url: pdfURL.current, body: exportBody })
      }).catch(reason => { if (!controller.signal.aborted) setPreviewError(reason.message) })
        .finally(() => { if (!controller.signal.aborted) setPreviewBusy(false) })
    }, 1000)
    return () => { clearTimeout(timer); controller.abort() }
  }, [step, selected, template, profile.id, exportBody, latexDocuments?.cv])
  useEffect(() => () => { if (pdfURL.current) URL.revokeObjectURL(pdfURL.current) }, [])
  const section = documentSections.find(item => item.key === selected)!
  const text = documents[selected]
  const finalCVText = template ? template.paragraphs.map(row => changes.find(change => change.id === row.id)?.text ?? row.text).join('\n') : documents.cv

  async function start(openChatGPT = true) {
    setError(''); setNotice(''); setBusy(true); setManualResult(false); setOptionsOpen(false); setManual(false)
    // Copy during the click, before opening a tab can take focus away. The
    // gesture-based copy also works in browsers that block the Clipboard API.
    const copying = (async () => {
      const field = document.createElement('textarea')
      const previousFocus = document.activeElement
      field.value = prompt; field.readOnly = true
      field.style.cssText = 'position:fixed;left:-9999px;top:0'
      document.body.appendChild(field)
      try {
        field.focus({ preventScroll: true }); field.select()
        if (document.execCommand('copy')) return true
      } catch { /* Try the Clipboard API when gesture-based copying is unavailable. */ }
      finally {
        field.remove()
        if (previousFocus instanceof HTMLElement) previousFocus.focus({ preventScroll: true })
      }
      try { await navigator.clipboard.writeText(prompt); return true }
      catch { return false }
    })()
    // Navigation must not depend on clipboard permission or an async result.
    const target = openChatGPT ? window.open(CHATGPT, '_blank') : null
    if (target) target.opener = null
    try {
      if (briefType === 'application') setStep('response')
      if (await copying) {
        if (openChatGPT && !target) setManual(true)
        setNotice(target || !openChatGPT ? 'Brief copied. Paste it in ChatGPT and send.' : 'Brief copied. Use Open ChatGPT to continue.')
      } else {
        setManual(true)
        setError('Clipboard access was blocked. Copy the selected brief, then paste it in ChatGPT.')
        setTimeout(() => { promptRef.current?.focus(); promptRef.current?.select() }, 0)
      }
    } finally { setBusy(false) }
  }

  async function importResponse(input = response) {
    const version = ++importVersion.current
    setError(''); setNotice('')
    try {
      const text = await readApplicationReturnInput(input, returnURL)
      if (version !== importVersion.current) return
      // Existing four-section replies remain importable from the manual field.
      const legacy = text.trim().replace(/^```(?:markdown|text)?\s*\n/, '').startsWith('<<<CV>>>')
      const parsed = legacy ? parseApplicationResponse(text) : null
      const imported = parsed && !cvText.includes('% radius:entry ') ? { documents: parsed, changes: template ? parseTemplateChanges(parsed.cv, template.paragraphs) : [], formAnswers: undefined, latexDocuments: undefined }
        : parseApplicationPackage(text, context, template?.paragraphs, form, cvText)
      if (template) assertFinishedText(template.paragraphs.map(row => imported.changes.find(change => change.id === row.id)?.text ?? row.text).join('\n'))
      setDocuments(imported.documents); setChanges(imported.changes); setSelected('cv'); setStep('edit')
      setLatexDocuments(imported.latexDocuments)
      if (imported.latexDocuments) setDocumentLayout('latex')
      setFormAnswers(imported.formAnswers)
      setNotice(''); setOptionsOpen(false)
    }
    catch (reason) { if (version === importVersion.current) setError(reason instanceof Error ? reason.message : 'Could not read this response.') }
  }

  async function pastePackage() {
    try {
      const value = await navigator.clipboard.readText()
      setResponse(value); await importResponse(value)
    } catch {
      setError('Paste the copied result in the box.')
      document.getElementById('application-package')?.focus()
    }
  }

  async function importFile(file?: File) {
    if (!file) return
    if (file.size > 200000) { setError('Choose an application package file under 200 KB.'); return }
    try { const value = await file.text(); setResponse(value); await importResponse(value) }
    catch { setError('Could not read this file. Copy and paste the application package instead.') }
  }

  function clearDraft() {
    importVersion.current++
    setManualResult(false); setOptionsOpen(false)
    try { localStorage.removeItem(draftKey); setStorageError('') }
    catch { setStorageError('This browser could not remove the saved draft. Clear this site’s browser storage to remove it.') }
    setDocuments({ cv: '', coverLetter: '', answers: '', reviewNotes: '' }); setChanges([]); setResponse(''); setNotes(''); setFormAnswers(undefined)
    setLatexDocuments(undefined)
    setLanguage('the language of the job posting'); setDraftExpiresAt(0)
    setStep('handoff'); setError(''); setNotice('Browser draft cleared. Your original CV is still attached.'); setManual(false)
  }

  async function downloadDocument(format: DocumentFormat) {
    setBusy(true); setError('')
    try {
      const kind = selected === 'cv' ? 'cv' : 'cover_letter'
      assertFinishedText(selected === 'cv' ? finalCVText : text)
      await downloadApplicationDocument({ ...context, cv: template ? changes : documents.cv, cover_letter: documents.coverLetter, latex_documents: latexDocuments }, kind, format, applicationDocumentFilename(job.company_name, kind === 'cv' ? 'cv' : 'coverLetter', format === 'latex_pdf' ? 'pdf' : format))
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not export this document.') }
    finally { setBusy(false) }
  }

  function printDocument() {
    try { assertFinishedText(text) } catch (reason) { setError((reason as Error).message); return }
    const target = window.open('about:blank', '_blank')
    if (!target) { setError('Allow popups to open the print window.'); return }
    target.opener = null
    target.document.open(); target.document.write(documentHTML(text, applicationDocumentFilename(job.company_name, selected, 'pdf').replace(/\.pdf$/, ''))); target.document.close()
    target.focus(); target.print()
    setNotice('Choose Save as PDF. Turn off browser headers and footers for a clean document.')
  }

  async function copyDocument() {
    setError(''); setNotice('')
    try { assertFinishedText(text) } catch (reason) { setError((reason as Error).message); return }
    try {
      if (typeof ClipboardItem !== 'undefined' && navigator.clipboard.write) await navigator.clipboard.write([new ClipboardItem({
        'text/plain': new Blob([documentPlainText(text)], { type: 'text/plain' }),
        'text/html': new Blob([documentHTML(text, section.label)], { type: 'text/html' }),
      })])
      else await navigator.clipboard.writeText(documentPlainText(text))
      setNotice(`${section.label} copied for your document editor.`)
    } catch { setError('Clipboard access was blocked. Copy the document text directly from the editor.') }
  }

  return <>
    {!loading && cvText && <span hidden className="application-cv-ready" data-original-cv-text={cvText} />}
    {step === 'handoff' && <div className="application-cv-ready"><span aria-hidden="true">✓</span><div><strong>{profile.cv?.languages?.[applicationLanguage === 'German' ? 'de' : 'en']?.filename ?? profile.cv?.filename}</strong></div><a href={editHref}>Change CV</a></div>}
    {(job.expired || job.is_active === false) && <p className="preparation-warning">This posting is closed or expired. Check the original post before applying.</p>}
    <ol className="finish-progress" aria-label="Application steps"><li aria-current={step === 'handoff' ? 'step' : undefined}>1 · ChatGPT</li><li aria-current={step === 'response' ? 'step' : undefined}>2 · Return</li><li aria-current={step === 'edit' ? 'step' : undefined}>3 · Download</li></ol>
    <section className="application-finish-card">
      {step === 'handoff' && <>
        <h2>{briefType === 'fit' ? 'Is this role worth applying for?' : 'Tailor this application.'}</h2>
        <label>Brief type<select aria-label="Brief type" value={briefType} onChange={event => { setBriefType(event.target.value as 'application' | 'fit'); setNotice(''); setError('') }}>
          <option value="application">Prepare application documents</option><option value="fit">Assess job fit</option>
        </select></label>
        <p>{briefType === 'fit' ? 'Compare the role with your evidence, career goals and working preferences in ChatGPT. The assessment stays in that conversation.' : 'Paste the brief in ChatGPT. It saves your LaTeX in code Canvases, then creates a return link containing that source.'}</p>
        <p className="finish-help"><a href={editHref.replace('#profile-cv', '#profile-personalization')}>Edit career evidence and writing voice</a></p>
        {form && <p className="finish-help">This application step’s questions and answer limits are included in your brief.</p>}
        <button className="primary-button" disabled={loading || !cvText || busy || !draftReady} onClick={() => void start()}>{loading ? 'Reading CV…' : briefType === 'fit' ? 'Assess fit in ChatGPT ↗' : 'Copy brief & open ChatGPT ↗'}</button>
        {briefType === 'fit' && manual && <a className="page-link" href={CHATGPT} target="_blank" rel="noreferrer">Open ChatGPT ↗</a>}
      </>}
      {step === 'response' && <>
        <h2>Finish in ChatGPT.</h2>
        <p>Click <strong>Return to Radius with my documents</strong> in ChatGPT.</p>
        <div className="finish-actions"><a className="primary-button" href={CHATGPT} target="_blank" rel="noreferrer">Open ChatGPT ↗</a></div>
        <details className="finish-options" open={manualResult} onToggle={event => setManualResult(event.currentTarget.open)}><summary>Import manually</summary>
          <label>Import result file<input type="file" accept=".json,.txt,application/json,text/plain" onChange={event => { void importFile(event.target.files?.[0]); event.currentTarget.value = '' }} /></label>
          <label htmlFor="application-package">Or paste a result<textarea id="application-package" aria-label="Application package" rows={5} value={response} maxLength={200000} onChange={event => setResponse(event.target.value)}
            onPaste={event => { const value = event.clipboardData.getData('text/plain'); if (value) { event.preventDefault(); setResponse(value); void importResponse(value) } }}
            placeholder="Paste the return link or package from ChatGPT." /></label>
          <button onClick={() => void pastePackage()}>Paste & open documents</button>
          {response.trim() && <button onClick={() => void importResponse()}>Open documents</button>}
          {documents.cv && <button className="finish-text-button" onClick={() => { setStep('edit'); setError(''); setManualResult(false) }}>Keep current documents</button>}
        </details>
      </>}
      {step === 'edit' && <>
        <h2>Your documents are ready.</h2>
        <div className="finish-document-picker" role="group" aria-label="Choose document">{documentSections.filter(item => item.key !== 'reviewNotes').map(item => <button key={item.key} aria-pressed={selected === item.key} onClick={() => { setSelected(item.key); setNotice(''); setError('') }}>{item.label}</button>)}</div>
        {selected === 'cv' && <p className="finish-help">CV downloads use LaTeX rendering. <a href={editHref}>Manage English and German base CVs</a>.</p>}
        {selected === 'coverLetter' && <>
          <label>PDF layout<select aria-label="PDF layout" value={documentLayout} onChange={event => setDocumentLayout(event.target.value as 'original' | 'latex')}>
            <option value="original">Standard layout</option><option value="latex">LaTeX · single column</option>
          </select></label>
          {documentLayout === 'latex' && <p className="finish-help">{latexDocuments?.cover_letter ? 'Downloads use the LaTeX source returned from ChatGPT. The link captures a snapshot; regenerate it after editing a Canvas.' : 'LaTeX creates a new layout from your finished text.'} Download the PDF to see the compiled layout.</p>}
        </>}
        <div className="finish-actions finish-download-actions">{(selected === 'cv' || selected === 'coverLetter') ? <>
          <button className="primary-button" disabled={busy} onClick={() => void downloadDocument(selected === 'cv' || documentLayout === 'latex' ? 'latex_pdf' : 'pdf')}>{busy ? 'Preparing…' : 'Download PDF'}</button>
          {selected !== 'cv' && <button disabled={busy} onClick={() => void downloadDocument('docx')}>Download editable DOCX</button>}
          {(selected === 'cv' || documentLayout === 'latex') && <button disabled={busy} onClick={() => void downloadDocument('tex')}>Download LaTeX source</button>}
        </> : <><button className="primary-button" disabled={!text.trim()} onClick={printDocument}>Print / save PDF</button><button disabled={!text.trim()} onClick={() => void copyDocument()}>Copy to document editor</button></>}</div>
        {selected === 'answers' && formAnswers && <section aria-label="Prepared form answers"><h3>Answers for the captured step</h3>{form?.form_id !== formAnswers.form_id && <p className="finish-help">These answers belong to an earlier capture. Question IDs are shown because the current captured form does not match.</p>}{formAnswers.answers.map(answer => <div key={answer.id}><strong>{(form?.form_id === formAnswers.form_id ? form.fields.find(field => field.id === answer.id)?.label : undefined) ?? answer.id}</strong><pre>{answer.value}</pre></div>)}<p className="finish-help">These answers fill only the unchanged captured form. Editing the reference text clears them.</p></section>}
        <div className="finish-download-preview">{selected === 'cv' ? <div className="template-pdf-preview">{previewBusy && <p role="status">Preparing preview…</p>}
          {previewError && <p role="alert">{previewError}</p>}{pdf?.body === exportBody && <PDFPreview key={pdf.url} url={pdf.url} />}</div>
          : <iframe title={`${section.label} preview`} sandbox="" srcDoc={documentHTML(text, section.label)} />}</div>
        <details className="finish-options"><summary>{selected === 'cv' ? 'Edit LaTeX source' : 'Edit document text'}</summary>
          {selected === 'cv' && !template ? <label>CV LaTeX source<textarea aria-label="CV LaTeX source" rows={18} maxLength={100000} value={latexDocuments?.cv ?? ''} onChange={event => setLatexDocuments(current => ({ ...current, cv: event.target.value }))} /><span>Keep the marked history and verified metadata intact. The PDF preview updates after edits.</span></label> : selected === 'cv' && template ? <div className="template-paragraph-editor"><p className="finish-help">Longer text can shift page breaks.</p>
            {template.paragraphs.map(row => <label key={row.id}>Paragraph {row.id + 1}<textarea aria-label={`CV paragraph ${row.id + 1}`} rows={Math.max(1, Math.min(4, Math.ceil(row.text.length / 65)))} maxLength={5000}
              value={changes.find(change => change.id === row.id)?.text ?? row.text} onChange={event => { setLatexDocuments(current => current ? { ...current, cv: undefined } : undefined); setChanges(current => {
                const next = current.filter(change => change.id !== row.id)
                return event.target.value === row.text ? next : [...next, { id: row.id, text: event.target.value }]
              }) }} /></label>)}</div>
            : <label>{section.label} text<textarea aria-label={`${section.label} text`} rows={18} value={text} onChange={event => { setDocuments(current => ({ ...current, [selected]: event.target.value })); if (selected === 'answers') setFormAnswers(undefined); if (selected === 'cv' || selected === 'coverLetter') setLatexDocuments(current => current ? { ...current, [selected === 'cv' ? 'cv' : 'cover_letter']: undefined } : undefined) }} /></label>}
        </details>
      </>}
      <details className="finish-options finish-secondary-options" open={optionsOpen} onToggle={event => setOptionsOpen(event.currentTarget.open)}><summary>Options</summary>
        {step === 'handoff' && <>
          <label>Document language<select aria-label="Document language" value={language} onChange={event => setLanguage(event.target.value)}>
            <option value="the language of the job posting">Match the job posting</option><option>English</option><option>German</option>
          </select></label>
          <p className="finish-help">Selected base CV: {applicationLanguage}. An explicit language selection overrides automatic detection.</p>
          <label>Additional instructions<textarea value={notes} maxLength={5000} rows={3} onChange={event => setNotes(event.target.value)} placeholder="Optional instructions for this application" /></label>
          {profile.custom_instructions?.trim() && <a className="finish-help" href={editHref.replace('#profile-cv', '#profile-custom-instructions')}>Edit profile instructions</a>}
          <button disabled={loading || !draftReady} onClick={() => { setOptionsOpen(false); setManualResult(true); setStep('response') }}>I already have a result</button>
        </>}
        {step !== 'handoff' && <button onClick={() => { setStep('handoff'); setOptionsOpen(false); setError('') }}>Back to brief</button>}
        {step === 'edit' && <button onClick={() => { setOptionsOpen(false); setManualResult(true); setStep('response') }}>Paste a new response</button>}
        {step !== 'edit' && <button disabled={!cvText} onClick={() => { setOptionsOpen(false); setManual(true); setTimeout(() => { promptRef.current?.focus(); promptRef.current?.select() }, 0) }}>Show brief</button>}
        {draftExpiresAt > 0 && <button onClick={clearDraft}>Clear browser draft</button>}
        {step === 'edit' && documents.reviewNotes && <p className="finish-help">{documents.reviewNotes}</p>}
      </details>
      {manual && step !== 'edit' && cvText && <div className="finish-manual">
        <label>Brief to copy<textarea ref={promptRef} aria-label="Prepared application prompt" value={prompt} readOnly rows={6} /></label>
        <button disabled={busy} onClick={() => void start(false)}>Copy brief</button>
      </div>}
      {notice && <p className="preparation-notice" role="status">{notice}</p>}
      {error && <p className="preparation-error" role="alert">{error} {!cvText && !loading && <a href={editHref}>Update CV</a>}</p>}
      {storageError && <p className="preparation-error" role="alert">{storageError}</p>}
    </section>
    {step !== 'handoff' && !storageError && <p className="finish-saved-note">Saved on this device · 7 days</p>}
  </>
}
