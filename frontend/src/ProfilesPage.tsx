import { useEffect, useRef, useState } from 'react'
import { profileForLanguage } from '../../extensions/application-companion/profile-language.js'
import SiteHeader from './SiteHeader'
import './profile-onboarding.css'
import { cvFormattingPrompt, cvFormattingPromptDe } from '../../extensions/application-companion/tex-cv-prompt.js'

const API = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '')
import type { SearchArea, Profile, ProfileSetupStep } from './profile'
import { profileBoardLink, profileDraftFingerprint, profileSetupSections, selectedLanguageLevels, skillEvidenceKey, updateProfileLanguage } from './profile'
const empty = (): Profile => ({ localized_content: {}, application_personalization: {}, application_details: { preferences: {} }, cv: null, name: '', summary: '', custom_instructions: '', skills: [], desired_roles: [], secondary_roles: [], work_styles: [], employment_types: [],
  seniority_levels: [], experience_years: null, languages: [], language_levels: {}, excluded_terms: [], skill_evidence: {}, search_area: null,
  education: [], certifications: [], matching_defaults: { min_match_score: 30, include_unknown: true, low_afd_vote_share_weight: 0 } })
const terms = (value: string) => value.split(',').map(term => term.trim()).filter(Boolean)
function withProfileDefaults(profile: Profile): Profile {
  const defaults = empty()
  return { ...defaults, ...Object.fromEntries(Object.keys(defaults).filter(key => key in profile).map(key => [key, profile[key as keyof Profile]])),
    matching_defaults: { ...defaults.matching_defaults, ...profile.matching_defaults }, id: profile.id }
}

const stepOrder = ['goals', 'experience', 'details', 'documents', 'review'] as const
const contactLabels = {
  first_name: 'First name', last_name: 'Last name', email: 'Email', phone: 'Phone',
  street: 'Street', postal_code: 'Postal code', city: 'City', country: 'Country',
  linkedin: 'LinkedIn URL', website: 'Website URL', portfolio: 'Portfolio URL', github: 'GitHub URL',
  salutation: 'Salutation / Anrede', birth_name: 'Birth name / Geburtsname', birth_date: 'Date of birth / Geburtsdatum',
  birth_place: 'Place of birth / Geburtsort', nationality: 'Nationality / Staatsangehörigkeit', driving_licence: 'Driving licence / Führerschein',
}

function profileStepFromHash(): typeof stepOrder[number] {
  const hash = window.location.hash.slice(1)
  if (['profile-cv', 'profile-personalization', 'profile-custom-instructions'].includes(hash)) return 'documents'
  const requested = hash.replace('profile-', '')
  return stepOrder.includes(requested as typeof stepOrder[number]) ? requested as typeof stepOrder[number] : 'goals'
}

export default function ProfilesPage() {
  const path = window.location.pathname
  const editorRoute = path.match(/^\/profile\/(new|\d+\/edit)\/?$/)
  const editRoute = path.match(/^\/profile\/(\d+)\/edit\/?$/)
  if (editorRoute) return <ProfileEditorPage profileId={editRoute ? Number(editRoute[1]) : undefined} />
  return <ProfileOverviewPage />
}

