"""Shapes reused by more than one resource."""
from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class ORMModel(BaseModel):
    """Base for schemas built straight from ORM instances."""
    model_config = {"from_attributes": True}


class AuthorOut(ORMModel):
    """Public identity stub embedded in memos, comments and conversations.

    `id` is None for anonymous memos, where the author must stay hidden.
    """
    id: int | None
    username: str
    avatar_url: str | None = None


class Page(BaseModel, Generic[T]):
    """Uniform pagination envelope for every list endpoint."""
    total: int
    page: int
    page_size: int
    items: list[T]
