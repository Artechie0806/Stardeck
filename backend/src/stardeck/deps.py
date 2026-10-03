"""Shared app state and FastAPI dependencies."""

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from stardeck.config import Settings
from stardeck.dashboards import DashboardService, ResultCache
from stardeck.db import Database
from stardeck.geo import Geocoder
from stardeck.llm import LLMClient
from stardeck.secrets import SecretBox
from stardeck.sources import EngineRegistry, SourceService


@dataclass
class AppState:
    settings: Settings
    db: Database
    sources: SourceService
    dashboards: DashboardService
    geocoder: Geocoder

    @classmethod
    def create(cls, settings: Settings) -> "AppState":
        db = Database(settings)
        sources = SourceService(settings, SecretBox(settings), EngineRegistry())
        return cls(settings, db, sources, DashboardService(sources, ResultCache()), Geocoder(settings))


def get_state(request: Request) -> AppState:
    return request.app.state.stardeck


def get_session(state: Annotated[AppState, Depends(get_state)]) -> Iterator[Session]:
    yield from state.db.session()


def get_llm(state: Annotated[AppState, Depends(get_state)]) -> Iterator[LLMClient]:
    client = LLMClient(state.settings)
    try:
        yield client
    finally:
        client.close()


StateDep = Annotated[AppState, Depends(get_state)]
SessionDep = Annotated[Session, Depends(get_session)]
LLMDep = Annotated[LLMClient, Depends(get_llm)]
