"""Single-workspace profile preferences and explicitly saved CV attachments."""
import json
import hashlib
import re
from datetime import date
from pathlib import Path
import subprocess
import sys
from urllib.parse import quote, urlsplit
from typing import Annotated, Any, Literal
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from hiring_scraper.app.database import get_session
from hiring_scraper.app.models import ProfileCV, UserProfile, utcnow
from hiring_scraper.app.cv_documents import MAX_BYTES, TYPES
from hiring_scraper.app.job_search import clear_profile_match_scores
from hiring_scraper.election_data import municipality_vote_result
from hiring_scraper.matching import extract_profile_skills, normalize_skills

router = APIRouter(prefix='/api/v1/profiles', tags=['Profiles'])
Term = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]


class Evidence(BaseModel):
    model_config = ConfigDict(extra='forbid')
    context: str = Field(default='', max_length=120)
    note: str = Field(default='', max_length=2000)


class Education(BaseModel):
    model_config = ConfigDict(extra='forbid')
    level: Literal['vocational', 'bachelor', 'master', 'doctorate']
    field: str = Field(default='', max_length=200)
    note: str = Field(default='', max_length=2000)


class SearchArea(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    label: str = Field(default='', max_length=255)
    city: str | None = Field(default=None, max_length=120)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    radius_km: float = Field(default=35, gt=0, le=200)
    country_code: Annotated[str, StringConstraints(to_upper=True, pattern=r'^[A-Za-z]{2}$')] | None = None
    city_election_result: dict[str, Any] | None = Field(default=None, exclude=True)

    @model_validator(mode='after')
    def coordinate_pair(self):
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError('Provide both latitude and longitude')
        return self


class MatchingDefaults(BaseModel):
    model_config = ConfigDict(extra='forbid')
    min_match_score: int = Field(default=0, ge=0, le=100)
    include_unknown: bool = True
    low_afd_vote_share_weight: int = Field(default=0, ge=0, le=20)


NEW_PREFERENCES = {'language_levels', 'skill_evidence', 'summary', 'custom_instructions', 'search_area',
                   'secondary_roles', 'education', 'certifications', 'matching_defaults', 'application_details',
                   'application_personalization'}


class ApplicationPersonalization(BaseModel):
    model_config = ConfigDict(extra='forbid')
    career_evidence: str = Field(default='', max_length=12000)
    writing_samples: str = Field(default='', max_length=12000)
    wording_to_avoid: str = Field(default='', max_length=3000)
    career_goals: str = Field(default='', max_length=5000)
    work_preferences: str = Field(default='', max_length=5000)


class ApplicationPreferences(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    available_from: str = Field(default='', max_length=300)
    notice_period: str = Field(default='', max_length=300)
    salary_amount: str = Field(default='', max_length=300)
    salary_currency: str = Field(default='', max_length=300)
    salary_period: str = Field(default='', max_length=300)
    authorization_country: str = Field(default='', max_length=300)
    employment_type: str = Field(default='', max_length=300)
    weekly_hours: str = Field(default='', max_length=300)
    authorized_to_work: bool | None = None
    sponsorship_required: bool | None = None
    willing_to_relocate: bool | None = None
    willing_to_work_onsite: bool | None = None

    @model_validator(mode='after')
    def validate_answers(self):
        if self.available_from:
            if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', self.available_from):
                raise ValueError('Available from must be YYYY-MM-DD')
            date.fromisoformat(self.available_from)
        if self.salary_amount and not re.fullmatch(r'\d+(?:\.\d{1,2})?', self.salary_amount):
            raise ValueError('Salary must be numeric without currency or separators')
        if self.salary_currency and not re.fullmatch(r'[A-Z]{3}', self.salary_currency):
            raise ValueError('Salary currency must be a three-letter code such as EUR')
        if self.salary_period and self.salary_period not in {'year', 'month', 'hour'}:
            raise ValueError('Salary period must be year, month or hour')
        if self.weekly_hours and (not re.fullmatch(r'\d+(?:\.\d)?', self.weekly_hours) or float(self.weekly_hours) > 168):
            raise ValueError('Weekly hours must be between 0 and 168')
        return self


class ApplicationExperience(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    id: str = Field(min_length=1, max_length=100, pattern=r'^[a-zA-Z0-9_-]+$')
    company: str = Field(min_length=1, max_length=300)
    title: str = Field(min_length=1, max_length=300)
    location: str = Field(default='', max_length=300)
    description: str = Field(default='', max_length=10000)
    start: str = Field(min_length=4, max_length=7)
    end: str | None = Field(default=None, max_length=7)
    current: bool = Field(default=False, strict=True)
    cv_bullet_ids: list[Annotated[int, Field(ge=0, strict=True)]] = Field(default_factory=list, max_length=50)

    @model_validator(mode='after')
    def validate_history(self):
        for value in (self.start, self.end):
            if value is not None and not re.fullmatch(r'[1-9][0-9]{3}(?:-(?:0[1-9]|1[0-2]))?', value):
                raise ValueError('Work dates must be a known year or month (YYYY or YYYY-MM)')
        if self.current and self.end:
            raise ValueError('A current role cannot have an end date')
        if not self.current and not self.end:
            raise ValueError('A previous role needs an end year or month')
        if self.end and (self.end[:4] < self.start[:4] or
                         (len(self.end) == len(self.start) == 7 and self.end < self.start)):
            raise ValueError('The end of a role cannot precede its start')
        if len(set(self.cv_bullet_ids)) != len(self.cv_bullet_ids):
            raise ValueError('Use unique CV bullet paragraph IDs')
        return self


class ApplicationDetails(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    first_name: str = Field(default='', max_length=300)
    last_name: str = Field(default='', max_length=300)
    email: str = Field(default='', max_length=300)
    phone: str = Field(default='', max_length=300)
    linkedin: str = Field(default='', max_length=300)
    city: str = Field(default='', max_length=300)
    country: str = Field(default='', max_length=300)
    street: str = Field(default='', max_length=300)
    postal_code: str = Field(default='', max_length=300)
    website: str = Field(default='', max_length=300)
    portfolio: str = Field(default='', max_length=300)
    github: str = Field(default='', max_length=300)
    experiences: list[ApplicationExperience] = Field(default_factory=list, max_length=20)
    preferences: ApplicationPreferences = Field(default_factory=ApplicationPreferences)

    @model_validator(mode='after')
    def validate_contacts(self):
        identities = [(entry.company.lower(), entry.title.lower(), entry.start) for entry in self.experiences]
        if len(set(identities)) != len(identities):
            raise ValueError('Remove duplicate work experiences')
        if len({entry.id for entry in self.experiences}) != len(self.experiences):
            raise ValueError('Use unique work experience IDs')
        bullet_ids = [bullet for entry in self.experiences for bullet in entry.cv_bullet_ids]
        if len(set(bullet_ids)) != len(bullet_ids):
            raise ValueError('Assign each CV bullet paragraph to only one role')
        if self.email and not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', self.email):
            raise ValueError('Enter a valid email address')
        for value in (self.linkedin, self.website, self.portfolio, self.github):
            if value:
                url = urlsplit(value)
                if url.scheme != 'https' or not url.hostname or url.username or url.password:
                    raise ValueError('Contact links must be complete HTTPS URLs')
        return self


class ProfileRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    name: str = Field(min_length=1, max_length=120)
    application_details: ApplicationDetails = Field(default_factory=ApplicationDetails)
    application_personalization: ApplicationPersonalization = Field(default_factory=ApplicationPersonalization)
    skills: list[Term] = Field(default_factory=list, max_length=100)
    desired_roles: list[Term] = Field(default_factory=list, max_length=20)
    work_styles: list[Literal['remote','hybrid','onsite']] = Field(default_factory=list, max_length=3)
    employment_types: list[Literal['full_time','part_time','contract','internship','working_student','apprenticeship','dual_study']] = Field(default_factory=list, max_length=7)
    seniority_levels: list[Literal['student','junior','senior','lead']] = Field(default_factory=list, max_length=4)
    experience_years: float | None = Field(default=None, ge=0, le=60)
    languages: list[Term] = Field(default_factory=list, max_length=20)
    excluded_terms: list[Term] = Field(default_factory=list, max_length=30)
    language_levels: dict[Term, Literal['A1','A2','B1','B2','C1','C2','native']] = Field(default_factory=dict, max_length=20)
    skill_evidence: dict[Term, Evidence] = Field(default_factory=dict, max_length=100)
    summary: str = Field(default='', max_length=5000)
    custom_instructions: str = Field(default='', max_length=5000)
    search_area: SearchArea | None = None
    secondary_roles: list[Term] = Field(default_factory=list, max_length=20)
    education: list[Education] = Field(default_factory=list, max_length=20)
    certifications: list[Term] = Field(default_factory=list, max_length=50)
    matching_defaults: MatchingDefaults = Field(default_factory=MatchingDefaults)


class CVPreview(BaseModel):
    text: str = Field(min_length=1, max_length=100000)


def profile_json(profile):
    data = {'id': profile.id, 'name': profile.name, **profile.preferences,
            'updated_at': profile.updated_at.isoformat(), 'cv': cv_metadata(profile.cv)}
    data.setdefault('custom_instructions', '')
    data.setdefault('application_personalization', ApplicationPersonalization().model_dump())
    defaults = data.get('matching_defaults')
    defaults = defaults if isinstance(defaults, dict) else {}
    data['matching_defaults'] = {
        **defaults,
        'low_afd_vote_share_weight': profile.low_afd_vote_share_weight,
    }
    area = data.get('search_area')
    if isinstance(area, dict):
        data['search_area'] = {
            **area,
            'city_election_result': municipality_vote_result(
                area.get('label') or area.get('city'), area.get('country_code')),
        }
    return data


def require_profile(profile_id, session):
    profile = session.get(UserProfile, profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail='Profile not found')
    return profile


@router.post('/preview')
def preview_cv(request: CVPreview):
    return {'skills': extract_profile_skills(request.text),
            'note': 'Review these suggestions before saving. CV text is not stored.'}


@router.get('')
def list_profiles(session: Session = Depends(get_session)):
    return {'items': [profile_json(row) for row in session.scalars(select(UserProfile).order_by(UserProfile.id))]}


def _save(profile, request, session):
    data = request.model_dump()
    profile.name = data.pop('name').strip()
    if not profile.name:
        raise HTTPException(status_code=422, detail='Enter a profile name')
    data['skills'] = normalize_skills(data['skills'])
    previous = profile.preferences or {}
    previous_weight = profile.low_afd_vote_share_weight
    defaults = data.get('matching_defaults') or {}
    requested_weight = defaults.pop('low_afd_vote_share_weight', 0)
    if ('matching_defaults' not in request.model_fields_set or
            'low_afd_vote_share_weight' not in request.matching_defaults.model_fields_set):
        requested_weight = previous_weight
    profile.low_afd_vote_share_weight = requested_weight
    for key in NEW_PREFERENCES - request.model_fields_set:
        if key in previous:
            data[key] = previous[key]
    profile.preferences = data
    profile.updated_at = utcnow()
    preferences_changed = data != previous or profile.low_afd_vote_share_weight != previous_weight
    if preferences_changed:
        profile.match_index_version = None
    session.add(profile)
    if profile.id is not None and preferences_changed:
        # Acquire the profile row lock before clearing derived rows. Job refreshes
        # lock profiles in the same order, so no stale New rows can race past an edit.
        session.flush()
        clear_profile_match_scores(session, profile.id)
    session.commit()
    return profile_json(profile)


@router.post('', status_code=201)
def create_profile(request: ProfileRequest, session: Session = Depends(get_session)):
    return _save(UserProfile(), request, session)


@router.put('/{profile_id}')
def update_profile(profile_id: int, request: ProfileRequest, session: Session = Depends(get_session)):
    return _save(require_profile(profile_id, session), request, session)


@router.delete('/{profile_id}')
def delete_profile(profile_id: int, session: Session = Depends(get_session)):
    session.delete(require_profile(profile_id, session))
    session.commit()
    return {'deleted': True}


def cv_metadata(cv):
    return None if cv is None else {'filename': cv.filename, 'content_type': cv.content_type,
                                   'size_bytes': cv.size_bytes, 'updated_at': cv.updated_at.isoformat()}


def store_profile_cv(session, profile_id, filename, data):
    extension = Path(filename).suffix.lower()
    if not filename or len(filename) > 200 or any(char in filename for char in '/\\\r\n\x00') or extension not in TYPES:
        raise HTTPException(422, 'Choose a PDF, DOCX, TXT or TEX file with a simple filename.')
    if len(data) > MAX_BYTES:
        raise HTTPException(413, 'CV files must be 5 MB or smaller.')
    if not data:
        raise HTTPException(422, 'Choose a nonempty CV file.')
    # No database connection is held while parsing untrusted uploaded documents.
    try:
        result = subprocess.run([sys.executable, '-m', 'hiring_scraper.app.cv_documents', extension],
                                input=data, capture_output=True, timeout=10)
        parsed = json.loads(result.stdout)
    except (subprocess.TimeoutExpired, ValueError):
        raise HTTPException(422, 'Could not read this CV within the limit. Try a simpler PDF, DOCX, TXT or TEX.') from None
    if result.returncode or not parsed.get('text'):
        raise HTTPException(422, parsed.get('error') or 'Could not read this CV.')
    profile = require_profile(profile_id, session)
    cv = profile.cv or ProfileCV(profile=profile)
    cv.filename, cv.content_type, cv.size_bytes = filename, TYPES[extension], len(data)
    cv.data, cv.extracted_text, cv.updated_at = data, parsed['text'], utcnow()
    session.add(cv)
    session.commit()
    return cv_metadata(cv)


@router.put('/{profile_id}/cv')
async def upload_cv(profile_id: int, request: Request, filename: str = Query(..., min_length=1, max_length=200),
                    session: Session = Depends(get_session)):
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > MAX_BYTES:
            raise HTTPException(413, 'CV files must be 5 MB or smaller.')
        data.extend(chunk)
    return await run_in_threadpool(store_profile_cv, session, profile_id, filename, bytes(data))


def require_cv(profile_id, session):
    require_profile(profile_id, session)
    cv = session.get(ProfileCV, profile_id)
    if cv is None:
        raise HTTPException(404, 'Attach a CV to this profile first.')
    return cv


@router.get('/{profile_id}/cv/text')
def cv_text(profile_id: int, session: Session = Depends(get_session)):
    cv = require_cv(profile_id, session)
    return JSONResponse({'text': cv.extracted_text, 'filename': cv.filename}, headers={'Cache-Control': 'no-store'})


@router.get('/{profile_id}/cv')
def download_cv(profile_id: int, session: Session = Depends(get_session)):
    cv = require_cv(profile_id, session)
    return Response(cv.data, media_type=cv.content_type, headers={
        'Content-Disposition': f"attachment; filename=\"cv{Path(cv.filename).suffix.lower()}\"; filename*=UTF-8''{quote(cv.filename, safe='')}",
        'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})


@router.delete('/{profile_id}/cv', status_code=204)
def delete_cv(profile_id: int, session: Session = Depends(get_session)):
    profile = require_profile(profile_id, session)
    profile.cv = None
    session.commit()
    return Response(status_code=204)


class TemplateChange(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: int = Field(ge=0)
    text: str = Field(max_length=5000)


class CVExport(BaseModel):
    model_config = ConfigDict(extra='forbid')
    source_sha256: Annotated[str, StringConstraints(pattern=r'^[a-f0-9]{64}$')]
    changes: list[TemplateChange] = Field(default_factory=list, max_length=1000)


@router.get('/{profile_id}/cv/template')
def cv_template(profile_id: int, session: Session = Depends(get_session)):
    from hiring_scraper.app.cv_templates import template_paragraphs
    cv = require_cv(profile_id, session)
    if not cv.filename.lower().endswith('.docx'):
        raise HTTPException(422, 'Attach a DOCX to keep and edit your original CV template.')
    data = cv.data
    return JSONResponse({'source_sha256': hashlib.sha256(data).hexdigest(), 'paragraphs': template_paragraphs(data)},
                        headers={'Cache-Control': 'no-store'})


@router.post('/{profile_id}/cv/export')
def export_cv(profile_id: int, request: CVExport, format: Literal['docx', 'pdf'] = 'docx',
              preview: bool = False, session: Session = Depends(get_session)):
    from hiring_scraper.app.cv_templates import edit_template, render_pdf
    cv = require_cv(profile_id, session)
    if not cv.filename.lower().endswith('.docx'):
        raise HTTPException(422, 'Attach a DOCX to keep and edit your original CV template.')
    data = cv.data
    if hashlib.sha256(data).hexdigest() != request.source_sha256:
        raise HTTPException(409, 'Your saved CV changed. Reload this application before exporting.')
    session.rollback()  # Don't keep a database connection while rendering a file.
    try:
        output = edit_template(data, [row.model_dump() for row in request.changes])
        if format == 'pdf':
            output = render_pdf(output)
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
    except RuntimeError as error:
        raise HTTPException(503, str(error)) from None
    return Response(output, media_type='application/pdf' if format == 'pdf' else TYPES['.docx'], headers={
        'Content-Disposition': f'{"inline" if preview else "attachment"}; filename="tailored-cv.{format}"',
        'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})
