"""Serving user-uploaded files."""
from fastapi import APIRouter
from fastapi.responses import FileResponse

from core.errors import NotFoundError
from services import storage_service

router = APIRouter(prefix="/uploads", tags=["uploads"])


@router.get("/{filename:path}")
def serve_upload(filename: str):
    # Path-traversal containment lives with the storage rules, not here.
    path = storage_service.resolve_public_path(filename)
    if not path:
        raise NotFoundError("File not found")
    return FileResponse(path)
