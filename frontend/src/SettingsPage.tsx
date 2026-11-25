import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import AnalysisProgress from './AnalysisProgress'
import SiteHeader from './SiteHeader'
import { getThemePreference, setThemePreference } from './theme'
import type { ThemePreference } from './theme'
import ChatGPTConnection from './ChatGPTConnection'

const API = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '')

const themeOptions: { value: ThemePreference; label: string }[] = [
  { value: 'system', label: 'System' },
  { value: 'light', label: 'Light' },
  { value: 'dark', label: 'Dark' },
]

const sections = [
  { id: 'matching', label: 'Matching' },
  { id: 'applications', label: 'Applications' },
  { id: 'sources', label: 'Job sources' },
  { id: 'appearance', label: 'Appearance' },
]

const sourceLinks = [
  { href: '/discover', label: 'Companies & jobs', detail: 'Search the full directory of employers and open roles.' },
  { href: '/locations', label: 'Search areas', detail: 'Places used to find local jobs.' },
  { href: '/feeds', label: 'Job feeds', detail: 'Employer websites and other job sources.' },
]

const modelChoices = ['gpt-6.1-sol', 'gpt-6.1-terra', 'gpt-6-luna']

type Usage = {
  connected: boolean
  available: boolean
  used_percent: number | null
  resets_at: number | null
  long_used_percent: number | null
  long_resets_at: number | null
  long_window_minutes: number | null
}

function Chevron() {
  return <svg className="settings-chevron" viewBox="0 0 16 16" width="16" height="16" aria-hidden="true">
    <path d="M6 3.5 10.5 8 6 12.5" />
  </svg>
}

/** One setting: what it is on the left, how to change it on the right. */
function Row({ title, help, children, id }: { title: string; help?: ReactNode; children: ReactNode; id?: string }) {
  return <div className="settings-row">
    <div className="settings-row-label">
      <h3 id={id}>{title}</h3>
      {help && <p>{help}</p>}
    </div>
    <div className="settings-row-control">{children}</div>
  </div>
}

function resetLabel(seconds: number | null) {
  if (!seconds) return ''
  const minutes = Math.max(0, Math.round((seconds * 1000 - Date.now()) / 60000))
  if (minutes < 60) return `resets in ${minutes} min`
  const hours = Math.round(minutes / 60)
  return hours < 48 ? `resets in ${hours} h` : `resets in ${Math.round(hours / 24)} days`
}

function UsageMeter({ label, percent, resets }: { label: string; percent: number; resets: number | null }) {
  const level = percent >= 90 ? 'high' : percent >= 70 ? 'warn' : 'ok'
  return <div className="usage-meter">
    <div className="usage-meter-label"><span>{label}</span><span><b>{Math.round(percent)}%</b> used · {resetLabel(resets)}</span></div>
    <div className={`usage-meter-bar usage-meter-${level}`} role="meter" aria-label={`${label} used`}
      aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(percent)}>
      <span style={{ width: `${Math.min(100, Math.max(0, percent))}%` }} />
    </div>
  </div>
}

/** Subscription limits at a glance; reading them never starts a model turn. */
function UsageTile() {
  const [usage, setUsage] = useState<Usage | null>(null)
  const [failed, setFailed] = useState(false)
  useEffect(() => {
    const controller = new AbortController()
    fetch(`${API}/api/v1/application-preparation/usage`, { cache: 'no-store', signal: controller.signal })
      .then(response => { if (!response.ok) throw new Error(); return response.json() })
      .then((data: Usage) => setUsage(data))
      .catch(() => { if (!controller.signal.aborted) setFailed(true) })
    return () => controller.abort()
  }, [])
  const weekly = usage?.long_window_minutes ? usage.long_window_minutes >= 10080 ? 'This week' : 'Longer window' : null
  return <section className="usage-tile" aria-labelledby="usage-heading">
    <div className="usage-tile-head">
      <h2 id="usage-heading">ChatGPT subscription</h2>
      <span>{usage?.connected ? 'Connected' : usage ? 'Not connected' : failed ? 'Unavailable' : 'Checking…'}</span>
    </div>
    {usage?.connected && usage.available && usage.used_percent != null ? <>
      <UsageMeter label="Next 5 hours" percent={usage.used_percent} resets={usage.resets_at} />
      {weekly && usage.long_used_percent != null && <UsageMeter label={weekly} percent={usage.long_used_percent} resets={usage.long_resets_at} />}
    </> : <p className="usage-tile-empty">
      {usage && !usage.connected ? <>Connect ChatGPT under <a href="#applications">Applications</a> to prepare documents.</>
        : usage || failed ? 'Usage limits are not available right now.' : 'Reading subscription limits…'}
    </p>}
  </section>
}

