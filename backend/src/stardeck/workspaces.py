"""Workspaces: each organisation's private space, its plan, status and settings.

Reads are cached for a short while: the operator CLI changes plans and status from another process,
and those changes should reach the server within half a minute without a restart.
"""

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from stardeck.config import Settings
from stardeck.db import DEFAULT_WORKSPACE, Database, WorkspaceRecord
from stardeck.ids import new_id
from stardeck.plans import Plan, resolve_plan

PlanId = Literal["community", "enterprise", "self_hosted"]
PLANS: tuple[str, ...] = ("community", "enterprise", "self_hosted")
CACHE_S = 30.0


@dataclass(frozen=True)
class WorkspaceInfo:
    id: str
    name: str
    plan: str
    status: str
    settings: dict[str, Any] = field(default_factory=dict)
    limits_override: dict[str, Any] | None = None

    @property
    def active(self) -> bool:
        return self.status == "active"


class WorkspaceService:
    def __init__(self, db: Database, settings: Settings):
        self._db = db
        self._settings = settings
        self._cache: dict[str, tuple[float, WorkspaceInfo | None]] = {}
        self._lock = threading.Lock()
        self._write_locks: dict[str, threading.Lock] = {}

    def get(self, workspace_id: str | None) -> WorkspaceInfo | None:
        if not workspace_id:
            return None
        now = time.monotonic()
        with self._lock:
            hit = self._cache.get(workspace_id)
            if hit and hit[0] > now:
                return hit[1]
        with self._db.platform_session() as s:
            rec = s.get(WorkspaceRecord, workspace_id)
            info = None if rec is None else WorkspaceInfo(rec.id, rec.name, rec.plan, rec.status,
                                                          dict(rec.settings or {}), rec.limits_override)
        with self._lock:
            self._cache[workspace_id] = (now + CACHE_S, info)
        return info

    def invalidate(self, workspace_id: str | None = None) -> None:
        with self._lock:
            if workspace_id is None:
                self._cache.clear()
            else:
                self._cache.pop(workspace_id, None)

    def plan(self, workspace_id: str | None) -> Plan:
        """What the workspace may use (always everything when self-hosted)."""
        info = self.get(workspace_id) if self._settings.hosted else None
        return resolve_plan(info.plan if info else None, info.limits_override if info else None, self._settings.hosted)

    def lock(self, workspace_id: str) -> threading.Lock:
        """Held while checking an allowance and adding the thing it counts, so two requests at once
        can't both squeeze into the last slot."""
        with self._lock:
            return self._write_locks.setdefault(workspace_id, threading.Lock())

    # ----- settings -----

    def fiscal_year_start(self, workspace_id: str | None) -> int:
        info = self.get(workspace_id or DEFAULT_WORKSPACE)
        v = (info.settings if info else {}).get("fiscal_year_start", self._settings.fiscal_year_start)
        return v if isinstance(v, int) and 1 <= v <= 12 else 4

    def set_setting(self, workspace_id: str, key: str, value: Any) -> None:
        with self._db.platform_session() as s:
            rec = s.get(WorkspaceRecord, workspace_id)
            if rec is None:
                raise KeyError(workspace_id)
            rec.settings = {**(rec.settings or {}), key: value}  # reassign so the JSON change is saved
            s.commit()
        self.invalidate(workspace_id)

    # ----- operator -----

    def create(self, name: str, plan: str = "community", workspace_id: str | None = None) -> WorkspaceRecord:
        if plan not in PLANS:
            raise ValueError(f"Unknown plan {plan}; use one of {', '.join(PLANS)}.")
        with self._db.platform_session() as s:
            rec = WorkspaceRecord(id=workspace_id or new_id("ws"), name=name.strip()[:200] or "Workspace",
                                  plan=plan, status="active",
                                  settings={"fiscal_year_start": self._settings.fiscal_year_start})
            s.add(rec)
            s.commit()
        self.invalidate(rec.id)
        return rec

    def update(self, workspace_id: str, **changes: Any) -> WorkspaceRecord:
        if "plan" in changes and changes["plan"] not in PLANS:
            raise ValueError(f"Unknown plan {changes['plan']}; use one of {', '.join(PLANS)}.")
        with self._db.platform_session() as s:
            rec = s.get(WorkspaceRecord, workspace_id)
            if rec is None:
                raise KeyError(workspace_id)
            for k, v in changes.items():
                setattr(rec, k, v)
            s.commit()
        self.invalidate(workspace_id)
        return rec
