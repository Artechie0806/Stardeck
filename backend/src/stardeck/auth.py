"""Sign-in: who each request runs as.

Self-hosted, three modes, picked automatically:
  open      - no users and no STARDECK_PASSWORD: a single-person local install, everyone is admin
  password  - STARDECK_PASSWORD set and no users yet: one shared password, signed-in people are admin
  users     - at least one user exists: each person signs in with a username and password and gets
              their role (viewer / editor / admin) and, for viewers, their row-level security rules
Hosted (STARDECK_HOSTED): always "users". People sign in with their email; accounts are created
from invitation links only, and each belongs to one workspace.

Sessions are stored (hashed) in the metadata DB, so a restart doesn't sign anyone out.
"""

import hashlib
import hmac
import secrets
import time
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from stardeck.config import Settings
from stardeck.db import DEFAULT_WORKSPACE, Database, SessionRecord, utcnow
from stardeck.invites import Invites
from stardeck.principal import OWNER, Principal
from stardeck.ratelimit import RateLimiter, client_key, too_many
from stardeck.users import UserDirectory, UserError, normalize, verify_password

COOKIE = "stardeck_session"
OPEN_PATHS = ("/api/auth/", "/api/ping")
# Shared links, the demo and the waitlist: for anyone, never as the signed-in person (no cookie used).
PUBLIC_PATHS = ("/api/public/",)
Mode = Literal["open", "password", "users"]
LEGACY = "legacy-password"  # session subject for the shared-password mode
SEEN_EVERY_S = 600  # how often a session's "last seen" is written
UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}
MAX_BODY = 4 * 1024 * 1024  # anything but uploads
UPLOAD_PATHS = ("/api/sources/upload",)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class SessionStore:
    """Signed-in browsers. Only a hash of each cookie is stored."""

    def __init__(self, db: Database, hours: int):
        self._db = db
        self.ttl = hours * 3600

    def create(self, subject: str, user_agent: str = "") -> str:
        token = secrets.token_urlsafe(32)
        now = time.time()
        with self._db.platform_session() as s:
            s.query(SessionRecord).filter(SessionRecord.expires_at < now).delete()
            s.add(SessionRecord(token_hash=_hash(token), subject=subject, expires_at=now + self.ttl,
                                last_seen_at=now, user_agent=user_agent[:300]))
            s.commit()
        return token

    def subject(self, token: str | None) -> str | None:
        if not token or len(token) > 100:
            return None
        now = time.time()
        with self._db.platform_session() as s:
            rec = s.get(SessionRecord, _hash(token))
            if rec is None or rec.expires_at < now:
                return None
            if now - rec.last_seen_at > SEEN_EVERY_S:
                rec.last_seen_at = now
                s.commit()
            return rec.subject

    def drop(self, token: str | None) -> None:
        if not token:
            return
        with self._db.platform_session() as s:
            s.query(SessionRecord).filter(SessionRecord.token_hash == _hash(token)).delete()
            s.commit()

    def drop_subject(self, subject: str, keep: str | None = None) -> None:
        """Sign `subject` out everywhere (except the browser holding `keep`)."""
        with self._db.platform_session() as s:
            q = s.query(SessionRecord).filter(SessionRecord.subject == subject)
            if keep:
                q = q.filter(SessionRecord.token_hash != _hash(keep))
            q.delete()
            s.commit()


def mode(settings: Settings, users: UserDirectory) -> Mode:
    if settings.hosted:
        return "users"
    return "users" if users.any_users() else "password" if settings.password else "open"


def resolve(request: Request, settings: Settings, users: UserDirectory, sessions: SessionStore) -> Principal | None:
    """Who this request runs as, or None if they must sign in first."""
    m = mode(settings, users)
    if m == "open":
        return OWNER
    subject = sessions.subject(request.cookies.get(COOKIE))
    if subject is None:
        return None
    if m == "password":
        return OWNER if subject == LEGACY else None
    return users.principal(subject)


def _users(request: Request) -> UserDirectory:
    return request.app.state.stardeck.users  # created at startup, after the middleware


def _sessions(request: Request) -> SessionStore:
    return request.app.state.sessions


def origin_of(url: str | None) -> str | None:
    if not url:
        return None
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}" if parts.scheme and parts.netloc else None


class AuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, settings: Settings):
        super().__init__(app)
        self.settings = settings
        self.app_origin = origin_of(settings.public_url)

    def _refuse(self, request: Request) -> JSONResponse | None:
        """Requests turned away before anything else runs."""
        path = request.url.path
        size = request.headers.get("content-length")
        limit = (self.settings.max_upload_mb + 1) * 1024 * 1024 if path.startswith(UPLOAD_PATHS) else MAX_BODY
        if size and size.isdigit() and int(size) > limit:
            return JSONResponse({"detail": "That's too large to upload."}, status_code=413)
        if self.settings.hosted and request.method in UNSAFE and not path.startswith(PUBLIC_PATHS):
            # Browsers send Origin on cross-site requests: only the app itself may change things.
            origin = request.headers.get("origin")
            if origin and origin != self.app_origin:
                return JSONResponse({"detail": "Cross-site request refused."}, status_code=403)
        return None

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path.startswith("/api/"):
            if (refused := self._refuse(request)) is not None:
                return refused
            if path.startswith(PUBLIC_PATHS):
                request.state.principal = None
                return await call_next(request)
            who = await run_in_threadpool(resolve, request, self.settings, _users(request), _sessions(request))
            request.state.principal = who
            if who is None and not path.startswith(OPEN_PATHS):
                return JSONResponse({"detail": "Sign in required"}, status_code=401)
        return await call_next(request)


class LoginRequest(BaseModel):
    username: str | None = Field(None, max_length=200)
    password: str = Field(max_length=500)


class SetupRequest(BaseModel):
    username: str = Field(min_length=2, max_length=120)
    name: str = Field("", max_length=200)
    password: str = Field(max_length=500)


class InviteAccept(BaseModel):
    name: str = Field("", max_length=200)
    password: str = Field(max_length=500)


class PasswordChange(BaseModel):
    current: str = Field(max_length=500)
    new: str = Field(max_length=500)


def _user_json(p: Principal | None) -> dict | None:
    return None if p is None else {"id": p.id, "username": p.username, "name": p.name, "role": p.role}


# Sign-in attempts: per address (any outcome) and per account (failures only).
LOGIN_PER_IP = (20, 15 * 60)
FAILURES_PER_ACCOUNT = (8, 15 * 60)
INVITES_PER_IP = (10, 60 * 60)


