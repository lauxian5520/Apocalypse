"""Check every link in the Harness chain, one stage at a time.

    cd backend && python ../tools/harness_check.py                 # 全链路（含真实 API 调用）
    cd backend && python ../tools/harness_check.py --offline       # 只查本地接线，不花 token
    cd backend && python ../tools/harness_check.py --url http://localhost:8000 --token <JWT>

Each stage is independent: one failure does not stop the rest, so a single run
tells you exactly which link is broken instead of only the first one.
"""
import argparse
import asyncio
import inspect
import io
import json
import os
import sys
import traceback
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import httpx  # noqa: E402

from core.config import get_settings  # noqa: E402
from core.database import Base, SessionLocal, engine  # noqa: E402
from core.errors import NotFoundError, ValidationError  # noqa: E402
from core.providers import provider_config  # noqa: E402
import models  # noqa: E402,F401  (registers the mappers)
from harness import agents, build_context, events as ev, run_turn, skills  # noqa: E402
from harness.context import build_subagent_context, compose_prompt  # noqa: E402
from harness.llm.pricing import estimate_cost  # noqa: E402
from harness.llm.registry import build_adapter  # noqa: E402
from harness.sandbox.local import LocalSandbox  # noqa: E402
from harness.sandbox.workspace import Workspace  # noqa: E402
from harness.session import manager  # noqa: E402
from harness.session.projection import derive_messages, logged_system_prompt  # noqa: E402
from harness.session.sqlite_store import SqliteSessionStore  # noqa: E402
from harness.tools.approval import (  # noqa: E402
    ALLOW, ASK, DENY, ApprovalPolicy, StrictApprovalPolicy)
from harness.tools.base import ToolContext, ToolSpec  # noqa: E402
from harness.tools.registry import ToolRegistry, list_presets, load_specs  # noqa: E402
from models.harness import HarnessSession  # noqa: E402
from models.user import User  # noqa: E402

settings = get_settings()

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"
_results: list[tuple[str, str, str]] = []


def _pad(text: str, width: int) -> str:
    """Left-justify by display width — CJK glyphs occupy two columns."""
    import unicodedata
    shown = sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)
    return text + " " * max(0, width - shown)


def record(stage: str, status: str, detail: str = "") -> None:
    mark = {PASS: "✓", FAIL: "✗", SKIP: "–"}[status]
    print(f"  {mark} {_pad(stage, 26)}  {detail}")
    _results.append((stage, status, detail))


async def stage(name: str, fn) -> bool:
    """Run one check, turning any exception into a FAIL line.

    Awaits whatever `fn` produces rather than inspecting `fn` itself: a lambda
    wrapping an async call is not a coroutine function, and testing the
    function reported such a stage as passing without ever running it.
    """
    try:
        detail = fn()
        if inspect.isawaitable(detail):
            detail = await detail
        record(name, PASS, detail or "")
        return True
    except Exception as e:
        record(name, FAIL, f"{e.__class__.__name__}: {e}")
        if "--trace" in sys.argv:
            traceback.print_exc()
        return False


# ── local wiring ─────────────────────────────────────────────────

def check_config() -> str:
    cfg = provider_config()
    if not cfg["url"]:
        raise RuntimeError(f"provider={settings.ai_provider} 缺少 API URL")
    key = "已配置" if cfg["key"] else "缺失"
    return f"{settings.ai_provider}/{cfg['model']} · key {key} · var={settings.var_dir}"


def check_database() -> str:
    Base.metadata.create_all(bind=engine)
    from sqlalchemy import inspect
    tables = set(inspect(engine).get_table_names())
    missing = {"harness_sessions", "harness_events"} - tables
    if missing:
        raise RuntimeError(f"缺少数据表：{sorted(missing)}")
    return f"{settings.db_type} · harness_sessions / harness_events 就绪"


