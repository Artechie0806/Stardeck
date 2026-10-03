from contextlib import asynccontextmanager

import anyio.to_thread
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from stardeck import __version__
from stardeck.api import (
    dashboards,
    health,
    maps,
    public,
    shares,
    sources,
    transforms,
    users,
    widgets,
)
from stardeck.auth import AuthMiddleware, SessionStore, make_router, origin_of
from stardeck.config import Settings, get_settings
from stardeck.db import DEFAULT_WORKSPACE
from stardeck.demo import retail
from stardeck.deps import AppState


def check_hosted(settings: Settings) -> None:
    """Hosted mode refuses to start half-configured."""
    if not settings.hosted:
        return
    problems = []
    if settings.password:
        problems.append("STARDECK_PASSWORD must not be set (hosted workspaces use accounts)")
    if not origin_of(settings.public_url):
        problems.append("STARDECK_PUBLIC_URL must be the app's address, e.g. https://app.example.com")
    if problems:
        raise RuntimeError("Hosted mode isn't configured: " + "; ".join(problems))


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    check_hosted(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Sync endpoints run in worker threads; some wait on the AI or a slow database.
        anyio.to_thread.current_default_thread_limiter().total_tokens = 80
        state = AppState.create(settings)
        app.state.sessions = SessionStore(state.db, settings.session_hours)
        if settings.hosted:
            retail.ensure(state.sources.demo_path)  # every workspace's sample data reads this one file
        elif settings.seed_demo:
            with state.db.new_session(DEFAULT_WORKSPACE) as db:
                state.seed_sample(db, fixed_ids=True)
        app.state.stardeck = state
        yield

    app = FastAPI(title="Stardeck", version=__version__, lifespan=lifespan)
    app.add_middleware(AuthMiddleware, settings=settings)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[origin_of(settings.public_url)] if settings.hosted else settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(make_router(settings), prefix="/api")
    app.include_router(health.router, prefix="/api")
    app.include_router(sources.router, prefix="/api")
    app.include_router(widgets.router, prefix="/api")
    app.include_router(dashboards.router, prefix="/api")
    app.include_router(maps.router, prefix="/api")
    app.include_router(transforms.router, prefix="/api")
    app.include_router(users.router, prefix="/api")
    app.include_router(shares.router, prefix="/api")
    app.include_router(public.router, prefix="/api")
    return app


app = create_app()
