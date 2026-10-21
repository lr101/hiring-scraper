export type CityElectionResult = {
  municipality_code: string; municipality: string; state: string; party: string; leading_party: string
  leading_party_vote_share_percent: number; vote_share_percent: number; party_votes: number
  valid_second_votes: number; postal_ballots_included: boolean
  election: string; vote_type: string; source: string; source_url: string
}
export type SearchArea = {
  label: string; city?: string | null; latitude: number | null; longitude: number | null
  radius_km: number; country_code?: string | null; city_election_result?: CityElectionResult | null
}
export type ApplicationExperience = {
  id: string; company: string; title: string; location: string; description: string
  start: string; end: string | null; current: boolean; cv_bullet_ids?: number[]
}
export type ApplicationDetails = {
  first_name?: string; last_name?: string; email?: string; phone?: string; linkedin?: string
  city?: string; country?: string; street?: string; postal_code?: string
  website?: string; portfolio?: string; github?: string
  experiences?: ApplicationExperience[]
  preferences?: {
    available_from?: string; notice_period?: string; salary_amount?: string
    salary_currency?: string; salary_period?: string; authorization_country?: string
    employment_type?: string; weekly_hours?: string
    authorized_to_work?: boolean | null; sponsorship_required?: boolean | null; willing_to_relocate?: boolean | null; willing_to_work_onsite?: boolean | null
  }
}
export type LocalizedProfileContent = Pick<Profile, 'summary' | 'custom_instructions' | 'application_personalization' | 'skills' | 'desired_roles' | 'secondary_roles' | 'skill_evidence' | 'education' | 'certifications'> & {
  application_preferences?: Pick<NonNullable<ApplicationDetails['preferences']>, 'notice_period' | 'employment_type'>
}
export type Profile = {
  localized_content?: Partial<Record<'en' | 'de', Partial<LocalizedProfileContent>>>

  application_personalization?: {
    career_evidence?: string; writing_samples?: string; wording_to_avoid?: string
    career_goals?: string; work_preferences?: string
  }
  application_details?: ApplicationDetails
  cv?: { languages?: Partial<Record<'en' | 'de', { filename: string }>>; filename: string; content_type: string; size_bytes: number; updated_at: string } | null
  id?: number; name: string; summary: string; custom_instructions: string; skills: string[]; desired_roles: string[]; secondary_roles: string[]
  work_styles: string[]; employment_types: string[]; seniority_levels: string[]; experience_years: number | null
  languages: string[]; language_levels: Record<string, string>; excluded_terms: string[]
  skill_evidence: Record<string, { context: string; note: string }>; search_area: SearchArea | null
  education: { level: 'vocational' | 'bachelor' | 'master' | 'doctorate'; field: string; note: string }[]
  certifications: string[]; matching_defaults: {
    min_match_score: number; include_unknown: boolean; low_afd_vote_share_weight: number
  }
}
export function profileBoardDefaults(profile: Pick<Profile, 'search_area'>, params = new URLSearchParams()) {
  const area = profile.search_area
  return {
    location: !params.has('country') && !params.has('state') && !params.has('lat') && !params.has('lon') && area?.latitude != null && area.longitude != null
      ? { ...area, latitude: area.latitude, longitude: area.longitude, precision: 'profile_search_area' } : null,
    radius: Number(params.get('radius_km') ?? area?.radius_km ?? 15),
  }
}
export function profileBoardLink(profile: Pick<Profile, 'id'>) {
  const params = new URLSearchParams({ view: 'jobs', profile_id: String(profile.id) })
  return `/discover?${params}`
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
export function boardLocationParams(location: { latitude: number; longitude: number; city?: string | null; country_code?: string | null; search_scope?: 'radius' | 'state' | 'country'; state?: string | null }, radius: number, countryOverride?: string | null, profileCountry?: string | null) {
  const params = location.search_scope === 'country'
    ? new URLSearchParams({ country: 'DE' })
    : location.search_scope === 'state' && location.state
    ? new URLSearchParams({ state: location.state })
    : new URLSearchParams({ latitude: String(location.latitude), longitude: String(location.longitude), radius_km: String(radius), place: location.city ?? '' })
  const country = countryOverride ?? location.country_code ?? profileCountry
  if (country) params.set('country_code', country.trim().toUpperCase())
  return params
}

/** Apply a tab's narrative edits while updating shared facts just once. */
export function updateProfileLanguage(base: Profile, next: Profile, language: 'en' | 'de'): Profile {
  const keys = ['summary', 'custom_instructions', 'application_personalization', 'skills', 'desired_roles', 'secondary_roles', 'skill_evidence', 'education', 'certifications'] as const
  const content = Object.fromEntries(keys.map(key => [key, next[key]])) as LocalizedProfileContent
  content.application_preferences = Object.fromEntries(['notice_period', 'employment_type'].map(key => [key, next.application_details?.preferences?.[key as 'notice_period'] ?? '']))
  const shared = { ...next, application_details: { ...next.application_details, preferences: { ...next.application_details?.preferences } } }
  if (language === 'de') {
    for (const key of keys) Object.assign(shared, { [key]: base[key] })
    Object.assign(shared.application_details.preferences, Object.fromEntries(['notice_period', 'employment_type'].map(key => [key, base.application_details?.preferences?.[key as 'notice_period'] ?? ''])))
  }
  return { ...shared, localized_content: { ...base.localized_content, [language]: content } }
}