def check_sandbox() -> str:
    workspace = Workspace(f"__check__{uuid.uuid4().hex[:8]}")
    try:
        workspace.ensure()
        target = workspace.resolve("probe/a.txt")
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        Path(target).write_text("ok", encoding="utf-8")
        if Path(target).read_text(encoding="utf-8") != "ok":
            raise RuntimeError("工作区读写不一致")

        # Containment is the whole point; prove both escapes are refused.
        for bad in ("../../etc/passwd", "/etc/passwd"):
            try:
                workspace.resolve(bad)
            except Exception:
                continue
            raise RuntimeError(f"路径收敛失效，{bad} 未被拒绝")
        return f"读写正常 · 越界与绝对路径均被拒绝 · 配额 {settings.harness_workspace_quota_mb}MB"
    finally:
        workspace.destroy()


def check_registry() -> str:
    specs = load_specs()               # binds data/tools/*.json to the handlers
    registry = ToolRegistry(settings.harness_preset)
    prompt = registry.system_prompt()
    if not prompt:
        raise RuntimeError("系统提示词为空")
    presets = [p["name"] for p in list_presets()]
    shell = "开" if settings.harness_shell_enabled else "关"
    return (f"契约绑定 {len(specs)} 个 · 本模式启用 {len(registry.describe())} 个 · "
            f"预设 {presets} · shell {shell}")


def check_discovery() -> str:
    """Tools must come from the contract files alone, in a stable order."""
    import harness.tools.registry as reg

    contracts = {p.stem for p in reg.TOOLS_DIR.glob("*.json") if not p.stem.startswith("_")}
    modules = {spec.module for spec in load_specs().values()}
    if contracts != modules:
        raise RuntimeError(f"契约 {sorted(contracts)} 与已加载模块 {sorted(modules)} 不一致")

    # The tools array reaches the provider in this order and prefix caching only
    # pays while it is byte-identical, so a rebuild must reproduce it exactly.
    first = [t["function"]["name"] for t in ToolRegistry("standard").schemas()]
    reg.load_specs.cache_clear()
    second = [t["function"]["name"] for t in ToolRegistry("standard").schemas()]
    if first != second:
        raise RuntimeError("两次发现得到的工具顺序不同，前缀缓存会失效")

    # A contract with no module is a packaging error and must not load quietly.
    ghost = reg.TOOLS_DIR / "_check_ghost.json"
    ghost.write_text('{"tools": []}', encoding="utf-8")
    orphan = reg.TOOLS_DIR / "check_ghost.json"
    orphan.write_text('{"tools": []}', encoding="utf-8")
    try:
        reg.load_specs.cache_clear()
        try:
            load_specs()
            raise RuntimeError("缺少实现的契约被静默接受了")
        except ValidationError:
            pass
    finally:
        ghost.unlink(missing_ok=True)
        orphan.unlink(missing_ok=True)
        reg.load_specs.cache_clear()

    dropped_in = [n for n in ("load_skill", "subagent") if n in load_specs()]
    return (f"契约驱动 {len(contracts)} 个模块 · 顺序稳定 · 孤儿契约被拒 · "
            f"零注册代码接入 {dropped_in}")


def check_skills() -> str:
    from harness import skills

    names = [s.name for s in skills.list_skills()]
    if not names:
        raise RuntimeError("没有发现任何技能")
    catalogue = skills.catalogue()
    for name in names:
        if name not in catalogue:
            raise RuntimeError(f"技能 {name} 不在目录清单里")
    if not skills.read_skill(names[0]).strip():
        raise RuntimeError(f"技能 {names[0]} 正文为空")

    # A name is looked up in the scanned index, never joined into a path.
    for bad in ("../../.env", "nope", f"{names[0]}/../../x"):
        try:
            skills.read_skill(bad)
            raise RuntimeError(f"{bad!r} 竟然读到了内容")
        except NotFoundError:
            pass
    try:
        skills.read_skill(names[0], allowed=[])
        raise RuntimeError("预设的技能白名单没有生效")
    except NotFoundError:
        pass

    # One malformed file must be skipped, not fatal.
    broken = skills.SKILLS_DIR / "_check_broken.md"
    broken.write_text("没有 frontmatter\n", encoding="utf-8")
    try:
        problems = skills.problems()
        if not any(p["file"] == broken.name for p in problems):
            raise RuntimeError("坏掉的技能文件没有被报告")
        if [s.name for s in skills.list_skills()] != names:
            raise RuntimeError("一个坏文件影响了其他技能")
    finally:
        broken.unlink(missing_ok=True)

    return f"技能 {names} · 清单已注入 · 越权与穿越均被拒 · 坏文件只跳过不致命"


