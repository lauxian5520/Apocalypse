"""Whether a tool call may proceed, and who decides.

The policy is the guard in front of every execution. It is the reason a public
deployment can hand an agent a shell at all: anything not provably harmless
stops and waits for a person.
"""
import json
import logging
from functools import lru_cache
from pathlib import Path

from harness.sandbox.local import parse_command
from harness.tools.base import PERMISSION_EXEC, PERMISSION_WRITE, ToolSpec

logger = logging.getLogger(__name__)

ALLOWLIST_FILE = Path(__file__).resolve().parent.parent / "data" / "shell_allowlist.json"

ALLOW = "allow"
ASK = "ask"
DENY = "deny"


class Decision:
    def __init__(self, verdict: str, reason: str = ""):
        self.verdict = verdict
        self.reason = reason

    @property
    def allowed(self) -> bool:
        return self.verdict == ALLOW


@lru_cache()
def _allowlist() -> tuple[frozenset, frozenset]:
    try:
        with open(ALLOWLIST_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        return frozenset(raw.get("auto_approved", [])), frozenset(raw.get("denied", []))
    except (OSError, json.JSONDecodeError) as e:
        # An unreadable allowlist must fail closed: every command asks.
        logger.error("[harness] shell allowlist unreadable (%s); all commands will ask", e)
        return frozenset(), frozenset()


class ApprovalPolicy:
    """Default policy: reads are free, workspace writes are free, exec is judged."""

    def decide(self, spec: ToolSpec, args: dict) -> Decision:
        if spec.permission == PERMISSION_EXEC:
            return self._judge_command(str(args.get("command", "")))
        if spec.permission == PERMISSION_WRITE:
            # The path was already forced inside the workspace by the sandbox,
            # so there is nothing left here for a human to add.
            return Decision(ALLOW)
        return Decision(ALLOW)

    @staticmethod
    def _judge_command(command: str) -> Decision:
        allowed, denied = _allowlist()
        try:
            argv = parse_command(command)
        except Exception as e:
            # Unparseable is not automatically dangerous, but it is not
            # automatically safe either — let a person look at it.
            return Decision(ASK, f"命令无法解析（{e}），需要人工确认")

        program = Path(argv[0]).name
        if program in denied:
            return Decision(DENY, f"命令 {program} 在禁用清单中，不允许执行")
        if program in allowed:
            return Decision(ALLOW)
        return Decision(ASK, f"命令 {program} 不在自动放行白名单中，需要人工批准")
