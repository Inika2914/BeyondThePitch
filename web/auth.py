"""
Auth for Beyond The Pitch's web layer.

Simple, dependency-free (stdlib only) session auth:
  - passwords hashed with PBKDF2-HMAC-SHA256 + a random per-user salt
  - login issues an opaque session token, stored server-side in the
    `sessions` table and handed to the browser as an httpOnly cookie
    (`btp_session`)
  - two roles: "admin" and "jury". Admin accounts create jury accounts;
    there's no public signup.

On first run (see seed_admin_if_needed, called from web/app.py at
startup) an admin account is auto-created if the `users` table is
empty, using ADMIN_USERNAME / ADMIN_PASSWORD env vars if set, else a
generated username/password printed to the console.
"""

import hashlib
import hmac
import os
import secrets
import string
import uuid
from typing import Optional

from fastapi import HTTPException, Request

from web import db

SESSION_COOKIE = "btp_session"
_PBKDF2_ITERATIONS = 200_000


def _hash_password(password: str, salt: Optional[str] = None) -> tuple[str, str]:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), _PBKDF2_ITERATIONS
    )
    return digest.hex(), salt


def _verify_password(password: str, password_hash: str, salt: str) -> bool:
    candidate, _ = _hash_password(password, salt)
    return hmac.compare_digest(candidate, password_hash)


def _random_password(length: int = 14) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


# ---------- account management ----------

def create_user(username: str, password: str, role: str, display_name: str = "") -> dict:
    username = username.strip()
    if not username:
        raise HTTPException(400, "Username is required.")
    if role not in ("admin", "jury"):
        raise HTTPException(400, "Role must be 'admin' or 'jury'.")
    if len(password) < 6:
        raise HTTPException(400, "Password must be at least 6 characters.")
    if db.get_user_by_username(username):
        raise HTTPException(400, f"Username '{username}' is already taken.")

    password_hash, salt = _hash_password(password)
    uid = str(uuid.uuid4())[:8]
    db.create_user(
        uid=uid,
        username=username,
        password_hash=password_hash,
        salt=salt,
        role=role,
        display_name=display_name or username,
    )
    return db.get_user(uid)


def seed_admin_if_needed() -> None:
    """Called once at startup. If there are no users at all yet, create
    the first admin account so there's a way to log in."""
    if db.count_users() > 0:
        return

    username = os.environ.get("ADMIN_USERNAME", "admin")
    password = os.environ.get("ADMIN_PASSWORD") or _random_password()
    create_user(username, password, role="admin", display_name="Admin")

    print("\n" + "=" * 60)
    print("Beyond The Pitch — first run: admin account created")
    print(f"  username: {username}")
    print(f"  password: {password}")
    print("Log in with these at http://localhost:8000")
    print("=" * 60 + "\n")


# ---------- sessions ----------

def login(username: str, password: str) -> tuple[str, dict]:
    user = db.get_user_by_username(username.strip())
    if not user or not _verify_password(password, user["password_hash"], user["salt"]):
        raise HTTPException(401, "Invalid username or password.")

    token = secrets.token_urlsafe(32)
    db.create_session(token, user["id"])
    return token, _public_user(user)


def logout(token: Optional[str]) -> None:
    if token:
        db.delete_session(token)


def _public_user(user: dict) -> dict:
    return {
        "id": user["id"],
        "username": user["username"],
        "role": user["role"],
        "display_name": user["display_name"],
    }


def current_user(request: Request) -> dict:
    """Dependency: returns the logged-in user's public dict, or raises 401."""
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(401, "Not logged in.")
    user = db.get_user_by_session(token)
    if not user:
        raise HTTPException(401, "Session expired or invalid.")
    return _public_user(user)


def require_admin(request: Request) -> dict:
    user = current_user(request)
    if user["role"] != "admin":
        raise HTTPException(403, "Admin access required.")
    return user