async def check_artifacts() -> str:
    """Files the agent produces, and the path that gets them back out."""
    import zipfile

    # A packaged skill must be discovered, and its scripts must land somewhere
    # the sandbox can actually reach.
    packaged = [sk for sk in skills.list_skills() if sk.packaged]
    workspace = Workspace(f"check-artifacts-{uuid.uuid4().hex[:8]}")
    workspace.ensure()
    try:
        installed = []
        if packaged:
            installed = skills.install(packaged[0].name, workspace)
            if not installed:
                raise RuntimeError(f"技能包 {packaged[0].name} 没有装出任何文件")
            for rel in installed:
                if not os.path.isfile(workspace.resolve(rel)):
                    raise RuntimeError(f"{rel} 声称已装入但不存在")
                if not rel.startswith(skills.INSTALL_DIR + "/"):
                    raise RuntimeError(f"{rel} 装到了约定目录之外")

        # The download route resolves through the same containment check every
        # tool uses; prove it still refuses the classics.
        with open(workspace.resolve("artifact.bin"), "wb") as f:
            f.write(b"\x00binary\xff" * 64)
        for bad in ("../../../etc/passwd", "/etc/passwd", "..", "a/../../../x"):
            try:
                workspace.resolve(bad)
            except ValidationError:
                continue
            raise RuntimeError(f"下载路径 {bad!r} 没有被拒绝")

        # And that a zip of the workspace is actually readable.
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for dirpath, _, filenames in os.walk(workspace.root):
                for name in filenames:
                    full = os.path.join(dirpath, name)
                    archive.write(full, workspace.relative(full))
        check = zipfile.ZipFile(io.BytesIO(buffer.getvalue()))
        if check.testzip() is not None:
            raise RuntimeError("打包出的 zip 损坏")
        entries = len(check.namelist())
    finally:
        workspace.destroy()

    names = [sk.name for sk in packaged] or ["（无）"]
    return (f"技能包 {names} 可装入工作区（{len(installed)} 个文件）· "
            f"越界路径被拒 · zip 可读（{entries} 项）")


async def check_subagent() -> str:
    """Everything about delegation that can be checked without spending tokens."""
    names = agents.names()
    if not names:
        raise RuntimeError("没有发现任何子代理定义")
    for name in names:
        registry = ToolRegistry(definition=agents.load_agent(name))
        if "subagent" in registry:
            raise RuntimeError(f"子代理 {name} 自己也能派发子代理，会无限递归")
        if registry.max_steps > settings.harness_subagent_max_steps:
            raise RuntimeError(f"子代理 {name} 的步数超过全局上限")

    parent = ToolRegistry("standard")
    if settings.harness_subagent_enabled and "subagent" not in parent:
        raise RuntimeError("已启用子代理，但标准模式里没有这个工具")
    if "可用子代理" not in compose_prompt(parent):
        raise RuntimeError("主提示词里没有子代理清单")

    workspace = Workspace(f"check-sub-{uuid.uuid4().hex[:8]}")
    try:
        child = build_subagent_context(
            "check-child", names[0], workspace, LocalSandbox(workspace), depth=0
        )
        if child.depth != 1:
            raise RuntimeError("子上下文的 depth 没有加一")
        if child.workspace is not workspace:
            raise RuntimeError("子代理没有共用父会话的工作区")
        if "可用子代理" in child.system_prompt:
            raise RuntimeError("子代理被告知了它无法使用的派发能力")

        # ask -> deny inside a child, because nobody can answer down there.
        strict = StrictApprovalPolicy()
        fake = ToolSpec(name="bash", description="", parameters={},
                        permission="exec", handler=lambda ctx: None)
        if strict.decide(fake, {"command": "curl http://x"}).verdict != DENY:
            raise RuntimeError("子代理里需要审批的命令没有被拒绝")

        # The spend guards, through the real execution path rather than by
        # reading the source: a guard that is never reached is not a guard.
        sandbox = LocalSandbox(workspace)
        rejected = {
            "深度": (ToolContext("check", sandbox, workspace,
                               depth=settings.harness_subagent_max_depth),
                    {"task": "x", "agent": names[0]}),
            "空任务": (ToolContext("check", sandbox, workspace), {"task": " ", "agent": names[0]}),
            "未知角色": (ToolContext("check", sandbox, workspace),
                       {"task": "x", "agent": "definitely-not-an-agent"}),
        }
        for label, (ctx, args) in rejected.items():
            text, is_error = await parent.execute("subagent", json.dumps(args), ctx)
            if not is_error:
                raise RuntimeError(f"{label}这一关没有拦住：{text}")
    finally:
        workspace.destroy()

    return (f"子代理 {names} · 不可递归 · 共用工作区 · depth 递增 · "
            f"严格策略把 ask 变 deny · 上限 {settings.harness_subagent_max_per_session} 次/会话")


