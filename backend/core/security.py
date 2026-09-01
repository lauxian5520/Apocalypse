"""Password hashing, access tokens and auth cookies.

Pure functions — no request objects, no database. `core.deps` turns these into
FastAPI dependencies.
"""
import secrets
from datetime import datetime, timedelta, timezone

from jose import jwt
from passlib.context import CryptContext
from starlette.responses import Response

from core.config import get_settings

settings = get_settings()

# ── Passwords ────────────────────────────────────────────────────────────────
# bcrypt silently ignores everything past 72 bytes (so "<72 bytes>" and
# "<72 bytes>anything" verify against the same hash), and bcrypt >= 4.1 raises
# ValueError instead of truncating. Both are handled here, once.
BCRYPT_MAX_BYTES = 72
MIN_PASSWORD_LENGTH = 8

_pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")


def _clamp(password: str) -> str:
    """Truncate to bcrypt's limit without splitting a UTF-8 sequence."""
    raw = password.encode("utf-8")
    if len(raw) <= BCRYPT_MAX_BYTES:
        return password
    return raw[:BCRYPT_MAX_BYTES].decode("utf-8", "ignore")


def password_error(password: str) -> str | None:
    """Return a user-facing validation message, or None when the password is ok."""
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"密码至少 {MIN_PASSWORD_LENGTH} 位"
    if len(password.encode("utf-8")) > BCRYPT_MAX_BYTES:
        return f"密码过长（最多 {BCRYPT_MAX_BYTES} 字节，中文约 24 个字符）"
    return None


def hash_password(password: str) -> str:
    return _pwd_ctx.hash(_clamp(password))


def verify_password(password: str, password_hash: str) -> bool:
    if not password_hash:
        return False
    try:
        return _pwd_ctx.verify(_clamp(password), password_hash)
    except ValueError:
        # Malformed/unknown hash in the DB — a failed login, not a 500.
        return False


# ── Tokens ───────────────────────────────────────────────────────────────────

def create_access_token(user_id: int, role: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(hours=settings.jwt_expire_hours)
    return jwt.encode(
        {"sub": str(user_id), "role": role, "exp": expire},
        settings.jwt_secret,
        algorithm="HS256",
    )


def decode_access_token(token: str) -> dict | None:
    """Return the payload, or None when the token is missing/invalid/expired."""
    if not token:
        return None
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except Exception:
        return None


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def csrf_tokens_match(cookie_token: str, header_token: str) -> bool:
    if not cookie_token or not header_token:
        return False
    return secrets.compare_digest(cookie_token, header_token)


# ── Cookies ──────────────────────────────────────────────────────────────────

def set_auth_cookies(response: Response, token: str) -> None:
    max_age = int(settings.jwt_expire_hours * 3600)
    domain = settings.cookie_domain or None
    response.set_cookie(
        key=settings.auth_cookie_name,
        value=token,
        max_age=max_age,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        path=settings.cookie_path,
        domain=domain,
    )
    response.set_cookie(
        key=settings.csrf_cookie_name,
        value=new_csrf_token(),
        max_age=max_age,
        httponly=False,          # the browser must be able to echo it back
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        path=settings.cookie_path,
        domain=domain,
    )


def clear_auth_cookies(response: Response) -> None:
    domain = settings.cookie_domain or None
    for name in (settings.auth_cookie_name, settings.csrf_cookie_name):
        response.delete_cookie(
            name,
            path=settings.cookie_path,
            domain=domain,
            secure=settings.cookie_secure,
            samesite=settings.cookie_samesite,
        )
