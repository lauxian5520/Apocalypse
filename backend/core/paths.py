"""Single source of truth for on-disk locations.

All *runtime data* (database, uploads, music, scraper cache) lives under one
`var/` directory outside the source tree, so the code directories stay
read-only and the whole application state can be backed up or mounted as a
single volume.
"""
import os
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent      # <repo>/backend
PROJECT_ROOT = BACKEND_DIR.parent                          # <repo>
DEFAULT_VAR_DIR = PROJECT_ROOT / "var"


def resolve(value: str, base: Path = PROJECT_ROOT) -> str:
    """Resolve a possibly-relative path against `base` (never against the cwd).

    Interpreting configured paths relative to the current working directory
    means starting the server from a different folder silently creates a second,
    empty database and orphans every upload.
    """
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base / path
    return str(path)


def contained_path(root: str, relative: str) -> str | None:
    """Resolve `relative` inside `root`, or None when it escapes.

    The single containment check in this codebase: uploads serve from it and
    the harness sandbox jails its tools with it. Symlinks are resolved before
    the comparison, so a link pointing outside `root` is rejected too.

    It lives here rather than beside the upload helpers that first needed it
    because `harness/` depends on it, and reaching into `services/` dragged
    `fastapi` in behind it — breaking both the layering rule and the promise in
    `harness/__init__.py` that nothing below that package imports the web
    framework. It is pure `os.path`, so `core/` is where it belongs.
    """
    safe = os.path.normpath(relative).lstrip("/\\")
    real_root = os.path.realpath(root)
    target = os.path.realpath(os.path.join(real_root, safe))
    # Compare on a path-component boundary: a bare startswith() would also
    # accept a sibling directory such as "<uploads>_backup".
    if target != real_root and not target.startswith(real_root + os.sep):
        return None
    return target