def check_approval() -> str:
    policy, registry = ApprovalPolicy(), ToolRegistry("standard")
    if "bash" not in registry:
        return "shell 未启用，跳过命令判定（策略本身已加载）"

    spec = registry.get("bash")
    expected = {"ls -la": ALLOW, "curl http://x": ASK, "sudo rm -rf /": DENY}
    for command, want in expected.items():
        got = policy.decide(spec, {"command": command}).verdict
        if got != want:
            raise RuntimeError(f"{command!r} 判定为 {got}，应为 {want}")
    return "放行 / 询问 / 拒绝 三类判定均正确"


def check_log_projection() -> str:
    """Write a synthetic log and fold it back — the traceability claim, offline."""
    with SessionLocal() as db:
        user = db.query(User).order_by(User.id).first()
        if user is None:
            raise RuntimeError("数据库里还没有用户，请先注册一个账号")
        user_id = user.id

    session_id = uuid.uuid4().hex
    with SessionLocal() as db:
        db.add(HarnessSession(id=session_id, user_id=user_id, preset="standard"))
        db.commit()

    try:
        store = SqliteSessionStore()
        store.append(session_id, ev.USER_MESSAGE, {"content": "连通性自检"})
        store.append(session_id, ev.ASSISTANT_MESSAGE, {"content": "", "tool_calls": [
            {"id": "probe", "type": "function",
             "function": {"name": "read", "arguments": '{"path":"x"}'}}]})
        store.append(session_id, ev.TOOL_RESULT,
                     {"tool_call_id": "probe", "name": "read", "content": "内容"})

        messages = derive_messages(store.read(session_id), "SYS")
        roles = [m["role"] for m in messages]
        if roles != ["system", "user", "assistant", "tool"]:
            raise RuntimeError(f"投影结果异常：{roles}")
        if messages[3]["tool_call_id"] != "probe":
            raise RuntimeError("工具结果没有和调用配对")
        return f"3 条事件 → {len(messages)} 条消息 {roles}"
    finally:
        with SessionLocal() as db:
            row = db.get(HarnessSession, session_id)
            if row:
                db.delete(row)
                db.commit()


# ── live provider ────────────────────────────────────────────────

async def check_model() -> str:
    # The budget is deliberately generous: a thinking model spends output
    # tokens on reasoning before writing anything, and a tight cap comes back
    # empty. An earlier version of this check used 20 and reported the empty
    # reply as a pass.
    result = await build_adapter().complete(
        [{"role": "user", "content": "只回复两个字：连通"}], max_tokens=512)
    reply = result.content.strip()
    if not reply:
        raise RuntimeError(f"模型没有返回正文（finish={result.finish_reason}）")

    usage, cost = result.usage, estimate_cost(result.usage)
    price = f"${cost:.6f}" if cost is not None else "价格表未收录（改 data/pricing.json）"
    thinking = "（含推理）" if result.reasoning else ""
    return (f"回复 {reply[:20]!r}{thinking} · "
            f"in {usage.prompt_tokens} / out {usage.completion_tokens} · {price}")


