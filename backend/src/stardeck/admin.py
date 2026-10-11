"""Operator commands for running a hosted Stardeck (they also work on a self-hosted install).

Run them where the server's data lives, e.g. inside the backend container:

    stardeck-admin workspace create "Acme" --plan community
    stardeck-admin workspace list
    stardeck-admin workspace plan ws-1a2b3c4d5e6f7a8b enterprise
    stardeck-admin workspace limits ws-1a2b3c4d5e6f7a8b '{"sources": 3, "features": ["measures"]}'
    stardeck-admin invite ravi@acme.in --workspace ws-1a2b3c4d5e6f7a8b --role editor
    stardeck-admin user reset-link asha@acme.in
    stardeck-admin demo setup
    stardeck-admin share revoke <token>
    stardeck-admin backup /backups

Invitation and reset links are printed for you to send (they work once, for a few days).
"""

import argparse
import json
import shutil
import sys
from pathlib import Path

from sqlalchemy import func

from stardeck import shares
from stardeck.config import Settings
from stardeck.db import (
    DashboardRecord,
    InviteRecord,
    SessionRecord,
    ShareLinkRecord,
    SourceRecord,
    UserRecord,
    WorkspaceRecord,
    utcnow,
)
from stardeck.deps import AppState
from stardeck.invites import INVITE_HOURS, RESET_HOURS, Invites
from stardeck.migrations import MIGRATIONS, backup, pending
from stardeck.plans import FEATURES, resolve_plan
from stardeck.principal import SYSTEM
from stardeck.workspaces import PLANS

ROLES = ("viewer", "editor", "admin")


class CommandError(RuntimeError):
    pass


def _link(state: AppState, kind: str, token: str) -> str:
    base = (state.settings.public_url or "http://localhost:5173").rstrip("/")
    return f"{base}/{kind}/{token}"


def _workspace(state: AppState, workspace_id: str) -> WorkspaceRecord:
    with state.db.platform_session() as s:
        rec = s.get(WorkspaceRecord, workspace_id)
    if rec is None:
        raise CommandError(f"No workspace {workspace_id}. See: workspace list")
    return rec


def _user(state: AppState, email: str) -> UserRecord:
    rec = state.users.find(email)
    if rec is None:
        raise CommandError(f"No account for {email}.")
    return rec


# ---------- workspaces ----------

def workspace_create(state: AppState, name: str, plan: str, sample: bool = True) -> WorkspaceRecord:
    rec = state.create_workspace(name, plan, sample=sample)
    print(f"Created {rec.id} \"{rec.name}\" ({rec.plan}){' with sample data' if sample else ''}.")
    return rec


def workspace_list(state: AppState) -> None:
    with state.db.platform_session() as s:
        def counts(model, real_only: bool = False) -> dict[str, int]:
            q = s.query(model.workspace_id, func.count()).group_by(model.workspace_id)
            if real_only:
                q = q.filter(model.sample.isnot(True))
            return dict(q.all())
        sources, dashboards, users = counts(SourceRecord, True), counts(DashboardRecord, True), counts(UserRecord)
        last = dict(s.query(UserRecord.workspace_id, func.max(UserRecord.last_login_at)).group_by(UserRecord.workspace_id).all())
        rows = s.query(WorkspaceRecord).order_by(WorkspaceRecord.created_at).all()
    print(f"{'WORKSPACE':22} {'PLAN':12} {'STATUS':10} {'SOURCES':>7} {'DASHB.':>6} {'PEOPLE':>6}  {'LAST SIGN-IN':16}  NAME")
    for w in rows:
        seen = last.get(w.id)
        print(f"{w.id:22} {w.plan:12} {w.status:10} {sources.get(w.id, 0):>7} {dashboards.get(w.id, 0):>6} "
              f"{users.get(w.id, 0):>6}  {seen.strftime('%Y-%m-%d %H:%M') if seen else '-':16}  {w.name}")


LIMIT_KEYS = {"sources", "dashboards", "sources_per_dashboard", "upload_mb", "ai_generations_per_day",
              "ai_messages_per_day", "ai_label_batches_per_day", "badge", "features"}


