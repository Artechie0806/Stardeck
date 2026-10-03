"""Single-use links: an invitation to a workspace, or a password reset.

Only a hash of each token is stored, so a copy of the database can't be used to sign in.
"""

import hashlib
import secrets
import time

from stardeck.db import Database, InviteRecord, utcnow

INVITE_HOURS = 72
RESET_HOURS = 24


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class Invites:
    def __init__(self, db: Database):
        self._db = db

    def create(self, workspace_id: str, email: str, role: str = "admin", kind: str = "invite",
               user_id: str | None = None) -> str:
        token = secrets.token_urlsafe(24)
        hours = RESET_HOURS if kind == "reset" else INVITE_HOURS
        with self._db.platform_session() as s:
            s.add(InviteRecord(token_hash=token_hash(token), kind=kind, workspace_id=workspace_id,
                               email=email.strip().lower(), role=role, user_id=user_id,
                               expires_at=time.time() + hours * 3600))
            s.commit()
        return token

    def lookup(self, token: str) -> InviteRecord | None:
        """The invitation if it's unused and hasn't expired."""
        if not token or len(token) > 100:
            return None
        with self._db.platform_session() as s:
            rec = s.get(InviteRecord, token_hash(token))
            if rec is None or rec.used_at is not None or rec.expires_at < time.time():
                return None
            return rec

    def use(self, token: str) -> None:
        with self._db.platform_session() as s:
            rec = s.get(InviteRecord, token_hash(token))
            if rec is not None:
                rec.used_at = utcnow()
                s.commit()

    def revoke_for(self, email: str) -> None:
        """Unused links for `email` stop working (e.g. when their account is removed)."""
        with self._db.platform_session() as s:
            for rec in s.query(InviteRecord).filter(InviteRecord.email == email.strip().lower(),
                                                    InviteRecord.used_at.is_(None)):
                rec.used_at = utcnow()
            s.commit()