type MatchingStatus = { mode: 'luna_first' | 'local'; active: 'luna' | 'local'; reason: string; max_5h_percent: number; max_weekly_percent: number }

/** Luna through the subscription first, local models as backup or only. */
function MatchingModel() {
  const [state, setState] = useState<MatchingStatus | null>(null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  useEffect(() => {
    const controller = new AbortController()
    fetch(`${API}/api/v1/matching-model`, { cache: 'no-store', signal: controller.signal })
      .then(response => { if (!response.ok) throw new Error(); return response.json() })
      .then((data: MatchingStatus) => setState(data))
      .catch(() => { if (!controller.signal.aborted) setError('Could not load the matching model.') })
    return () => controller.abort()
  }, [])
  async function choose(mode: MatchingStatus['mode']) {
    setSaving(true); setError('')
    try {
      const response = await fetch(`${API}/api/v1/matching-model`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ mode }),
      })
      if (!response.ok) throw new Error()
      setState(await response.json())
    } catch { setError('Could not save the matching model.') }
    finally { setSaving(false) }
  }
  const options: { value: MatchingStatus['mode']; label: string }[] = [
    { value: 'luna_first', label: 'Luna first' }, { value: 'local', label: 'Local only' },
  ]
  return <div className="settings-model">
    <div className="settings-segmented" role="radiogroup" aria-labelledby="matching-model-heading">
      {options.map(option => <button type="button" role="radio" key={option.value} disabled={!state || saving}
        aria-checked={state?.mode === option.value} onClick={() => void choose(option.value)}>{option.label}</button>)}
    </div>
    {state && <p className="settings-note" role="status">
      Now scoring with <b>{state.active === 'luna' ? 'GPT-6 Luna' : 'the local models'}</b>
      {state.mode === 'luna_first' && state.active === 'local' ? ` · ${state.reason}` : ''}
    </p>}
    {state?.mode === 'luna_first' && <p className="settings-note">
      Luna runs below {state.max_5h_percent}% of the 5-hour window and {state.max_weekly_percent}% of the week, leaving the rest for applications.
    </p>}
    {error && <p className="settings-error" role="alert">{error}</p>}
  </div>
}

function ApplicationModel({ onSaved }: { onSaved: () => void }) {
  const [choice, setChoice] = useState('')
  const [custom, setCustom] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    const controller = new AbortController()
    fetch(`${API}/api/v1/application-preparation`, { cache: 'no-store', signal: controller.signal })
      .then(response => { if (!response.ok) throw new Error(); return response.json() })
      .then(data => {
        const model = typeof data.model === 'string' ? data.model : null
        setChoice(model ? (modelChoices.includes(model) ? model : 'custom') : '')
        setCustom(model && !modelChoices.includes(model) ? model : '')
        setLoading(false)
      }).catch(() => { if (!controller.signal.aborted) { setError('Could not load the model setting. Reload to try again.'); setLoading(false) } })
    return () => controller.abort()
  }, [])

  async function save(next: string, nextCustom = custom) {
    const model = next === 'custom' ? nextCustom.trim() : next
    if (next === 'custom' && !/^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$/.test(model)) {
      setError('Enter a valid model ID.'); setMessage(''); return
    }
    setSaving(true); setError(''); setMessage('')
    try {
      const response = await fetch(`${API}/api/v1/application-preparation/model`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ model: model || null }),
      })
      const data = await response.json().catch(() => null)
      if (!response.ok) throw new Error(typeof data?.detail === 'string' ? data.detail : 'Could not save the model.')
      setMessage('Saved')
      onSaved()
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not save the model.') }
    finally { setSaving(false) }
  }

  return <div className="settings-model">
    <select aria-labelledby="application-model-heading" value={choice} disabled={loading || saving} onChange={event => {
      setChoice(event.target.value); setMessage(''); setError('')
      if (event.target.value !== 'custom') void save(event.target.value)
    }}>
      <option value="">Codex default</option>
      <option value="gpt-6.1-sol">GPT-6.1 Sol</option><option value="gpt-6.1-terra">GPT-6.1 Terra</option><option value="gpt-6-luna">GPT-6 Luna</option>
      <option value="custom">Other model…</option>
    </select>
    {choice === 'custom' && <form className="settings-inline-form" onSubmit={event => { event.preventDefault(); void save('custom') }}>
      <input aria-label="Model ID" value={custom} disabled={saving} maxLength={100} placeholder="model-id"
        onChange={event => { setCustom(event.target.value); setMessage(''); setError('') }} />
      <button type="submit" disabled={saving || !custom.trim()}>{saving ? 'Saving…' : 'Save'}</button>
    </form>}
    {message && <span className="settings-saved" role="status">{message}</span>}
    {error && <p className="settings-error" role="alert">{error}</p>}
  </div>
}

