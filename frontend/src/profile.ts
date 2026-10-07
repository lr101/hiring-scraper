export type SearchArea = { label: string; city?: string | null; latitude: number | null; longitude: number | null; radius_km: number; country_code?: string | null }
export type Profile = {
  id?: number; name: string; summary: string; skills: string[]; desired_roles: string[]; secondary_roles: string[]
  work_styles: string[]; employment_types: string[]; seniority_levels: string[]; experience_years: number | null
  languages: string[]; language_levels: Record<string, string>; excluded_terms: string[]
  skill_evidence: Record<string, { context: string; note: string }>; search_area: SearchArea | null
  education: { level: 'vocational' | 'bachelor' | 'master' | 'doctorate'; field: string; note: string }[]
  certifications: string[]; matching_defaults: { min_match_score: number; include_unknown: boolean }
}
export function profileBoardDefaults(profile: Pick<Profile, 'search_area' | 'matching_defaults'>, params = new URLSearchParams()) {
  const area = profile.search_area
  return {
    location: !params.has('lat') && !params.has('lon') && area?.latitude != null && area.longitude != null
      ? { ...area, latitude: area.latitude, longitude: area.longitude, precision: 'profile_search_area' } : null,
    radius: Number(params.get('radius_km') ?? area?.radius_km ?? 15),
    minimumScore: params.get('min_match_score') ?? String(profile.matching_defaults?.min_match_score ?? 30),
    includeUnknown: params.has('include_unknown') ? params.get('include_unknown') !== 'false' : profile.matching_defaults?.include_unknown !== false,
  }
}
export function profileBoardLink(profile: Pick<Profile, 'id' | 'search_area' | 'matching_defaults'>) {
  const params = new URLSearchParams({ view: 'jobs', profile_id: String(profile.id),
    min_match_score: String(profile.matching_defaults?.min_match_score ?? 30), include_unknown: String(profile.matching_defaults?.include_unknown !== false) })
  const area = profile.search_area
  if (area?.latitude != null && area.longitude != null) {
    params.set('lat', String(area.latitude)); params.set('lon', String(area.longitude)); params.set('radius_km', String(area.radius_km))
    params.set('place', area.city || area.label)
  }
  return `/?${params}`
}

/** Persist only visible languages, preserving proficiency across case-only edits. */
export function selectedLanguageLevels(languages: string[], levels: Record<string, string>) {
  const selected: Record<string, string> = {}
  const seen = new Set<string>()
  for (const text of languages) {
    const language = text.trim()
    const identity = language.toLocaleLowerCase()
    if (!language || seen.has(identity)) continue
    seen.add(identity)
    const key = Object.hasOwn(levels, language) ? language : Object.keys(levels).find(name => name.trim().toLocaleLowerCase() === identity)
    if (key) selected[language] = levels[key]
  }
  return selected
}

/** Match the canonical imported skill to the original evidence entry for editing. */
export function skillEvidenceKey(skill: string, evidence: Profile['skill_evidence']) {
  const identity = (name: string) => name.trim().toLocaleLowerCase().replace(/^stakeholder management$/, 'stakeholder coordination')
  return Object.hasOwn(evidence, skill) ? skill : Object.keys(evidence).find(name => identity(name) === identity(skill)) ?? skill
}

/** Use one effective country for board requests and the URL carried into details. */
export function boardLocationParams(location: { latitude: number; longitude: number; city?: string | null; country_code?: string | null }, radius: number, countryOverride?: string | null, profileCountry?: string | null) {
  const params = new URLSearchParams({ latitude: String(location.latitude), longitude: String(location.longitude), radius_km: String(radius), place: location.city ?? '' })
  const country = countryOverride ?? location.country_code ?? profileCountry
  if (country) params.set('country_code', country.trim().toUpperCase())
  return params
}
