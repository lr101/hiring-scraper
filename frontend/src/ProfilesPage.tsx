import { useEffect, useState } from 'react'
import SiteHeader from './SiteHeader'

const API = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '')
type Profile = {
  id?: number; name: string; skills: string[]; desired_roles: string[]; work_styles: string[]
  employment_types: string[]; seniority_levels: string[]; experience_years: number | null
  languages: string[]; excluded_terms: string[]
}
const empty = (): Profile => ({ name: '', skills: [], desired_roles: [], work_styles: [], employment_types: [],
  seniority_levels: [], experience_years: null, languages: [], excluded_terms: [] })
const terms = (value: string) => value.split(',').map(term => term.trim()).filter(Boolean)

export default function ProfilesPage() {
  const [profiles, setProfiles] = useState<Profile[]>([])
  const [form, setForm] = useState<Profile>(empty())
  const [skillsText, setSkillsText] = useState('')
  const [rolesText, setRolesText] = useState('')
  const [excludedText, setExcludedText] = useState('')
  const [cv, setCV] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [loading, setLoading] = useState(true)

  async function request(path: string, options?: RequestInit) {
    const response = await fetch(`${API}/api/v1/profiles${path}`, options)
    const data = await response.json()
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Check the profile fields and try again.')
    return data
  }
  async function load() {
    const data = await request('')
    setProfiles(data.items)
  }
  useEffect(() => { void load().catch(reason => setError(String(reason.message))).finally(() => setLoading(false)) }, [])

  function edit(profile: Profile) {
    setForm(profile); setSkillsText(profile.skills.join(', ')); setRolesText(profile.desired_roles.join(', ')); setExcludedText(profile.excluded_terms.join(', ')); setCV(''); setNotice(''); setError('')
  }
  function toggle(key: 'work_styles' | 'employment_types' | 'seniority_levels' | 'languages', value: string) {
    setForm(previous => ({ ...previous, [key]: previous[key].includes(value) ? previous[key].filter(item => item !== value) : [...previous[key], value] }))
  }
  async function save(event: React.FormEvent) {
    event.preventDefault(); setBusy(true); setError(''); setNotice('')
    try {
      const { id, ...payload } = { ...form, skills: terms(skillsText), desired_roles: terms(rolesText), excluded_terms: terms(excludedText) }
      const saved = await request(id ? `/${id}` : '', { method: id ? 'PUT' : 'POST',
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })
      edit(saved); await load(); setNotice('Profile saved. You can now use it to find matching jobs.')
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not save your profile.') }
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
  async function remove(id: number) {
    setBusy(true); setError('')
    try { await request(`/${id}`, { method: 'DELETE' }); await load(); if (form.id === id) edit(empty()); setNotice('Profile deleted.') }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not delete this profile.') }
    finally { setBusy(false) }
  }
  const choices = (key: 'work_styles' | 'employment_types' | 'seniority_levels' | 'languages', label: string, values: [string,string][]) =>
    <fieldset className="profile-choices"><legend>{label} <small>{key === 'languages' ? 'Optional; omitted languages stay unknown' : 'Leave empty for any'}</small></legend>{values.map(([value,text]) =>
      <label key={value}><input type="checkbox" checked={form[key].includes(value)} onChange={() => toggle(key,value)} />{text}</label>)}</fieldset>

  return <div className="app-shell"><SiteHeader active="profile" /><main className="content detail-page">
    <section className="detail-hero"><p className="eyebrow">YOUR NEXT ROLE</p><h1>Your job profile</h1>
      <p className="intro-copy">Tell us what you can do and what you’re looking for. Jobs stay within your selected area, plus remote roles.</p>
      <p className="muted">This early version uses explicit skills and interests. It does not infer qualifications from your name, age, or other personal details.</p>
    </section>
    {error && <div className="error-banner" role="alert">{error}</div>}
    {notice && <div className="evidence-note" role="status">{notice}</div>}
    <div className="profile-grid">
      <section className="detail-panel"><h2>Saved profiles</h2>{loading ? <p>Loading profiles…</p> : profiles.length ? profiles.map(profile =>
        <article className="saved-profile" key={profile.id}><h3>{profile.name}</h3><p>{profile.skills.join(', ') || 'No skills entered'}</p>
          <div className="detail-links"><a className="page-link" href={`/?view=jobs&profile_id=${profile.id}`}>Find matching jobs →</a>
            <button type="button" disabled={busy} onClick={() => edit(profile)}>Edit</button>
            <button type="button" disabled={busy} onClick={() => void remove(profile.id!)}>Delete profile</button></div></article>) : <p className="muted">Create your first profile to see which jobs fit.</p>}
        <button type="button" disabled={busy} onClick={() => edit(empty())}>Create another profile</button>
      </section>
      <form className="detail-panel profile-form" onSubmit={save}>
        <h2>{form.id ? 'Edit your profile' : 'Create a profile'}</h2>
        <label>Profile name<input required maxLength={120} value={form.name} onChange={event => setForm({ ...form, name: event.target.value })} placeholder="e.g. Backend developer" /></label>
        <label>Skills <small>Separate with commas; you can add skills beyond our suggestions</small><textarea maxLength={8000} value={skillsText} onChange={event => setSkillsText(event.target.value)} placeholder="Python, SQL, Docker, project management" /></label>
        <label>Roles you’re interested in <small>Separate with commas</small><input value={rolesText} onChange={event => setRolesText(event.target.value)} placeholder="Software developer, backend" /></label>
        <label>Years of relevant experience <small>Optional; used only when a job states a minimum</small><input type="number" min={0} max={60} step={0.5} value={form.experience_years ?? ''} onChange={event => setForm({ ...form, experience_years: event.target.value === '' ? null : Number(event.target.value) })} /></label>
        {choices('work_styles','Preferred work styles',[['remote','Remote'],['hybrid','Hybrid'],['onsite','On-site']])}
        {choices('employment_types','Employment types',[['full_time','Full time'],['part_time','Part time'],['contract','Contract'],['internship','Internship'],['working_student','Student job'],['apprenticeship','Apprenticeship']])}
        {choices('seniority_levels','Experience levels',[['student','Student / trainee'],['junior','Junior'],['senior','Senior'],['lead','Lead']])}
        {choices('languages','Languages you can work in',[['German','German'],['English','English']])}
        <label>Hide job titles containing <small>Separate with commas</small><input value={excludedText} onChange={event => setExcludedText(event.target.value)} placeholder="Internship, Praktikum" /></label>
        <button className="primary-button" disabled={busy} type="submit">{busy ? 'Working…' : 'Save profile'}</button>
        {form.id && <a className="page-link" href={`/?view=jobs&profile_id=${form.id}`}>View matching jobs →</a>}
        <section className="cv-import"><h3>Start from your CV</h3><p className="muted">Paste CV text to suggest skills, then review the list above. The text is processed on this app’s server and is not saved or sent to an external AI service.</p>
          <label>CV text<textarea maxLength={100000} value={cv} onChange={event => setCV(event.target.value)} placeholder="Paste the relevant experience and skills sections…" /></label>
          <button type="button" disabled={busy || !cv.trim()} onClick={() => void suggestSkills()}>Suggest skills from this text</button>
        </section>
      </form>
    </div>
  </main></div>
}
