from fastapi import APIRouter

from stardeck import __version__
from stardeck.deps import StateDep
from stardeck.llm import LLMClient, LLMError

router = APIRouter(tags=["health"])


@router.get("/health")
def health(state: StateDep) -> dict:
    settings = state.settings
    llm: dict = {"base_url": settings.llm_base_url, "model": settings.llm_model}
    client = LLMClient(settings)
    try:
        available = client.list_models()
        llm["reachable"] = True
        llm["model_available"] = settings.llm_model in available
    except LLMError as exc:
        llm["reachable"] = False
        llm["error"] = str(exc)
    finally:
        client.close()
    return {"status": "ok", "version": __version__, "llm": llm}