def workspace_limits(state: AppState, workspace_id: str, override: str | None, clear: bool) -> None:
    """Exceptions to a workspace's plan, e.g. one more data source or a feature for a trial."""
    ws = _workspace(state, workspace_id)
    if clear or override:
        value = None
        if override and not clear:
            try:
                value = json.loads(override)
            except json.JSONDecodeError as exc:
                raise CommandError(f"Not valid JSON: {exc}") from exc
            if not isinstance(value, dict) or (unknown := set(value) - LIMIT_KEYS):
                raise CommandError(f"Use an object with any of: {', '.join(sorted(LIMIT_KEYS))}"
                                   + (f" (not {', '.join(sorted(unknown))})" if isinstance(value, dict) else ""))
            if bad := set(value.get("features") or []) - set(FEATURES):
                raise CommandError(f"Unknown feature {', '.join(sorted(bad))}; use any of: {', '.join(FEATURES)}")
        ws = state.workspaces.update(workspace_id, limits_override=value)
    plan = resolve_plan(ws.plan, ws.limits_override, hosted=True)
    print(f"{ws.id} \"{ws.name}\": {plan.name}" + (f", with exceptions {json.dumps(ws.limits_override)}" if ws.limits_override else ""))
    print(json.dumps(plan.to_json(), indent=2))


def workspace_delete(state: AppState, workspace_id: str) -> None:
    """Everything the workspace has: sources and their uploaded files, dashboards, people, their
    sign-ins and invitations, then the workspace itself."""
    _workspace(state, workspace_id)
    with state.db.new_session(workspace_id) as db:
        for src in db.query(SourceRecord).all():
            state.sources.delete(db, src)  # removes uploaded files (never the shared sample data)
        for d in db.query(DashboardRecord).all():
            db.delete(d)
        for link in db.query(ShareLinkRecord).all():
            db.delete(link)
        people = db.query(UserRecord).all()
        ids = [u.id for u in people]
        for u in people:
            db.delete(u)
        db.commit()
    with state.db.platform_session() as s:
        if ids:
            s.query(SessionRecord).filter(SessionRecord.subject.in_(ids)).delete()
        s.query(InviteRecord).filter(InviteRecord.workspace_id == workspace_id).delete()
        s.query(WorkspaceRecord).filter(WorkspaceRecord.id == workspace_id).delete()
        s.commit()
    folder = state.sources.uploads_root / workspace_id
    if folder.is_dir() and folder.resolve().is_relative_to(state.sources.uploads_root):
        shutil.rmtree(folder)
    state.workspaces.invalidate(workspace_id)
    state.users.changed()
    print(f"Deleted {workspace_id} and everything in it.")


# ---------- people ----------

def invite(state: AppState, email: str, workspace_id: str, role: str = "admin", name: str = "") -> str:
    ws = _workspace(state, workspace_id)
    email = state.users.check_username(email) if state.settings.hosted else email.strip().lower()
    if state.users.find(email) is not None:
        raise CommandError(f"{email} already has an account. Send a reset link instead: user reset-link {email}")
    link = _link(state, "invite", Invites(state.db).create(ws.id, email, role))
    greeting = f"Hi {name.split()[0]}," if name.strip() else "Hi,"
    print(f"Invitation for {email} ({role}) to \"{ws.name}\" (works once, for {INVITE_HOURS} hours):\n{link}\n")
    print("--- email you can send ---")
    print(f"Subject: Your Stardeck invitation\n\n{greeting}\n\n"
          f"Here's your private Stardeck workspace: {link}\n\n"
          f"Open the link to choose your password. It works once, within {INVITE_HOURS // 24} days.\n"
          "Your workspace starts with sample retail data so you can try everything right away;\n"
          "then upload a spreadsheet or connect your database.\n")
    return link


def reset_link(state: AppState, email: str) -> str:
    user = _user(state, email)
    link = _link(state, "reset", Invites(state.db).create(user.workspace_id or "", user.username, user.role, "reset",
                                                          user_id=user.id))
    print(f"Password reset for {user.username} (works once, for {RESET_HOURS} hours):\n{link}")
    return link


def set_disabled(state: AppState, email: str, disabled: bool) -> None:
    user = _user(state, email)
    with state.db.platform_session() as s:
        rec = s.get(UserRecord, user.id)
        rec.disabled = disabled
        if disabled:
            s.query(SessionRecord).filter(SessionRecord.subject == user.id).delete()
        s.commit()
    print(f"{user.username} {'can no longer sign in' if disabled else 'can sign in again'}.")


