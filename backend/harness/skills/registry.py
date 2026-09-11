"""Skills — one Markdown file per procedure, loaded on demand.

The system prompt carries only a one-line catalogue; the model calls
`load_skill` when it decides a procedure is relevant. That routing matters for
more than tokens: the body arrives as an ordinary `tool/result`, so it lands in
the event log like everything else the model sees, and a replay reproduces it
exactly. Injecting bodies into the prompt behind the model's back would put
context into a request that the log could not account for.

Nothing here is cached. A skill is content dropped in at runtime, and scanning
a handful of small files once per turn costs nothing next to being able to edit
one and have the next turn use it. Tools cannot work that way — their handlers
are imported at process start.
"""
import logging
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from core.errors import NotFoundError, ValidationError

logger = logging.getLogger(__name__)

SKILLS_DIR = Path(__file__).resolve().parent.parent / "data" / "skills"

MAX_SKILL_BYTES = 32 * 1024          # one file must not be able to flood a context
MAX_DESCRIPTION_CHARS = 200
NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

FENCE = "---"

# A packaged skill is a directory whose entry point is this file, matching the
# layout the published skill packages use (SKILL.md + scripts/ + references/).
MANIFEST = "SKILL.md"

# Where a package is unpacked inside a session's workspace. Dot-prefixed so it
# stays out of the user's file list and out of `glob` results, while still
# being an ordinary workspace path the shell can reach.
INSTALL_DIR = ".skills"

MAX_PACKAGE_BYTES = 8 * 1024 * 1024   # a skill is instructions, not a dataset
# Never copied into a workspace: build noise and version control, not runtime.
SKIP_DIRS = frozenset({"__pycache__", ".git", ".github", "node_modules"})

CATALOGUE_HEADER = (
    "\n\n## 可用技能\n\n"
    "下列技能记录了具体流程。判断某项与当前任务相关时，先用 `load_skill` 把正文读进来再动手，"
    "不要凭名字猜内容。\n\n"
)


@dataclass(frozen=True)
class Skill:
    """One skill's metadata. The body stays on disk until someone asks for it."""

    name: str
    description: str
    keywords: tuple[str, ...]
    path: Path                       # the markdown entry point
    root: Path | None = None         # package directory; None for a lone .md

    @property
    def packaged(self) -> bool:
        return self.root is not None

    def bundled_files(self) -> list[str]:
        """Everything shipped alongside SKILL.md, as package-relative paths."""
        if self.root is None:
            return []
        out = []
        for item in sorted(self.root.rglob("*")):
            if not item.is_file() or item == self.path:
                continue
            rel = item.relative_to(self.root)
            if any(part in SKIP_DIRS or part.startswith(".") for part in rel.parts):
                continue
            out.append(rel.as_posix())
        return out

    def describe(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "keywords": list(self.keywords),
            "packaged": self.packaged,
            "files": self.bundled_files(),
        }


def list_skills(allowed: list[str] | None = None) -> list[Skill]:
    """Every readable skill this session may use, by name."""
    skills, _ = _scan()
    return [s for s in skills.values() if allowed is None or s.name in allowed]


def problems() -> list[dict]:
    """Files that failed to load, for the plugin panel to show in red.

    A malformed skill is skipped rather than raised: unlike a tool contract,
    which is a packaging error inside the repository, a skill is content someone
    dropped in at runtime, and one bad file must not take the whole session down.
    """
    _, issues = _scan()
    return issues


def catalogue(allowed: list[str] | None = None) -> str:
    """The block appended to the system prompt. Empty when there is nothing."""
    skills = list_skills(allowed)
    if not skills:
        return ""
    lines = "\n".join(f"- `{s.name}` — {s.description}" for s in skills)
    return CATALOGUE_HEADER + lines + "\n"


def read_skill(name: str, allowed: list[str] | None = None) -> str:
    """A skill's body.

    The name is looked up in the scanned index and never joined into a path, so
    `../../.env` is not a traversal risk here — it is simply not a key.
    """
    skills, _ = _scan()
    skill = skills.get(name)
    if skill is None or (allowed is not None and name not in allowed):
        known = ", ".join(s.name for s in list_skills(allowed)) or "（当前没有可用技能）"
        raise NotFoundError(f"没有名为 {name} 的技能。可用技能：{known}")

    _, body = _parse(skill.path.read_text(encoding="utf-8", errors="replace"))
    return body


