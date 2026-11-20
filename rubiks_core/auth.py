"""Accounts, sessions and invites.

- The first account created becomes the owner. After that, sign-up needs an invite code,
  so nobody on the same Wi-Fi can create an account on an agent that can run commands.
- Session tokens are random; only their SHA-256 is stored.
"""

import hashlib
import re
import secrets
import time

from werkzeug.security import check_password_hash, generate_password_hash

from . import db

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
VALID_DEVICES = ("phone", "tablet", "desktop", "terminal")
VALID_PERSONAS = ("professional", "casual")
VALID_APPROVAL = ("ask", "autopilot")


class AuthError(Exception):
    pass


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def has_users() -> bool:
    return db.one("SELECT 1 AS x FROM users LIMIT 1") is not None


def public_user(u: dict) -> dict:
    return {
        "id": u["id"],
        "name": u["name"],
        "email": u["email"],
        "role": u["role"],
        "device_mode": u.get("device_mode"),
        "persona": u.get("persona", "professional"),
        "approval_mode": u.get("approval_mode", "ask"),
        "about": u.get("about", ""),
    }


def create_user(name: str, email: str, password: str, invite: str = "") -> dict:
    name = (name or "").strip()[:60]
    email = (email or "").strip().lower()[:120]
    if not name:
        raise AuthError("Tell us what to call you.")
    if not EMAIL_RE.match(email):
        raise AuthError("That email doesn't look right.")
    if len(password or "") < 8:
        raise AuthError("Use at least 8 characters for your password.")
    if db.one("SELECT id FROM users WHERE email = ?", (email,)):
        raise AuthError("An account with that email already exists. Sign in instead.")

    first = not has_users()
    if not first:
        code = (invite or "").strip().upper()
        row = db.one("SELECT * FROM invites WHERE code = ? AND used_by IS NULL", (code,))
        if not row:
            raise AuthError("Sign-up needs an invite code from the owner of this Rubiks.")

    uid = db.execute(
        "INSERT INTO users (name, email, pw_hash, role, created_at) VALUES (?, ?, ?, ?, ?)",
        (name, email, generate_password_hash(password), "owner" if first else "member", time.time()),
    )
    if not first:
        db.execute("UPDATE invites SET used_by = ? WHERE code = ?", (uid, (invite or "").strip().upper()))
    return db.one("SELECT * FROM users WHERE id = ?", (uid,))


def verify_login(email: str, password: str) -> dict:
    email = (email or "").strip().lower()
    u = db.one("SELECT * FROM users WHERE email = ?", (email,))
    if not u or not check_password_hash(u["pw_hash"], password or ""):
        raise AuthError("Email or password is wrong.")
    return u


def create_session(user_id: int, label: str = "") -> str:
    token = secrets.token_urlsafe(32)
    now = time.time()
    db.execute(
        "INSERT INTO sessions (token_hash, user_id, label, created_at, last_seen) VALUES (?, ?, ?, ?, ?)",
        (_hash_token(token), user_id, (label or "")[:80], now, now),
    )
    return token


def user_from_token(token: str):
    if not token:
        return None
    th = _hash_token(token)
    row = db.one(
        "SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token_hash = ?",
        (th,),
    )
    if row:
        db.execute("UPDATE sessions SET last_seen = ? WHERE token_hash = ?", (time.time(), th))
    return row


def revoke(token: str):
    if token:
        db.execute("DELETE FROM sessions WHERE token_hash = ?", (_hash_token(token),))


def create_invite(owner_id: int) -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    code = "-".join("".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(2))
    db.execute("INSERT INTO invites (code, created_by, created_at) VALUES (?, ?, ?)", (code, owner_id, time.time()))
    return code


def update_prefs(user_id: int, data: dict) -> dict:
    fields = {}
    if data.get("device_mode") in VALID_DEVICES:
        fields["device_mode"] = data["device_mode"]
    if data.get("persona") in VALID_PERSONAS:
        fields["persona"] = data["persona"]
    if data.get("approval_mode") in VALID_APPROVAL:
        fields["approval_mode"] = data["approval_mode"]
    if isinstance(data.get("about"), str):
        fields["about"] = data["about"].strip()[:2000]
    if isinstance(data.get("name"), str) and data["name"].strip():
        fields["name"] = data["name"].strip()[:60]
    if fields:
        sets = ", ".join(f"{k} = ?" for k in fields)
        db.execute(f"UPDATE users SET {sets} WHERE id = ?", (*fields.values(), user_id))
    return db.one("SELECT * FROM users WHERE id = ?", (user_id,))
