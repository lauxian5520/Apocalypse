from datetime import datetime

from pydantic import BaseModel

from schemas.common import ORMModel


class UserProfileOut(ORMModel):
    id: int
    username: str
    avatar_url: str | None = None
    role: str
    created_at: datetime | None = None
    memo_count: int
    comment_count: int


class AdminUserOut(ORMModel):
    id: int
    username: str
    email: str
    avatar_url: str | None = None
    role: str
    is_disabled: bool
    created_at: datetime | None = None


class UserRoleIn(BaseModel):
    role: str


class UserStatusIn(BaseModel):
    is_disabled: bool


class ResetPasswordIn(BaseModel):
    new_password: str | None = None
    password: str | None = None

    @property
    def value(self) -> str:
        raw = self.new_password if self.new_password is not None else self.password
        return (raw or "").strip()
