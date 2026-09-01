from datetime import datetime

from pydantic import BaseModel

from schemas.common import ORMModel


class LoginIn(BaseModel):
    username: str
    password: str


class UserOut(ORMModel):
    id: int
    username: str
    email: str
    avatar_url: str | None = None
    role: str
    is_disabled: bool = False
    created_at: datetime | None = None


class CaptchaOut(BaseModel):
    image: str
    token: str


class LoginOut(BaseModel):
    user: UserOut
