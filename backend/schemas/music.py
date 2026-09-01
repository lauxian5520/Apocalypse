from datetime import datetime

from pydantic import BaseModel

from schemas.common import ORMModel


class MusicOut(ORMModel):
    id: int
    title: str
    artist: str | None = None
    stored_filename: str
    duration_sec: int | None = None
    sort_order: int
    is_active: bool
    url: str
    created_at: datetime


class MusicUpdateIn(BaseModel):
    title: str | None = None
    artist: str | None = None
    sort_order: int | None = None
    is_active: bool | None = None