export default function SettingsPage() {
  const [theme, setTheme] = useState<ThemePreference>(getThemePreference)
  const [modelRevision, setModelRevision] = useState(0)
  const [profileIds, setProfileIds] = useState<number[] | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    fetch(`${API}/api/v1/profiles`, { cache: 'no-store', signal: controller.signal })
      .then(response => { if (!response.ok) throw new Error(); return response.json() })
      .then((data: { items?: Array<{ id?: number }> }) => {
        setProfileIds((data.items ?? []).map(item => item.id).filter((id): id is number => typeof id === 'number' && id >= 1))
      }).catch(() => { if (!controller.signal.aborted) setProfileIds([]) })
    return () => controller.abort()
  }, [])

  function chooseTheme(value: ThemePreference) {
    setThemePreference(value)
    setTheme(value)
  }

  return <div className="app-shell">
    <SiteHeader active="settings" />
    <main className="content settings-page">
      <header className="settings-head">
        <h1>Settings</h1>
        <p>How Radius finds jobs, scores them against your profile and prepares applications.</p>
      </header>

      <nav className="settings-nav" aria-label="Settings sections">
        {sections.map(section => <a key={section.id} href={`#${section.id}`}>{section.label}</a>)}
      </nav>

      <section className="settings-overview" aria-label="Status">
        {profileIds && profileIds.length > 0
          ? <AnalysisProgress base={API} profileIds={profileIds} />
          : <section className="usage-tile" aria-labelledby="matching-status-heading">
            <div className="usage-tile-head"><h2 id="matching-status-heading">Matching</h2><span>{profileIds ? 'No profile yet' : 'Checking…'}</span></div>
            <p className="usage-tile-empty">{profileIds ? <>Create a <a href="/profile">job profile</a> to start matching.</> : 'Loading matching progress…'}</p>
          </section>}
        <UsageTile key={modelRevision} />
      </section>

      <section className="settings-section" id="matching" aria-labelledby="matching-heading">
        <h2 id="matching-heading">Matching</h2>
        <Row title="Job profiles" help="Roles, skills, CV and preferences that every match is scored against.">
          <a className="settings-action" href="/profile">Manage profiles <Chevron /></a>
        </Row>
        <Row id="matching-model-heading" title="Matching model"
          help="GPT-6 Luna reads postings and checks your CV through your ChatGPT subscription while it has room. Otherwise the local models take over.">
          <MatchingModel />
        </Row>
      </section>

      <section className="settings-section" id="applications" aria-labelledby="applications-heading">
        <h2 id="applications-heading">Applications</h2>
        <Row title="ChatGPT connection" help="Your existing subscription drafts tailored CVs and cover letters. No API key needed.">
          <ChatGPTConnection refreshKey={modelRevision} />
        </Row>
        <Row id="application-model-heading" title="Application model" help="Used for new preparations and revisions. Availability depends on your subscription.">
          <ApplicationModel onSaved={() => setModelRevision(value => value + 1)} />
        </Row>
        <Row title="Chrome companion" help="Fills known details on employer application pages after your click. You always review and submit yourself.">
          <a className="settings-action" href="/downloads/radius-application-companion.zip" download>Download extension <Chevron /></a>
          <p className="settings-note">Extract the ZIP and load its folder in chrome://extensions with Developer mode on. The ZIP includes full instructions.</p>
        </Row>
      </section>

      <section className="settings-section" id="sources" aria-labelledby="sources-heading">
        <h2 id="sources-heading">Job sources</h2>
        <ul className="settings-link-rows">
          {sourceLinks.map(item => <li key={item.href}><a href={item.href}>
            <span><strong>{item.label}</strong><small>{item.detail}</small></span><Chevron />
          </a></li>)}
        </ul>
      </section>

      <section className="settings-section" id="appearance" aria-labelledby="appearance-heading">
        <h2 id="appearance-heading">Appearance</h2>
        <Row id="theme-heading" title="Color theme" help="Applies to this device.">
          <div className="settings-segmented" role="radiogroup" aria-labelledby="theme-heading">
            {themeOptions.map(option => <button type="button" role="radio" key={option.value}
              aria-checked={theme === option.value} onClick={() => chooseTheme(option.value)}>{option.label}</button>)}
          </div>
        </Row>
      </section>
    </main>
  </div>
}
