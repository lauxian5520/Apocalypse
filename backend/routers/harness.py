"""Harness endpoints: sessions, streamed turns, approvals and the registry.

The HTTP layer only frames what the agent loop produces. Turn logic lives in
`harness/loop/agent.py`, which has no idea this file exists.
"""
import logging
import os
from typing import AsyncIterator

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from core.config import get_settings
from core.deps import require_user
from core.errors import AppError, ValidationError
from core.sse import SSE_DONE, SSE_HEADERS, sse
from harness import build_context, resume_turn, run_turn
from harness.context import build_hooks
from harness.llm.registry import build_adapter
from harness.loop import interrupt
from harness.sandbox.workspace import Workspace
from harness.session import manager
from harness.session.projection import derive_messages
from harness.session.sqlite_store import SqliteSessionStore
from harness.session.title import generate_title
from harness.tools.registry import ToolRegistry, list_presets
from models.user import User
from schemas.common import Page
from schemas.harness import (
    ApprovalIn, AttachmentOut, EventOut, MessageIn, PresetOut, RegistryOut,
    SessionCreateIn, SessionDetailOut, SessionOut,
)
from services import storage_service

router = APIRouter(prefix="/harness", tags=["harness"])
logger = logging.getLogger(__name__)
settings = get_settings()

MAX_MESSAGE_CHARS = 20000
MAX_WORKSPACE_LISTING = 200


def require_harness_user(user: User = Depends(require_user)) -> User:
    """Auth for every harness endpoint.

    The agent can run commands, so who reaches it is a deployment decision, not
    a code one. Both gates default to the restrictive setting.
    """
    if not settings.harness_enabled:
        raise HTTPException(status_code=404, detail="Harness 未启用")
    if settings.harness_require_admin and user.role != "admin":
        raise HTTPException(status_code=403, detail="Harness 仅限管理员使用")
    return user


# ── sessions ─────────────────────────────────────────────────────

@router.get("/sessions", response_model=Page[SessionOut])
def list_sessions(
    q: str = "",
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=1, le=100),
    user: User = Depends(require_harness_user),
):
    total, rows = manager.list_for_user(user.id, query=q, page=page, page_size=page_size)
    return Page(total=total, page=page, page_size=page_size, items=rows)


@router.post("/sessions", response_model=SessionOut, status_code=201)
def create_session(body: SessionCreateIn, user: User = Depends(require_harness_user)):
    return manager.create(user.id, body.preset or settings.harness_preset, body.title)


@router.get("/sessions/{session_id}", response_model=SessionDetailOut)
def get_session(session_id: str, user: User = Depends(require_harness_user)):
    row = manager.get_owned(session_id, user.id)
    return SessionDetailOut(
        **{c: getattr(row, c) for c in (
            "id", "title", "preset", "status", "forked_from", "forked_at_seq",
            "created_at", "updated_at")},
        usage=manager.usage_summary(session_id),
        workspace_files=_workspace_files(session_id),
    )


@router.delete("/sessions/{session_id}", status_code=204)
def delete_session(session_id: str, user: User = Depends(require_harness_user)):
    manager.delete(session_id, user.id)


@router.post("/sessions/{session_id}/fork", response_model=SessionOut, status_code=201)
def fork_session(
    session_id: str,
    seq: int = Query(..., ge=0, description="复制到该事件序号为止"),
    user: User = Depends(require_harness_user),
):
    return manager.fork(session_id, user.id, seq)


# ── trajectory ───────────────────────────────────────────────────

@router.get("/sessions/{session_id}/events", response_model=list[EventOut])
def read_events(
    session_id: str,
    after: int = Query(-1, description="只返回序号大于该值的事件"),
    user: User = Depends(require_harness_user),
):
    manager.get_owned(session_id, user.id)
    return [e.to_dict() for e in SqliteSessionStore().read(session_id, after_seq=after)]


@router.get("/sessions/{session_id}/messages/derived")
def read_derived_messages(session_id: str, user: User = Depends(require_harness_user)):
    """Exactly what the next request would send — the traceability claim, checkable."""
    row = manager.get_owned(session_id, user.id)
    hctx = build_context(session_id, row.preset)
    return {"messages": derive_messages(SqliteSessionStore().read(session_id), hctx.system_prompt)}


# ── running a turn ───────────────────────────────────────────────