def revoke_sessions(state: AppState, email: str) -> None:
    user = _user(state, email)
    with state.db.platform_session() as s:
        n = s.query(SessionRecord).filter(SessionRecord.subject == user.id).delete()
        s.commit()
    print(f"Signed {user.username} out of {n} browser{'s' if n != 1 else ''}.")


# ---------- public demo and share links ----------

DEMO_FILTERS = ({"id": "city", "table": "customers", "column": "city", "label": "City"},
                {"id": "category", "table": "products", "column": "category", "label": "Category"})


def _share_base(state: AppState) -> str:
    return (state.settings.share_url or state.settings.public_url or "http://localhost:5173").rstrip("/")


def demo_setup(state: AppState, name: str = "Stardeck demo") -> str:
    """The public demo: a workspace with the sample data, City and Category filters on its dashboard,
    and a share link. Safe to run again: it keeps what's there and adds what's missing."""
    ws_id = state.settings.demo_workspace_id
    with state.db.platform_session() as s:
        missing = s.get(WorkspaceRecord, ws_id) is None
    if missing:
        state.create_workspace(name, "enterprise", sample=True, workspace_id=ws_id)
    with state.db.new_session(ws_id) as db:
        dash = (db.query(DashboardRecord).filter(DashboardRecord.sample.is_(True)).first()
                or db.query(DashboardRecord).first())
        if dash is None:
            raise CommandError(f"{ws_id} has no dashboard. Delete it (workspace delete {ws_id} --yes) and run this again.")
        have = {f["id"] for f in dash.filters or []}
        added = [{**f, "sourceId": dash.source_id, "values": []} for f in DEMO_FILTERS if f["id"] not in have]
        if added:
            dash.filters = [*(dash.filters or []), *added]
            db.commit()
        link = next((x for x in shares.links_for(db, dash.id) if shares.is_active(x)), None) \
            or shares.create(db, dash.id, SYSTEM)
        token, title = link.token, dash.name
    base = _share_base(state)
    print(f"Demo workspace {ws_id}: \"{title}\" with City and Category filters.")
    print(f"Share link, with the tour: {base}/s/{token}?tour=1")
    print(f"Always the current demo:   {base}/demo")
    return token


def share_list(state: AppState, workspace_id: str | None) -> None:
    with state.db.platform_session() as s:
        q = s.query(ShareLinkRecord).filter(ShareLinkRecord.revoked_at.is_(None))
        if workspace_id:
            q = q.filter(ShareLinkRecord.workspace_id == workspace_id)
        rows = q.order_by(ShareLinkRecord.created_at).all()
        names = dict(s.query(DashboardRecord.id, DashboardRecord.name).all())
    for r in rows:
        state_ = "active" if shares.is_active(r) else "expired"
        print(f"{r.created_at:%Y-%m-%d}  {r.workspace_id:22} {state_:8} {r.views or 0:>6} views  {r.token}  "
              f"{names.get(r.dashboard_id, r.dashboard_id)}")
    print(f"{len(rows)} link{'s' if len(rows) != 1 else ''}")


def share_revoke(state: AppState, token: str) -> None:
    with state.db.platform_session() as s:
        link = s.get(ShareLinkRecord, token)
        if link is None or link.revoked_at is not None:
            raise CommandError("No working link with that token.")
        link.revoked_at = utcnow()
        s.commit()
    print("Turned off. Anyone opening it now sees that the link doesn't work any more.")


# ---------- operations ----------

def backup_all(state: AppState, folder: Path) -> None:
    """A consistent copy of the metadata DB plus every uploaded file (the encryption key is not
    included: keep it separately)."""
    stamp = utcnow().strftime("%Y%m%d-%H%M%S")
    db_copy = backup(state.db, folder, "manual")
    if db_copy is None:
        print("The metadata DB isn't SQLite: back it up with your database's own tools.")
    uploads = state.sources.uploads_root
    if uploads.is_dir():
        shutil.copytree(uploads, folder / f"uploads-{stamp}")
    print(f"Backed up to {folder}" + (f" ({db_copy.name})" if db_copy else ""))