def install(name: str, workspace, allowed: list[str] | None = None) -> list[str]:
    """Unpack a packaged skill into the session workspace. Returns what landed.

    A skill's scripts are useless where they live: the sandbox can only reach
    paths inside the workspace, by design. Copying the package in is what turns
    "here is how to build a deck" into something the shell can actually run,
    without punching a hole in the containment rule that every other tool
    depends on.

    A lone `.md` skill has nothing to install and returns an empty list.
    """
    skills, _ = _scan()
    skill = skills.get(name)
    if skill is None or (allowed is not None and name not in allowed):
        raise NotFoundError(f"没有名为 {name} 的技能")
    if skill.root is None:
        return []

    sources = [(skill.root / rel, rel) for rel in skill.bundled_files()]
    total = sum(src.stat().st_size for src, _ in sources if src.exists())
    if total > MAX_PACKAGE_BYTES:
        raise ValidationError(
            f"技能 {name} 的附带文件共 {total // 1024} KB，超过 "
            f"{MAX_PACKAGE_BYTES // 1024 // 1024} MB 上限"
        )
    workspace.check_quota(total)

    installed: list[str] = []
    for src, rel in sources:
        # Through resolve(), like every other write: the destination is built
        # from a scanned relative path, and it still gets checked.
        target = workspace.resolve(f"{INSTALL_DIR}/{name}/{rel}")
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copy2(src, target)
        installed.append(f"{INSTALL_DIR}/{name}/{rel}")
    return installed


def _entry_points() -> list[tuple[str, Path, Path | None]]:
    """Every skill on disk as `(name, markdown, package_root)`.

    Two shapes are accepted: a lone `<name>.md`, and a `<name>/SKILL.md`
    package that can carry scripts and reference material beside it. The
    packaged form is what the published skill bundles use; without it, dropping
    one in does nothing at all and says nothing about why.
    """
    found: list[tuple[str, Path, Path | None]] = []
    for path in sorted(SKILLS_DIR.glob("*.md")):
        found.append((path.stem, path, None))
    for directory in sorted(p for p in SKILLS_DIR.iterdir() if p.is_dir()):
        if directory.name.startswith("."):
            continue
        manifest = directory / MANIFEST
        if manifest.is_file():
            found.append((directory.name, manifest, directory))
    return found


def _scan() -> tuple[dict[str, Skill], list[dict]]:
    """Read the skills directory. Returns `(by_name, problems)`."""
    skills: dict[str, Skill] = {}
    issues: list[dict] = []

    if not SKILLS_DIR.is_dir():
        return skills, issues

    for name, path, root in _entry_points():
        label = f"{name}/{MANIFEST}" if root else path.name
        # The filename is the name. One fewer field that can disagree with
        # itself, and it makes the catalogue and the directory listing match.
        if not NAME_PATTERN.match(name):
            issues.append({"file": label, "error": "名称不合法（只允许小写字母、数字、- 和 _）"})
            continue
        if name in skills:
            issues.append({"file": label, "error": "与另一个同名技能冲突，已忽略"})
            continue
        try:
            if path.stat().st_size > MAX_SKILL_BYTES:
                issues.append({"file": label, "error": f"{MANIFEST} 超过 {MAX_SKILL_BYTES // 1024} KB 上限"})
                continue
            meta, _ = _parse(path.read_text(encoding="utf-8", errors="replace"))
        except OSError as e:
            issues.append({"file": label, "error": f"无法读取：{e}"})
            continue

        description = meta.get("description", "").strip()
        if not description:
            issues.append({"file": label, "error": "frontmatter 缺少 description"})
            continue

        skills[name] = Skill(
            name=name,
            description=description[:MAX_DESCRIPTION_CHARS],
            keywords=tuple(k for k in _split(meta.get("keywords", "")) if k),
            path=path,
            root=root,
        )

    if issues:
        logger.warning("[harness] %d skill file(s) skipped: %s",
                       len(issues), ", ".join(i["file"] for i in issues))
    return skills, issues


def _parse(text: str) -> tuple[dict, str]:
    """Split `---` frontmatter from the body.

    A deliberately tiny `key: value` reader rather than a YAML dependency: the
    only keys that matter are `description` and `keywords`, and adding a parser
    to the requirements for two strings would be a poor trade.
    """
    lines = text.lstrip("﻿").splitlines()
    if not lines or lines[0].strip() != FENCE:
        return {}, text.strip()

    meta: dict[str, str] = {}
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == FENCE:
            return meta, "\n".join(lines[index + 1:]).strip()
        key, sep, value = line.partition(":")
        if sep and key.strip():
            meta[key.strip().lower()] = value.strip()

    # Unterminated frontmatter: treat the whole file as body rather than
    # silently swallowing it as metadata.
    return {}, text.strip()


def _split(raw: str) -> list[str]:
    return [part.strip() for part in re.split(r"[,，]", raw) if part.strip()]
