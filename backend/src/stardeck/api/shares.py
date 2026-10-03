"""/api/dashboards/{id}/shares: public, read-only links to a dashboard (editors make and turn them off)."""

from fastapi import APIRouter, HTTPException
from pydantic import Field

from stardeck import shares
from stardeck.db import DashboardRecord
from stardeck.deps import EditorDep, SessionDep
from stardeck.sources import ApiModel

router = APIRouter(prefix="/dashboards/{dashboard_id}/shares", tags=["shares"])


class ShareCreate(ApiModel):
    # None: until it's turned off.
    expires_in_days: int | None = Field(None, ge=1, le=365)


def _dashboard(db, dashboard_id: str) -> DashboardRecord:
    rec = db.get(DashboardRecord, dashboard_id)
    if rec is None:
        raise HTTPException(404, f"Dashboard {dashboard_id} not found")
    return rec


@router.get("")
def list_links(dashboard_id: str, db: SessionDep, who: EditorDep) -> list[dict]:
    _dashboard(db, dashboard_id)
    return [shares.to_json(link) for link in shares.links_for(db, dashboard_id)]


@router.post("", status_code=201)
def create_link(dashboard_id: str, req: ShareCreate, db: SessionDep, who: EditorDep) -> dict:
    _dashboard(db, dashboard_id)
    try:
        return shares.to_json(shares.create(db, dashboard_id, who, req.expires_in_days))
    except shares.ShareError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.delete("/{token}", status_code=204)
def revoke_link(dashboard_id: str, token: str, db: SessionDep, who: EditorDep) -> None:
    _dashboard(db, dashboard_id)
    if not shares.revoke(db, dashboard_id, token):
        raise HTTPException(404, "That link doesn't exist or is already off.")
