"""FastAPI dependencies — the single place that turns a request into a session
or an authenticated `User`.

Previously every router imported `require_user` from `routers.auth`, which made
each router depend on a sibling router. They now all depend on this module and
on nothing else in the HTTP layer.
"""
from collections.abc import Iterator

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from core.config import get_settings
from core.database import SessionLocal
from core.security import csrf_tokens_match, decode_access_token
from models.user import User

settings = get_settings()

CSRF_HEADER = "x-csrf-token"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

_bearer = HTTPBearer(auto_error=False)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _token_from_request(request: Request, creds: HTTPAuthorizationCredentials | None) -> str | None:
    if creds and creds.credentials:
        return creds.credentials
    return request.cookies.get(settings.auth_cookie_name)


def _check_csrf(request: Request, used_bearer: bool) -> None:
    """Double-submit CSRF check, required only for cookie-authenticated writes.

    A Bearer token cannot be attached by the browser automatically, so requests
    that carry one are not cross-site forgeable.
    """
    if request.method in SAFE_METHODS or used_bearer:
        return
    cookie_token = request.cookies.get(settings.csrf_cookie_name) or ""
    header_token = request.headers.get(CSRF_HEADER) or ""
    if not csrf_tokens_match(cookie_token, header_token):
        raise HTTPException(status_code=403, detail="CSRF token missing or invalid")


def _lookup_active_user(db: Session, token: str | None) -> User | None:
    payload = decode_access_token(token or "")
    if not payload:
        return None
    try:
        user_id = int(payload.get("sub"))
    except (TypeError, ValueError):
        return None
    user = db.query(User).filter(User.id == user_id).first()
    if not user or user.is_disabled:
        return None
    return user


def require_user(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User:
    """Authenticated user, or 401/403. Enforces CSRF on unsafe methods."""
    token = _token_from_request(request, creds)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    _check_csrf(request, used_bearer=bool(creds and creds.credentials))

    payload = decode_access_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    try:
        user_id = int(payload.get("sub"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    if user.is_disabled:
        raise HTTPException(status_code=403, detail="账号已被管理员禁用")
    return user


def require_admin(user: User = Depends(require_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    return user


def optional_user(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User | None:
    """Viewer identity for public endpoints — never raises.

    Used by the memo feed so anonymous posts can still reveal their author to
    the author themselves and to admins.
    """
    return _lookup_active_user(db, _token_from_request(request, creds))
