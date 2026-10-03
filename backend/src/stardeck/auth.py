"""Optional single shared password (STARDECK_PASSWORD) with cookie sessions.

Deliberately simple: one password for the whole install, sessions kept in memory (a restart
signs everyone out). It keeps a LAN-exposed instance from being open to anyone who finds it.
"""

import hmac
import secrets
import threading
import time

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from stardeck.config import Settings

COOKIE = "stardeck_session"
OPEN_PATHS = ("/api/auth/",)


class SessionStore:
    def __init__(self, hours: int):
        self.ttl = hours * 3600
        self._sessions: dict[str, float] = {}
        self._lock = threading.Lock()

    def create(self) -> str:
        token = secrets.token_urlsafe(32)
        with self._lock:
            now = time.time()
            self._sessions = {t: exp for t, exp in self._sessions.items() if exp > now}
            self._sessions[token] = now + self.ttl
        return token

    def valid(self, token: str | None) -> bool:
        with self._lock:
            return bool(token) and self._sessions.get(token, 0) > time.time()

    def drop(self, token: str | None) -> None:
        with self._lock:
            self._sessions.pop(token or "", None)


class AuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, settings: Settings, sessions: SessionStore):
        super().__init__(app)
        self.settings, self.sessions = settings, sessions

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if (self.settings.password and path.startswith("/api/") and not path.startswith(OPEN_PATHS)
                and not self.sessions.valid(request.cookies.get(COOKIE))):
            return JSONResponse({"detail": "Sign in required"}, status_code=401)
        return await call_next(request)


class LoginRequest(BaseModel):
    password: str


def make_router(settings: Settings, sessions: SessionStore) -> APIRouter:
    router = APIRouter(prefix="/auth", tags=["auth"])

    @router.get("/status")
    def status(request: Request) -> dict:
        required = bool(settings.password)
        return {"required": required,
                "authenticated": not required or sessions.valid(request.cookies.get(COOKIE))}

    @router.post("/login")
    def login(req: LoginRequest, request: Request, response: Response) -> dict:
        if not settings.password:
            return {"authenticated": True}
        if not hmac.compare_digest(req.password.encode(), settings.password.encode()):
            time.sleep(1)  # slows down guessing
            raise HTTPException(401, "Wrong password")
        response.set_cookie(COOKIE, sessions.create(), max_age=settings.session_hours * 3600, httponly=True,
                            samesite="lax", secure=request.url.scheme == "https")
        return {"authenticated": True}

    @router.post("/logout")
    def logout(request: Request, response: Response) -> dict:
        sessions.drop(request.cookies.get(COOKIE))
        response.delete_cookie(COOKIE)
        return {"authenticated": False}

    return router
