"""احراز هویت: PBKDF2 برای گذرواژه، JWT برای نشست، نقش‌ها: admin / engineer / viewer."""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time

import jwt
from starlette.exceptions import HTTPException
from starlette.requests import Request

from .db import DATA_DIR, db, one

ROLE_RANK = {"viewer": 1, "engineer": 2, "admin": 3}
TOKEN_TTL = 12 * 3600


def _secret() -> str:
    env = os.environ.get("AQUA_SECRET")
    if env:
        return env
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    p = DATA_DIR / "secret.key"
    if not p.exists():
        p.write_text(secrets.token_hex(32))
        try:
            p.chmod(0o600)
        except OSError:
            pass
    return p.read_text().strip()


def hash_password(pw: str, salt: bytes | None = None) -> str:
    salt = salt or os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, 200_000)
    return f"pbkdf2${salt.hex()}${dk.hex()}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        _, salt, dk = stored.split("$")
        calc = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), 200_000)
        return hmac.compare_digest(calc.hex(), dk)
    except Exception:
        return False


def make_token(user: dict) -> str:
    now = int(time.time())
    return jwt.encode({"sub": str(user["id"]), "role": user["role"], "iat": now, "exp": now + TOKEN_TTL},
                      _secret(), algorithm="HS256")


def current_user(request: Request) -> dict:
    h = request.headers.get("authorization", "")
    if not h.lower().startswith("bearer "):
        raise HTTPException(401, "ورود لازم است")
    try:
        data = jwt.decode(h[7:], _secret(), algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(401, "نشست نامعتبر یا منقضی شده است")
    with db() as c:
        u = one(c.execute("SELECT id,username,full_name,role,active FROM users WHERE id=?", (int(data["sub"]),)))
    if not u or not u["active"]:
        raise HTTPException(401, "کاربر غیرفعال است")
    return u


def require(request: Request, role: str = "viewer") -> dict:
    u = current_user(request)
    if ROLE_RANK[u["role"]] < ROLE_RANK[role]:
        raise HTTPException(403, "دسترسی کافی ندارید")
    return u


def log(user: dict | None, action: str, entity: str = "", entity_id: str | int = "", detail: str = ""):
    with db() as c:
        c.execute("INSERT INTO audit_log(user_id,username,action,entity,entity_id,detail) VALUES(?,?,?,?,?,?)",
                  (user["id"] if user else None, user["username"] if user else None,
                   action, entity, str(entity_id), detail))
