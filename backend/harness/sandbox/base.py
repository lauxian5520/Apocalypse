"""The sandbox seam: where a tool's side effects are allowed to land."""
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass
class ExecResult:
    exit_code: int
    stdout: str
    stderr: str
    truncated: bool = False
    timed_out: bool = False

    def render(self) -> str:
        """Flatten to the text a model reads back."""
        parts = []
        if self.stdout:
            parts.append(self.stdout)
        if self.stderr:
            parts.append(f"[stderr]\n{self.stderr}")
        if self.timed_out:
            parts.append("[命令超时，已终止]")
        if self.truncated:
            parts.append("[输出过长，已截断]")
        if not parts:
            parts.append(f"[无输出，退出码 {self.exit_code}]")
        return "\n".join(parts)


@runtime_checkable
class Sandbox(Protocol):
    """Confinement for anything a tool runs or touches."""

    @property
    def root(self) -> str:
        """Absolute path the sandbox confines file access to."""
        ...

    def resolve(self, relative: str) -> str:
        """Absolute path for `relative` inside the sandbox, or raise if it escapes."""
        ...

    async def run(self, command: str, timeout: int = 0) -> ExecResult:
        """Execute `command` under the sandbox's limits."""
        ...