def make_router(settings: Settings) -> APIRouter:
    router = APIRouter(prefix="/auth", tags=["auth"])
    limits = RateLimiter()

    def sign_in(subject: str, request: Request, response: Response) -> str:
        token = _sessions(request).create(subject, request.headers.get("user-agent", ""))
        response.set_cookie(COOKIE, token, max_age=settings.session_hours * 3600, httponly=True, samesite="lax",
                            secure=settings.hosted or request.url.scheme == "https")
        return token

    def workspace_json(request: Request, who: Principal | None) -> dict | None:
        info = request.app.state.stardeck.workspaces.get(who.workspace_id) if who else None
        return None if info is None else {"id": info.id, "name": info.name, "plan": info.plan}

    @router.get("/status")
    def status(request: Request) -> dict:
        users = _users(request)
        m = mode(settings, users)
        who = resolve(request, settings, users, _sessions(request))
        return {"mode": m, "required": m != "open", "authenticated": who is not None, "user": _user_json(who),
                "hosted": settings.hosted, "workspace": workspace_json(request, who) if settings.hosted else None}

    @router.post("/login")
    def login(req: LoginRequest, request: Request, response: Response) -> dict:
        users = _users(request)
        m = mode(settings, users)
        if m == "open":
            return {"authenticated": True, "user": _user_json(OWNER)}
        if wait := limits.hit(f"ip:{client_key(request)}", *LOGIN_PER_IP):
            raise too_many(wait, "sign-in attempts")
        if m == "password":
            if not hmac.compare_digest(req.password.encode(), (settings.password or "").encode()):
                raise HTTPException(401, "Wrong password")
            sign_in(LEGACY, request, response)
            return {"authenticated": True, "user": _user_json(OWNER)}
        account = f"user:{normalize(req.username or '')}"
        if wait := limits.retry_after(account, *FAILURES_PER_ACCOUNT):
            raise too_many(wait, "failed sign-ins for this account")
        who = users.authenticate(req.username or "", req.password)
        if who is None:
            limits.hit(account, *FAILURES_PER_ACCOUNT)
            raise HTTPException(401, "Wrong email or password" if settings.hosted else "Wrong username or password")
        limits.reset(account)
        sign_in(who.id, request, response)
        return {"authenticated": True, "user": _user_json(who)}

    @router.post("/setup")
    def setup(req: SetupRequest, request: Request, response: Response) -> dict:
        """Turn on sign-in by creating the first admin. Only while no users exist, and only for
        someone who already has admin access (open mode, or signed in with the shared password).
        Hosted workspaces are created by the operator instead."""
        if settings.hosted:
            raise HTTPException(404, "Not found")
        users = _users(request)
        if users.any_users():
            raise HTTPException(409, "Sign-in is already set up.")
        who = resolve(request, settings, users, _sessions(request))
        if who is None or not who.can("admin"):
            raise HTTPException(401, "Sign in first.")
        with request.app.state.stardeck.db.new_session(DEFAULT_WORKSPACE) as db:
            try:
                rec = users.create(db, req.username, req.name, req.password, "admin")
            except UserError as exc:
                raise HTTPException(400, str(exc)) from exc
            rec.last_login_at = utcnow()  # signed in from here on
            db.commit()
            user_id = rec.id
        sign_in(user_id, request, response)
        return {"authenticated": True, "user": _user_json(users.principal(user_id))}

    @router.post("/logout")
    def logout(request: Request, response: Response) -> dict:
        _sessions(request).drop(request.cookies.get(COOKIE))
        response.delete_cookie(COOKIE)
        return {"authenticated": False}

    # ----- invitations and password resets -----

    def _invite(request: Request, token: str):
        if wait := limits.hit(f"invite:{client_key(request)}", *INVITES_PER_IP):
            raise too_many(wait, "attempts")
        rec = Invites(request.app.state.stardeck.db).lookup(token)
        if rec is None:
            raise HTTPException(404, "This link has expired or was already used. Ask for a new one.")
        return rec

    @router.get("/invite/{token}")
    def invite_info(token: str, request: Request) -> dict:
        rec = _invite(request, token)
        info = request.app.state.stardeck.workspaces.get(rec.workspace_id)
        return {"kind": rec.kind, "email": rec.email, "workspace": info.name if info else ""}

    @router.post("/invite/{token}")
    def accept_invite(token: str, req: InviteAccept, request: Request, response: Response) -> dict:
        """Join a workspace (set name and password), or set a new password; then signed in."""
        rec = _invite(request, token)
        state = request.app.state.stardeck
        users = _users(request)
        try:
            if rec.kind == "reset":
                users.set_password(rec.user_id or "", req.password)
                user_id = rec.user_id or ""
                _sessions(request).drop_subject(user_id)  # whoever had the old password is out
            else:
                with state.db.new_session(rec.workspace_id) as db:
                    user = users.create(db, rec.email, req.name or rec.email.split("@")[0], req.password,
                                        rec.role)  # type: ignore[arg-type]
                    user.last_login_at = utcnow()
                    db.commit()
                    user_id = user.id
        except UserError as exc:
            raise HTTPException(400, str(exc)) from exc
        Invites(state.db).use(token)
        who = users.principal(user_id)
        if who is None:
            raise HTTPException(403, "This account can't sign in right now.")
        sign_in(user_id, request, response)
        return {"authenticated": True, "user": _user_json(who)}

    @router.post("/password")
    def change_password(req: PasswordChange, request: Request, response: Response) -> dict:
        """Change your own password; other browsers signed in as you are signed out."""
        who = resolve(request, settings, _users(request), _sessions(request))
        if who is None or who.kind != "user":
            raise HTTPException(401, "Sign in first.")
        account = f"user:{who.username}"
        if wait := limits.retry_after(account, *FAILURES_PER_ACCOUNT):
            raise too_many(wait, "failed attempts")
        rec = _users(request).find(who.username)
        if rec is None or not verify_password(req.current, rec.password_hash):
            limits.hit(account, *FAILURES_PER_ACCOUNT)
            raise HTTPException(400, "Your current password isn't right.")
        try:
            _users(request).set_password(who.id, req.new)
        except UserError as exc:
            raise HTTPException(400, str(exc)) from exc
        _sessions(request).drop_subject(who.id)
        sign_in(who.id, request, response)
        return {"ok": True}

    return router
