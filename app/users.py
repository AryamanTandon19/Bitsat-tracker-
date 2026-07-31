"""Operator account management.

    python -m app.users list
    python -m app.users add ramesh "Ramesh K." guard
    python -m app.users passwd ramesh
    python -m app.users disable ramesh
    python -m app.users enable ramesh

Passwords are prompted for, never passed as arguments — an argument ends up in
the shell history and in `ps` output for anyone else on the box.
"""
from __future__ import annotations

import argparse
import getpass
import secrets
import sys
import time

from .auth import ROLES
from .db import Database


def bootstrap_admin(db: Database) -> tuple[str, str] | None:
    """On a database with no accounts, create one admin with a generated
    password and return it once. Returns None if accounts already exist.

    A first-run account is needed or nobody can sign in at all; generating the
    password means there is never a published default to guess.
    """
    if db.list_users():
        return None
    password = secrets.token_urlsafe(12)
    db.add_user("admin", "Administrator", "admin", password, actor="bootstrap")
    return "admin", password


def _load(args) -> Database:
    import yaml
    with open(args.config) as f:
        cfg = yaml.safe_load(f) or {}
    return Database((cfg.get("storage") or {}).get("db_path", "watchdog.db"))


def _prompt_password(username: str) -> str:
    pw = getpass.getpass(f"new password for {username}: ")
    if len(pw) < 8:
        sys.exit("password must be at least 8 characters")
    if pw != getpass.getpass("repeat: "):
        sys.exit("passwords do not match")
    return pw


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m app.users",
                                 description=__doc__.split("\n\n")[0])
    ap.add_argument("--config", default="config.yaml")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list")
    p = sub.add_parser("add")
    p.add_argument("username")
    p.add_argument("display_name")
    p.add_argument("role", choices=ROLES)
    for name in ("passwd", "disable", "enable"):
        sp = sub.add_parser(name)
        sp.add_argument("username")

    args = ap.parse_args(argv)
    db = _load(args)

    if args.cmd == "list":
        users = db.list_users()
        if not users:
            print("no accounts yet — add one with:  "
                  "python -m app.users add <username> \"<name>\" guard")
            return 0
        print(f"{'username':<16}{'name':<22}{'role':<11}{'state':<9}last login")
        for u in users:
            last = (time.strftime("%Y-%m-%d %H:%M", time.localtime(u["last_login"]))
                    if u["last_login"] else "never")
            print(f"{u['username']:<16}{u['display_name']:<22}{u['role']:<11}"
                  f"{'active' if u['active'] else 'disabled':<9}{last}")
        return 0

    if args.cmd == "add":
        if db.get_user(args.username):
            sys.exit(f"{args.username} already exists")
        db.add_user(args.username, args.display_name, args.role,
                    _prompt_password(args.username), actor="cli")
        print(f"added {args.username} ({args.role})")
        return 0

    if args.cmd == "passwd":
        if not db.get_user(args.username):
            sys.exit(f"no such account: {args.username}")
        db.set_user_password(args.username, _prompt_password(args.username),
                             actor="cli")
        print(f"password changed — {args.username} is signed out everywhere")
        return 0

    want_active = args.cmd == "enable"
    if not db.set_user_active(args.username, want_active, actor="cli"):
        sys.exit(f"no such account: {args.username}")
    print(f"{args.username} {'enabled' if want_active else 'disabled'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
