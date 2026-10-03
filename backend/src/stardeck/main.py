from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from stardeck import __version__
from stardeck.api import dashboards, health, maps, sources, transforms, widgets
from stardeck.auth import AuthMiddleware, SessionStore, make_router
from stardeck.config import Settings, get_settings
from stardeck.db import SourceRecord
from stardeck.deps import AppState
from stardeck.sources import DEMO_ID


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        state = AppState.create(settings)
        if settings.seed_demo:
            with state.db.new_session() as db:
                state.sources.seed_demo(db)
                if db.get(SourceRecord, DEMO_ID):
                    state.dashboards.seed_demo(db, DEMO_ID)
        app.state.stardeck = state
        yield

    app = FastAPI(title="Stardeck", version=__version__, lifespan=lifespan)
    sessions = SessionStore(settings.session_hours)
    app.add_middleware(AuthMiddleware, settings=settings, sessions=sessions)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(make_router(settings, sessions), prefix="/api")
    app.include_router(health.router, prefix="/api")
    app.include_router(sources.router, prefix="/api")
    app.include_router(widgets.router, prefix="/api")
    app.include_router(dashboards.router, prefix="/api")
    app.include_router(maps.router, prefix="/api")
    app.include_router(transforms.router, prefix="/api")
    return app


app = create_app()
