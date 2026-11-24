"""Manage the ChatGPT connection used for application preparation."""
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from hiring_scraper.app import codex_runtime

router = APIRouter(prefix='/api/v1', tags=['application-preparation'])


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


@router.get('/application-preparation')
def preparation_status():
    return JSONResponse(codex_runtime.status(), headers={'Cache-Control': 'no-store'})


class ModelSetting(StrictModel):
    model: str | None = Field(default=None, min_length=1, max_length=100, pattern=r'^[A-Za-z0-9][A-Za-z0-9._-]*$')


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
