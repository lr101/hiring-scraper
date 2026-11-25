"""Manage the ChatGPT connection used for application preparation."""
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from typing import Literal

from hiring_scraper.app import codex_runtime, remote_matching
from hiring_scraper.app.codex_usage import evaluate

router = APIRouter(prefix='/api/v1', tags=['application-preparation'])


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


@router.get('/application-preparation')
def preparation_status():
    return JSONResponse(codex_runtime.status(), headers={'Cache-Control': 'no-store'})


class ModelSetting(StrictModel):
    model: str | None = Field(default=None, min_length=1, max_length=100, pattern=r'^[A-Za-z0-9][A-Za-z0-9._-]*$')


@router.get('/application-preparation/usage')
def preparation_usage():
    """Subscription usage windows for the settings overview; never starts a model turn."""
    connected = bool(codex_runtime.status().get('enabled'))
    usage = codex_runtime.subscription_usage() if connected else evaluate(None)
    return JSONResponse({'connected': connected, **usage}, headers={'Cache-Control': 'no-store'})


@router.get('/matching-model')
def matching_model():
    """The matching model selected in Settings, and whether it can run now."""
    return JSONResponse(remote_matching.status(), headers={'Cache-Control': 'no-store'})


class MatchingModeSetting(StrictModel):
    mode: Literal['luna', 'local']


@router.put('/matching-model')
def set_matching_model(request: MatchingModeSetting):
    remote_matching.set_mode(request.mode)
    return JSONResponse(remote_matching.status(), headers={'Cache-Control': 'no-store'})


@router.put('/application-preparation/model')
def set_preparation_model(request: ModelSetting):
    codex_runtime.set_model(request.model)
    return JSONResponse(codex_runtime.status(), headers={'Cache-Control': 'no-store'})


class ConnectionAction(StrictModel):
    pass


@router.post('/application-preparation/login')
def preparation_login(request: ConnectionAction):
    return JSONResponse(codex_runtime.start_login(), headers={'Cache-Control': 'no-store'})


@router.post('/application-preparation/logout')
def preparation_logout(request: ConnectionAction):
    return JSONResponse(codex_runtime.logout(), headers={'Cache-Control': 'no-store'})