function ProfileOverviewPage() {
  const [profiles, setProfiles] = useState<Profile[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  async function request(path: string, options?: RequestInit) {
    const response = await fetch(`${API}/api/v1/profiles${path}`, options)
    const data = await response.json()
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Could not load profiles.')
    return data
  }
  useEffect(() => {
    request('').then(data => setProfiles(data.items.map(withProfileDefaults)))
      .catch(reason => setError(String(reason.message)))
      .finally(() => setLoading(false))
  }, [])

  return <div className="app-shell"><SiteHeader active="settings" /><main className="content profile-overview-page">
    <section className="profile-overview-heading"><div><h1>Your job profiles</h1>
      <p className="intro-copy">Your skills, your search and the details you reuse in every application. Set up once, refine as you go.</p></div>
      {profiles.length > 0 && <a className="profile-create-button" href="/profile/new">Create a profile</a>}</section>
    {error && <div className="error-banner" role="alert">{error}</div>}
    {loading ? <div className="loading-state"><span className="spinner" /> Loading profiles…</div> : error ? null : profiles.length === 0 ? <section className="profile-welcome">
      <div className="profile-welcome-copy"><h2>Start with where you want to go.</h2><p>A profile brings your job goals and application details together. Add what you know now; you can fill in the rest later.</p><a className="profile-create-button" href="/profile/new">Set up my profile</a><small>No CV needed to get started.</small></div>
      <ol><li><strong>Shape your search</strong><span>Roles, skills and a place to work.</span></li><li><strong>Save the details once</strong><span>Contact details and practical preferences.</span></li><li><strong>Make applications your own</strong><span>Your CV, real examples and your voice.</span></li></ol>
    </section> : <div className="profile-overview-grid">
      {profiles.map(profile => {
        const setup = profileSetupSections(profile)
        const germanContent = profileForLanguage(profile, 'de')
        const skills = profile.skills.length ? profile.skills : germanContent.skills ?? []
        const roles = profile.desired_roles.length ? profile.desired_roles : germanContent.desired_roles ?? []
        const next = setup.find(section => !section.ready)
        const tags = [`${skills.length} skills`, ...roles.slice(0, 2),
          ...(profile.search_area ? [`${profile.search_area.city || profile.search_area.label} · ${profile.search_area.radius_km} km`] : []),
          ...(profile.search_area?.city_election_result ? [`AfD ${profile.search_area.city_election_result.vote_share_percent}% · 2025`] : []),
          ...(profile.matching_defaults?.low_afd_vote_share_weight ? [`City preference · up to ${profile.matching_defaults.low_afd_vote_share_weight} pts`] : []),
          ...profile.work_styles.slice(0, 2).map(style => style.replaceAll('_', ' '))].slice(0, 5)
        return <article className="saved-profile" key={profile.id}>
          <header className="saved-profile-header"><span className="saved-profile-avatar" aria-hidden="true">{profile.name.slice(0, 1).toUpperCase()}</span>
            <div><h2>{profile.name}</h2><small>{profile.summary || germanContent.summary || skills.slice(0, 3).join(' · ') || 'Add skills and roles to tune this profile.'}</small></div>
          </header>
          <div className="profile-overview-tags">{tags.map((tag, index) => <span key={`${tag}-${index}`}>{tag}</span>)}</div>
          <p className="saved-profile-cv">{profile.cv ? `CV attached · ${profile.cv.filename}` : 'No CV attached yet'}</p>
          <div className="profile-setup-links" aria-label={`${profile.name} setup`}>{setup.map(section => <a key={section.id} href={`/profile/${profile.id}/edit#profile-${section.id}`}><span aria-hidden="true">{section.ready ? '✓' : '+'}</span>{section.title}<small>{section.ready ? 'Added' : 'Add details'}</small></a>)}</div>
          {next && <p className="profile-next-hint">Next, if you like: <a href={`/profile/${profile.id}/edit#profile-${next.id}`}>{next.hint}</a></p>}
          <footer className="saved-profile-actions">
            <a className="page-link" href={profileBoardLink(profile)}>View jobs in this area <span aria-hidden="true">→</span></a>
            <a className="page-link" href={`/applications?profile_id=${profile.id}`}>Applications <span aria-hidden="true">→</span></a>
            <a className="profile-edit-link" href={`/profile/${profile.id}/edit`}>Edit profile</a>
          </footer>
        </article>
      })}
      <a className="profile-create-card" href="/profile/new"><span aria-hidden="true">+</span><strong>Create new profile</strong>
        <small>Add a set of skills and preferences for another kind of role.</small></a>
    </div>}
  </main></div>
}

function ProfileEditorPage({ profileId }: { profileId?: number }) {
  const [step, setStep] = useState(profileStepFromHash)
  useEffect(() => {
    const followHash = () => setStep(profileStepFromHash())
    window.addEventListener('hashchange', followHash)
    return () => window.removeEventListener('hashchange', followHash)
  }, [])
  const [loadFailed, setLoadFailed] = useState(false)
  const [baseline, setBaseline] = useState('')
  const allowLeave = useRef(false)
  const [storedForm, setStoredForm] = useState<Profile>(empty())
  const [contentLanguage, setContentLanguage] = useState<'en' | 'de'>('en')
  const german = contentLanguage === 'de'
  const text = (english: string, deutsch: string) => german ? deutsch : english
  const localized = profileForLanguage(storedForm, contentLanguage)
  const form: Profile = { ...storedForm, ...Object.fromEntries(Object.entries(localized).filter(([, value]) => value !== undefined)) }
  for (const key of ['summary', 'custom_instructions', 'application_personalization', 'skills', 'desired_roles', 'secondary_roles', 'skill_evidence', 'education', 'certifications'] as const) {
    if (localized[key] === undefined) Object.assign(form, { [key]: empty()[key] })
  }
  function setForm(value: Profile | ((previous: Profile) => Profile)) {
    const next = typeof value === 'function' ? value(form) : value
    setStoredForm(previous => updateProfileLanguage(previous, next, contentLanguage))
  }
  const [skillsText, setSkillsText] = useState('')
  const [rolesText, setRolesText] = useState('')
  const [secondaryText, setSecondaryText] = useState('')
  const [languageText, setLanguageText] = useState('')
  const [certificationsText, setCertificationsText] = useState('')
  const [excludedText, setExcludedText] = useState('')
  const [areaQuery, setAreaQuery] = useState('')
  const [areaChoices, setAreaChoices] = useState<SearchArea[]>([])
  const [cv, setCV] = useState('')
  const [pendingCVs, setPendingCVs] = useState<Partial<Record<'en' | 'de', File>>>({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [loading, setLoading] = useState(Boolean(profileId))
  const feedback = useRef<HTMLDivElement>(null)
  useEffect(() => { if (error || notice) feedback.current?.scrollIntoView({ block: 'nearest' }) }, [error, notice])
  const requestedReturn = new URLSearchParams(window.location.search).get('return_to') ?? ''
  const returnTo = /^\/jobs\/\d+\/apply(?:\?profile_id=\d+)?$/.test(requestedReturn) ? requestedReturn : ''

  const currentProfile = updateProfileLanguage(storedForm, { ...form, skills: terms(skillsText), desired_roles: terms(rolesText), secondary_roles: terms(secondaryText), certifications: terms(certificationsText), languages: terms(languageText), language_levels: selectedLanguageLevels(terms(languageText), form.language_levels), excluded_terms: terms(excludedText) }, contentLanguage)
  const snapshot = profileDraftFingerprint(currentProfile)
  const dirty = Boolean(baseline && snapshot !== baseline) || Object.keys(pendingCVs).length > 0 || Boolean(cv.trim())
  useEffect(() => { if (!baseline && !loading) setBaseline(snapshot) }, [baseline, loading, snapshot])
  useEffect(() => {
    const guard = (event: BeforeUnloadEvent) => { if (dirty && !allowLeave.current) { event.preventDefault(); event.returnValue = '' } }
    window.addEventListener('beforeunload', guard)
    return () => window.removeEventListener('beforeunload', guard)
  }, [dirty])

  async function request(path: string, options?: RequestInit) {
    const response = await fetch(`${API}/api/v1/profiles${path}`, options)
    const data = await response.json()
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Check the profile fields and try again.')
    return data
  }
  function edit(profile: Profile) {
    setBaseline('')
    const normalized = withProfileDefaults(profile)
    const localized = profileForLanguage(normalized, contentLanguage)
    setStoredForm(normalized)
    setSecondaryText((localized.secondary_roles ?? []).join(', ')); setLanguageText(normalized.languages.join(', ')); setCertificationsText((localized.certifications ?? []).join(', ')); setSkillsText((localized.skills ?? []).join(', ')); setRolesText((localized.desired_roles ?? []).join(', ')); setExcludedText(normalized.excluded_terms.join(', ')); setAreaChoices([]); setAreaQuery(''); setNotice(''); setError('')
  }
  function switchContentLanguage(language: 'en' | 'de') {
    if (busy) return
    const updated = updateProfileLanguage(storedForm, { ...form, skills: terms(skillsText), desired_roles: terms(rolesText), secondary_roles: terms(secondaryText), certifications: terms(certificationsText) }, contentLanguage)
    setStoredForm(updated)
    const selected = profileForLanguage(updated, language)
    setSkillsText((selected.skills ?? []).join(', ')); setRolesText((selected.desired_roles ?? []).join(', '))
    setSecondaryText((selected.secondary_roles ?? []).join(', ')); setCertificationsText((selected.certifications ?? []).join(', '))
    setContentLanguage(language)
  }
  function toggle(key: 'work_styles' | 'employment_types' | 'seniority_levels' | 'languages', value: string) {
    setForm(previous => ({ ...previous, [key]: previous[key].includes(value) ? previous[key].filter(item => item !== value) : [...previous[key], value] }))
  }
  async function save(event: React.FormEvent) {
    event.preventDefault(); if (!validate()) return; setBusy(true); setError(''); setNotice('')
    try {
      const edited = { ...form, skills: terms(skillsText), desired_roles: terms(rolesText), excluded_terms: terms(excludedText), secondary_roles: terms(secondaryText), languages: terms(languageText), language_levels: selectedLanguageLevels(terms(languageText), form.language_levels), certifications: terms(certificationsText) }
      const payload = updateProfileLanguage(storedForm, edited, contentLanguage)
      const id = payload.id
      delete payload.id
      delete payload.cv
      const saved = await request(id ? `/${id}` : '', { method: id ? 'PUT' : 'POST',
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })
      setStoredForm(current => ({ ...current, id: saved.id, cv: saved.cv }))
      if (!id && saved.id) window.history.replaceState({}, '', `/profile/${saved.id}/edit${returnTo ? `?return_to=${encodeURIComponent(returnTo)}` : ''}#profile-${step}`)
      for (const [language, pendingCV] of Object.entries(pendingCVs)) {
        const uploaded = await fetch(`${API}/api/v1/profiles/${saved.id}/cv?filename=${encodeURIComponent(pendingCV.name)}&language=${language}`,
          { method: 'PUT', headers: { 'Content-Type': 'application/octet-stream' }, body: pendingCV })
        const metadata = await uploaded.json()
        if (!uploaded.ok) throw new Error(typeof metadata.detail === 'string' ? metadata.detail : 'Could not attach this CV.')
        saved.cv = metadata
        setStoredForm(current => ({ ...current, cv: metadata }))
        setPendingCVs(current => { const remaining = { ...current }; delete remaining[language as 'en' | 'de']; return remaining })
      }
      edit(saved); setNotice(returnTo && !saved.cv ? 'Profile saved. Add a CV in CVs & writing to continue your application.' : 'Profile saved. You can keep editing or explore jobs.')
      if (returnTo && saved.cv) {
        if (cv.trim()) {
          setNotice('Profile saved. Suggest skills from your pasted CV text, or discard it, before continuing to your application.')
          navigateStep('experience')
          return
        }
        const destination = new URL(returnTo, window.location.origin)
        destination.searchParams.set('profile_id', String(saved.id))
        allowLeave.current = true
        window.location.href = destination.pathname + destination.search
      }
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not save your profile.') }
    finally { setBusy(false) }
  }
  async function removeCV() {
    if (!form.id) return
    setBusy(true); setError('')
    try {
      const response = await fetch(`${API}/api/v1/profiles/${form.id}/cv`, { method: 'DELETE' })
      if (!response.ok) throw new Error('Could not remove the CV. Try again.')
      setForm(current => ({ ...current, cv: null })); setPendingCVs({}); setNotice('CV removed from this profile.')
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not remove the CV.') }
    finally { setBusy(false) }
  }
  async function removeProfile() {
    if (!form.id || busy || !window.confirm(`Delete “${form.name}”? This cannot be undone.`)) return
    setBusy(true); setError(''); setNotice('')
    try {
      await request(`/${form.id}`, { method: 'DELETE' })
      allowLeave.current = true
      window.location.href = '/profile'
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not delete this profile.') }
    finally { setBusy(false) }
  }
  async function findArea() {
    setBusy(true); setError('')
    try {
      const response = await fetch(`${API}/api/v1/locations/resolve`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ query: areaQuery.trim() }) })
      const payload = await response.json()
      if (!response.ok) throw new Error('Could not find that area. Try a German city or postcode.')
      setAreaChoices(payload.results.map((item: SearchArea) => ({ label: item.label, city: item.city, latitude: item.latitude, longitude: item.longitude,
        country_code: 'DE', city_election_result: item.city_election_result, radius_km: form.search_area?.radius_km ?? 35 })))
      if (!payload.results.length) setError('No matching area found. Try a German city or postcode.')
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not find that area.') }
    finally { setBusy(false) }
  }
  async function suggestSkills() {
    setBusy(true); setError(''); setNotice('')
    try {
      const data = await request('/preview', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text: cv }) })
      setSkillsText([...new Set([...terms(skillsText), ...data.skills])].join(', ')); setCV('')
      setNotice(`${data.skills.length} skill suggestions added. Review them before saving. The pasted text was discarded.`)
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not read this CV.') }
    finally { setBusy(false) }
  }
  useEffect(() => {
    if (!profileId) { setLoading(false); return }
    request('').then(data => {
      const profile = (data.items as Profile[]).find(item => item.id === profileId)
      if (!profile) throw new Error('That profile could not be found.')
      edit(profile)
    }).catch(reason => { setError(String(reason.message)); setLoadFailed(true) }).finally(() => setLoading(false))
  }, [profileId])
  const choices = (key: 'work_styles' | 'employment_types' | 'seniority_levels' | 'languages', label: string, values: [string,string][]) =>
    <fieldset className="profile-choices"><legend>{label} <small>{key === 'languages' ? 'Optional; omitted languages stay unknown' : 'Leave empty for any'}</small></legend>{values.map(([value,text]) =>
      <label key={value}><input type="checkbox" checked={form[key].includes(value)} onChange={() => toggle(key,value)} />{text}</label>)}</fieldset>

  const setup = profileSetupSections(currentProfile).map(section => section.id === 'documents' && Object.keys(pendingCVs).length ? { ...section, ready: true } : section)
  const navigateStep = (next: ProfileSetupStep | 'review') => {
    setStep(next)
    window.history.replaceState({}, '', `${window.location.pathname}${window.location.search}#profile-${next}`)
    requestAnimationFrame(() => document.getElementById(`heading-${next}`)?.focus())
  }
  function validate(section?: string) {
    const inputs = document.querySelectorAll<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>('#profile-editor-form input, #profile-editor-form textarea, #profile-editor-form select')
    for (const input of inputs) {
      if (section && input.closest('[data-profile-step]')?.getAttribute('data-profile-step') !== section) continue
      if (input.id === 'profile-name') input.setCustomValidity(input.value.trim() ? '' : 'Give your profile a name.')
      if (!input.validity.valid) {
        const target = input.closest<HTMLElement>('[data-profile-step]')?.dataset.profileStep as ProfileSetupStep
        navigateStep(target)
        requestAnimationFrame(() => { for (const ancestor of input.closest('.profile-step')?.querySelectorAll('details') ?? []) { if (ancestor.contains(input)) ancestor.open = true }; input.focus(); input.reportValidity() })
        return false
      }
    }
    return true
  }
  function applicationField(key: keyof typeof contactLabels) {
    const label = contactLabels[key]
    return (<label key={key}>{label}<input maxLength={300} type={key === 'birth_date' ? 'date' : key === 'email' ? 'email' : key === 'phone' ? 'tel' : ['linkedin', 'website', 'portfolio', 'github'].includes(key) ? 'url' : 'text'} value={form.application_details?.[key] ?? ''}
              onChange={event => setForm({ ...form, application_details: { ...form.application_details, [key]: event.target.value } })} /></label>)
  }

  return <div className="app-shell"><SiteHeader active="settings" /><main className="content profile-editor-page">
    <a className="page-link profile-back-link" href="/profile">← All profiles</a>
    <section className="profile-setup-hero"><div><h1>{form.id ? 'Edit your job profile.' : 'Set up your job profile.'}</h1>
      <p>{form.id ? 'Update your search and the details you reuse in every application.' : 'Start with your goals. Add your skills and application details at your own pace.'}</p></div>
      {form.id && <span className="profile-setup-caption">{form.name}</span>}
    </section>
    <div ref={feedback}>{error && <div className="error-banner" role="alert">{error}</div>}
    {notice && <div className="profile-save-notice" role="status"><strong>{notice}</strong>{form.id && !returnTo && <a className="page-link" href={profileBoardLink(form)}>Explore jobs with this profile</a>}</div>}</div>
    {loading ? <div className="loading-state"><span className="spinner" /> Loading profile…</div> : loadFailed ? <div className="detail-panel"><p>This profile could not be loaded. Your saved details have not been changed.</p><a href="/profile" className="page-link">Back to profiles</a></div> : <div className="profile-workspace">
      <aside className="profile-setup-sidebar"><nav aria-label="Profile setup"><ol>{[...setup, { id: 'review' as const, title: 'Review', ready: false, hint: '' }].map((section, index) => <li key={section.id}>
        <button type="button" disabled={busy} aria-current={step === section.id ? 'step' : undefined} onClick={() => navigateStep(section.id)}>
          <span className="profile-step-number" aria-hidden="true">{index + 1}</span><span>{section.title}<small>{section.id === 'review' ? 'Check & save' : section.ready ? 'Details added' : 'Add when ready'}</small></span>
        </button></li>)}</ol></nav>
        <p>Only a profile name is required. You can save now and keep building later.</p>
        {form.id && <a className="page-link" href={profileBoardLink(form)}>View jobs</a>}
      </aside>
      <form id="profile-editor-form" className="detail-panel profile-form profile-guided-form" noValidate onChangeCapture={() => setNotice('')} onSubmit={save}>
        <fieldset disabled={busy} className="profile-editor-fields">
        <div className="profile-content-toolbar"><div><strong>Application content</strong><small>English and German wording are saved separately. Contact details and search settings are shared.</small></div>
          <div role="tablist" aria-label="Profile content language" className="profile-language-tabs">{(['en', 'de'] as const).map(language => <button type="button" disabled={busy} key={language} role="tab" id={`profile-tab-${language}`} aria-controls="profile-language-panel" aria-selected={contentLanguage === language} tabIndex={contentLanguage === language ? 0 : -1} onKeyDown={event => {
            if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
            event.preventDefault(); const next = event.key === 'Home' ? 'en' : event.key === 'End' ? 'de' : contentLanguage === 'en' ? 'de' : 'en'
            switchContentLanguage(next); document.getElementById(`profile-tab-${next}`)?.focus()
          }} onClick={() => switchContentLanguage(language)}>{language === 'de' ? 'Deutsch' : 'English'}</button>)}</div>
        </div>
          <div role="tabpanel" id="profile-language-panel" aria-labelledby={`profile-tab-${contentLanguage}`}>

        <section className="profile-step" data-profile-step="goals" hidden={step !== 'goals'} aria-labelledby="heading-goals">
          <header className="profile-step-heading"><p>Step 1 of 5</p><h2 id="heading-goals" tabIndex={-1}>What would you like to do next?</h2><p>Give this search a name, choose roles and decide where you’d like to work.</p></header>
        <label>Profile name<input id="profile-name" required maxLength={120} value={form.name} onChange={event => setForm({ ...form, name: event.target.value })} placeholder="e.g. Product roles in Berlin" /></label>
        <label>{text('Roles you’re interested in', 'Gewünschte Positionen')} <small>{text('Separate with commas', 'Mit Kommas trennen')}</small><input value={rolesText} onChange={event => setRolesText(event.target.value)} placeholder="e.g. Product Manager, Product Owner" /></label>
        <details className="profile-advanced"><summary>Add a short profile summary</summary>
        <label>{text('Profile summary', 'Profilzusammenfassung')}<textarea aria-label={text('Profile summary', 'Profilzusammenfassung')} maxLength={5000} value={form.summary} onChange={event => setForm({ ...form, summary: event.target.value })} /></label>
        </details>
        {choices('work_styles','Preferred work styles',[['remote','Remote'],['hybrid','Hybrid'],['onsite','On-site']])}
        <fieldset className="profile-section"><legend>Search area</legend>
          <p className="muted">Local roles within your radius appear by default. Choose Remote in the jobs board location filter to see remote roles with country eligibility checked.</p>
          <label>Change city or postcode<input value={areaQuery} onChange={event => setAreaQuery(event.target.value)} placeholder="e.g. Mannheim" /></label>
          <button type="button" disabled={busy || areaQuery.trim().length < 2} onClick={() => void findArea()}>Find area</button>
          {areaChoices.map((area,index) => <button type="button" key={index} onClick={() => { setForm({ ...form, search_area: area }); setAreaChoices([]) }}>{area.label}</button>)}
          {form.search_area?.city_election_result && <p className="evidence-note city-election-note"><strong>{form.search_area.city_election_result.election}:</strong> AfD received {form.search_area.city_election_result.vote_share_percent}% of valid second votes in {form.search_area.city_election_result.municipality}. This is an aggregate election result, not a current poll. {form.search_area.city_election_result.postal_ballots_included ? 'Assigned postal ballots are included.' : 'This figure includes polling-place votes only; postal ballots were counted for a shared local authority.'} <a href={form.search_area.city_election_result.source_url} target="_blank" rel="noreferrer">Source</a></p>}
          {form.search_area ? <><div className="profile-area-grid">
            <label>Selected city<input readOnly value={form.search_area.city || form.search_area.label} /></label>
            <label>Radius (km)<input type="number" min={1} max={200} value={form.search_area.radius_km} onChange={event => setForm({ ...form, search_area: { ...form.search_area!, radius_km: Number(event.target.value) } })} /></label>
            <label>Country code<input maxLength={2} value={form.search_area.country_code ?? ''} placeholder="DE" onChange={event => setForm({ ...form, search_area: { ...form.search_area!, country_code: event.target.value.toUpperCase() || null } })} /></label>
          </div><details><summary>Map coordinates</summary><div className="profile-area-grid">{(['latitude','longitude'] as const).map(key => <label key={key}>{key}<input type="number" step="any" min={key === 'latitude' ? -90 : -180} max={key === 'latitude' ? 90 : 180} value={form.search_area![key] ?? ''} onChange={event => setForm({ ...form, search_area: { ...form.search_area!, [key]: event.target.value === '' ? null : Number(event.target.value) } })} /></label>)}</div></details><button type="button" onClick={() => setForm({ ...form, search_area: null })}>Use board area instead</button></> : <button type="button" onClick={() => setForm({ ...form, search_area: { label: 'Heidelberg, Germany', city: 'Heidelberg', latitude: 49.40936, longitude: 8.69472, radius_km: 35, country_code: 'DE' } })}>Set Heidelberg area</button>}
        </fieldset>
<details className="profile-advanced"><summary>More search preferences</summary>
        <label>{text('Adjacent roles', 'Weitere Positionen')} <small>{text('Other roles you would consider; separate with commas', 'Weitere mögliche Positionen; mit Kommas trennen')}</small><input value={secondaryText} onChange={event => setSecondaryText(event.target.value)} /></label>
        {choices('employment_types','Employment types',[['full_time','Full time'],['part_time','Part time'],['contract','Contract'],['internship','Internship'],['working_student','Student job'],['apprenticeship','Apprenticeship'],['dual_study','Dual study']])}
        <fieldset className="profile-section"><legend>Board defaults</legend>
          <label>Minimum overlap score<input type="number" min={0} max={100} value={form.matching_defaults.min_match_score} onChange={event => setForm({ ...form, matching_defaults: { ...form.matching_defaults, min_match_score: Number(event.target.value) } })} /></label>
          <label className="unknown-filter"><input type="checkbox" checked={form.matching_defaults.include_unknown} onChange={event => setForm({ ...form, matching_defaults: { ...form.matching_defaults, include_unknown: event.target.checked } })} />Include possible leads with missing information</label>
          <label>Prefer cities with lower AfD vote shares <small>Adjust scores by up to this many points; set to 0 to turn it off. Shares below 25% raise a score and shares above 25% lower it. Only unambiguous municipality results with assigned postal ballots affect scores; fully remote jobs stay unchanged.</small><input type="number" min={0} max={20} value={form.matching_defaults.low_afd_vote_share_weight ?? 0} onChange={event => setForm({ ...form, matching_defaults: { ...form.matching_defaults, low_afd_vote_share_weight: Number(event.target.value) } })} /></label>
        </fieldset>
        <label>Hide job titles containing <small>Separate with commas</small><input value={excludedText} onChange={event => setExcludedText(event.target.value)} placeholder="Internship, Praktikum" /></label>
</details>
        </section>
        <section className="profile-step" data-profile-step="experience" hidden={step !== 'experience'} aria-labelledby="heading-experience">
          <header className="profile-step-heading"><p>Step 2 of 5</p><h2 id="heading-experience" tabIndex={-1}>Start with what you know.</h2><p>Paid work, study and personal projects all count. Add only the experience you can stand behind.</p></header>
        <details className="cv-import profile-advanced"><summary>Suggest skills from my CV text</summary><p className="muted">Paste CV text to suggest skills, then review the list above. The text is processed on this app’s server and is not saved or sent to an external AI service.</p>
          <label>CV text<textarea aria-label="CV text" maxLength={100000} value={cv} onChange={event => setCV(event.target.value)} placeholder="Paste the relevant experience and skills sections…" /></label>
          <p className="muted">Pasted text is temporary and isn’t included in Save profile. Suggest skills before leaving, or discard it when you’re finished.</p>
          <div className="profile-editor-actions"><button type="button" disabled={busy || !cv.trim()} onClick={() => void suggestSkills()}>Suggest skills from this text</button>{cv && <button type="button" disabled={busy} onClick={() => setCV('')}>Discard pasted text</button>}</div>
        </details>
        <label>{text('Skills', 'Fähigkeiten')} <small>{text('Separate with commas; you can add skills beyond our suggestions', 'Mit Kommas trennen; eigene Fähigkeiten sind ebenfalls möglich')}</small><textarea aria-label={text('Skills', 'Fähigkeiten')} maxLength={8000} value={skillsText} onChange={event => setSkillsText(event.target.value)} placeholder="e.g. User research, Scrum, project management" /></label>
        <label>Years of relevant experience <small>Optional; used only when a job states a minimum</small><input type="number" min={0} max={60} step={0.5} value={form.experience_years ?? ''} onChange={event => setForm({ ...form, experience_years: event.target.value === '' ? null : Number(event.target.value) })} /></label>
        {choices('seniority_levels','Experience levels',[['student','Student / trainee'],['junior','Junior'],['senior','Senior'],['lead','Lead']])}
        <fieldset className="profile-section"><legend>{text('Evidence behind your skills', 'Belege für deine Fähigkeiten')}</legend><p className="muted">{text('Distinguish employment from academic or research experience. Leave unknown experience unstated.', 'Unterscheide Beschäftigung von Studien- und Forschungserfahrung. Lasse unbekannte Erfahrung offen.')}</p>
          {terms(skillsText).map(skill => { const evidenceKey = skillEvidenceKey(skill, form.skill_evidence); return <details className="profile-evidence-row" key={skill}><summary>{skill} <small>{form.skill_evidence[evidenceKey]?.context || 'Context not recorded'}</small></summary>
            <label>{text('Experience context', 'Erfahrungskontext')}<select value={form.skill_evidence[evidenceKey]?.context ?? ''} onChange={event => setForm({ ...form, skill_evidence: { ...form.skill_evidence, [evidenceKey]: { note: form.skill_evidence[evidenceKey]?.note ?? '', context: event.target.value } } })}><option value="">Unknown</option>{['professional','academic','research'].map(context => <option key={context} value={context}>{context}</option>)}{form.skill_evidence[evidenceKey]?.context && !['professional','academic','research'].includes(form.skill_evidence[evidenceKey].context) && <option value={form.skill_evidence[evidenceKey].context}>{form.skill_evidence[evidenceKey].context}</option>}</select></label>
            <label>{text('Supporting experience', 'Belegende Erfahrung')}<textarea maxLength={2000} value={form.skill_evidence[evidenceKey]?.note ?? ''} onChange={event => setForm({ ...form, skill_evidence: { ...form.skill_evidence, [evidenceKey]: { context: form.skill_evidence[evidenceKey]?.context ?? '', note: event.target.value } } })} /></label>
          </details> })}
        </fieldset>
        <fieldset className="profile-section"><legend>{text('Education and certifications', 'Ausbildung und Zertifikate')}</legend>
          {form.education.map((row,index) => <div className="profile-education-row" key={index}>
            <label>{text('Qualification', 'Abschluss')}<select value={row.level} onChange={event => setForm({ ...form, education: form.education.map((item,i) => i === index ? { ...item, level: event.target.value as typeof row.level } : item) })}>{[['vocational','Vocational'],['bachelor','Bachelor’s degree'],['master','Master’s degree'],['doctorate','Doctorate']].map(([value,label]) => <option value={value} key={value}>{label}</option>)}</select></label>
            <label>{text('Field of study', 'Studienfach')}<input value={row.field} maxLength={200} onChange={event => setForm({ ...form, education: form.education.map((item,i) => i === index ? { ...item, field: event.target.value } : item) })} /></label>
            <label>{text('Details and recognition notes', 'Einzelheiten und Anerkennung')}<textarea value={row.note} maxLength={2000} onChange={event => setForm({ ...form, education: form.education.map((item,i) => i === index ? { ...item, note: event.target.value } : item) })} /></label>
            <button type="button" onClick={() => setForm({ ...form, education: form.education.filter((_,i) => i !== index) })}>{text('Remove qualification', 'Abschluss entfernen')}</button>
          </div>)}
          <button type="button" onClick={() => setForm({ ...form, education: [...form.education, { level: 'bachelor', field: '', note: '' }] })}>{text('Add qualification', 'Abschluss hinzufügen')}</button>
          <label>{text('Certifications', 'Zertifikate')} <small>{text('Separate with commas', 'Mit Kommas trennen')}</small><input value={certificationsText} onChange={event => setCertificationsText(event.target.value)} /></label>
        </fieldset>
        <fieldset className="profile-section"><legend>Languages and proficiency</legend>
          <label>Languages <small>Separate with commas; leave a level blank if unknown</small><input value={languageText} onChange={event => setLanguageText(event.target.value)} placeholder="Spanish, English, German, French" /></label>
          <div className="profile-language-grid">{terms(languageText).map(language => <label key={language}>{language}<select aria-label={`${language} proficiency`} value={selectedLanguageLevels(terms(languageText), form.language_levels)[language] ?? ''} onChange={event => { const next = selectedLanguageLevels(terms(languageText), form.language_levels); if (event.target.value) next[language] = event.target.value; else delete next[language]; setForm({ ...form, language_levels: next }) }}>
            <option value="">Unknown</option>{['A1','A2','B1','B2','C1','C2','native'].map(level => <option key={level} value={level}>{level === 'native' ? 'Native' : level}</option>)}
          </select></label>)}</div>
        </fieldset>
        </section>
        <section className="profile-step" data-profile-step="details" hidden={step !== 'details'} aria-labelledby="heading-details">
          <header className="profile-step-heading"><p>Step 3 of 5</p><h2 id="heading-details" tabIndex={-1}>Fewer forms to fill in later.</h2><p>Save your contact details and practical preferences once. Leave any answer you’re unsure about blank.</p></header>
        <fieldset className="profile-section"><legend>Application details</legend>
          <p className="muted">Saved with this profile and synced to the browser extension. Enter confirmed facts only; leave unknown answers blank. Your profile name above is a label, not your applicant name.</p>
          <div className="profile-contact-grid">{(['first_name', 'last_name', 'email', 'phone', 'city', 'country'] as const).map(key => applicationField(key))}</div>
          <details className="profile-advanced"><summary>Address, links and additional personal details</summary><p className="muted">Only add details you want to reuse in application forms.</p>
          <div className="profile-contact-grid">{(['street', 'postal_code', 'linkedin', 'website', 'portfolio', 'github', 'salutation', 'birth_name', 'birth_date', 'birth_place', 'nationality', 'driving_licence'] as const).map(key => applicationField(key))}</div>
          </details>
          <label>Additional information / Weitere Informationen<textarea maxLength={10000} value={form.application_details?.additional_information ?? ''}
            onChange={event => setForm({ ...form, application_details: { ...form.application_details, additional_information: event.target.value } })} /></label>
        </fieldset>
        <fieldset className="profile-section"><legend>Availability, salary and work eligibility</legend>
          <p className="muted">These answers help fill application forms. Leave work authorization and sponsorship unknown unless you have confirmed your situation.</p>
          <div className="profile-contact-grid">
          {([['salary_amount', 'Expected salary'], ['salary_currency', 'Salary currency (e.g. EUR)'], ['salary_period', 'Salary period (e.g. year)'], ['available_from', 'Available from'], ['weekly_hours', 'Weekly hours'], ['authorization_country', 'Work authorization country']] as const).map(([key, label]) =>
            <label key={key}>{label}<input aria-label={label} maxLength={300} value={form.application_details?.preferences?.[key] ?? ''}
              onChange={event => setForm({ ...form, application_details: { ...form.application_details, preferences: { ...form.application_details?.preferences, [key]: event.target.value } } })} /></label>)}
          {([['authorized_to_work', 'Authorized to work'], ['sponsorship_required', 'Requires sponsorship'], ['willing_to_relocate', 'Willing to relocate'], ['willing_to_work_onsite', 'Willing to work on site']] as const).map(([key, label]) =>
            <label key={key}>{label}<select aria-label={label} value={form.application_details?.preferences?.[key] == null ? '' : String(form.application_details.preferences[key])}
              onChange={event => setForm({ ...form, application_details: { ...form.application_details, preferences: { ...form.application_details?.preferences, [key]: event.target.value === '' ? null : event.target.value === 'true' } } })}>
              <option value="">Unknown</option><option value="true">Yes</option><option value="false">No</option></select></label>)}
          </div>
        </fieldset>
        <fieldset className="profile-section"><legend>{text('Application wording', 'Formulierungen für Bewerbungen')}</legend>
          {([['notice_period', text('Notice period', 'Kündigungsfrist')], ['employment_type', text('Employment type', 'Beschäftigungsart')]] as const).map(([key, label]) => <label key={key}>{label}<input aria-label={label} maxLength={300} value={form.application_details?.preferences?.[key] ?? ''} onChange={event => setForm({ ...form, application_details: { ...form.application_details, preferences: { ...form.application_details?.preferences, [key]: event.target.value } } })} /></label>)}
        </fieldset>
        </section>
        <section className="profile-step" data-profile-step="documents" hidden={step !== 'documents'} aria-labelledby="heading-documents">
          <header className="profile-step-heading"><p>Step 4 of 5</p><h2 id="heading-documents" tabIndex={-1}>Make each application sound like you.</h2><p>Attach a CV in either language and add examples of your work. You can come back to this before your first application.</p></header>
        <fieldset id="profile-cv" className="profile-section profile-cv-attachment"><legend>{text('Base LaTeX CVs', 'LaTeX-Lebenslauf')}</legend>
          <p><a href="/cv-templates/cv-en.tex" download>English starter</a> · <a href="/cv-templates/cv-de.tex" download>German starter</a>. Replace all placeholders with verified facts before uploading.</p>
          <p>{text('Save an English and a German .tex CV. Applications automatically select the matching language. Work and study history, including responsibilities, comes directly from the marked CV.', 'Speichere einen englischen und einen deutschen .tex-Lebenslauf. Bewerbungen wählen automatisch die passende Sprache. Beruflicher Werdegang, Ausbildung und Aufgaben werden aus den markierten Einträgen übernommen.')}</p>
          {([contentLanguage] as const).map(language => <div key={language}>
            <label>{language === 'en' ? 'English' : 'German'} base CV<input type="file" accept=".tex" disabled={busy}
              onChange={event => { const file = event.target.files?.[0]; if (!file) return; if (!/\.tex$/i.test(file.name) || file.size > 5 * 1024 * 1024) { setError('Choose a LaTeX .tex CV up to 5 MB.'); event.target.value = ''; return } setPendingCVs(current => ({ ...current, [language]: file })); event.target.value = ''; setError('') }} /></label>
            {form.cv?.languages?.[language] && <a href={`${API}/api/v1/profiles/${form.id}/cv?language=${language}`}>Download {form.cv.languages[language].filename}</a>}
            {pendingCVs[language] && <div className="profile-pending-cv"><p>{pendingCVs[language]!.name} · ready to save</p><button type="button" onClick={() => setPendingCVs(current => { const remaining = { ...current }; delete remaining[language]; return remaining })}>Cancel upload</button></div>}
          </div>)}
          {form.cv && !Object.keys(form.cv.languages ?? {}).length && <p>Replace the previous CV ({form.cv.filename}) with marked LaTeX sources.</p>}
          {form.cv && <button type="button" disabled={busy} onClick={() => void removeCV()}>{text('Remove all base CVs', 'Alle Lebensläufe entfernen')}</button>}
          <details><summary>{text('Prompt for formatting my existing LaTeX CV', 'Prompt zum Formatieren meines LaTeX-Lebenslaufs')}</summary><p>{text('Paste this into ChatGPT with your original CV. Review the facts before uploading both files.', 'Füge diesen Prompt mit deinem ursprünglichen Lebenslauf in ChatGPT ein. Prüfe die Fakten, bevor du beide Dateien hochlädst.')}</p><textarea aria-label="CV formatting prompt" readOnly rows={12} value={contentLanguage === 'de' ? cvFormattingPromptDe : cvFormattingPrompt} /></details>
        </fieldset>
        <fieldset className="profile-section" id="profile-personalization"><legend>{text('About me for applications', 'Über mich für Bewerbungen')}</legend>
          <p className="muted">{text('Add confirmed examples and preferences once. They’re used by Prepare inside Radius and included in application and fit briefs for ChatGPT. You can draft these in your usual ChatGPT conversation, then check the facts before saving here.', 'Speichere bestätigte Beispiele und Wünsche einmal. Sie werden bei Prepare inside Radius und in Bewerbungs- und Eignungsprompts für ChatGPT verwendet. Du kannst sie im gewohnten ChatGPT-Gespräch entwerfen und die Fakten vor dem Speichern prüfen.')}</p>
          {([
            ['career_evidence', text('Career evidence bank', 'Berufliche Beispiele und Belege'), 12000, text('For each role or project: the problem, your own actions, tools or methods used, and the verified outcome. Add scope, dates, links and measured results only when known. Distinguish academic projects from paid work; concrete examples help more than job titles or skill lists.', 'Je Position oder Projekt: Problem, dein eigener Beitrag, verwendete Werkzeuge oder Methoden und belegtes Ergebnis. Umfang, Daten, Links und Messwerte nur nennen, wenn bekannt. Trenne Studienprojekte von bezahlter Arbeit; konkrete Beispiele helfen mehr als Titel oder Kompetenzlisten.')],
            ['writing_samples', text('Writing samples', 'Eigene Textbeispiele'), 12000, text('Paste two or three short pieces you wrote. ChatGPT will use these as voice examples, without borrowing their facts for this application.', 'Füge zwei oder drei kurze eigene Texte ein. Sie dienen als Stilvorbilder, nicht als Quelle zusätzlicher Fakten.')],
            ['cover_letter_example', text('Example cover letter (English)', 'Beispielanschreiben (Deutsch)'), 16000, text('Paste a full English cover letter as a reference for structure, formatting and voice. The matching language example is used when preparing an application; old employers, role names and claims are not reused as facts. Add the German example in the Deutsch tab.', 'Füge ein vollständiges deutsches Anschreiben als Vorlage für Aufbau, Format und Stil ein. Der Prompt verwendet das Beispiel der Bewerbungssprache; frühere Arbeitgeber, Stellen und Behauptungen werden nicht als Fakten übernommen. Das englische Beispiel hinterlegst du im English-Tab.')],
            ['wording_to_avoid', text('Wording to avoid', 'Zu vermeidende Formulierungen'), 3000, text('Words or phrases that don’t sound like you.', 'Wörter oder Formulierungen, die nicht nach dir klingen.')],
            ['career_goals', text('Career goals', 'Berufliche Ziele'), 5000, text('What you want to do next, skills you want to develop, and roles you would consider.', 'Deine nächsten beruflichen Ziele, gewünschte Fähigkeiten und mögliche Positionen.')],
            ['work_preferences', text('Working preferences and dealbreakers', 'Arbeitswünsche und Ausschlusskriterien'), 5000, text('What energizes or drains you, team environment, management style, travel limits and practical needs. These guide fit and transferable career paths; private dealbreakers stay out of application prose.', 'Was dir Energie gibt oder nimmt, Teamumfeld, Führungsstil, Reisegrenzen und praktische Bedürfnisse. Das hilft bei Passung und möglichen Berufswegen; private Ausschlusskriterien bleiben außerhalb der Bewerbungstexte.')],
          ] as const).map(([key, label, limit, hint]) => <details className="profile-writing-detail" key={key} open={key === 'career_evidence' || key === 'career_goals'}><summary>{label}</summary><label>
            <textarea aria-label={label} rows={key === 'cover_letter_example' ? 10 : key === 'career_evidence' || key === 'writing_samples' ? 5 : 3} maxLength={limit}
              value={form.application_personalization?.[key] ?? ''} placeholder={hint}
              onChange={event => setForm({ ...form, application_personalization: { ...form.application_personalization, [key]: event.target.value } })} />
            <small>{hint}</small></label></details>)}
        </fieldset>
        <fieldset className="profile-section"><legend>{text('Application writing preferences', 'Schreibstil für Bewerbungen')}</legend>
          <label htmlFor="profile-custom-instructions">{text('Custom application instructions', 'Eigene Bewerbungsanweisungen')}</label>
          <textarea id="profile-custom-instructions" aria-describedby="profile-instructions-help" rows={5} maxLength={5000} value={form.custom_instructions} onChange={event => setForm({ ...form, custom_instructions: event.target.value })}
            placeholder="For example: Write in German. Keep my cover letter under 250 words, use a warm, direct voice, and focus on research coordination." />
          <small id="profile-instructions-help">{text('Saved for every application using this profile. Add your preferred voice, language or points to emphasize. Documents match the job posting by default. You can override these preferences for an individual application.', 'Gilt für jede Bewerbung mit diesem Profil. Beschreibe deinen bevorzugten Stil und Schwerpunkte. Die Dokumentensprache richtet sich nach der Bewerbung; einzelne Bewerbungen können eigene Vorgaben erhalten.')}</small>
        </fieldset>
        </section>
        <section className="profile-step" data-profile-step="review" hidden={step !== 'review'} aria-labelledby="heading-review">
          <header className="profile-step-heading"><p>Step 5 of 5</p><h2 id="heading-review" tabIndex={-1}>Review your profile</h2><p>Everything here can be edited later. Missing optional details won’t stop you from saving.</p></header>
          <div className="profile-review-intro"><span className="saved-profile-avatar" aria-hidden="true">{form.name.slice(0, 1).toUpperCase() || '?'}</span><div><h3>{form.name || 'Your new profile'}</h3><p>{form.summary || 'Your job search and application details, together.'}</p></div></div>
          <dl className="profile-review-list">{setup.map(section => <div key={section.id}><dt>{section.title}<small>{section.ready ? 'Details added' : 'Optional · add later'}</small></dt><dd>{section.id === 'goals' ? [rolesText || 'No roles added in this language', form.search_area ? `${form.search_area.city || form.search_area.label}, ${form.search_area.radius_km} km` : 'Use the area on the jobs board'].join(' · ') : section.id === 'experience' ? [skillsText || 'No skills added in this language', languageText].filter(Boolean).join(' · ') : section.id === 'details' ? [form.application_details?.first_name, form.application_details?.last_name, form.application_details?.email].filter(Boolean).join(' ') || section.hint : (['en', 'de'] as const).map(language => pendingCVs[language]?.name || form.cv?.languages?.[language]?.filename).filter(Boolean).join(' · ') || section.hint}</dd><button type="button" aria-label={`Edit ${section.title}`} onClick={() => navigateStep(section.id)}>Edit</button></div>)}</dl>
          <p className="muted">Your search area is used on the jobs board. Your CV, examples and preferences help prepare applications; review each application before using it.</p>
        </section>
          </div>
        </fieldset>
        <footer className="profile-flow-footer"><span className="profile-save-state">{busy ? 'Working…' : dirty ? 'Unsaved changes' : form.id ? 'All changes saved' : 'Not saved yet'}</span><div>
          {step !== 'goals' && <button type="button" disabled={busy} onClick={() => navigateStep(stepOrder[stepOrder.indexOf(step) - 1])}>Back</button>}
          <button className={step === 'review' ? 'primary-button' : ''} disabled={busy} type="submit">{busy ? 'Saving…' : returnTo ? 'Save & continue' : !form.id && step !== 'review' ? 'Save progress' : 'Save profile'}</button>
          {step !== 'review' && <button className="primary-button" disabled={busy} type="button" onClick={() => { if (validate(step)) navigateStep(stepOrder[stepOrder.indexOf(step) + 1]) }}>Continue</button>}
        </div></footer>
        {form.id && <details className="profile-delete-section"><summary>Manage profile</summary><p>Delete this profile and its saved application context.</p><button type="button" className="profile-delete-button" disabled={busy} onClick={() => void removeProfile()}>Delete profile</button></details>}
      </form></div>}
  </main></div>
}
