"""Shared app state and FastAPI dependencies (including who the request runs as)."""

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from stardeck.config import Settings
from stardeck.dashboards import DashboardService, ResultCache
from stardeck.db import Database, WorkspaceRecord
from stardeck.geo import Geocoder
from stardeck.llm import LLMBusy, LLMClient, LLMError, ai_message
from stardeck.llm.gate import Gate, GenerationSlots
from stardeck.llm.status import LLMStatus
from stardeck.plans import Plan, need
from stardeck.principal import Principal, Role
from stardeck.secrets import SecretBox
from stardeck.sources import EngineRegistry, SourceService
from stardeck.usage import Quotas
from stardeck.users import UserDirectory
from stardeck.workspaces import WorkspaceService


@dataclass
class AppState:
    settings: Settings
    db: Database
    workspaces: WorkspaceService
    sources: SourceService
    dashboards: DashboardService
    geocoder: Geocoder
    users: UserDirectory
    llm_status: LLMStatus
    quotas: Quotas
    gate: Gate = field(default_factory=lambda: Gate(4))
    generations: GenerationSlots = field(default_factory=GenerationSlots)

    @classmethod
    def create(cls, settings: Settings) -> "AppState":
        db = Database(settings)
        workspaces = WorkspaceService(db, settings)
        sources = SourceService(settings, SecretBox(settings), EngineRegistry(), workspaces)
        return cls(settings, db, workspaces, sources, DashboardService(sources, ResultCache()),
                   Geocoder(settings), UserDirectory(db, settings.hosted, settings.operator_emails, workspaces),
                   LLMStatus(settings), Quotas(db, workspaces), Gate(settings.llm_parallel))

    def llm(self, background: bool = False, cancel=None, wait_s: float | None = None) -> LLMClient:
        """A client behind the shared AI queue (see llm/gate.py)."""
        return LLMClient(self.settings, gate=self.gate, status=self.llm_status, background=background, cancel=cancel,
                         wait_s=wait_s)

    def seed_sample(self, db: Session, fixed_ids: bool = False) -> None:
        """Add the sample retail data and its dashboard to `db`'s workspace (once)."""
        src = self.sources.seed_sample(db, fixed_ids=fixed_ids)
        if src is not None:
            self.dashboards.seed_sample(db, src.id, fixed_ids=fixed_ids)

    def create_workspace(self, name: str, plan: str = "community", sample: bool = True,
                         workspace_id: str | None = None) -> WorkspaceRecord:
        rec = self.workspaces.create(name, plan, workspace_id)
        if sample:
            with self.db.new_session(rec.id) as db:
                self.seed_sample(db)
        return rec


def get_state(request: Request) -> AppState:
    return request.app.state.stardeck


def current_principal(request: Request) -> Principal:
    """Set by AuthMiddleware for every /api request."""
    who = getattr(request.state, "principal", None)
    if who is None:
        raise HTTPException(401, "Sign in required")
    return who


def get_session(state: Annotated[AppState, Depends(get_state)],
                who: Annotated[Principal, Depends(current_principal)]) -> Iterator[Session]:
    """The request's database session: it only sees the signed-in person's workspace."""
    if not who.workspace_id:
        raise HTTPException(401, "Sign in required")
    yield from state.db.session(who.workspace_id)


def get_llm(state: Annotated[AppState, Depends(get_state)]) -> Iterator[LLMClient]:
    """For things someone is waiting on (chat): may use every AI slot."""
    client = state.llm()
    try:
        yield client
    finally:
        client.close()


def get_background_llm(state: Annotated[AppState, Depends(get_state)]) -> Iterator[LLMClient]:
    """For AI suggestions and other extras: never takes the slot kept for chat."""
    client = state.llm(background=True)
    try:
        yield client
    finally:
        client.close()


def get_label_llm(state: Annotated[AppState, Depends(get_state)]) -> Iterator[LLMClient]:
    """Chart labels are a nicety: wait a moment at most for the AI, else shorten them without it."""
    client = state.llm(background=True, wait_s=3.0)
    try:
        yield client
    finally:
        client.close()


def ai_error(exc: LLMError) -> HTTPException:
    """The AI couldn't help: a message for people, and when to try again."""
    return HTTPException(503, ai_message(exc), headers={"Retry-After": "30"} if isinstance(exc, LLMBusy) else None)


def require(role: Role) -> Callable[[Principal], Principal]:
    def check(who: Annotated[Principal, Depends(current_principal)]) -> Principal:
        if not who.can(role):
            raise HTTPException(403, f"This needs {role} access. Ask an admin if you need it.")
        return who
    return check


def require_feature(feature: str) -> Callable[..., Plan]:
    """A dependency: the workspace's plan must include `feature` (402 otherwise)."""
    def check(state: Annotated[AppState, Depends(get_state)],
              who: Annotated[Principal, Depends(current_principal)]) -> Plan:
        plan = state.workspaces.plan(who.workspace_id)
        need(plan, feature)
        return plan
    return check


def get_plan(state: Annotated[AppState, Depends(get_state)],
             who: Annotated[Principal, Depends(current_principal)]) -> Plan:
    return state.workspaces.plan(who.workspace_id)


StateDep = Annotated[AppState, Depends(get_state)]
PlanDep = Annotated[Plan, Depends(get_plan)]
SessionDep = Annotated[Session, Depends(get_session)]
LLMDep = Annotated[LLMClient, Depends(get_llm)]
BackgroundLLMDep = Annotated[LLMClient, Depends(get_background_llm)]
LabelLLMDep = Annotated[LLMClient, Depends(get_label_llm)]
WhoDep = Annotated[Principal, Depends(current_principal)]
EditorDep = Annotated[Principal, Depends(require("editor"))]
AdminDep = Annotated[Principal, Depends(require("admin"))]
