"""People who can sign in: storage, password hashing, and turning a session into a Principal.

Sign-in looks people up across every workspace (usernames are unique everywhere); everything else
works inside the caller's workspace session, so admins only ever see and change their own people.
"""

import base64
import hashlib
import hmac
import re
import secrets
import threading
from typing import Any

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from stardeck.db import Database, UserRecord, utcnow
from stardeck.ids import new_id
from stardeck.principal import Principal, Role

USERNAME = re.compile(r"^[a-z0-9][a-z0-9._@+-]{1,119}$")
EMAIL = re.compile(r"^[a-z0-9._%+-]+@[a-z0-9-]+(\.[a-z0-9-]+)+$")
MIN_PASSWORD = 8
_SCRYPT = {"n": 2**14, "r": 8, "p": 1}


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT, dklen=32)
    return "scrypt${n}${r}${p}${salt}${hash}".format(
        **_SCRYPT, salt=base64.b64encode(salt).decode(), hash=base64.b64encode(digest).decode())


def verify_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, digest = stored.split("$")
        got = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p), dklen=32)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got, base64.b64decode(digest))


class UserError(ValueError):
    """A user-facing problem with a user change."""


def check_password(password: str) -> None:
    if len(password) < MIN_PASSWORD:
        raise UserError(f"Use a password of at least {MIN_PASSWORD} characters.")


def normalize(username: str) -> str:
    return username.strip().lower()


class UserDirectory:
    """Looks up users; remembers whether any exist (that switches sign-in on when self-hosted)."""

    def __init__(self, db: Database, hosted: bool = False, operators: list[str] | None = None, workspaces=None):
        self._db = db
        self.hosted = hosted
        self._operators = {normalize(e) for e in operators or []}
        self._workspaces = workspaces  # stardeck.workspaces.WorkspaceService: suspended workspaces can't sign in
        self._lock = threading.Lock()
        self._any: bool | None = None

    def to_principal(self, rec: UserRecord) -> Principal:
        return Principal(id=rec.id, username=rec.username, name=rec.name or rec.username, role=rec.role,  # type: ignore[arg-type]
                         rules=tuple(rec.rules or ()), workspace_id=rec.workspace_id,
                         operator=rec.username in self._operators)

    def _usable(self, rec: UserRecord | None) -> bool:
        if rec is None or rec.disabled or not rec.workspace_id:
            return False
        if self._workspaces is None:
            return True
        info = self._workspaces.get(rec.workspace_id)
        return info is not None and info.active

    def any_users(self) -> bool:
        with self._lock:
            if self._any is None:
                with self._db.platform_session() as s:
                    self._any = (s.query(func.count(UserRecord.id)).scalar() or 0) > 0
            return self._any

    def changed(self) -> None:
        with self._lock:
            self._any = None

    def principal(self, user_id: str) -> Principal | None:
        with self._db.platform_session() as s:
            rec = s.get(UserRecord, user_id)
            return self.to_principal(rec) if self._usable(rec) else None

    def find(self, username: str) -> UserRecord | None:
        with self._db.platform_session() as s:
            return s.query(UserRecord).filter(UserRecord.username == normalize(username)).one_or_none()

    def authenticate(self, username: str, password: str) -> Principal | None:
        with self._db.platform_session() as s:
            rec = s.query(UserRecord).filter(UserRecord.username == normalize(username)).one_or_none()
            if not self._usable(rec) or not verify_password(password, rec.password_hash):
                return None
            rec.last_login_at = utcnow()
            s.commit()
            return self.to_principal(rec)

    def set_password(self, user_id: str, password: str) -> None:
        check_password(password)
        with self._db.platform_session() as s:
            rec = s.get(UserRecord, user_id)
            if rec is None:
                raise UserError("That account no longer exists.")
            rec.password_hash = hash_password(password)
            s.commit()

    # ----- admin (inside a workspace) -----

    def check_username(self, username: str) -> str:
        username = normalize(username)
        if self.hosted:
            if not EMAIL.match(username) or len(username) > 120:
                raise UserError("Use the person's email address.")
        elif not USERNAME.match(username):
            raise UserError("Use a username of 2+ characters: letters, digits, . _ @ + or -.")
        return username

    def create(self, db: Session, username: str, name: str, password: str, role: Role,
               rules: list[dict[str, Any]] | None = None) -> UserRecord:
        """Add a person to `db`'s workspace."""
        username = self.check_username(username)
        if self.find(username) is not None:  # usernames are unique across all workspaces
            raise UserError(f"There's already an account for {username}.")
        check_password(password)
        rec = UserRecord(id=new_id("u"), username=username, name=name.strip(), password_hash=hash_password(password),
                         role=role, rules=rules or [], workspace_id=db.info.get("workspace_id"))
        db.add(rec)
        try:
            db.commit()
        except IntegrityError as exc:  # someone took it in the meantime
            db.rollback()
            raise UserError(f"There's already an account for {username}.") from exc
        self.changed()
        return rec

    def admins(self, db: Session) -> int:
        return db.query(func.count(UserRecord.id)).filter(UserRecord.role == "admin",
                                                          UserRecord.disabled.is_(False)).scalar() or 0
