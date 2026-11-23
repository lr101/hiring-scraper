import { useEffect, useRef, useState } from 'react'
import ChatGPTConnection from './ChatGPTConnection'
import { selectCVLanguage } from '../../extensions/application-companion/tex-cv.js'
import { DocumentEditor } from './documents/DocumentEditor'
import type { CvDocument, Finding } from './documents/types'
import { findUnitById, updateUnitText } from './documents/editing'
import {
  applicationPostingURL, applicationRequest, applicationStartLink, isActive, jsonRequest, pollDocumentPdf,
  preparationStages, taskErrorMessage,
} from './applicationDocuments'
import type { DocumentKind, Documents, Language, Mode, Revision, Structure, Task, Version } from './applicationDocuments'
import { readApplicationForm } from './applicationForm'
import { useApplicationDraft } from './useApplicationDraft'
import type { Profile } from './profile'
import './application-preparation.css'

const API = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '')
const KINDS = ['cv', 'letter'] as const
const LABELS = { cv: 'CV', letter: 'Cover letter', review: 'Review' }
type Job = { id: number; title: string; company_name: string; url?: string; description?: string | null; expired?: boolean; is_active?: boolean }

function message(reason: unknown) {
  return reason instanceof Error ? reason.message : 'Radius could not complete this request. Try again.'
}

