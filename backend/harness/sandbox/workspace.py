"""Per-session workspace: the only directory a session's tools can see.

Containment reuses `services.storage_service.contained_path`, the same check
that guards public uploads — one implementation, so a fix to the path logic
protects both surfaces.
"""
import logging
import os
import shutil

from core.config import get_settings
from core.errors import ValidationError
from services.storage_service import contained_path

settings = get_settings()
logger = logging.getLogger(__name__)


class Workspace:
    """A session's private directory under `var/harness/workspaces/`."""

    def __init__(self, session_id: str):
        self.session_id = session_id
        self._root = os.path.join(settings.harness_workspace_dir, session_id)

    @property
    def root(self) -> str:
        return self._root

    def ensure(self) -> str:
        os.makedirs(self._root, exist_ok=True)
        return self._root

    def resolve(self, relative: str) -> str:
        """Absolute path inside the workspace, or `ValidationError`.

        This is the boundary the whole sandbox rests on: every file tool goes
        through it, and `..`, absolute paths and symlinks out are all rejected.
        """
        path = relative or "."
        # `contained_path` strips a leading slash, which is right for serving a
        # URL but wrong here: it would quietly turn "/etc/passwd" into
        # "<workspace>/etc/passwd" and report success for a path the model
        # never asked for. Say no instead, so the model corrects itself.
        if os.path.isabs(path):
            raise ValidationError(f"请使用相对工作区的路径，不接受绝对路径：{relative}")

        target = contained_path(self.ensure(), path)
        if target is None:
            raise ValidationError(f"路径越界，工作区之外不可访问：{relative}")
        return target

    def relative(self, absolute: str) -> str:
        """Path as the model should see it — workspace-relative, never the host's."""
        try:
            return os.path.relpath(absolute, os.path.realpath(self._root))
        except ValueError:
            return absolute

    def usage_bytes(self) -> int:
        total = 0
        for dirpath, _, filenames in os.walk(self._root):
            for name in filenames:
                try:
                    total += os.path.getsize(os.path.join(dirpath, name))
                except OSError:
                    continue
        return total

    def check_quota(self, incoming_bytes: int = 0) -> None:
        limit = settings.harness_workspace_quota_mb * 1024 * 1024
        if self.usage_bytes() + incoming_bytes > limit:
            raise ValidationError(
                f"工作区超出配额（上限 {settings.harness_workspace_quota_mb} MB），请先清理文件"
            )

    def destroy(self) -> None:
        """Remove the workspace. Safe to call when it was never created."""
        if not os.path.isdir(self._root):
            return
        # Refuse to delete anything that is not actually under the workspace
        # root — a corrupted session id must not turn into an rm -rf elsewhere.
        if contained_path(settings.harness_workspace_dir, self.session_id) is None:
            logger.error("[harness] refusing to destroy suspicious workspace %r", self.session_id)
            return
        shutil.rmtree(self._root, ignore_errors=True)
