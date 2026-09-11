"""Skill loading handler.

Thin on purpose: the scanning, validation and access rules all live in
`harness/skills/registry.py`, so the same behaviour applies whether a skill is
reached through this tool or listed by the API.
"""
from harness import skills
from harness.tools.base import ToolContext


async def load_skill(ctx: ToolContext, name: str) -> str:
    body = skills.read_skill(name, ctx.skills)

    # A packaged skill ships scripts, and scripts are only reachable from
    # inside the workspace. Unpack them and tell the model where they landed —
    # otherwise it reads instructions referring to files it cannot open.
    installed = skills.install(name, ctx.workspace, ctx.skills)
    if installed:
        listing = "\n".join(f"- `{path}`" for path in installed)
        body += (
            f"\n\n---\n\n## 本技能的附带文件（已装入当前工作区）\n\n{listing}\n\n"
            "上面这些是**工作区内的相对路径**，可以直接读取或执行，"
            f"例如 `bash(\"python3 {installed[0]}\")`。"
            "SKILL.md 里提到的脚本路径请对应到这里。"
        )
    return body


HANDLERS = {"load_skill": load_skill}
