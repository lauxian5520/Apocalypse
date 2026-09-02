"""Bounded local execution.

A fence, not a jail: this raises the cost of a mistake, it does not make one
impossible. The real containment for a public deployment is the layer around
it — admin-only access, the shell tool off by default, and a container that
drops capabilities and does not run as root.

What it does enforce: no shell metacharacters (argv is parsed, never handed to
`sh -c`), the workspace as cwd, a scrubbed environment, CPU/memory/file-size
limits, a wall-clock timeout, and a bounded amount of output.
"""
import asyncio
import logging
import os
import resource
import shlex
import signal

from core.config import get_settings
from core.errors import ValidationError
from harness.sandbox.base import ExecResult
from harness.sandbox.workspace import Workspace

settings = get_settings()
logger = logging.getLogger(__name__)

# Address-space ceiling for a child. Generous enough for a python one-liner,
# small enough that a runaway allocation dies instead of swapping the host.
MAX_ADDRESS_SPACE_BYTES = 1024 * 1024 * 1024      # 1 GiB
MAX_WRITE_BYTES = 64 * 1024 * 1024                # 64 MiB per file

# RLIMIT_NPROC is deliberately not set: it counts processes per *real UID*, so
# a low limit would be hit by the web server's own workers rather than by the
# child. Runaway forking is handled by the timeout plus the process-group kill.

_SAFE_ENV_KEYS = ("PATH", "HOME", "LANG", "LC_ALL", "TZ")

# Bare tokens that only mean something to a shell. argv never reaches one,
# so their presence means the model assumed semantics it will not get.
SHELL_OPERATORS = frozenset({"|", "||", "&&", "&", ";", ">", ">>", "<", "<<"})


class LocalSandbox:
    """Runs commands inside one `Workspace`."""

    def __init__(self, workspace: Workspace):
        self._workspace = workspace

    @property
    def root(self) -> str:
        return self._workspace.root

    def resolve(self, relative: str) -> str:
        return self._workspace.resolve(relative)

    async def run(self, command: str, timeout: int = 0) -> ExecResult:
        argv = parse_command(command)
        timeout = timeout or settings.harness_shell_timeout_seconds
        cwd = self._workspace.ensure()

        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                cwd=cwd,
                env=_child_env(cwd),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.DEVNULL,
                start_new_session=True,     # its own process group, so we can kill the tree
                preexec_fn=_apply_limits,
            )
        except FileNotFoundError:
            raise ValidationError(f"命令不存在：{argv[0]}")
        except PermissionError:
            raise ValidationError(f"命令不可执行：{argv[0]}")

        timed_out = False
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            timed_out = True
            _kill_tree(proc)
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=5)
            except (asyncio.TimeoutError, ProcessLookupError):
                stdout, stderr = b"", b""

        cap = settings.harness_shell_max_output_bytes
        out, out_cut = _decode_capped(stdout, cap)
        err, err_cut = _decode_capped(stderr, cap)

        return ExecResult(
            exit_code=proc.returncode if proc.returncode is not None else -1,
            stdout=out,
            stderr=err,
            truncated=out_cut or err_cut,
            timed_out=timed_out,
        )


def parse_command(command: str) -> list[str]:
    """Split a command line into argv, refusing anything shell-only.

    The tool contract is "run one program with arguments", not "run a shell".
    Pipes, redirection and substitution are rejected rather than silently
    treated as literal arguments, so the model gets a clear correction.
    """
    text = (command or "").strip()
    if not text:
        raise ValidationError("命令不能为空")

    try:
        argv = shlex.split(text)
    except ValueError as e:
        raise ValidationError(f"命令无法解析：{e}")
    if not argv:
        raise ValidationError("命令不能为空")

    # Scan the parsed tokens, not the raw string. A metacharacter inside quotes
    # is ordinary argument text — `python3 -c "import time; time.sleep(1)"` is
    # perfectly safe here, because argv goes straight to execve and no shell
    # ever interprets it. Only a bare operator token means the model expected
    # shell semantics it is not going to get.
    operator = next((t for t in argv if t in SHELL_OPERATORS), "")
    if operator:
        raise ValidationError(
            f"命令包含 shell 操作符 {operator!r}；请拆成单条命令分别调用"
            "（不支持管道、重定向与命令串联）"
        )
    return argv


def _child_env(cwd: str) -> dict:
    """A minimal environment — the parent's secrets do not travel downward."""
    env = {k: os.environ[k] for k in _SAFE_ENV_KEYS if k in os.environ}
    env.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
    env["HOME"] = cwd
    env["PWD"] = cwd
    return env


def _apply_limits() -> None:
    """Runs in the child between fork and exec."""
    cpu_seconds = max(1, settings.harness_shell_timeout_seconds)
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 1))
    resource.setrlimit(resource.RLIMIT_AS, (MAX_ADDRESS_SPACE_BYTES,) * 2)
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_WRITE_BYTES,) * 2)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def _kill_tree(proc) -> None:
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        try:
            proc.kill()
        except ProcessLookupError:
            pass


def _decode_capped(raw: bytes, cap: int) -> tuple[str, bool]:
    truncated = len(raw) > cap
    return raw[:cap].decode("utf-8", "replace"), truncated
