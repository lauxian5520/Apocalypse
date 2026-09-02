"""The tool registry: binds data contracts to code handlers, and guards execution.

Contracts come from `harness/data/tools/*.json`; handlers come from the modules
in `harness/tools/builtin/`. Neither half knows about the other until they are
matched by name here, which is what lets a tool's description or schema change
without touching Python.
"""
import inspect
import json
import logging
from functools import lru_cache
from pathlib import Path

from core.config import get_settings
from core.errors import AppError, NotFoundError, ValidationError
from harness.tools.base import PERMISSIONS, ToolContext, ToolSpec
from harness.tools.builtin import clock, fs, plan, shell, web

settings = get_settings()
logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
TOOLS_DIR = DATA_DIR / "tools"
PRESETS_DIR = DATA_DIR / "presets"

# Contract file -> the module supplying that file's handlers.
_HANDLER_MODULES = {"fs": fs, "shell": shell, "web": web, "plan": plan, "clock": clock}


@lru_cache()
def load_specs() -> dict[str, ToolSpec]:
    """Every tool the build knows about, bound and validated. Cached per process."""
    specs: dict[str, ToolSpec] = {}

    for module_name, module in _HANDLER_MODULES.items():
        contract_file = TOOLS_DIR / f"{module_name}.json"
        try:
            with open(contract_file, "r", encoding="utf-8") as f:
                contracts = json.load(f).get("tools", [])
        except (OSError, json.JSONDecodeError) as e:
            raise ValidationError(f"工具契约文件无法读取（{contract_file.name}）：{e}")

        handlers = getattr(module, "HANDLERS", {})
        for contract in contracts:
            name = contract.get("name", "")
            handler = handlers.get(name)
            # A contract with no handler is a packaging mistake, not a runtime
            # condition — surface it at load time rather than mid-conversation.
            if handler is None:
                raise ValidationError(f"工具 {name!r} 在 {contract_file.name} 中声明，但没有对应实现")
            permission = contract.get("permission", "")
            if permission not in PERMISSIONS:
                raise ValidationError(f"工具 {name!r} 的 permission 非法：{permission!r}")

            specs[name] = ToolSpec(
                name=name,
                description=contract.get("description", ""),
                parameters=contract.get("parameters", {"type": "object", "properties": {}}),
                permission=permission,
                handler=handler,
                module=module_name,
                stops_turn=bool(contract.get("stops_turn", False)),
            )

    return specs


@lru_cache()
def load_preset(name: str) -> dict:
    path = PRESETS_DIR / f"{name}.json"
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        raise NotFoundError(f"运行模式不存在：{name}")


def list_presets() -> list[dict]:
    out = []
    for path in sorted(PRESETS_DIR.glob("*.json")):
        preset = load_preset(path.stem)
        out.append({
            "name": preset["name"],
            "label": preset.get("label", preset["name"]),
            "description": preset.get("description", ""),
            "tools": preset.get("tools", []),
            "max_steps": preset.get("max_steps", settings.harness_max_steps),
        })
    return out


class ToolRegistry:
    """The tools one session actually has, and the pipeline that runs them."""

    def __init__(self, preset_name: str = ""):
        preset = load_preset(preset_name or settings.harness_preset)
        self.preset = preset
        self.max_steps = min(
            int(preset.get("max_steps", settings.harness_max_steps)), settings.harness_max_steps
        )

        available = load_specs()
        self._specs: dict[str, ToolSpec] = {}
        for name in preset.get("tools", []):
            spec = available.get(name)
            if spec is None:
                logger.warning("[harness] preset %r lists unknown tool %r", preset["name"], name)
                continue
            # The shell is opt-in. A preset asking for it does not override the
            # deployment's decision not to have one.
            if spec.module == "shell" and not settings.harness_shell_enabled:
                continue
            self._specs[name] = spec

    def __contains__(self, name: str) -> bool:
        return name in self._specs

    def get(self, name: str) -> ToolSpec:
        spec = self._specs.get(name)
        if spec is None:
            raise NotFoundError(f"未注册的工具：{name}")
        return spec

    def schemas(self) -> list[dict]:
        """The `tools` array for a provider request."""
        return [spec.to_wire() for spec in self._specs.values()]

    def describe(self) -> list[dict]:
        """Registry contents for the UI's plugin panel."""
        return [spec.describe() for spec in self._specs.values()]

    def system_prompt(self) -> str:
        filename = self.preset.get("system_prompt", "system.md")
        try:
            with open(DATA_DIR / "prompts" / filename, "r", encoding="utf-8") as f:
                return f.read().strip()
        except OSError as e:
            raise ValidationError(f"系统提示词无法读取（{filename}）：{e}")

    async def execute(self, name: str, raw_arguments: str, ctx: ToolContext) -> tuple[str, bool]:
        """Run one call. Returns `(text_for_the_model, is_error)`.

        Tool failures are values, not exceptions: the model needs to read what
        went wrong and try something else, so an `AppError` becomes ordinary
        result text instead of tearing down the turn.
        """
        spec = self.get(name)
        try:
            args = parse_arguments(raw_arguments)
            _reject_unknown_arguments(spec, args)
            result = await spec.handler(ctx, **args)
            return str(result), False
        except AppError as e:
            return f"错误：{e.message}", True
        except TypeError as e:
            return f"错误：参数不符合 {name} 的定义（{e}）", True
        except Exception as e:
            logger.exception("[harness] tool %s failed", name)
            return f"错误：{name} 执行失败（{e.__class__.__name__}: {e}）", True


def parse_arguments(raw: str) -> dict:
    """Decode the JSON string a model wrote as a call's arguments."""
    text = (raw or "").strip()
    if not text:
        return {}
    try:
        args = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValidationError(f"参数不是合法 JSON：{e}")
    if not isinstance(args, dict):
        raise ValidationError("参数必须是一个 JSON 对象")
    return args


def _reject_unknown_arguments(spec: ToolSpec, args: dict) -> None:
    """Drop nothing silently: an unexpected argument means a misread contract."""
    accepted = set(inspect.signature(spec.handler).parameters) - {"ctx"}
    unknown = set(args) - accepted
    if unknown:
        raise ValidationError(
            f"{spec.name} 不接受参数 {sorted(unknown)}，可用参数：{sorted(accepted)}"
        )
