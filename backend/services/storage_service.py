"""Saving user-uploaded files.

Every upload endpoint (avatars, memo images, comment images, chat attachments,
music) used to re-implement the same four steps: check the MIME type, check the
size, invent a stored filename, write the bytes. They all call this instead.
"""
import os
import uuid
from dataclasses import dataclass

from fastapi import UploadFile

from core.config import get_settings
from core.errors import ValidationError

settings = get_settings()

IMAGE_TYPES = frozenset({"image/jpeg", "image/png", "image/gif", "image/webp"})
AUDIO_TYPES = frozenset({"audio/mpeg", "audio/mp3", "audio/ogg", "audio/wav", "audio/flac", "audio/aac"})

_TYPE_LABELS = {
    IMAGE_TYPES: "JPEG/PNG/GIF/WebP",
    AUDIO_TYPES: "MP3/OGG/WAV/FLAC/AAC",
}


@dataclass(frozen=True)
class StoredFile:
    """Where a file landed and how to reach it over HTTP."""
    filename: str          # name on disk
    path: str              # absolute path
    url: str               # public URL
    size_bytes: int
    content_type: str
    original_name: str


def _extension(original_name: str, fallback: str) -> str:
    return os.path.splitext(original_name or "")[1] or fallback


async def read_upload(
    upload: UploadFile,
    *,
    allowed_types: frozenset[str] | None = None,
    max_mb: int,
    label: str = "文件",
) -> bytes:
    """Validate type and size, returning the file's bytes."""
    if allowed_types is not None and upload.content_type not in allowed_types:
        kinds = _TYPE_LABELS.get(allowed_types, "、".join(sorted(allowed_types)))
        raise ValidationError(f"{label}仅支持 {kinds} 格式")

    data = await upload.read()
    if len(data) > max_mb * 1024 * 1024:
        raise ValidationError(f"{label}不能超过 {max_mb}MB")
    return data


def write_bytes(
    data: bytes,
    *,
    prefix: str,
    original_name: str,
    content_type: str,
    directory: str,
    url_prefix: str,
    default_ext: str = ".bin",
) -> StoredFile:
    """Write `data` under a collision-proof name and describe where it went."""
    filename = f"{prefix}{uuid.uuid4().hex}{_extension(original_name, default_ext)}"
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, filename)
    with open(path, "wb") as f:
        f.write(data)
    return StoredFile(
        filename=filename,
        path=path,
        url=f"{url_prefix.rstrip('/')}/{filename}",
        size_bytes=len(data),
        content_type=content_type or "application/octet-stream",
        original_name=original_name or filename,
    )


async def save_upload(
    upload: UploadFile,
    *,
    prefix: str,
    directory: str,
    url_prefix: str,
    allowed_types: frozenset[str] | None = None,
    max_mb: int,
    label: str = "文件",
    default_ext: str = ".bin",
) -> StoredFile:
    """Validate then persist an UploadFile in one call."""
    data = await read_upload(upload, allowed_types=allowed_types, max_mb=max_mb, label=label)
    return write_bytes(
        data,
        prefix=prefix,
        original_name=upload.filename or "",
        content_type=upload.content_type or "",
        directory=directory,
        url_prefix=url_prefix,
        default_ext=default_ext,
    )


# ── Ready-made variants for the four upload kinds in this app ────────────────

async def save_avatar(upload: UploadFile, user_id: int) -> StoredFile:
    return await save_upload(
        upload,
        prefix=f"avatar_{user_id}_",
        directory=settings.avatar_dir,
        url_prefix="/api/uploads/avatars",
        allowed_types=IMAGE_TYPES,
        max_mb=settings.max_image_size_mb,
        label="头像",
        default_ext=".jpg",
    )


async def save_memo_image(upload: UploadFile, memo_id: int) -> StoredFile:
    return await save_upload(
        upload,
        prefix=f"{memo_id}_",
        directory=settings.upload_dir,
        url_prefix="/api/uploads",
        allowed_types=IMAGE_TYPES,
        max_mb=settings.max_image_size_mb,
        label="图片",
        default_ext=".jpg",
    )


async def save_comment_image(upload: UploadFile, memo_id: int) -> StoredFile:
    return await save_upload(
        upload,
        prefix=f"comment_{memo_id}_",
        directory=settings.upload_dir,
        url_prefix="/api/uploads",
        allowed_types=IMAGE_TYPES,
        max_mb=settings.max_image_size_mb,
        label="评论图片",
        default_ext=".jpg",
    )


async def save_message_attachment(upload: UploadFile, sender_id: int) -> StoredFile:
    # Chat accepts any file type; only the size is capped.
    return await save_upload(
        upload,
        prefix=f"msg_{sender_id}_",
        directory=settings.upload_dir,
        url_prefix="/api/uploads",
        allowed_types=None,
        max_mb=settings.max_attachment_size_mb,
        label="附件",
    )


async def save_music(upload: UploadFile) -> StoredFile:
    return await save_upload(
        upload,
        prefix="",
        directory=settings.music_dir,
        url_prefix="/api/music/stream",
        allowed_types=AUDIO_TYPES,
        max_mb=settings.max_audio_size_mb,
        label="音频",
        default_ext=".mp3",
    )


def delete_file(directory: str, filename: str) -> None:
    """Best-effort removal of a stored file."""
    if not filename:
        return
    path = os.path.join(directory, filename)
    if os.path.isfile(path):
        os.remove(path)


def contained_path(root: str, relative: str) -> str | None:
    """Resolve `relative` inside `root`, or None when it escapes.

    The single containment check in this codebase: uploads serve from it and
    the harness sandbox jails its tools with it. Symlinks are resolved before
    the comparison, so a link pointing outside `root` is rejected too.
    """
    safe = os.path.normpath(relative).lstrip("/\\")
    real_root = os.path.realpath(root)
    target = os.path.realpath(os.path.join(real_root, safe))
    # Compare on a path-component boundary: a bare startswith() would also
    # accept a sibling directory such as "<uploads>_backup".
    if target != real_root and not target.startswith(real_root + os.sep):
        return None
    return target


def resolve_public_path(filename: str) -> str | None:
    """Map a request path under /api/uploads to a real file, or None."""
    target = contained_path(settings.upload_dir, filename)
    return target if target and os.path.isfile(target) else None
