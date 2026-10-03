"""Public share links: anyone with the link sees one dashboard, read-only, without signing in.

The link's token is the whole secret (see `ShareLinkRecord`). People opening it run as a "share"
principal of the dashboard's workspace with no row rules: they see the charts as the dashboard's
editors do. Data access rules for signed-in viewers don't apply to them, and the app says so
before a link is made. What they can do is narrow: see the charts (never the SQL), change the
dates, pick values in the dashboard's saved filters, and click a chart to filter the others.
"""

import secrets
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy.orm import Session

from stardeck.db import Database, ShareLinkRecord, WorkspaceRecord, utcnow
from stardeck.principal import Principal
from stardeck.sources import as_utc

MAX_PER_DASHBOARD = 10


class ShareError(ValueError):
    pass


@dataclass(frozen=True)
class OpenLink:
    token: str
    workspace_id: str
    dashboard_id: str

    @property
    def principal(self) -> Principal:
        return Principal(id=f"share:{self.token[:8]}", username="", name="Shared link", role="viewer",
                         workspace_id=self.workspace_id, kind="share")


def is_active(link: ShareLinkRecord) -> bool:
    expires = as_utc(link.expires_at)
    return link.revoked_at is None and (expires is None or expires > utcnow())


def to_json(link: ShareLinkRecord) -> dict:
    return {"token": link.token, "createdAt": as_utc(link.created_at).isoformat(),
            "expiresAt": as_utc(link.expires_at).isoformat() if link.expires_at else None,
            "views": link.views or 0,
            "lastViewedAt": as_utc(link.last_viewed_at).isoformat() if link.last_viewed_at else None,
            "active": is_active(link)}


def links_for(db: Session, dashboard_id: str) -> list[ShareLinkRecord]:
    """A dashboard's links that haven't been turned off, newest first (`db` is the workspace's session)."""
    return (db.query(ShareLinkRecord)
            .filter(ShareLinkRecord.dashboard_id == dashboard_id, ShareLinkRecord.revoked_at.is_(None))
            .order_by(ShareLinkRecord.created_at.desc()).all())


def create(db: Session, dashboard_id: str, who: Principal, expires_in_days: int | None = None) -> ShareLinkRecord:
    if sum(1 for link in links_for(db, dashboard_id) if is_active(link)) >= MAX_PER_DASHBOARD:
        raise ShareError(f"A dashboard can have {MAX_PER_DASHBOARD} links at most. Turn one off first.")
    link = ShareLinkRecord(token=secrets.token_urlsafe(24), dashboard_id=dashboard_id, created_by=who.id,
                           expires_at=utcnow() + timedelta(days=expires_in_days) if expires_in_days else None)
    db.add(link)
    db.commit()
    return link


def revoke(db: Session, dashboard_id: str, token: str) -> bool:
    link = db.get(ShareLinkRecord, token)
    if link is None or link.dashboard_id != dashboard_id or link.revoked_at is not None:
        return False
    link.revoked_at = utcnow()
    db.commit()
    return True


def open_link(database: Database, token: str, count_view: bool = False) -> OpenLink | None:
    """The link behind `token` if it works: not turned off, not expired, its workspace active."""
    if not token or len(token) > 64:
        return None
    with database.platform_session() as s:
        link = s.get(ShareLinkRecord, token)
        if link is None or not is_active(link):
            return None
        ws = s.get(WorkspaceRecord, link.workspace_id)
        if ws is None or ws.status != "active":
            return None
        if count_view:
            link.views = (link.views or 0) + 1
            link.last_viewed_at = utcnow()
            s.commit()
        return OpenLink(token=link.token, workspace_id=link.workspace_id, dashboard_id=link.dashboard_id)


def newest_link(database: Database, workspace_id: str) -> str | None:
    """The newest working link in a workspace (the public demo)."""
    with database.platform_session() as s:
        for link in (s.query(ShareLinkRecord).filter(ShareLinkRecord.workspace_id == workspace_id,
                                                     ShareLinkRecord.revoked_at.is_(None))
                     .order_by(ShareLinkRecord.created_at.desc())):
            if is_active(link):
                return link.token
    return None
