"""Shared career-fact and job-fact projections used by profiles and application drafting."""
from __future__ import annotations

from hiring_scraper.app.enrichment import job_input
from hiring_scraper.app.models import UserProfile

_CAPTURE_METADATA_FIELDS = frozenset({
    'source_observed_at', 'source_api_url', 'search_terms', 'detail_state',
})


def _strip_capture_metadata(value):
    """Strip capture bookkeeping while retaining job facts."""
    if isinstance(value, dict):
        return {key: _strip_capture_metadata(item) for key, item in value.items()
                if not (isinstance(key, str) and
                        (key.startswith('_') or key in _CAPTURE_METADATA_FIELDS))}
    if isinstance(value, list):
        return [_strip_capture_metadata(item) for item in value]
    return value


def cv_career_data(source):
    """Use the same explicit CV fact projection as deterministic matching."""
    from hiring_scraper.app.tex_cv import career_facts_from_tex
    return career_facts_from_tex(source)


def _cv_source(profile):
    cv = profile.cv
    if cv is None:
        return ''
    variants = cv.variants or {}
    canonical = variants.get('en') or variants.get('de')
    return canonical.get('text', '') if canonical else cv.extracted_text


def profile_data(profile_or_preferences):
    """Project career facts, goals and relevant logistics from a profile or preference dict."""
    preferences = (profile_or_preferences.preferences if isinstance(profile_or_preferences, UserProfile)
                   else profile_or_preferences) or {}
    fields = ('skills', 'desired_roles', 'secondary_roles', 'summary', 'skill_evidence',
              'work_styles', 'employment_types', 'seniority_levels', 'experience_years',
              'languages', 'language_levels', 'education', 'certifications', 'excluded_terms', 'search_area')
    result = {key: preferences[key] for key in fields
              if preferences.get(key) or (key == 'experience_years' and preferences.get(key) == 0)}
    personalization = preferences.get('application_personalization') or {}
    result.update({key: personalization[key] for key in ('career_evidence', 'career_goals', 'work_preferences')
                   if personalization.get(key)})
    details = preferences.get('application_details') or {}
    experiences = details.get('experiences') or []
    if experiences:
        result['work_history'] = [{key: row[key] for key in ('company', 'title', 'location', 'description',
                                                            'start', 'end', 'current') if key in row}
                                  for row in experiences]
    logistics = details.get('preferences') or {}
    allowed = ('available_from', 'notice_period', 'salary_amount', 'salary_currency', 'salary_period',
               'authorization_country', 'employment_type', 'weekly_hours', 'authorized_to_work',
               'sponsorship_required', 'willing_to_relocate', 'willing_to_work_onsite')
    result['application_preferences'] = {key: logistics[key] for key in allowed
                                          if key in logistics and logistics[key] not in ('', None)}
    if isinstance(profile_or_preferences, UserProfile):
        from hiring_scraper.app.tex_cv import cv_language_fallback
        cv_levels = cv_language_fallback(profile_or_preferences.cv, preferences.get('language_levels'))
        if cv_levels:
            result['cv_language_levels'] = cv_levels
        cv_facts = cv_career_data(_cv_source(profile_or_preferences))
        if cv_facts:
            result['cv_career_facts'] = cv_facts
    return result


def job_data(job):
    result = job_input(job)
    result['raw_metadata'] = _strip_capture_metadata(result.get('raw_metadata') or {})
    for key in ('department', 'date_posted', 'is_active', 'closed_at'):
        result[key] = getattr(job, key)
    result['locations'] = [{'label': row.label, 'country_code': row.country_code,
                            'latitude': row.latitude, 'longitude': row.longitude, 'precision': row.precision}
                           for row in job.locations]
    return result
