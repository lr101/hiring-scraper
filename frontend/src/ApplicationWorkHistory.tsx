import type { ApplicationExperience } from './profile'

export default function ApplicationWorkHistory({ entries, onChange }: {
  entries: ApplicationExperience[]
  onChange: (entries: ApplicationExperience[]) => void
}) {
  const update = (index: number, patch: Partial<ApplicationExperience>) =>
    onChange(entries.map((entry, position) => position === index ? { ...entry, ...patch } : entry))
  return <fieldset className="profile-section"><legend>Application work history</legend>
    <p className="muted">Set this up once for the application companion. Add your most recent role first. Use known years or months; a CV is not required.</p>
    {entries.map((entry, index) => <fieldset className="profile-section" key={entry.id}>
      <legend>Work experience {index + 1}</legend>
      {([['company', 'Company'], ['title', 'Job title'], ['location', 'Location']] as const).map(([key, label]) =>
        <label key={key}>{label}<input required={key !== 'location'} maxLength={300} value={entry[key]}
          onChange={event => update(index, { [key]: event.target.value })} /></label>)}
      <label>Responsibilities<textarea maxLength={10000} value={entry.description}
        onChange={event => update(index, { description: event.target.value })} /></label>
      <label>Start year or month<input required maxLength={7} pattern="[1-9][0-9]{3}(-(0[1-9]|1[0-2]))?" placeholder="YYYY or YYYY-MM" value={entry.start}
        onChange={event => update(index, { start: event.target.value })} /></label>
      <label>End year or month<input required={!entry.current} disabled={entry.current} maxLength={7} pattern="[1-9][0-9]{3}(-(0[1-9]|1[0-2]))?" placeholder="YYYY or YYYY-MM" value={entry.end ?? ''}
        onChange={event => update(index, { end: event.target.value || null })} /></label>
      <label><input type="checkbox" checked={entry.current}
        onChange={event => update(index, { current: event.target.checked, ...(event.target.checked ? { end: null } : {}) })} />I currently work here</label>
      <button type="button" onClick={() => onChange(entries.filter((_, position) => position !== index))}>Remove work experience {index + 1}</button>
    </fieldset>)}
    <button type="button" disabled={entries.length >= 20} onClick={() => onChange([...entries, {
      id: crypto.randomUUID(), company: '', title: '', location: '', description: '', start: '', end: null, current: false,
    }])}>Add work experience</button>
  </fieldset>
}
