"""/api/config, /api/geocode (place names -> coordinates) and /api/labels/shorten (chart labels)."""

from fastapi import APIRouter
from pydantic import BaseModel, Field

from stardeck.deps import LLMDep, SessionDep, StateDep
from stardeck.labels import shorten

router = APIRouter(tags=["maps"])


class GeocodeRequest(BaseModel):
    places: list[str] = Field(max_length=500)


@router.get("/config")
def client_config(state: StateDep) -> dict:
    s = state.settings
    return {"mapTiles": s.map_tiles_url, "mapAttribution": s.map_attribution, "geocoding": state.geocoder.enabled}


@router.post("/geocode")
def geocode(req: GeocodeRequest, state: StateDep, db: SessionDep) -> dict:
    places, pending = state.geocoder.locate(db, req.places)
    return {"places": places, "pending": pending}


class ShortenRequest(BaseModel):
    labels: list[str] = Field(max_length=200)
    max_chars: int = Field(14, ge=6, le=40, alias="maxChars")

    model_config = {"populate_by_name": True}


@router.post("/labels/shorten")
def shorten_labels(req: ShortenRequest, db: SessionDep, llm: LLMDep) -> dict:
    """AI-shortened versions of long chart labels (cached). Full labels stay in tooltips."""
    mapping, used_ai = shorten(db, llm, req.labels, req.max_chars)
    return {"labels": mapping, "ai": used_ai}
