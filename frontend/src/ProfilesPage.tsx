import { useEffect, useState } from 'react'
import SiteHeader from './SiteHeader'

const API = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '')
import type { SearchArea, Profile } from './profile'
import { profileBoardLink, selectedLanguageLevels, skillEvidenceKey } from './profile'
const empty = (): Profile => ({ name: '', summary: '', skills: [], desired_roles: [], secondary_roles: [], work_styles: [], employment_types: [],
  seniority_levels: [], experience_years: null, languages: [], language_levels: {}, excluded_terms: [], skill_evidence: {}, search_area: null,
  education: [], certifications: [], matching_defaults: { min_match_score: 30, include_unknown: true } })
const terms = (value: string) => value.split(',').map(term => term.trim()).filter(Boolean)

export default function ProfilesPage() {
  const [profiles, setProfiles] = useState<Profile[]>([])
  const [form, setForm] = useState<Profile>(empty())
  const [skillsText, setSkillsText] = useState('')
  const [rolesText, setRolesText] = useState('')
  const [secondaryText, setSecondaryText] = useState('')
  const [languageText, setLanguageText] = useState('')
  const [certificationsText, setCertificationsText] = useState('')
  const [excludedText, setExcludedText] = useState('')
  const [areaQuery, setAreaQuery] = useState('')
  const [areaChoices, setAreaChoices] = useState<SearchArea[]>([])
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
    setForm({ ...empty(), ...Object.fromEntries(Object.keys(empty()).filter(key => key in profile).map(key => [key, profile[key as keyof Profile]])), id: profile.id }); setSecondaryText((profile.secondary_roles ?? []).join(', ')); setLanguageText(profile.languages.join(', ')); setCertificationsText((profile.certifications ?? []).join(', ')); setSkillsText(profile.skills.join(', ')); setRolesText(profile.desired_roles.join(', ')); setExcludedText(profile.excluded_terms.join(', ')); setCV(''); setAreaChoices([]); setAreaQuery(''); setNotice(''); setError('')
  }
  function toggle(key: 'work_styles' | 'employment_types' | 'seniority_levels' | 'languages', value: string) {
    setForm(previous => ({ ...previous, [key]: previous[key].includes(value) ? previous[key].filter(item => item !== value) : [...previous[key], value] }))
  }
  async function save(event: React.FormEvent) {
    event.preventDefault(); setBusy(true); setError(''); setNotice('')
    try {
      const { id, ...payload } = { ...form, skills: terms(skillsText), desired_roles: terms(rolesText), excluded_terms: terms(excludedText), secondary_roles: terms(secondaryText), languages: terms(languageText), language_levels: selectedLanguageLevels(terms(languageText), form.language_levels), certifications: terms(certificationsText) }
      const saved = await request(id ? `/${id}` : '', { method: id ? 'PUT' : 'POST',
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })
      edit(saved); await load(); setNotice('Profile saved. You can now use it to find matching jobs.')
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not save your profile.') }
    finally { setBusy(false) }
  }
  async function findArea() {
    setBusy(true); setError('')
    try {
      const response = await fetch(`${API}/api/v1/locations/resolve`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ query: areaQuery.trim() }) })
      const payload = await response.json()
      if (!response.ok) throw new Error('Could not find that area. Try a German city or postcode.')
      setAreaChoices(payload.results.map((item: SearchArea) => ({ label: item.label, city: item.city, latitude: item.latitude, longitude: item.longitude, country_code: 'DE', radius_km: form.search_area?.radius_km ?? 35 })))
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
      <p className="intro-copy">Tell us what you can do and what you’re looking for. Matching jobs start within your selected area; choose remote roles separately on the jobs board.</p>
      <p className="muted">This early version uses explicit skills and interests. It does not infer qualifications from your name, age, or other personal details.</p>
    </section>
    {error && <div className="error-banner" role="alert">{error}</div>}
    {notice && <div className="evidence-note" role="status">{notice}</div>}
    <div className="profile-grid">
      <section className="detail-panel"><h2>Saved profiles</h2>{loading ? <p>Loading profiles…</p> : profiles.length ? profiles.map(profile =>
        <article className="saved-profile" key={profile.id}><h3>{profile.name}</h3><p>{profile.summary || profile.skills.join(', ') || 'No skills entered'}</p><p className="muted">{profile.search_area ? `${profile.search_area.city || profile.search_area.label} · ${profile.search_area.radius_km} km` : 'Choose an area on the board'} · {Object.entries(profile.language_levels ?? {}).map(([language, level]) => `${language} ${level}`).join(' · ')}</p>
          <div className="detail-links"><a className="page-link" href={profileBoardLink(profile)}>Find matching jobs →</a>
            <a className="page-link" href={`/applications?profile_id=${profile.id}`}>My applications →</a>
            <button type="button" disabled={busy} onClick={() => edit(profile)}>Edit</button>
            <button type="button" disabled={busy} onClick={() => void remove(profile.id!)}>Delete profile</button></div></article>) : <p className="muted">Create your first profile to see which jobs fit.</p>}
        <button type="button" disabled={busy} onClick={() => edit(empty())}>Create another profile</button>
      </section>
      <form className="detail-panel profile-form" onSubmit={save}>
        <h2>{form.id ? 'Edit your profile' : 'Create a profile'}</h2>
        <label>Profile name<input required maxLength={120} value={form.name} onChange={event => setForm({ ...form, name: event.target.value })} placeholder="e.g. Backend developer" /></label>
        <label>Profile summary<textarea maxLength={5000} value={form.summary} onChange={event => setForm({ ...form, summary: event.target.value })} /></label>
        <label>Skills <small>Separate with commas; you can add skills beyond our suggestions</small><textarea maxLength={8000} value={skillsText} onChange={event => setSkillsText(event.target.value)} placeholder="Python, SQL, Docker, project management" /></label>
        <label>Roles you’re interested in <small>Separate with commas</small><input value={rolesText} onChange={event => setRolesText(event.target.value)} placeholder="Software developer, backend" /></label>
        <label>Adjacent roles <small>Other roles you would consider; separate with commas</small><input value={secondaryText} onChange={event => setSecondaryText(event.target.value)} /></label>
        <label>Years of relevant experience <small>Optional; used only when a job states a minimum</small><input type="number" min={0} max={60} step={0.5} value={form.experience_years ?? ''} onChange={event => setForm({ ...form, experience_years: event.target.value === '' ? null : Number(event.target.value) })} /></label>
        {choices('work_styles','Preferred work styles',[['remote','Remote'],['hybrid','Hybrid'],['onsite','On-site']])}
        {choices('employment_types','Employment types',[['full_time','Full time'],['part_time','Part time'],['contract','Contract'],['internship','Internship'],['working_student','Student job'],['apprenticeship','Apprenticeship']])}
        {choices('seniority_levels','Experience levels',[['student','Student / trainee'],['junior','Junior'],['senior','Senior'],['lead','Lead']])}
        <fieldset className="profile-section"><legend>Languages and proficiency</legend>
          <label>Languages <small>Separate with commas; leave a level blank if unknown</small><input value={languageText} onChange={event => setLanguageText(event.target.value)} placeholder="Spanish, English, German, French" /></label>
          <div className="profile-language-grid">{terms(languageText).map(language => <label key={language}>{language}<select aria-label={`${language} proficiency`} value={selectedLanguageLevels(terms(languageText), form.language_levels)[language] ?? ''} onChange={event => { const next = selectedLanguageLevels(terms(languageText), form.language_levels); if (event.target.value) next[language] = event.target.value; else delete next[language]; setForm({ ...form, language_levels: next }) }}>
            <option value="">Unknown</option>{['A1','A2','B1','B2','C1','C2','native'].map(level => <option key={level} value={level}>{level === 'native' ? 'Native' : level}</option>)}
          </select></label>)}</div>
        </fieldset>
        <fieldset className="profile-section"><legend>Search area</legend>
          <p className="muted">Local roles within your radius appear by default. Choose Remote in the jobs board location filter to see remote roles with country eligibility checked.</p>
          <label>Change city or postcode<input value={areaQuery} onChange={event => setAreaQuery(event.target.value)} placeholder="e.g. Mannheim" /></label>
          <button type="button" disabled={busy || areaQuery.trim().length < 2} onClick={() => void findArea()}>Find area</button>
          {areaChoices.map((area,index) => <button type="button" key={index} onClick={() => { setForm({ ...form, search_area: area }); setAreaChoices([]) }}>{area.label}</button>)}
          {form.search_area ? <><div className="profile-area-grid">
            <label>Selected city<input readOnly value={form.search_area.city || form.search_area.label} /></label>
            <label>Radius (km)<input type="number" min={1} max={200} value={form.search_area.radius_km} onChange={event => setForm({ ...form, search_area: { ...form.search_area!, radius_km: Number(event.target.value) } })} /></label>
            <label>Country code<input maxLength={2} value={form.search_area.country_code ?? ''} placeholder="DE" onChange={event => setForm({ ...form, search_area: { ...form.search_area!, country_code: event.target.value.toUpperCase() || null } })} /></label>
          </div><details><summary>Map coordinates</summary><div className="profile-area-grid">{(['latitude','longitude'] as const).map(key => <label key={key}>{key}<input type="number" step="any" min={key === 'latitude' ? -90 : -180} max={key === 'latitude' ? 90 : 180} value={form.search_area![key] ?? ''} onChange={event => setForm({ ...form, search_area: { ...form.search_area!, [key]: event.target.value === '' ? null : Number(event.target.value) } })} /></label>)}</div></details><button type="button" onClick={() => setForm({ ...form, search_area: null })}>Use board area instead</button></> : <button type="button" onClick={() => setForm({ ...form, search_area: { label: 'Heidelberg, Germany', city: 'Heidelberg', latitude: 49.40936, longitude: 8.69472, radius_km: 35, country_code: 'DE' } })}>Set Heidelberg area</button>}
        </fieldset>
        <fieldset className="profile-section"><legend>Evidence behind your skills</legend><p className="muted">Distinguish employment from academic or research experience. Leave unknown experience unstated.</p>
          {terms(skillsText).map(skill => { const evidenceKey = skillEvidenceKey(skill, form.skill_evidence); return <details className="profile-evidence-row" key={skill}><summary>{skill} <small>{form.skill_evidence[evidenceKey]?.context || 'Context not recorded'}</small></summary>
            <label>Experience context<select value={form.skill_evidence[evidenceKey]?.context ?? ''} onChange={event => setForm({ ...form, skill_evidence: { ...form.skill_evidence, [evidenceKey]: { note: form.skill_evidence[evidenceKey]?.note ?? '', context: event.target.value } } })}><option value="">Unknown</option>{['professional','academic','research'].map(context => <option key={context} value={context}>{context}</option>)}{form.skill_evidence[evidenceKey]?.context && !['professional','academic','research'].includes(form.skill_evidence[evidenceKey].context) && <option value={form.skill_evidence[evidenceKey].context}>{form.skill_evidence[evidenceKey].context}</option>}</select></label>
            <label>Supporting experience<textarea maxLength={2000} value={form.skill_evidence[evidenceKey]?.note ?? ''} onChange={event => setForm({ ...form, skill_evidence: { ...form.skill_evidence, [evidenceKey]: { context: form.skill_evidence[evidenceKey]?.context ?? '', note: event.target.value } } })} /></label>
          </details> })}
        </fieldset>
        <fieldset className="profile-section"><legend>Education and certifications</legend>
          {form.education.map((row,index) => <div className="profile-education-row" key={index}>
            <label>Qualification<select value={row.level} onChange={event => setForm({ ...form, education: form.education.map((item,i) => i === index ? { ...item, level: event.target.value as typeof row.level } : item) })}>{[['vocational','Vocational'],['bachelor','Bachelor’s degree'],['master','Master’s degree'],['doctorate','Doctorate']].map(([value,label]) => <option value={value} key={value}>{label}</option>)}</select></label>
            <label>Field of study<input value={row.field} maxLength={200} onChange={event => setForm({ ...form, education: form.education.map((item,i) => i === index ? { ...item, field: event.target.value } : item) })} /></label>
            <label>Details and recognition notes<textarea value={row.note} maxLength={2000} onChange={event => setForm({ ...form, education: form.education.map((item,i) => i === index ? { ...item, note: event.target.value } : item) })} /></label>
            <button type="button" onClick={() => setForm({ ...form, education: form.education.filter((_,i) => i !== index) })}>Remove qualification</button>
          </div>)}
          <button type="button" onClick={() => setForm({ ...form, education: [...form.education, { level: 'bachelor', field: '', note: '' }] })}>Add qualification</button>
          <label>Certifications <small>Separate with commas</small><input value={certificationsText} onChange={event => setCertificationsText(event.target.value)} /></label>
        </fieldset>
        <fieldset className="profile-section"><legend>Board defaults</legend>
          <label>Minimum overlap score<input type="number" min={0} max={100} value={form.matching_defaults.min_match_score} onChange={event => setForm({ ...form, matching_defaults: { ...form.matching_defaults, min_match_score: Number(event.target.value) } })} /></label>
          <label className="unknown-filter"><input type="checkbox" checked={form.matching_defaults.include_unknown} onChange={event => setForm({ ...form, matching_defaults: { ...form.matching_defaults, include_unknown: event.target.checked } })} />Include possible leads with missing information</label>
        </fieldset>
        <label>Hide job titles containing <small>Separate with commas</small><input value={excludedText} onChange={event => setExcludedText(event.target.value)} placeholder="Internship, Praktikum" /></label>
        <button className="primary-button" disabled={busy} type="submit">{busy ? 'Working…' : 'Save profile'}</button>
        {form.id && <a className="page-link" href={profileBoardLink(form)}>View matching jobs →</a>}
        <section className="cv-import"><h3>Start from your CV</h3><p className="muted">Paste CV text to suggest skills, then review the list above. The text is processed on this app’s server and is not saved or sent to an external AI service.</p>
          <label>CV text<textarea maxLength={100000} value={cv} onChange={event => setCV(event.target.value)} placeholder="Paste the relevant experience and skills sections…" /></label>
          <button type="button" disabled={busy || !cv.trim()} onClick={() => void suggestSkills()}>Suggest skills from this text</button>
        </section>
      </form>
    </div>
  </main></div>
}
