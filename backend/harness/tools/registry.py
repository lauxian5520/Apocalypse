"""The tool registry: binds data contracts to code handlers, and guards execution.

Contracts come from `harness/data/tools/*.json`; handlers come from the modules
in `harness/tools/builtin/`. Neither half knows about the other until they are
matched by name here, which is what lets a tool's description or schema change
without touching Python.

Discovery is contract-driven: the JSON files decide which modules exist. A
handler module nobody wrote a contract for contributes nothing to a request, so
the contract is the half that counts as the declaration.
"""
import inspect
import json
import logging
from functools import lru_cache
from importlib import import_module
from pathlib import Path
from types import ModuleType

from core.config import get_settings
from core.errors import AppError, NotFoundError, ValidationError
from harness.tools.base import PERMISSIONS, ToolContext, ToolSpec

settings = get_settings()
logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
TOOLS_DIR = DATA_DIR / "tools"
PROMPTS_DIR = DATA_DIR / "prompts"
PRESETS_DIR = DATA_DIR / "presets"
BUILTIN_DIR = Path(__file__).resolve().parent / "builtin"

# Modules a deployment can switch off wholesale, and the setting that decides.
# Anything gated here disappears from every preset at once, so a preset asking
# for it never overrides the deployment's answer.
_MODULE_GATES = {
    "shell": "harness_shell_enabled",
    "subagent": "harness_subagent_enabled",
    "corpus": "harness_corpus_enabled",
}

# Loader-internal key: where this definition's system prompt file lives.
# Prefixed so it is never mistaken for part of the data contract.
PROMPT_DIR_KEY = "_prompt_dir"


def _discover() -> list[tuple[str, ModuleType]]:
    """Every `(module_name, module)` pair the build offers, in a stable order.

    Sorted deliberately. The resulting order reaches the provider as the request's
    `tools` array, and prefix caching only pays off while that prefix is byte
    identical — letting it follow directory order would throw the cache away on
    a whim of the filesystem.
    """
    found: list[tuple[str, ModuleType]] = []
    for path in sorted(TOOLS_DIR.glob("*.json")):
        name = path.stem
        if name.startswith("_"):
            continue
        try:
            found.append((name, import_module(f"harness.tools.builtin.{name}")))
        except ImportError as e:
            raise ValidationError(
                f"工具契约 {path.name} 没有对应实现 harness/tools/builtin/{name}.py：{e}"
            )

    _warn_about_orphan_modules({name for name, _ in found})
    return found


def _warn_about_orphan_modules(declared: set[str]) -> None:
    """A handler module with no contract is dead code — say so at startup.

    Silence here is the confusing case: the file is on disk, the tool never
    appears, and nothing explains why.
    """
    for path in sorted(BUILTIN_DIR.glob("*.py")):
        if path.stem.startswith("_") or path.stem in declared:
            continue
        logger.info(
            "[harness] %s has no contract in data/tools/%s.json and is not loaded",
            path.name, path.stem,
        )


@lru_cache()
def load_specs() -> dict[str, ToolSpec]:
    """Every tool the build knows about, bound and validated. Cached per process."""
    specs: dict[str, ToolSpec] = {}

    for module_name, module in _discover():
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


def expand_tools(names: list, available: dict[str, ToolSpec]) -> list[str]:
    """Resolve a definition's `tools` list, honouring `*` and `module:*`.

    Order follows the definition, then discovery order inside a wildcard, so the
    request's `tools` array stays byte-stable across restarts.
    """
    out: list[str] = []
    seen: set[str] = set()

    def take(name: str) -> None:
        if name not in seen:
            seen.add(name)
            out.append(name)

    for entry in names:
        entry = str(entry)
        if entry == "*":
            for name in available:
                take(name)
        elif entry.endswith(":*"):
            module = entry[:-2]
            matched = [n for n, s in available.items() if s.module == module]
            if not matched:
                logger.warning("[harness] no tools found for module wildcard %r", entry)
            for name in matched:
                take(name)
        else:
            take(entry)

    return out


