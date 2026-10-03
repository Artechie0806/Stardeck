"""/api/users: people, their roles and row-level security rules (admins only)."""

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import Field

from stardeck.db import SourceRecord, UserRecord
from stardeck.deps import AdminDep, SessionDep, StateDep, require, require_feature
from stardeck.invites import Invites
from stardeck.principal import Role
from stardeck.sources import ApiModel, as_utc
from stardeck.users import UserError, check_password, hash_password

router = APIRouter(prefix="/users", tags=["users"],
                   dependencies=[Depends(require("admin")), Depends(require_feature("people"))])


class RuleIn(ApiModel):
    """Viewers only see rows of `table` where `column` is one of `values` (and linked rows)."""

    source_id: str
    table: str
    column: str
    values: list[Any] = Field(default_factory=list, max_length=500)


class UserIn(ApiModel):
    username: str = Field(min_length=2, max_length=120)
    name: str = Field("", max_length=200)
    password: str
    role: Role = "viewer"
    rules: list[RuleIn] = Field(default_factory=list, max_length=20)


class UserPatch(ApiModel):
    name: str | None = Field(None, max_length=200)
    password: str | None = None  # reset
    role: Role | None = None
    rules: list[RuleIn] | None = Field(None, max_length=20)
    disabled: bool | None = None


class UserOut(ApiModel):
    id: str
    username: str
    name: str
    role: str
    rules: list[dict]
    disabled: bool
    created_at: datetime
    last_login_at: datetime | None


def _out(u: UserRecord) -> UserOut:
    return UserOut(id=u.id, username=u.username, name=u.name, role=u.role, rules=u.rules or [],
                   disabled=bool(u.disabled), created_at=as_utc(u.created_at), last_login_at=as_utc(u.last_login_at))


def _rules(state, db, rules: list[RuleIn]) -> list[dict]:
    out = []
    for r in rules:
        src = db.get(SourceRecord, r.source_id)
        if src is None:
            raise HTTPException(400, "A rule refers to a data source that doesn't exist.")
        table = next((t for t in state.sources.tables(src) if t.name.lower() == r.table.lower()), None)
        if table is None or table.prepared:
            raise HTTPException(400, f"Rules apply to the source's own tables; {r.table} isn't one.")
        if not any(c.name == r.column for c in table.columns):
            raise HTTPException(400, f"{table.name} has no column {r.column}.")
        out.append({"sourceId": src.id, "table": table.name, "column": r.column, "values": list(r.values)})
    return out


def _get(db, user_id: str) -> UserRecord:
    u = db.get(UserRecord, user_id)
    if u is None:
        raise HTTPException(404, "User not found")
    return u


@router.get("", response_model=list[UserOut])
def list_users(db: SessionDep):
    return [_out(u) for u in db.query(UserRecord).order_by(UserRecord.created_at).all()]


@router.post("", response_model=UserOut, status_code=201)
def create_user(req: UserIn, state: StateDep, db: SessionDep):
    rules = _rules(state, db, req.rules)
    try:
        u = state.users.create(db, req.username, req.name, req.password, req.role, rules)
    except UserError as exc:
        raise HTTPException(400, str(exc)) from exc
    return _out(u)


@router.patch("/{user_id}", response_model=UserOut)
def update_user(user_id: str, req: UserPatch, request: Request, state: StateDep, db: SessionDep, who: AdminDep):
    u = _get(db, user_id)
    losing_admin = u.role == "admin" and not u.disabled and (
        (req.role is not None and req.role != "admin") or req.disabled is True)
    if u.id == who.id and (req.disabled or (req.role and req.role != "admin")):
        raise HTTPException(400, "You can't remove your own admin access.")
    if losing_admin and state.users.admins(db) <= 1:
        raise HTTPException(400, "Stardeck needs at least one admin. Make someone else an admin first.")
    if req.name is not None:
        u.name = req.name.strip()
    if req.role is not None:
        u.role = req.role
    if req.rules is not None:
        u.rules = _rules(state, db, req.rules)
    if req.disabled is not None:
        u.disabled = req.disabled
    if req.password:
        try:
            check_password(req.password)
        except UserError as exc:
            raise HTTPException(400, str(exc)) from exc
        u.password_hash = hash_password(req.password)
    db.commit()
    if u.disabled or req.password:
        request.app.state.sessions.drop_subject(u.id)  # sign them out everywhere
    return _out(u)


@router.delete("/{user_id}", status_code=204)
def delete_user(user_id: str, request: Request, state: StateDep, db: SessionDep, who: AdminDep):
    u = _get(db, user_id)
    if u.id == who.id:
        raise HTTPException(400, "You can't remove yourself.")
    if u.role == "admin" and not u.disabled and state.users.admins(db) <= 1:
        raise HTTPException(400, "Stardeck needs at least one admin.")
    email = u.username
    db.delete(u)
    db.commit()
    state.users.changed()
    request.app.state.sessions.drop_subject(user_id)
    Invites(state.db).revoke_for(email)  # a pending reset link mustn't outlive the account
