"""Single-workspace profile preferences; CV text is parsed transiently, never stored."""
from typing import Annotated, Literal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import select
from sqlalchemy.orm import Session
from hiring_scraper.app.database import get_session
from hiring_scraper.app.models import UserProfile, utcnow
from hiring_scraper.matching import extract_profile_skills, normalize_skills

router = APIRouter(prefix='/api/v1/profiles', tags=['Profiles'])
Term = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]


class ProfileRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    skills: list[Term] = Field(default_factory=list, max_length=100)
    desired_roles: list[Term] = Field(default_factory=list, max_length=20)
    work_styles: list[Literal['remote','hybrid','onsite']] = Field(default_factory=list, max_length=3)
    employment_types: list[Literal['full_time','part_time','contract','internship','working_student','apprenticeship']] = Field(default_factory=list, max_length=6)
    seniority_levels: list[Literal['student','junior','senior','lead']] = Field(default_factory=list, max_length=4)
    experience_years: float | None = Field(default=None, ge=0, le=60)
    languages: list[Term] = Field(default_factory=list, max_length=20)
    excluded_terms: list[Term] = Field(default_factory=list, max_length=30)


class CVPreview(BaseModel):
    text: str = Field(min_length=1, max_length=100000)


def profile_json(profile):
    return {'id': profile.id, 'name': profile.name, **profile.preferences,
            'updated_at': profile.updated_at.isoformat()}


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
    profile.preferences = data
    profile.updated_at = utcnow()
    session.add(profile)
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
