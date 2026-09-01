"""Single source of truth for on-disk locations.

All *runtime data* (database, uploads, music, scraper cache) lives under one
`var/` directory outside the source tree, so the code directories stay
read-only and the whole application state can be backed up or mounted as a
single volume.
"""
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
