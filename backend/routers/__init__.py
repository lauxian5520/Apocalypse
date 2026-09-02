"""HTTP layer.

Each module maps one resource onto endpoints and does nothing else: parse the
request, call a service, return a schema. The aggregate router below is the
single place the URL map is defined, so `main.py` never lists routes.
"""
from fastapi import APIRouter

from routers import (
    admin, ai, auth, comments, feeds, harness, memos, messages, music, uploads, users,
)

api_router = APIRouter(prefix="/api")
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(memos.router)
api_router.include_router(comments.router)
api_router.include_router(messages.router)
api_router.include_router(music.router)
api_router.include_router(uploads.router)
api_router.include_router(feeds.router)
api_router.include_router(ai.router)
api_router.include_router(harness.router)
api_router.include_router(admin.router)

__all__ = ["api_router"]