@router.post("/sessions/{session_id}/messages")
async def send_message(
    session_id: str, body: MessageIn, user: User = Depends(require_harness_user)
):
    row = manager.get_owned(session_id, user.id)
    text = body.text.strip()
    if not text:
        raise ValidationError("消息不能为空")
    if len(text) > MAX_MESSAGE_CHARS:
        raise ValidationError(f"消息过长，请控制在 {MAX_MESSAGE_CHARS} 字以内")
    if manager.is_busy(session_id):
        raise ValidationError("该会话正在运行，请先等待或中断当前任务")

    hctx = build_context(session_id, row.preset)
    if not row.title:
        await _title_session(hctx, text)
    return _stream(session_id, run_turn(hctx, text))


@router.post("/sessions/{session_id}/approvals/{call_id}")
async def resolve_approval(
    session_id: str, call_id: str, body: ApprovalIn, user: User = Depends(require_harness_user)
):
    row = manager.get_owned(session_id, user.id)
    if manager.is_busy(session_id):
        raise ValidationError("该会话正在运行，请先等待或中断当前任务")

    hctx = build_context(session_id, row.preset)
    return _stream(session_id, resume_turn(hctx, call_id, body.approved))


@router.post("/sessions/{session_id}/interrupt")
def interrupt_session(session_id: str, user: User = Depends(require_harness_user)):
    manager.get_owned(session_id, user.id)
    stopped = interrupt.request(session_id)
    return {"interrupted": stopped}


def _stream(session_id: str, events: AsyncIterator) -> StreamingResponse:
    """Frame an event stream as SSE and keep the session's status honest."""
    manager.set_status(session_id, "running")

    async def generator():
        try:
            async for event in events:
                yield sse(event.to_dict())
            yield SSE_DONE
        except AppError as e:
            # Headers are already on the wire, so errors travel in-band.
            yield sse({"error": e.message})
        except Exception as e:
            logger.exception("[harness] turn failed for %s", session_id)
            yield sse({"error": str(e) or e.__class__.__name__})

    # Cleanup lives in a background task, not in the generator's `finally`: a
    # client that closes its tab leaves the generator suspended, and Python
    # runs its `finally` only when the loop eventually finalizes it. Starlette
    # awaits this after the response ends either way, so the status settles and
    # the session never gets stuck in "running".
    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
        background=BackgroundTask(_finish_turn, session_id),
    )


def _finish_turn(session_id: str) -> None:
    interrupt.finish(session_id)
    manager.finalize_turn(session_id)


async def _title_session(hctx, seed: str) -> None:
    """Best-effort: a missing title never fails a turn."""
    try:
        title = await generate_title(hctx.llm, seed)
        if title:
            manager.set_title(hctx.session_id, title)
    except Exception as e:
        logger.info("[harness] auto-title skipped for %s: %s", hctx.session_id, e)


# ── workspace ────────────────────────────────────────────────────

@router.post("/sessions/{session_id}/attachments", response_model=AttachmentOut, status_code=201)
async def upload_attachment(
    session_id: str,
    file: UploadFile = File(...),
    user: User = Depends(require_harness_user),
):
    """Drop a file straight into the workspace, where `read` and `glob` find it."""
    manager.get_owned(session_id, user.id)
    data = await storage_service.read_upload(
        file, max_mb=settings.max_attachment_size_mb, label="附件"
    )

    workspace = Workspace(session_id)
    workspace.check_quota(len(data))
    name = os.path.basename(file.filename or "attachment")
    target = workspace.resolve(name)
    with open(target, "wb") as f:
        f.write(data)

    manager.touch(session_id)
    return AttachmentOut(filename=workspace.relative(target), size_bytes=len(data))


# ── registry ─────────────────────────────────────────────────────

@router.get("/tools", response_model=RegistryOut)
def read_registry(preset: str = "", user: User = Depends(require_harness_user)):
    """What is loaded right now — the plugin panel's data source."""
    registry = ToolRegistry(preset or settings.harness_preset)
    return RegistryOut(
        preset=registry.preset["name"],
        model=build_adapter().model,
        shell_enabled=settings.harness_shell_enabled,
        tools=registry.describe(),
        hooks=build_hooks().describe(),
    )


@router.get("/presets", response_model=list[PresetOut])
def read_presets(_: User = Depends(require_harness_user)):
    return list_presets()


def _workspace_files(session_id: str) -> list[str]:
    workspace = Workspace(session_id)
    root = workspace.ensure()
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for name in filenames:
            out.append(workspace.relative(os.path.join(dirpath, name)))
            if len(out) >= MAX_WORKSPACE_LISTING:
                return sorted(out)
    return sorted(out)
