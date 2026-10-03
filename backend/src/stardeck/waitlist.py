"""People asking for an invitation: the website's form (POST /api/public/waitlist) and the operator
CLI (`stardeck-admin waitlist ...`) both land here.

The form has a honeypot field people never see: bots fill it in and are quietly dropped. Each
sign-up can ping the operator (ntfy or Telegram), best effort, in the background.
"""

import logging
import re
import threading

import httpx
from pydantic import Field, field_validator

from stardeck.db import Database, WaitlistRecord
from stardeck.sources import ApiModel

log = logging.getLogger("stardeck.waitlist")
PER_IP = (5, 60 * 60)  # sign-ups per address per hour
PER_DAY = (300, 24 * 60 * 60)  # sign-ups per day, all addresses together
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
FIELDS = ("name", "company", "role", "data_source", "use_case")


class WaitlistIn(ApiModel):
    email: str = Field(max_length=200)
    name: str = Field("", max_length=200)
    company: str = Field("", max_length=200)
    role: str = Field("", max_length=200)
    data_source: str = Field("", max_length=200)
    use_case: str = Field("", max_length=2000)
    # The honeypot: hidden from people, so anything in it came from a bot.
    website: str = Field("", max_length=500)

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        v = v.strip().lower()
        if not EMAIL.match(v):
            raise ValueError("Enter a valid email address.")
        return v


def add(db: Database, email: str, overwrite: bool = True, **fields: str) -> bool:
    """Put someone on the list (True if they're new). Existing entries keep their status; with
    `overwrite`, the details given replace theirs (the CLI), otherwise only empty ones are filled in."""
    email = email.strip().lower()
    with db.platform_session() as s:
        rec = s.query(WaitlistRecord).filter(WaitlistRecord.email == email).one_or_none()
        new = rec is None
        if new:
            rec = WaitlistRecord(email=email)
            s.add(rec)
        for k, v in fields.items():
            if v and v.strip() and (overwrite or not getattr(rec, k)):
                setattr(rec, k, v.strip()[:2000])
        s.commit()
    return new


def join(state, req: WaitlistIn) -> None:
    """A sign-up from the website."""
    if req.website.strip():
        log.info("Waitlist sign-up dropped (honeypot)")
        return
    if add(state.db, req.email, overwrite=False, **{k: getattr(req, k) for k in FIELDS}):
        who = " · ".join(x for x in (req.name, req.company, req.email) if x.strip())
        notify(state.settings, f"New Stardeck waitlist sign-up: {who}" + (f"\n{req.use_case.strip()[:300]}" if req.use_case.strip() else ""))


def notify(settings, text: str) -> None:
    """Tell the operator, if they set up ntfy or Telegram. Never holds up the sign-up."""
    targets = []
    if settings.waitlist_ntfy_url:
        targets.append((settings.waitlist_ntfy_url, {"content": text.encode()}))
    if settings.telegram_bot_token and settings.telegram_chat_id:
        targets.append((f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
                        {"json": {"chat_id": settings.telegram_chat_id, "text": text}}))
    if not targets:
        return

    def send() -> None:
        for url, body in targets:
            try:
                httpx.post(url, timeout=5, **body).raise_for_status()
            except httpx.HTTPError as exc:
                log.warning("Waitlist notification failed: %s", type(exc).__name__)

    threading.Thread(target=send, name="waitlist-notify", daemon=True).start()