def migrations(state: AppState) -> None:
    todo = {v for v, _ in pending(state.db)}
    for version, name, _ in MIGRATIONS:
        print(f"{version:4}  {name:20} {'pending' if version in todo else 'applied'}")


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="stardeck-admin", description="Operator commands for Stardeck.")
    sub = p.add_subparsers(dest="group", required=True)

    ws = sub.add_parser("workspace", help="Create and manage workspaces").add_subparsers(dest="cmd", required=True)
    c = ws.add_parser("create")
    c.add_argument("name")
    c.add_argument("--plan", default="community", choices=PLANS)
    c.add_argument("--no-sample", action="store_true", help="start without the sample data")
    ws.add_parser("list")
    c = ws.add_parser("plan")
    c.add_argument("workspace")
    c.add_argument("plan", choices=PLANS)
    c = ws.add_parser("limits", help="show or set exceptions to a workspace's plan")
    c.add_argument("workspace")
    c.add_argument("override", nargs="?", help='JSON, e.g. \'{"sources": 3, "features": ["measures"]}\'')
    c.add_argument("--clear", action="store_true", help="back to the plan as it is")
    for name in ("suspend", "resume"):
        ws.add_parser(name).add_argument("workspace")
    c = ws.add_parser("delete")
    c.add_argument("workspace")
    c.add_argument("--yes", action="store_true", help="really delete everything in it")

    c = sub.add_parser("invite", help="Invite someone to a workspace")
    c.add_argument("email")
    c.add_argument("--workspace", required=True)
    c.add_argument("--role", default="admin", choices=ROLES)
    c.add_argument("--name", default="")

    us = sub.add_parser("user", help="Accounts").add_subparsers(dest="cmd", required=True)
    for name in ("reset-link", "disable", "enable"):
        us.add_parser(name).add_argument("email")

    se = sub.add_parser("sessions", help="Sign-ins").add_subparsers(dest="cmd", required=True)
    se.add_parser("revoke", help="sign someone out everywhere").add_argument("email")

    de = sub.add_parser("demo", help="The public demo").add_subparsers(dest="cmd", required=True)
    de.add_parser("setup", help="create or refresh it and print its link").add_argument("--name", default="Stardeck demo")

    sh = sub.add_parser("share", help="Public share links").add_subparsers(dest="cmd", required=True)
    sh.add_parser("list").add_argument("--workspace")
    sh.add_parser("revoke", help="turn a link off").add_argument("token")

    sub.add_parser("backup", help="Copy the metadata DB and uploads").add_argument("folder", type=Path, nargs="?")
    sub.add_parser("migrations", help="Show database migrations")
    return p


def run(args: argparse.Namespace, state: AppState) -> None:
    match (args.group, getattr(args, "cmd", None)):
        case ("workspace", "create"):
            workspace_create(state, args.name, args.plan, sample=not args.no_sample)
        case ("workspace", "list"):
            workspace_list(state)
        case ("workspace", "plan"):
            _workspace(state, args.workspace)
            state.workspaces.update(args.workspace, plan=args.plan)
            print(f"{args.workspace} is now on {args.plan} (live within 30 seconds).")
        case ("workspace", "limits"):
            workspace_limits(state, args.workspace, args.override, args.clear)
        case ("workspace", ("suspend" | "resume") as cmd):
            _workspace(state, args.workspace)
            state.workspaces.update(args.workspace, status="suspended" if cmd == "suspend" else "active")
            print(f"{args.workspace} {'is suspended: nobody in it can sign in' if cmd == 'suspend' else 'is active again'}.")
        case ("workspace", "delete"):
            if not args.yes:
                raise CommandError("This deletes the workspace's data, files and people. Add --yes to confirm.")
            workspace_delete(state, args.workspace)
        case ("invite", _):
            invite(state, args.email, args.workspace, args.role, args.name)
        case ("user", "reset-link"):
            reset_link(state, args.email)
        case ("user", ("disable" | "enable") as cmd):
            set_disabled(state, args.email, cmd == "disable")
        case ("sessions", "revoke"):
            revoke_sessions(state, args.email)
        case ("demo", "setup"):
            demo_setup(state, args.name)
        case ("share", "list"):
            share_list(state, args.workspace)
        case ("share", "revoke"):
            share_revoke(state, args.token)
        case ("backup", _):
            backup_all(state, args.folder or state.settings.data_dir / "backups")
        case ("migrations", _):
            migrations(state)


def main(argv: list[str] | None = None, settings: Settings | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        run(args, AppState.create(settings or Settings()))
    except (CommandError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
