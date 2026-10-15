from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_validator

from schemas.common import ORMModel

MAX_USERNAME_LENGTH = 50


class UsernameUpdateIn(BaseModel):
    """Only the current user's username may be changed through this payload."""

    model_config = ConfigDict(extra="forbid")
    username: str

    @field_validator("username")
    @classmethod
    def validate_username(cls, value: str) -> str:
        """Normalize surrounding whitespace and enforce registration limits."""
        username = value.strip()
        if not username:
            raise ValueError("用户名不能为空")
        if len(username) > MAX_USERNAME_LENGTH:
            raise ValueError(f"用户名不能超过 {MAX_USERNAME_LENGTH} 个字符")
        return username


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
    can_use_harness: bool = False
    created_at: datetime | None = None


class CaptchaOut(BaseModel):
    image: str
    token: str


class LoginOut(BaseModel):
    user: UserOut
