"""Request and response shapes for the harness endpoints."""
from datetime import datetime

from pydantic import BaseModel

from schemas.common import ORMModel


class SessionCreateIn(BaseModel):
    preset: str = ""
    title: str = ""


class MessageIn(BaseModel):
    text: str


class ApprovalIn(BaseModel):
    approved: bool


class UsageOut(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    total_tokens: int = 0
    requests: int = 0
    # None when a model in this session has no published rate — an unknown
    # cost is reported as unknown rather than as zero.
    cost_usd: float | None = None


class SessionOut(ORMModel):
    id: str
    title: str
    preset: str
    status: str
    # Non-empty only for a subagent run; the pair says which parent dispatched
    # it and in which role.
    parent_id: str | None = None
    agent: str = ""
    forked_from: str | None = None
    forked_at_seq: int | None = None
    created_at: datetime
    updated_at: datetime


class SessionDetailOut(SessionOut):
    # Includes every subagent this session dispatched — their tokens are billed
    # the same as anyone else's.
    usage: UsageOut
    workspace_files: list[str] = []


class ChildSessionOut(SessionOut):
    usage: UsageOut


class EventOut(BaseModel):
    seq: int
    type: str
    time: int
    data: dict


class ToolOut(BaseModel):
    name: str
    description: str
    permission: str
    module: str
    stops_turn: bool = False


class PresetOut(BaseModel):
    name: str
    label: str
    description: str = ""
    tools: list[str] = []
    max_steps: int = 0


class SkillOut(BaseModel):
    name: str
    description: str
    keywords: list[str] = []
    # True for a directory package (SKILL.md + scripts/…), false for a lone .md.
    packaged: bool = False
    files: list[str] = []


class SkillsOut(BaseModel):
    skills: list[SkillOut]
    # Files that failed to load. Reported rather than raised: one malformed
    # skill must not take a session down, but it must not vanish either.
    problems: list[dict] = []


class AgentOut(BaseModel):
    name: str
    label: str
    description: str = ""
    tools: list[str] = []
    max_steps: int = 0


class RegistryOut(BaseModel):
    """What the plugin panel renders."""

    preset: str
    model: str
    shell_enabled: bool
    subagent_enabled: bool
    tools: list[ToolOut]
    hooks: list[dict]
    skills: list[SkillOut] = []
    agents: list[AgentOut] = []


class AttachmentOut(BaseModel):
    filename: str
    size_bytes: int


class WorkspaceFileOut(BaseModel):
    """One artefact the agent produced, with enough to render a download row."""

    path: str
    size_bytes: int
    modified_at: int          # unix epoch milliseconds
    is_skill_asset: bool = False   # unpacked from a skill package, not produced work
