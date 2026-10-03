"""How much of its plan a workspace is using (sample data never counts), and its daily AI allowance.

AI use is counted per workspace and day in India time, so allowances reset at midnight IST. A unit
is charged when an action starts (a dashboard build, an assistant message, an AI suggestion) and
refunded when the failure was on our side: the AI server offline, busy, or the person gone before it
answered. Self-hosted installs have no allowances, so nothing is counted.
"""

import math
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from stardeck.db import DashboardRecord, Database, SourceRecord, UsageRecord
from stardeck.plans import Plan

IST = timezone(timedelta(hours=5, minutes=30))  # no daylight saving, so no tz database needed
KINDS = ("generation", "assistant", "labels")


def now_ist() -> datetime:
    return datetime.now(IST)


def next_midnight(now: datetime) -> datetime:
    return (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)


def seconds_to_midnight(now: datetime) -> int:
    return max(1, math.ceil((next_midnight(now) - now).total_seconds()))


def real_sources(db: Session) -> int:
    return db.query(func.count(SourceRecord.id)).filter(SourceRecord.sample.isnot(True)).scalar() or 0


def real_dashboards(db: Session) -> int:
    return db.query(func.count(DashboardRecord.id)).filter(DashboardRecord.sample.isnot(True)).scalar() or 0


def allowance(plan: Plan, kind: str) -> int | None:
    return {"generation": plan.ai_generations_per_day, "assistant": plan.ai_messages_per_day,
            "labels": plan.ai_label_batches_per_day}[kind]


class QuotaExceeded(HTTPException):
    """Today's allowance is used up (429, retry after midnight IST)."""

    def __init__(self, plan: Plan, kind: str, limit: int, retry_after: int):
        what = {"generation": f"{limit} AI dashboard build{'s' if limit != 1 else ''}",
                "assistant": f"{limit} assistant message{'s' if limit != 1 else ''}",
                "labels": "AI label shortening"}[kind]
        more = " Upgrade to Enterprise for more." if plan.id == "community" else ""
        super().__init__(429, f"You've used today's {what} on the {plan.name} plan. They reset at midnight "
                              f"(India time).{more}", headers={"Retry-After": str(retry_after)})


@dataclass(frozen=True)
class Charge:
    workspace_id: str
    day: str
    kind: str


class Quotas:
    def __init__(self, db: Database, workspaces):
        self._db = db
        self._workspaces = workspaces  # stardeck.workspaces.WorkspaceService
        self._lock = threading.Lock()  # one process: read-check-increment can't interleave

    def used(self, workspace_id: str, kind: str, now: datetime | None = None) -> int:
        day = (now or now_ist()).date().isoformat()
        with self._db.platform_session() as s:
            rec = s.get(UsageRecord, (workspace_id, day, kind))
            return rec.count if rec else 0

    def charge(self, workspace_id: str, kind: str) -> Charge | None:
        """Count one use, or raise QuotaExceeded. None when the plan has no allowance (nothing counted)."""
        plan = self._workspaces.plan(workspace_id)
        limit = allowance(plan, kind)
        if limit is None:
            return None
        now = now_ist()
        day = now.date().isoformat()
        with self._lock, self._db.platform_session() as s:
            rec = s.get(UsageRecord, (workspace_id, day, kind))
            if (rec.count if rec else 0) >= limit:
                raise QuotaExceeded(plan, kind, limit, seconds_to_midnight(now))
            if rec is None:
                s.add(UsageRecord(workspace_id=workspace_id, day=day, kind=kind, count=1))
            else:
                rec.count += 1
            s.commit()
        return Charge(workspace_id, day, kind)

    def refund(self, charge: Charge | None) -> None:
        if charge is None:
            return
        with self._lock, self._db.platform_session() as s:
            rec = s.get(UsageRecord, (charge.workspace_id, charge.day, charge.kind))
            if rec is not None and rec.count > 0:
                rec.count -= 1
                s.commit()

    def summary(self, workspace_id: str) -> dict:
        plan = self._workspaces.plan(workspace_id)
        now = now_ist()
        out = {kind: {"used": self.used(workspace_id, kind, now), "limit": allowance(plan, kind)}
               for kind in ("generation", "assistant")}
        return {**out, "resetsAt": next_midnight(now).isoformat()}


def summary(db: Session, plan: Plan) -> dict:
    return {"sources": {"used": real_sources(db), "limit": plan.sources},
            "dashboards": {"used": real_dashboards(db), "limit": plan.dashboards}}
