"""Agent definitions — the roles `subagent` can delegate to.

Shaped exactly like a preset (`tools`, `max_steps`, `system_prompt`) because it
does the same job: it configures a `ToolRegistry`. The difference is the
audience. A preset is chosen by a person from the UI; an agent is chosen by the
model, which is why its `description` is written as *when to delegate to this*
rather than as a label.

Not cached, for the same reason skills are not: these are small files, and being
able to edit a role and have the next turn use it beats saving a directory scan.
"""
import json
import logging
import re
from pathlib import Path

from core.config import get_settings
from core.errors import NotFoundError
from harness.tools.registry import PROMPT_DIR_KEY

settings = get_settings()
logger = logging.getLogger(__name__)

AGENTS_DIR = Path(__file__).resolve().parent.parent / "data" / "agents"
NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")

CATALOGUE_HEADER = (
    "\n\n## 可用子代理\n\n"
    "遇到步骤多、中间产物杂、结论却很短的子任务（大量检索、逐个文件排查），"
    "用 `subagent` 派给下面的角色，只有结论会回到这里，过程不会占用当前上下文。"
    "一两步能做完的事情自己做，派发本身也要花钱。\n\n"
)


def names() -> list[str]:
    return [path.stem for path in sorted(AGENTS_DIR.glob("*.json"))]


def load_agent(name: str) -> dict:
    """One agent definition, ready to hand to `ToolRegistry(definition=...)`."""
    if not NAME_PATTERN.match(name or ""):
        raise NotFoundError(f"不是合法的子代理名：{name}")
    path = AGENTS_DIR / f"{name}.json"
    try:
        with open(path, "r", encoding="utf-8") as f:
            definition = json.load(f)
    except (OSError, json.JSONDecodeError):
        raise NotFoundError(f"没有名为 {name} 的子代理。可用：{'、'.join(names()) or '（无）'}")

    definition["name"] = name          # the filename is the name, as with skills
    definition[PROMPT_DIR_KEY] = AGENTS_DIR
    return definition


def list_agents() -> list[dict]:
    """Definitions for the plugin panel. No filesystem paths leave this function."""
    out = []
    for name in names():
        try:
            definition = load_agent(name)
        except NotFoundError as e:
            logger.warning("[harness] agent %s unreadable: %s", name, e)
            continue
        out.append({
            "name": name,
            "label": definition.get("label", name),
            "description": definition.get("description", ""),
            "tools": definition.get("tools", []),
            "max_steps": min(
                int(definition.get("max_steps", settings.harness_subagent_max_steps)),
                settings.harness_subagent_max_steps,
            ),
        })
    return out


def catalogue() -> str:
    """The block appended to the main agent's prompt. Empty when there are none."""
    agents = list_agents()
    if not agents:
        return ""
    lines = "\n".join(f"- `{a['name']}` — {a['description']}" for a in agents)
    return CATALOGUE_HEADER + lines + "\n"
