"""/api/config, /api/geocode (place names -> coordinates) and /api/labels/shorten (chart labels)."""

from fastapi import APIRouter
from pydantic import BaseModel, Field

from stardeck.deps import AdminDep, LabelLLMDep, PlanDep, SessionDep, StateDep, WhoDep
from stardeck.labels import shorten
from stardeck.principal import Principal
from stardeck.usage import QuotaExceeded, summary

router = APIRouter(tags=["maps"])


class GeocodeRequest(BaseModel):
    places: list[str] = Field(max_length=500)


@router.get("/config")
def client_config(state: StateDep, who: WhoDep) -> dict:
    return _config(state, who)


def _config(state, who: Principal) -> dict:
    s = state.settings
    upgrade = s.upgrade_url or (f"{s.site_url.rstrip('/')}/#pricing" if s.site_url else None)
    return {"mapTiles": s.map_tiles_url, "mapAttribution": s.map_attribution, "geocoding": state.geocoder.enabled,
            "fiscalYearStart": state.workspaces.fiscal_year_start(who.workspace_id), "hosted": s.hosted,
            "plan": state.workspaces.plan(who.workspace_id).to_json(), "upgradeUrl": upgrade,
            # Where public links live (hosted: a separate host that never sees sign-in cookies).
            "shareUrl": (s.share_url or s.public_url or "").rstrip("/") or None}


@router.get("/usage")
def usage(state: StateDep, db: SessionDep, plan: PlanDep, who: WhoDep) -> dict:
    """How much of the plan the workspace uses (the sample data doesn't count) and today's AI use."""
    return {**summary(db, plan), "ai": state.quotas.summary(who.workspace_id)}


class SettingsUpdate(BaseModel):
    fiscal_year_start: int | None = Field(None, ge=1, le=12, alias="fiscalYearStart")

    model_config = {"populate_by_name": True}


@router.put("/settings")
def update_settings(req: SettingsUpdate, state: StateDep, who: AdminDep) -> dict:
    """The workspace's settings (admins)."""
    if req.fiscal_year_start is not None:
        state.workspaces.set_setting(who.workspace_id, "fiscal_year_start", req.fiscal_year_start)
    return _config(state, who)


@router.post("/geocode")
def geocode(req: GeocodeRequest, state: StateDep, db: SessionDep) -> dict:
    places, pending = state.geocoder.locate(db, req.places)
    return {"places": places, "pending": pending}


class ShortenRequest(BaseModel):
    labels: list[str] = Field(max_length=200)
    max_chars: int = Field(14, ge=6, le=40, alias="maxChars")

    model_config = {"populate_by_name": True}


@router.post("/labels/shorten")
def shorten_labels(req: ShortenRequest, state: StateDep, db: SessionDep, llm: LabelLLMDep, who: WhoDep) -> dict:
    """AI-shortened versions of long chart labels (cached). Full labels stay in tooltips. Uses the AI
    only while the workspace's label allowance lasts; otherwise labels are shortened without it."""
    charge = None

    def allow() -> bool:
        nonlocal charge
        try:
            charge = state.quotas.charge(who.workspace_id, "labels")
        except QuotaExceeded:
            return False
        return True

    mapping, used_ai = shorten(db, llm, req.labels, req.max_chars, allow_ai=allow)
    if not used_ai:
        state.quotas.refund(charge)
    return {"labels": mapping, "ai": used_ai}
