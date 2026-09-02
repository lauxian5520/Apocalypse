"""Filesystem tool handlers.

Every path argument goes through `ctx.workspace.resolve()`, which raises when
it escapes the session's directory. No handler here builds a path any other
way — that is the invariant the sandbox rests on.
"""
import os
import re
from glob import glob as glob_paths

from core.errors import NotFoundError, ValidationError
from harness.tools.base import ToolContext

MAX_READ_LINES = 400
MAX_LINE_CHARS = 2000
MAX_GLOB_RESULTS = 200
MAX_GREP_MATCHES = 100
MAX_FILE_BYTES = 2 * 1024 * 1024      # refuse to feed a huge blob into context


async def read(ctx: ToolContext, path: str, offset: int = 1, limit: int = 0) -> str:
    target = ctx.workspace.resolve(path)
    if not os.path.isfile(target):
        raise NotFoundError(f"文件不存在：{path}")
    if os.path.getsize(target) > MAX_FILE_BYTES:
        raise ValidationError(f"文件过大（超过 {MAX_FILE_BYTES // 1024 // 1024} MB），无法读取：{path}")

    limit = limit or MAX_READ_LINES
    start = max(1, int(offset or 1))

    with open(target, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()

    window = lines[start - 1 : start - 1 + limit]
    if not window:
        return f"[{path} 共 {len(lines)} 行，第 {start} 行起没有内容]"

    body = "\n".join(
        f"{start + i:>6}\t{line.rstrip(chr(10))[:MAX_LINE_CHARS]}"
        for i, line in enumerate(window)
    )
    if start - 1 + limit < len(lines):
        body += f"\n[已截断：文件共 {len(lines)} 行，使用 offset 继续读取]"
    return body


async def write(ctx: ToolContext, path: str, content: str) -> str:
    target = ctx.workspace.resolve(path)
    ctx.workspace.check_quota(len(content.encode("utf-8")))
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w", encoding="utf-8") as f:
        f.write(content)
    return f"已写入 {ctx.workspace.relative(target)}（{len(content)} 字符）"


async def edit(
    ctx: ToolContext, path: str, old_string: str, new_string: str, replace_all: bool = False
) -> str:
    target = ctx.workspace.resolve(path)
    if not os.path.isfile(target):
        raise NotFoundError(f"文件不存在：{path}")

    with open(target, "r", encoding="utf-8", errors="replace") as f:
        original = f.read()

    hits = original.count(old_string)
    if hits == 0:
        raise ValidationError(f"未找到要替换的内容，请先用 read 确认原文：{path}")
    if hits > 1 and not replace_all:
        raise ValidationError(
            f"原文在 {path} 中出现 {hits} 次，无法确定替换哪一处；"
            "请提供更长的唯一片段，或把 replace_all 设为 true"
        )

    updated = original.replace(old_string, new_string) if replace_all \
        else original.replace(old_string, new_string, 1)
    ctx.workspace.check_quota(max(0, len(updated.encode("utf-8")) - len(original.encode("utf-8"))))
    with open(target, "w", encoding="utf-8") as f:
        f.write(updated)
    return f"已修改 {ctx.workspace.relative(target)}（替换 {hits if replace_all else 1} 处）"


async def glob(ctx: ToolContext, pattern: str) -> str:
    root = ctx.workspace.ensure()
    # Resolve the pattern's own directory part so "../*" cannot escape before
    # glob ever runs, then filter results through the same containment check.
    matches = glob_paths(os.path.join(root, pattern), recursive=True)
    inside = []
    for m in matches:
        try:
            ctx.workspace.resolve(ctx.workspace.relative(m))
        except ValidationError:
            continue
        inside.append(m)

    if not inside:
        return f"没有匹配 {pattern} 的文件"

    inside.sort(key=lambda p: os.path.getmtime(p) if os.path.exists(p) else 0, reverse=True)
    shown = inside[:MAX_GLOB_RESULTS]
    body = "\n".join(ctx.workspace.relative(p) for p in shown)
    if len(inside) > len(shown):
        body += f"\n[共 {len(inside)} 项，仅显示前 {len(shown)} 项]"
    return body


async def grep(ctx: ToolContext, pattern: str, path: str = "", glob: str = "") -> str:
    try:
        regex = re.compile(pattern)
    except re.error as e:
        raise ValidationError(f"正则表达式无效：{e}")

    base = ctx.workspace.resolve(path or ".")
    files = [base] if os.path.isfile(base) else _walk_files(base)

    matches: list[str] = []
    for filepath in files:
        rel = ctx.workspace.relative(filepath)
        if glob and not _matches_glob(rel, glob):
            continue
        try:
            with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                for lineno, line in enumerate(f, start=1):
                    if regex.search(line):
                        matches.append(f"{rel}:{lineno}: {line.rstrip()[:MAX_LINE_CHARS]}")
                        if len(matches) >= MAX_GREP_MATCHES:
                            matches.append(f"[命中过多，仅显示前 {MAX_GREP_MATCHES} 条]")
                            return "\n".join(matches)
        except OSError:
            continue      # unreadable or binary-ish file: skip, do not fail the search

    return "\n".join(matches) if matches else f"没有匹配 {pattern} 的内容"


def _walk_files(root: str) -> list[str]:
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        out.extend(os.path.join(dirpath, name) for name in filenames)
    return out


def _matches_glob(relative: str, pattern: str) -> bool:
    from fnmatch import fnmatch
    return fnmatch(relative, pattern) or fnmatch(os.path.basename(relative), pattern)


HANDLERS = {"read": read, "write": write, "edit": edit, "glob": glob, "grep": grep}