export default function ApplicationPreparationPage({ jobId }: { jobId: number }) {
  const [data, setData] = useState<{ job: Job; profiles: Profile[] }>()
  const [profileId, setProfileId] = useState(new URLSearchParams(location.search).get('profile_id') ?? '')
  const [error, setError] = useState('')

  useEffect(() => {
    const controller = new AbortController()
    const { signal } = controller
    void Promise.all([
      applicationRequest<Job>(`${API}/api/v1/jobs/${jobId}`, { signal }),
      applicationRequest<{ items: Profile[] }>(`${API}/api/v1/profiles`, { signal }),
    ]).then(([job, profiles]) => {
      if (signal.aborted) return
      setData({ job, profiles: profiles.items })
      if (!new URLSearchParams(location.search).has('profile_id') && profiles.items.length === 1) {
        setProfileId(String(profiles.items[0].id))
      }
    }).catch(reason => { if (!signal.aborted) setError(message(reason)) })
    return () => controller.abort()
  }, [jobId])

  const profile = data?.profiles.find(item => String(item.id) === profileId)
  const returnTo = applicationStartLink(jobId, profile?.id)
  const editHref = profile ? `/profile/${profile.id}/edit?return_to=${encodeURIComponent(returnTo)}#profile-cv` : '/profile/new'

  return <div className="app-shell application-workspace"><main className="content application-finish-page">
    <a className="back-link" href={`/jobs/${jobId}${profileId ? `?profile_id=${encodeURIComponent(profileId)}` : ''}`}>← Back to job</a>
    {error ? <p className="error-banner" role="alert">{error}</p> : !data ? <p role="status">Loading application…</p> : <>
      <header className="application-finish-heading"><h1>{data.job.company_name}</h1><p>{data.job.title}</p></header>
      {!profile ? <section className="application-finish-card"><h2>{data.profiles.length ? 'Choose your profile' : 'Add a profile to get started'}</h2>
        {data.profiles.length ? <label>Application profile<select value={profileId} onChange={event => {
          const next = event.target.value
          setProfileId(next)
          history.replaceState(history.state, '', applicationStartLink(jobId, Number(next)))
        }}><option value="">Choose a profile</option>{data.profiles.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
          : <a className="primary-button" href={`/profile/new?return_to=${encodeURIComponent(returnTo)}`}>Create profile</a>}
      </section> : !profile.cv ? <section className="application-finish-card"><h2>Attach a base CV</h2>
        <p>Add your CV to {profile.name} to prepare an application.</p>
        <a className="primary-button" href={editHref}>Attach CV to profile</a>
      </section> : <Preparation key={`${data.job.id}:${profile.id}`} job={data.job} profile={profile} editHref={editHref} />}
    </>}
  </main></div>
}

function Preparation({ job, profile, editHref }: { job: Job; profile: Profile; editHref: string }) {
  const root = `${API}/api/v1/profiles/${profile.id}`
  const documentsUrl = `${root}/application-documents`
  const query = `?job_id=${job.id}`
  const cv = useApplicationDraft(`${documentsUrl}/cv${query}`)
  const letter = useApplicationDraft(`${documentsUrl}/letter${query}`)
  const [language, setLanguage] = useState<Language>(() => selectCVLanguage('auto', `${job.title}\n${job.description ?? ''}`) === 'de' ? 'de' : 'en')
  const [mode, setMode] = useState<Mode>('full')
  const [base, setBase] = useState<Structure>()
  const [baseBody, setBaseBody] = useState<CvDocument | null>(null)
  const [baseError, setBaseError] = useState('')
  const [baseOpen, setBaseOpen] = useState(false)
  const [baseSaving, setBaseSaving] = useState(false)
  const [baseReload, setBaseReload] = useState(0)
  const [task, setTask] = useState<Task | null>(null)
  const [taskLoaded, setTaskLoaded] = useState(false)
  const [documentsLoaded, setDocumentsLoaded] = useState(false)
  const [pollError, setPollError] = useState('')
  const [actionError, setActionError] = useState('')
  const [busy, setBusy] = useState(false)
  const taskMutation = useRef(false)
  const pollEpoch = useRef(0)
  const taskSettingsLoaded = useRef(false)
  const [refresh, setRefresh] = useState(0)
  const [selectedKind, setSelectedKind] = useState<DocumentKind>()
  const [dismissedOutput, setDismissedOutput] = useState<string[]>([])
  const [form, setForm] = useState(() => readApplicationForm(job.id, profile.id!))
  const [connected, setConnected] = useState(false)
  const closed = job.expired || job.is_active === false
  const postingUrl = applicationPostingURL(job.url)
  const baseDirty = baseBody !== (base?.document ?? null)
  const active = isActive(task)
  const selected = selectedKind ?? (cv.body ? 'cv' : 'letter')
  const currentDraft = selected === 'cv' ? cv : letter
  const structureUrl = `${root}/cv/structure?language=${language}`
  const photoUrl = profile.photo ? `${root}/photo?v=${encodeURIComponent(profile.photo.updated_at)}` : undefined

  useEffect(() => {
    const controller = new AbortController()
    setBase(undefined)
    setBaseBody(null)
    setBaseError('')
    void applicationRequest<Structure>(structureUrl, { signal: controller.signal }).then(data => {
      if (controller.signal.aborted) return
      setBase(data)
      setBaseBody(data.document)
      setBaseOpen(data.source !== 'stored')
    }).catch(reason => { if (!controller.signal.aborted) setBaseError(message(reason)) })
    return () => controller.abort()
  }, [structureUrl, baseReload])

  useEffect(() => {
    const update = () => {
      const next = readApplicationForm(job.id, profile.id!)
      setForm(current => current?.form_id === next?.form_id ? current : next)
    }
    document.addEventListener('radius-form-updated', update)
    window.addEventListener('storage', update)
    update()
    return () => {
      document.removeEventListener('radius-form-updated', update)
      window.removeEventListener('storage', update)
    }
  }, [job.id, profile.id])

  const receiveCV = cv.receive
  const receiveLetter = letter.receive
  useEffect(() => {
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout>
    async function poll() {
      const epoch = pollEpoch.current
      const signal = AbortSignal.any([controller.signal, AbortSignal.timeout(15000)])
      const [status, latest] = await Promise.allSettled([
        applicationRequest<{ task: Task | null }>(`${documentsUrl}/task${query}`, { signal }),
        applicationRequest<Documents>(documentsUrl + query, { signal }),
      ])
      if (controller.signal.aborted) return
      if (latest.status === 'fulfilled') {
        receiveCV(latest.value.cv)
        receiveLetter(latest.value.letter)
        setDocumentsLoaded(true)
      }
      if (status.status === 'fulfilled' && epoch === pollEpoch.current && !taskMutation.current) {
        const next = status.value.task
        if (!taskSettingsLoaded.current && next?.preparation) {
          setLanguage(next.preparation.language)
          setMode(next.preparation.mode)
        }
        taskSettingsLoaded.current = true
        setTask(next)
        setTaskLoaded(true)
      }
      const failure = [status, latest].find(item => item.status === 'rejected')
      setPollError(failure ? 'Could not refresh preparation. Radius will try again.' : '')
      timer = setTimeout(() => void poll(), status.status === 'fulfilled' && !isActive(status.value.task) ? 5000 : 1500)
    }
    void poll()
    return () => { controller.abort(); clearTimeout(timer) }
  }, [documentsUrl, query, receiveCV, receiveLetter, refresh])

  const unsaved = baseDirty || cv.dirty || letter.dirty
  useEffect(() => {
    if (!unsaved) return
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = '' }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [unsaved])

  async function taskAction(url: string, payload: unknown) {
    if (taskMutation.current) return
    taskMutation.current = true
    pollEpoch.current++
    setBusy(true)
    setActionError('')
    try {
      const result = await applicationRequest<{ task: Task }>(url, jsonRequest('POST', payload, AbortSignal.timeout(20000)))
      setTask(result.task)
      setTaskLoaded(true)
    } catch (reason) { setActionError(message(reason)) }
    finally {
      taskMutation.current = false
      setBusy(false)
      setRefresh(current => current + 1)
    }
  }

  async function saveBase() {
    if (!baseBody || baseSaving) return
    setBaseSaving(true)
    setBaseError('')
    try {
      const saved = await applicationRequest<Structure>(structureUrl, jsonRequest('PUT', { document: baseBody }, AbortSignal.timeout(20000)))
      setBase(saved)
      setBaseBody(saved.document)
      setBaseOpen(false)
      setRefresh(current => current + 1)
    } catch (reason) { setBaseError(message(reason)) }
    finally { setBaseSaving(false) }
  }

  const canPrepare = !closed && !active && !busy && connected && taskLoaded
    && base?.source === 'stored' && !baseDirty && !baseSaving
  function prepare() {
    if (canPrepare) void taskAction(`${documentsUrl}/prepare`, { job_id: job.id, language, mode })
  }

  const canRevise = connected && taskLoaded && !closed && !active && !busy
  function revise(kind: DocumentKind, unitId: string, instruction: string, version: number) {
    void taskAction(`${documentsUrl}/${kind}/revise${query}`, {
      unit_id: unitId, instruction, expected_version: version,
    })
  }

  const retryRevision = task?.kind === 'revise' ? task.revision : undefined
  const revisionDraft = retryRevision?.document === 'cv' ? cv : letter
  const retryVersion = revisionDraft.draft?.saved.version
  const retryUnit = retryRevision && revisionDraft.body && findUnitById(revisionDraft.body, retryRevision.unit_id)
  const canRetryRevision = canRevise && retryVersion !== undefined && retryUnit
    && !revisionDraft.dirty && !revisionDraft.saving && !revisionDraft.conflict
  const retryPrepare = task?.kind === 'prepare' && (task.state === 'failed' || task.state === 'cancelled')
  const retryMatchesControls = !task?.preparation || (task.preparation.language === language && task.preparation.mode === mode)

  return <div className="application-preparation-v2">
    {closed && <p className="preparation-warning">This posting is closed or expired. Check the original posting before applying.</p>}
    <section className="application-finish-card" aria-labelledby="prepare-documents-heading">
      <h2 id="prepare-documents-heading">Prepare your application</h2>
      <ChatGPTConnection onChange={setConnected} preparing={active || busy} compact />
      <div className="preparation-settings">
        <label>Document language<select value={language} disabled={baseSaving || active || busy || !taskLoaded} onChange={event => {
          if (baseDirty && !window.confirm('Discard unsaved edits to this base CV?')) return
          setBase(undefined)
          setBaseBody(null)
          setLanguage(event.target.value as Language)
        }}><option value="en">English</option><option value="de">Deutsch</option></select></label>
        <label>Preparation mode<select value={mode} disabled={active || busy || !taskLoaded} onChange={event => setMode(event.target.value as Mode)}>
          <option value="full">Full (with review)</option><option value="fast">Fast (CV and cover letter)</option>
        </select></label>
      </div>
      {baseError && <p className="preparation-error" role="alert">{baseError}</p>}
      {!base ? <p role="status">{baseError ? <button onClick={() => setBaseReload(current => current + 1)}>Retry loading base CV</button> : 'Loading base CV…'}</p>
        : !baseBody ? <div><ul>{base.errors.map(error => <li key={error}>{error}</li>)}</ul><a href={editHref}>Manage base CVs in your profile</a></div>
        : <details className="base-cv-review" open={baseOpen} onToggle={event => setBaseOpen(event.currentTarget.open)}>
          <summary>{base.source === 'imported' ? 'Review imported base CV' : 'Review or edit base CV'}</summary>
          <p>Radius uses this CV as the source for future applications in this language. Review each editable section before saving.</p>
          <DocumentEditor value={baseBody} onChange={setBaseBody} photoUrl={photoUrl} readOnly={baseSaving} saving={baseSaving} />
          <button type="button" disabled={baseSaving || (!baseDirty && base.source === 'stored')} onClick={() => void saveBase()}>
            {baseSaving ? 'Saving base CV…' : base.source === 'imported' ? 'Confirm and save base CV' : 'Save base CV changes'}
          </button>
        </details>}
      {!!form?.fields.length && <p className="finish-help">{form.fields.length} questions captured by the application companion. Open the employer form to review them.</p>}
      <div className="preparation-actions">
        <button type="button" className="primary-button" disabled={!canPrepare} onClick={prepare}>
          {busy && !active ? 'Starting…' : retryPrepare && retryMatchesControls ? 'Retry preparation' : 'Prepare documents'}
        </button>
        {retryPrepare && !retryMatchesControls && <button type="button" disabled={!task?.preparation || baseDirty || baseSaving}
          onClick={() => { if (task?.preparation) { setLanguage(task.preparation.language); setMode(task.preparation.mode) } }}>
          Restore previous preparation settings
        </button>}
      </div>
      {task && (active || task.state === 'failed' || task.state === 'cancelled') && <PreparationStatus task={task}>
        {task.state === 'running' && <button type="button" disabled={busy} onClick={() => void taskAction(
          `${documentsUrl}/task/cancel${query}&task_id=${encodeURIComponent(task.id)}`, {},
        )}>Cancel {task.kind === 'revise' ? 'revision' : 'preparation'}</button>}
        {retryRevision && (task.state === 'failed' || task.state === 'cancelled') && <button type="button" disabled={!canRetryRevision}
          onClick={() => { if (retryVersion !== undefined) revise(retryRevision.document, retryRevision.unit_id, retryRevision.instruction, retryVersion) }}>
          Retry revision
        </button>}
      </PreparationStatus>}
      {actionError && <p className="preparation-error" role="alert">{actionError}</p>}
      {pollError && <p role="status">{pollError}</p>}
    </section>

    {!documentsLoaded && <p role="status">Loading saved documents…</p>}
    {(cv.body || letter.body) && <section className="application-finish-card" aria-labelledby="application-documents-heading">
      <h2 id="application-documents-heading">Application documents</h2>
      <div className="preparation-actions" role="tablist" aria-label="Document tabs">
        {KINDS.map(kind => <button type="button" key={kind} role="tab" id={`application-tab-${kind}`}
          aria-selected={selected === kind} aria-controls="application-document-panel"
          tabIndex={selected === kind ? 0 : -1}
          onClick={() => setSelectedKind(kind)} onKeyDown={event => {
            const next = event.key === 'Home' ? KINDS[0] : event.key === 'End' ? KINDS[1]
              : ['ArrowLeft', 'ArrowRight'].includes(event.key) ? KINDS.find(item => item !== kind) : undefined
            if (!next) return
            event.preventDefault()
            setSelectedKind(next)
            document.getElementById(`application-tab-${next}`)?.focus()
          }}>{LABELS[kind]}</button>)}
      </div>
      {(cv.draft?.saved.stale_inputs || letter.draft?.saved.stale_inputs) && <div className="preparation-warning" role="status">
        <p>The saved inputs changed after these documents were prepared.</p>
        <button type="button" disabled={!canPrepare} onClick={prepare}>Regenerate</button>
      </div>}
      <div id="application-document-panel" role="tabpanel" aria-labelledby={`application-tab-${selected}`}>
        {currentDraft.body ? <TailoredDocument key={selected} kind={selected} draft={currentDraft} documentsUrl={documentsUrl} query={query}
        photoUrl={photoUrl} canRevise={canRevise} onRevise={revise} task={task} dismissedOutput={dismissedOutput}
        dismissOutput={key => setDismissedOutput(current => [...current, key])}
        refresh={() => setRefresh(current => current + 1)} />
          : <p role="status">{LABELS[selected]} will appear here when it is ready.</p>}
      </div>
      {postingUrl && <div className="preparation-actions">
        <a href={postingUrl} target="_blank" rel="noopener noreferrer">Open job posting ↗</a>
      </div>}
    </section>}
  </div>
}

function PreparationStatus({ task, children }: { task: Task; children: React.ReactNode }) {
  const active = isActive(task)
  const retryAt = task.next_attempt_at ? Date.parse(task.next_attempt_at) : NaN
  const eta = Number.isFinite(retryAt) ? Math.max(0, Math.ceil((retryAt - Date.now()) / 1000)) : null
  const stages = preparationStages(task.preparation?.mode)
  const stage = stages.find(step => step === task.stage)
  const label = task.state === 'cancelling' ? 'Cancelling…'
    : task.state === 'cancelled' ? 'Cancelled'
      : task.state === 'failed' ? (task.kind === 'revise' ? 'Revision failed' : 'Preparation failed')
        : task.stage === 'retry_wait' ? 'Waiting to retry'
          : task.stage === 'queued' ? 'Queued'
            : task.kind === 'revise' ? 'Revising a document'
              : task.stage === 'finalize' ? 'Saving documents'
                : stage === 'review' ? 'Reviewing documents'
                  : stage ? `Preparing ${LABELS[stage]}` : 'Starting preparation'
  return <div className="generation-status">
    <div role={task.state === 'failed' ? 'alert' : 'status'}>
      <strong>{label}</strong>
      {task.attempts > 0 && task.kind !== 'legacy' && <p>Attempt {task.attempts} of 3</p>}
      {eta !== null && <p>Next retry {eta > 0 ? `in about ${eta} seconds` : 'is due now'}.</p>}
      {task.error_code && <p>{taskErrorMessage(task)}</p>}
    </div>
    {active && task.kind === 'prepare' && <ol aria-label="Preparation progress">
      {stages.map(step => <li key={step} aria-current={stage === step ? 'step' : undefined}>
        {LABELS[step]}{step !== stage && task.stages[step] ? ' · started' : ''}
      </li>)}
    </ol>}
    {children}
  </div>
}

type TailoredDocumentProps = {
  kind: DocumentKind
  draft: ReturnType<typeof useApplicationDraft>
  documentsUrl: string
  query: string
  photoUrl?: string
  canRevise: boolean
  onRevise: (kind: DocumentKind, unitId: string, instruction: string, version: number) => void
  task: Task | null
  dismissedOutput: string[]
  dismissOutput: (key: string) => void
  refresh: () => void
}

function TailoredDocument({
  kind, draft, documentsUrl, query, photoUrl, canRevise, onRevise, task, dismissedOutput, dismissOutput, refresh,
}: TailoredDocumentProps) {
  const [versions, setVersions] = useState<Version[]>()
  const [selectedVersion, setSelectedVersion] = useState('')
  const [versionBusy, setVersionBusy] = useState(false)
  const [reverting, setReverting] = useState(false)
  const [reloading, setReloading] = useState(false)
  const [error, setError] = useState('')
  const [instruction, setInstruction] = useState<{ unitId: string; text: string }>()
  const [findingEdit, setFindingEdit] = useState<{ finding: Finding; original: string; text: string }>()
  const [pdfBusy, setPdfBusy] = useState(false)
  const [pdfError, setPdfError] = useState('')
  const [pdfRetryVersion, setPdfRetryVersion] = useState<number>()
  const downloadController = useRef<AbortController | null>(null)
  useEffect(() => () => downloadController.current?.abort(), [])

  if (!draft.body || !draft.draft) return null
  const { body } = draft
  const { saved } = draft.draft
  const url = `${documentsUrl}/${kind}`
  const unsettled = draft.dirty || draft.saving || draft.conflict || reverting
  const readOnly = reverting || reloading || Boolean(draft.draft.held)
  const outputKey = `${task?.id}:${kind}`
  const showOutput = !dismissedOutput.includes(outputKey) && !isActive(task)
  const revision = showOutput && task?.state === 'completed' && task.kind === 'revise' ? task.result as Revision | undefined : undefined
  const originalUnit = revision?.document === kind && findUnitById(body, revision.unit_id)
  const suggestion = revision?.document === kind && !revision.applied && originalUnit
    && originalUnit.text !== revision.text ? revision : undefined
  const canAccept = Boolean(originalUnit && originalUnit.text === suggestion?.original_text)
  const held = showOutput && task?.held?.[kind]

  async function getLatest() {
    const current = await applicationRequest<Documents>(documentsUrl + query, { signal: AbortSignal.timeout(15000) })
    if (!current[kind]) throw new Error('This document is no longer stored.')
    return current[kind]
  }

  async function reloadSaved() {
    setError('')
    setReloading(true)
    try {
      draft.reload(await getLatest())
    } catch (reason) { setError(message(reason)) }
    finally { setReloading(false) }
  }

  async function loadVersions() {
    if (versions) {
      setVersions(undefined)
      return
    }
    setError('')
    setVersionBusy(true)
    try {
      const result = await applicationRequest<{ versions: Version[] }>(`${url}/versions${query}`,
        { signal: AbortSignal.timeout(15000) })
      setVersions(result.versions)
      setSelectedVersion('')
    } catch (reason) { setError(message(reason)) }
    finally { setVersionBusy(false) }
  }

  async function revertVersion() {
    if (unsettled || !selectedVersion) return
    setError('')
    setReverting(true)
    try {
      await applicationRequest(`${url}/revert${query}`, jsonRequest('POST', {
        version: Number(selectedVersion), expected_version: saved.version,
      }, AbortSignal.timeout(20000)))
      draft.receive(await getLatest())
      setVersions(undefined)
      setSelectedVersion('')
    } catch (reason) { setError(message(reason)) }
    finally { setReverting(false); refresh() }
  }

  async function download() {
    if (unsettled || downloadController.current) return
    const controller = new AbortController()
    downloadController.current = controller
    setPdfBusy(true)
    setPdfError('')
    const version = saved.version
    try {
      const blob = await pollDocumentPdf(`${url}/pdf${query}&version=${version}`, controller.signal, pdfRetryVersion === version)
      if (controller.signal.aborted) return
      const href = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = href
      anchor.download = `application-${kind}-v${version}.pdf`
      anchor.click()
      setTimeout(() => URL.revokeObjectURL(href), 1000)
      setPdfRetryVersion(undefined)
    } catch (reason) {
      if (!controller.signal.aborted) {
        setPdfError(message(reason))
        setPdfRetryVersion(version)
      }
    } finally {
      downloadController.current = null
      if (!controller.signal.aborted) setPdfBusy(false)
    }
  }

  return <>
    <div className="preparation-actions">
      <span>Version {saved.version} · {saved.language === 'de' ? 'Deutsch' : 'English'}</span>
      <button type="button" disabled={versionBusy || reverting} onClick={() => void loadVersions()}>
        {versions ? 'Close version history' : 'Version history'}
      </button>
      {draft.error && <button type="button" disabled={draft.saving} onClick={() => void draft.save()}>Retry save</button>}
    </div>
    {versions && <div className="preparation-actions">
      <label>Restore a previous version<select value={selectedVersion} onChange={event => setSelectedVersion(event.target.value)}>
        <option value="">Choose a version</option>
        {versions.filter(item => item.version < saved.version).map(item => <option key={item.version} value={item.version}>
          Version {item.version} · {new Date(item.created_at).toLocaleString()}
        </option>)}
      </select></label>
      <button type="button" disabled={unsettled || !selectedVersion} onClick={() => void revertVersion()}>
        {reverting ? 'Restoring…' : 'Restore version'}
      </button>
    </div>}
    {draft.conflict && <div className="preparation-warning" role="alert">
      <p>A newer version is saved in Radius. Your local edits are still here.</p>
      <div className="preparation-actions">
        <button type="button" disabled={draft.saving || reloading} onClick={() => void reloadSaved()}>Reload saved version</button>
        <button type="button" disabled={draft.saving || reloading || !draft.remote} onClick={() => { if (draft.remote) void draft.save(draft.remote) }}>
          Overwrite with my edits
        </button>
      </div>
    </div>}
    {error && <p className="preparation-error" role="alert">{error}</p>}
    {pdfBusy && <p role="status">Waiting for PDF rendering…</p>}
    {pdfError && pdfRetryVersion === saved.version && <p className="preparation-error" role="alert">
      {pdfError} <button type="button" disabled={unsettled} onClick={() => void download()}>Retry PDF</button>
    </p>}
    {held && task && <div className="preparation-warning">
      <p>A prepared {LABELS[kind].toLowerCase()} is available. Radius kept your newer edits as the saved version.</p>
      <details><summary>Preview generated document</summary>
        <DocumentEditor value={held.body} onChange={() => {}} findings={held.findings} photoUrl={photoUrl} readOnly />
      </details>
      <div className="preparation-actions">
        <button type="button" disabled={unsettled} onClick={() => {
          draft.adopt(held.body, { id: task.id, findings: held.findings, stale_inputs: held.stale_inputs })
          dismissOutput(outputKey)
        }}>Use generated document</button>
        <button type="button" onClick={() => dismissOutput(outputKey)}>Keep my document</button>
      </div>
    </div>}
    {suggestion && <section className="preparation-review-panel" aria-label="AI revision suggestion">
      <h3>AI revision suggestion</h3>
      <p><strong>Original</strong> {suggestion.original_text}</p>
      <p><strong>Suggested</strong> {suggestion.text}</p>
      {suggestion.findings.length > 0 && <ul>{suggestion.findings.map(finding => <li key={finding.id}>{finding.message}</li>)}</ul>}
      {!canAccept && <p>This section changed after the revision was requested. Ask for a new suggestion to revise the current text.</p>}
      <div className="preparation-actions">
        <button type="button" disabled={!canAccept || readOnly} onClick={() => {
          draft.edit(updateUnitText(body, suggestion.unit_id, suggestion.text))
          dismissOutput(outputKey)
        }}>Accept suggestion</button>
        <button type="button" onClick={() => dismissOutput(outputKey)}>Dismiss suggestion</button>
      </div>
    </section>}
    {instruction && <form className="preparation-review-panel" onSubmit={event => {
      event.preventDefault()
      if (!canRevise || unsettled || !instruction.text.trim() || !findUnitById(body, instruction.unitId)) return
      onRevise(kind, instruction.unitId, instruction.text.trim(), saved.version)
      setInstruction(undefined)
    }}>
      <label>How should AI improve this section?
        <textarea value={instruction.text} maxLength={500} rows={3} onChange={event => setInstruction({ ...instruction, text: event.target.value })} autoFocus />
      </label>
      <div className="preparation-actions">
        <button type="submit" disabled={!canRevise || unsettled || !instruction.text.trim()}>Request suggestion</button>
        <button type="button" onClick={() => setInstruction(undefined)}>Cancel</button>
      </div>
    </form>}
    {findingEdit && <form className="preparation-review-panel" onSubmit={event => {
      event.preventDefault()
      const id = findingEdit.finding.unit_id
      if (readOnly || !id || findUnitById(body, id)?.text !== findingEdit.original) return
      draft.edit(updateUnitText(body, id, findingEdit.text))
      draft.dismiss(findingEdit.finding)
      setFindingEdit(undefined)
    }}>
      <p>{findingEdit.finding.suggestion}</p>
      <label>Replacement text
        <textarea value={findingEdit.text} maxLength={1200} rows={3}
          onChange={event => setFindingEdit({ ...findingEdit, text: event.target.value })} autoFocus />
      </label>
      <div className="preparation-actions">
        <button type="submit" disabled={readOnly || findingEdit.original === findingEdit.text
          || findUnitById(body, findingEdit.finding.unit_id!)?.text !== findingEdit.original}>Apply edit</button>
        <button type="button" onClick={() => setFindingEdit(undefined)}>Cancel</button>
      </div>
    </form>}
    <DocumentEditor value={body} onChange={draft.edit} photoUrl={photoUrl} findings={draft.findings}
      saving={draft.saving ? 'saving' : draft.error ? 'error' : unsettled ? 'idle' : 'saved'}
      saveError={draft.error} readOnly={readOnly}
      onDownloadPdf={() => void download()} downloadDisabled={unsettled || pdfBusy}
      onImproveUnit={unitId => { setFindingEdit(undefined); setInstruction({ unitId, text: '' }) }}
      improveDisabled={!canRevise || unsettled}
      onDismissFinding={draft.dismiss}
      onApplyFinding={finding => {
        const unit = finding.unit_id && findUnitById(body, finding.unit_id)
        if (unit) {
          setInstruction(undefined)
          const replacement = finding.code === 'review_style' || finding.code === 'review_coverage'
            ? finding.suggestion : unit.text
          setFindingEdit({ finding, original: unit.text, text: replacement })
        }
      }} />
  </>
}
