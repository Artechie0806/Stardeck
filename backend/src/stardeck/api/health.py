from fastapi import APIRouter

from stardeck import __version__
from stardeck.deps import StateDep, WhoDep

router = APIRouter(tags=["health"])


@router.get("/health")
def health(state: StateDep, who: WhoDep) -> dict:
    """Whether the AI is available. The model and server address are only for the people who run
    it: admins when self-hosted, the operator when hosted."""
    status = state.llm_status.get()
    llm = {k: v for k, v in status.items() if k in ("reachable", "model_available")}
    if who.operator or (not state.settings.hosted and who.can("admin")):
        llm |= {"base_url": state.settings.llm_base_url, "model": state.settings.llm_model}
        if "error" in status:
            llm["error"] = status["error"]
    return {"status": "ok", "version": __version__, "llm": llm}


@router.get("/ping")
def ping() -> dict:
    """For uptime monitors: no sign-in, no database, no AI."""
    return {"ok": True}
