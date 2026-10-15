"""Single-workspace profile preferences; CV text is parsed transiently, never stored."""
from typing import Annotated, Any, Literal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session
from hiring_scraper.app.database import get_session
from hiring_scraper.app.models import UserProfile, utcnow
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


NEW_PREFERENCES = {'language_levels', 'skill_evidence', 'summary', 'search_area',
                   'secondary_roles', 'education', 'certifications', 'matching_defaults'}


class ProfileRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    name: str = Field(min_length=1, max_length=120)
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
    search_area: SearchArea | None = None
    secondary_roles: list[Term] = Field(default_factory=list, max_length=20)
    education: list[Education] = Field(default_factory=list, max_length=20)
    certifications: list[Term] = Field(default_factory=list, max_length=50)
    matching_defaults: MatchingDefaults = Field(default_factory=MatchingDefaults)


class CVPreview(BaseModel):
    text: str = Field(min_length=1, max_length=100000)


def profile_json(profile):
    data = {'id': profile.id, 'name': profile.name, **profile.preferences,
            'updated_at': profile.updated_at.isoformat()}
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
