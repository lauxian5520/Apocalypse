"""Background music: public playlist, admin-managed library."""
from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from core.deps import get_db, require_admin
from core.errors import NotFoundError
from models.music import Music
from models.user import User
from schemas.music import MusicOut, MusicUpdateIn
from services import storage_service

router = APIRouter(prefix="/music", tags=["music"])


def _to_out(track: Music) -> MusicOut:
    return MusicOut(
        id=track.id,
        title=track.title,
        artist=track.artist,
        stored_filename=track.stored_filename,
        duration_sec=track.duration_sec,
        sort_order=track.sort_order,
        is_active=track.is_active,
        url=f"/api/music/stream/{track.stored_filename}",
        created_at=track.created_at,
    )


@router.get("", response_model=list[MusicOut])
def list_music(db: Session = Depends(get_db)):
    tracks = (
        db.query(Music)
        .filter(Music.is_active.is_(True))
        .order_by(Music.sort_order.asc(), Music.created_at.asc())
        .all()
    )
    return [_to_out(t) for t in tracks]


@router.get("/stream/{filename}")
def stream_music(filename: str):
    import os
    # Only ever serve a name that exists as a row, never an arbitrary path.
    safe = os.path.basename(filename)
    path = os.path.join(storage_service.settings.music_dir, safe)
    if not os.path.isfile(path):
        raise NotFoundError("音乐文件不存在")
    return FileResponse(path, media_type="audio/mpeg")


@router.post("", status_code=201, response_model=MusicOut)
async def upload_music(
    title: str = Form(...),
    artist: str = Form(""),
    sort_order: int = Form(0),
    file: UploadFile = File(...),
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    stored = await storage_service.save_music(file)
    track = Music(
        title=title,
        artist=artist or None,
        stored_filename=stored.filename,
        sort_order=sort_order,
    )
    db.add(track)
    db.commit()
    db.refresh(track)
    return _to_out(track)


@router.patch("/{track_id}", response_model=MusicOut)
def update_music(
    track_id: int,
    body: MusicUpdateIn,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    track = db.query(Music).filter(Music.id == track_id).first()
    if not track:
        raise NotFoundError("曲目不存在")
    for field, value in body.model_dump(exclude_unset=True, exclude_none=True).items():
        setattr(track, field, value)
    db.commit()
    db.refresh(track)
    return _to_out(track)


@router.delete("/{track_id}", status_code=204)
def delete_music(
    track_id: int,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    track = db.query(Music).filter(Music.id == track_id).first()
    if not track:
        raise NotFoundError("曲目不存在")
    storage_service.delete_file(storage_service.settings.music_dir, track.stored_filename)
    db.delete(track)
    db.commit()