async def check_tool_calling() -> str:
    """The one thing plain chat never exercises: streamed tool_calls assembly."""
    registry = ToolRegistry("standard")
    adapter = build_adapter()
    messages = [
        {"role": "system", "content": "你可以使用工具。需要写文件时必须调用 write 工具。"},
        {"role": "user", "content": "把 hello 写进 note.txt"},
    ]

    result = None
    async for delta in adapter.stream(messages, tools=registry.schemas()):
        if delta.result is not None:
            result = delta.result

    if result is None:
        raise RuntimeError("流式响应没有给出最终结果")
    if not result.tool_calls:
        raise RuntimeError(f"模型没有发起工具调用（finish={result.finish_reason}）")

    call = result.tool_calls[0]
    args = json.loads(call.arguments)      # proves the fragments reassembled into valid JSON
    return f"{call.name}({', '.join(args)}) · id={call.id} · 参数 JSON 完整"


async def check_full_turn() -> str:
    with SessionLocal() as db:
        user = db.query(User).order_by(User.id).first()
        if user is None:
            raise RuntimeError("数据库里还没有用户，请先注册一个账号")
        user_id = user.id

    session = manager.create(user_id, settings.harness_preset, title="连通性自检")
    try:
        hctx = build_context(session.id, settings.harness_preset)
        log, types, tools_used = [], [], []
        async for event in run_turn(hctx, "把当前时间写进 probe.txt，然后读回来确认。不要联网。"):
            log.append(event)
            types.append(event.type)
            if event.type == ev.TOOL_RESULT:
                # A rejected or failed call still produces a result event;
                # listing it as "used" made a disabled tool look available.
                tools_used.append(
                    (event.data["name"], not event.data.get("is_error")))

        if ev.TURN_END not in types:
            raise RuntimeError(f"这一轮没有正常收尾：{types[-1] if types else '无事件'}")
        if ev.AGENT_ERROR in types and ev.TOOL_RESULT not in types:
            raise RuntimeError("整轮没有成功调用任何工具")

        # The system prompt must be in the log, not recomputed at read time:
        # it embeds today's date, so replaying an old session without a
        # snapshot would show a date the model never saw.
        snapshot = logged_system_prompt(log)
        if snapshot is None:
            raise RuntimeError("日志里没有系统提示词快照，历史将无法忠实重放")
        if derive_messages(log, "SENTINEL")[0]["content"] != snapshot:
            raise RuntimeError("投影没有采用日志中的系统提示词快照")

        # Chunks must reassemble into the message they built, per step —
        # the property that makes the trajectory view a faithful replay.
        pending = []
        for event in log:
            if event.type == ev.ASSISTANT_CHUNK:
                pending.append(event.data.get("delta", ""))
            elif event.type == ev.ASSISTANT_MESSAGE:
                if "".join(pending) != (event.data.get("content") or ""):
                    raise RuntimeError("流式片段与最终消息不一致，日志无法忠实重放")
                pending = []

        files = sorted(p.name for p in Path(hctx.workspace.root).rglob("*") if p.is_file())
        usage = manager.usage_summary(session.id)
        chunks = sum(1 for t in types if t == ev.ASSISTANT_CHUNK)
        snapshots = sum(1 for t in types if t == ev.CONFIG_CHANGE)
        ok_tools = [n for n, ok in tools_used if ok]
        failed = [n for n, ok in tools_used if not ok]
        detail = f"成功 {ok_tools or '无'}" + (f" · 失败 {failed}" if failed else "")
        return (f"{len(types)} 个事件（{chunks} 个流式片段 + {snapshots} 份提示词快照，可完整重放）· "
                f"{detail} · 文件 {files or '无'} · {usage['total_tokens']} tokens")
    finally:
        manager.delete(session.id, user_id)


# ── HTTP layer ───────────────────────────────────────────────────

