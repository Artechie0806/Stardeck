"""What each plan includes, in one place: the server enforces it and /api/config tells the app.

Self-hosted installs are always unlimited. Hosted workspaces are Community (free) or Enterprise;
the operator can make exceptions per workspace (`WorkspaceRecord.limits_override`, e.g.
{"sources": 3, "features": ["measures"]}).

Missing features and full allowances answer 402 with a sentence the app shows as is. Downgrading
never breaks anything already built: existing measures and prepared tables keep working in charts;
they just can't be changed until the workspace upgrades.
"""

from dataclasses import asdict, dataclass, replace
from typing import Any

from fastapi import HTTPException

FEATURES: tuple[str, ...] = ("people", "measures", "relationships", "prepared_tables", "multi_source")

LOCKED = {
    "people": "Adding people, roles and data access rules is part of Stardeck Enterprise.",
    "measures": "Measures are part of Stardeck Enterprise.",
    "relationships": "Editing how tables connect is part of Stardeck Enterprise.",
    "prepared_tables": "Data preparation (prepared tables) is part of Stardeck Enterprise.",
    "multi_source": "Using several data sources on one dashboard is part of Stardeck Enterprise.",
}


@dataclass(frozen=True)
class Plan:
    id: str
    name: str
    features: frozenset[str]
    sources: int | None = None  # None: no limit (sample data never counts)
    dashboards: int | None = None
    sources_per_dashboard: int = 10
    upload_mb: int | None = None  # None: the server's STARDECK_MAX_UPLOAD_MB
    ai_generations_per_day: int | None = None
    ai_messages_per_day: int | None = None
    ai_label_batches_per_day: int | None = None  # AI-shortened chart labels (quietly falls back when used up)
    badge: bool = False  # "Made with Stardeck" on public links

    def has(self, feature: str) -> bool:
        return feature in self.features

    def to_json(self) -> dict[str, Any]:
        d = asdict(self)
        return {"id": self.id, "name": self.name, "features": sorted(self.features), "badge": self.badge,
                "limits": {"sources": d["sources"], "dashboards": d["dashboards"],
                           "sourcesPerDashboard": d["sources_per_dashboard"], "uploadMb": d["upload_mb"],
                           "aiGenerationsPerDay": d["ai_generations_per_day"], "aiMessagesPerDay": d["ai_messages_per_day"]}}


PLANS: dict[str, Plan] = {
    "community": Plan("community", "Community", frozenset(), sources=1, dashboards=2, sources_per_dashboard=1,
                      upload_mb=25, ai_generations_per_day=3, ai_messages_per_day=5, ai_label_batches_per_day=20,
                      badge=True),
    "enterprise": Plan("enterprise", "Enterprise", frozenset(FEATURES), upload_mb=100,
                       ai_generations_per_day=30, ai_messages_per_day=60, ai_label_batches_per_day=200),
    "self_hosted": Plan("self_hosted", "Self-hosted", frozenset(FEATURES)),
}
_OVERRIDABLE = {"sources", "dashboards", "sources_per_dashboard", "upload_mb", "ai_generations_per_day",
                "ai_messages_per_day", "ai_label_batches_per_day", "badge"}


def resolve_plan(plan_id: str | None, override: dict[str, Any] | None, hosted: bool) -> Plan:
    if not hosted:
        return PLANS["self_hosted"]
    plan = PLANS.get(plan_id or "community", PLANS["community"])
    if not override:
        return plan
    extra = {f for f in override.get("features") or [] if f in FEATURES}
    return replace(plan, features=plan.features | extra, **{k: v for k, v in override.items() if k in _OVERRIDABLE})


class PlanLimit(HTTPException):
    """Not included in the workspace's plan (402); the message is shown to people as is."""

    def __init__(self, message: str):
        super().__init__(402, message)


def need(plan: Plan, feature: str) -> None:
    if not plan.has(feature):
        raise PlanLimit(LOCKED[feature])


_NOUNS = {"sources": ("data source", "data sources", "Remove"), "dashboards": ("dashboard", "dashboards", "Delete")}


def check_room(plan: Plan, what: str, used: int) -> None:
    """Room for one more of `what` ("sources" or "dashboards")?"""
    limit = getattr(plan, what)
    if limit is not None and used >= limit:
        one, many, verb = _NOUNS[what]
        raise PlanLimit(f"Your {plan.name} plan includes {limit} {one if limit == 1 else many}, and the sample "
                        f"doesn't count. {verb} one, or upgrade to Stardeck Enterprise for more.")


def check_sources_per_dashboard(plan: Plan, count: int) -> None:
    if count > plan.sources_per_dashboard:
        raise PlanLimit(LOCKED["multi_source"] if plan.sources_per_dashboard == 1
                        else f"A dashboard can use up to {plan.sources_per_dashboard} data sources.")