@lru_cache()
def load_preset(name: str) -> dict:
    path = PRESETS_DIR / f"{name}.json"
    try:
        with open(path, "r", encoding="utf-8") as f:
            preset = json.load(f)
    except (OSError, json.JSONDecodeError):
        raise NotFoundError(f"运行模式不存在：{name}")
    preset[PROMPT_DIR_KEY] = PROMPTS_DIR
    return preset


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
    """The tools one session actually has, and the pipeline that runs them.

    Built either from a named preset (a person picked it) or from a definition
    dict already in hand (an agent definition, chosen by the model). Both shapes
    carry the same keys, so there is one code path.
    """

    def __init__(self, name: str = "", *, definition: dict | None = None):
        definition = definition if definition is not None else \
            load_preset(name or settings.harness_preset)
        self.definition = definition
        self.max_steps = min(
            int(definition.get("max_steps", settings.harness_max_steps)), settings.harness_max_steps
        )

        available = load_specs()
        self._specs: dict[str, ToolSpec] = {}
        for tool_name in expand_tools(definition.get("tools", []), available):
            spec = available.get(tool_name)
            if spec is None:
                logger.warning(
                    "[harness] %r lists unknown tool %r", definition.get("name", "?"), tool_name
                )
                continue
            if _gated_off(spec.module):
                continue
            self._specs[tool_name] = spec

    @property
    def name(self) -> str:
        return self.definition.get("name", "")

    @property
    def allowed_skills(self) -> list[str] | None:
        """Skill names this definition exposes. `None` means every skill."""
        allowed = self.definition.get("skills")
        return None if allowed is None else [str(s) for s in allowed]

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
        filename = self.definition.get("system_prompt", "system.md")
        directory = self.definition.get(PROMPT_DIR_KEY, PROMPTS_DIR)
        try:
            with open(Path(directory) / filename, "r", encoding="utf-8") as f:
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


def _gated_off(module: str) -> bool:
    setting = _MODULE_GATES.get(module)
    return setting is not None and not getattr(settings, setting)


def parse_arguments(raw: str) -> dict:
    """Decode the JSON string a model wrote as a call's arguments.

    Two different failures arrive here and they need different answers.

    A model writing a file routinely puts real newlines and tabs inside the
    JSON string instead of escaping them — `write` is where this shows up,
    because file content is mostly newlines. That is invalid JSON by the
    letter of the spec and Python rejects it, but it is unambiguous, so parse
    it again with `strict=False` rather than failing the call over a quoting
    detail the model cannot see in its own output.

    Truncation is the other one and it is not recoverable here: the output
    budget ended the stream mid-arguments, so the string never closes.
    Reporting that as "not valid JSON" sends the model straight back to
    rewriting the same too-long file and it is cut off again. Name the cause
    and say what would actually work.
    """
    text = (raw or "").strip()
    if not text:
        return {}
    try:
        args = json.loads(text)
    except json.JSONDecodeError:
        try:
            args = json.loads(text, strict=False)   # literal newlines and tabs
        except json.JSONDecodeError as e:
            raise ValidationError(_argument_error(text, e))
    if not isinstance(args, dict):
        raise ValidationError("参数必须是一个 JSON 对象")
    return args


def _argument_error(text: str, e: json.JSONDecodeError) -> str:
    """Tell a cut-off call apart from a genuinely malformed one.

    A string that never closes, or a decode that runs off the end of the
    input, means the arguments stopped arriving. A syntax error anywhere
    before the end is the model's mistake, not the budget's.
    """
    if e.msg.startswith("Unterminated string") or e.pos >= len(text.rstrip()):
        return (
            f"参数在第 {len(text)} 个字符处戛然而止（{e.msg}），说明这次调用的输出"
            "超过了单次上限、被截断了，不是写错了格式。请不要原样重试："
            "把内容拆成几次较小的调用（先写一部分，再用 edit 逐段追加）。"
        )
    return f"参数不是合法 JSON：{e}"


def _reject_unknown_arguments(spec: ToolSpec, args: dict) -> None:
    """Drop nothing silently: an unexpected argument means a misread contract."""
    accepted = set(inspect.signature(spec.handler).parameters) - {"ctx"}
    unknown = set(args) - accepted
    if unknown:
        raise ValidationError(
            f"{spec.name} 不接受参数 {sorted(unknown)}，可用参数：{sorted(accepted)}"
        )