async def check_http(base_url: str, token: str) -> str:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    # trust_env=False: this checks your own service, so it must go straight
    # there rather than through whatever HTTP(S)_PROXY the shell exports — a
    # proxy in the way turns a healthy server into a confusing failure. The
    # provider calls above deliberately keep the environment's proxy settings,
    # since reaching an external API may genuinely require one.
    async with httpx.AsyncClient(
        timeout=30, base_url=base_url.rstrip("/"), trust_env=False
    ) as client:
        health = await client.get("/healthz")
        health.raise_for_status()

        page = await client.get("/harness.html")
        if page.status_code != 200:
            raise RuntimeError(f"harness.html 返回 {page.status_code}")

        if not token:
            return f"healthz {health.json()['status']} · 页面就绪（未提供 token，接口未测）"

        tools = await client.get("/api/harness/tools", headers=headers)
        if tools.status_code in (401, 403):
            raise RuntimeError(f"鉴权失败 {tools.status_code}：{tools.text[:120]}")
        tools.raise_for_status()

        created = await client.post("/api/harness/sessions", json={}, headers=headers)
        created.raise_for_status()
        session_id = created.json()["id"]
        try:
            # One real SSE turn through the wire, framing included.
            frames = []
            async with client.stream(
                "POST", f"/api/harness/sessions/{session_id}/messages",
                json={"text": "回复：连通"}, headers=headers,
            ) as response:
                if response.status_code != 200:
                    body = (await response.aread()).decode("utf-8", "replace")
                    raise RuntimeError(f"HTTP {response.status_code}: {body[:200]}")
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        frames.append(line[6:])

            if not frames or frames[-1] != "[DONE]":
                raise RuntimeError("SSE 流没有以 [DONE] 结束")
            kinds = {json.loads(f)["type"] for f in frames[:-1] if "type" in json.loads(f)}
            return (f"healthz ok · 工具 {len(tools.json()['tools'])} 个 · "
                    f"SSE {len(frames)} 帧 · 事件类型 {len(kinds)} 种")
        finally:
            await client.delete(f"/api/harness/sessions/{session_id}", headers=headers)


# ── driver ───────────────────────────────────────────────────────

async def main() -> int:
    parser = argparse.ArgumentParser(description="Harness 连通性自检")
    parser.add_argument("--offline", action="store_true", help="跳过真实 API 调用")
    parser.add_argument("--url", default="", help="已启动的服务地址，加上则检查 HTTP 层")
    parser.add_argument("--token", default="", help="管理员 JWT，用于 HTTP 层鉴权")
    parser.add_argument("--trace", action="store_true", help="失败时打印完整堆栈")
    args = parser.parse_args()

    print("\n本地接线")
    await stage("配置", check_config)
    await stage("数据库", check_database)
    await stage("沙箱与路径收敛", check_sandbox)
    await stage("工具注册表", check_registry)
    await stage("工具自动发现", check_discovery)
    await stage("技能", check_skills)
    await stage("子代理装配", check_subagent)
    await stage("文件产出与下载", check_artifacts)
    await stage("审批策略", check_approval)
    await stage("事件日志与消息投影", check_log_projection)

    print("\n模型连通")
    if args.offline:
        for name in ("模型调用", "工具调用（流式拼装）", "完整一轮"):
            record(name, SKIP, "--offline")
    else:
        ok = await stage("模型调用", check_model)
        if ok:
            await stage("工具调用（流式拼装）", check_tool_calling)
            await stage("完整一轮", check_full_turn)
        else:
            # Nothing downstream can pass if the provider is unreachable, and
            # three copies of the same error is noise.
            for name in ("工具调用（流式拼装）", "完整一轮"):
                record(name, SKIP, "provider 不可达")

    print("\nHTTP 层")
    if args.url:
        await stage("接口与 SSE", lambda: check_http(args.url, args.token))
    else:
        record("接口与 SSE", SKIP, "未提供 --url")

    passed = sum(1 for _, s, _ in _results if s == PASS)
    failed = [n for n, s, _ in _results if s == FAIL]
    skipped = sum(1 for _, s, _ in _results if s == SKIP)

    print(f"\n{'─' * 60}")
    print(f"通过 {passed} · 失败 {len(failed)} · 跳过 {skipped}")
    if failed:
        print(f"失败项：{', '.join(failed)}")
        print("加 --trace 可看完整堆栈。")
    else:
        print("链路完好。")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
