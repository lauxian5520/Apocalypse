"""Pydantic request/response contracts.

Kept separate from `routers` so the same shape (an author stub, a comment, a
page envelope) is defined exactly once and shared by every endpoint that needs
it, instead of being re-declared per router.
"""
