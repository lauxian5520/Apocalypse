"""What a tool is: a data-defined contract bound to a code handler."""
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

# Permission classes. They drive the approval policy and nothing else, so a new
# class only needs a rule in `harness/tools/approval.py`.
PERMISSION_READ = "read"
PERMISSION_WRITE = "write"
PERMISSION_EXEC = "exec"
PERMISSIONS = (PERMISSION_READ, PERMISSION_WRITE, PERMISSION_EXEC)


@dataclass
class ToolContext:
    """What a handler is allowed to reach.

    Handlers get this instead of module-level globals so a tool can be exercised
    against a throwaway workspace without a running server.
    """

    session_id: str
    sandbox: Any                       # harness.sandbox.base.Sandbox
    workspace: Any                     # harness.sandbox.workspace.Workspace


@dataclass(frozen=True)
class ToolSpec:
    """One tool: its model-facing contract plus the function that runs it."""

    name: str
    description: str
    parameters: dict                   # JSON Schema, straight from data/tools/*.json
    permission: str
    handler: Callable[..., Awaitable[str]]
    module: str = ""                   # which builtin module contributed it
    stops_turn: bool = False           # ends the turn after this call (exit_plan_mode)

    def to_wire(self) -> dict:
        """The entry the provider sees in the request's `tools` array."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def describe(self) -> dict:
        """Plain description for the UI's plugin panel."""
        return {
            "name": self.name,
            "description": self.description,
            "permission": self.permission,
            "module": self.module,
            "stops_turn": self.stops_turn,
        }
