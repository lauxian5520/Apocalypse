"""Staged self-check for the RL workbench.

    python -m rl.checks.rl_check --offline     # no provider calls, no tokens
    python -m rl.checks.rl_check               # + one real rollout

Deliberately the same shape as `tools/harness_check.py`: each `check_*` returns
a detail string on success and *raises* on failure, a `stage()` wrapper records
PASS/FAIL/SKIP, and the process exits non-zero if anything failed — so it drops
into a deploy script or a pre-commit hook unchanged. `CLAUDE.md` asks for stages
over one-off scripts; this is the `rl/` half of that, kept separate only because
it needs the corpus and the harness check must stay runnable without one.

The stages that matter most are the equivalence and fixture ones. They encode
things that are true today and would break silently:

- the in-memory store and SQLite produce byte-identical model messages, which is
  the premise letting rollouts skip the database;
- the verifier's verdicts on hand-checked trajectories, including the
  comparison-question false positive that once voided a perfect trajectory;
- `corpus_answer` never leaks correctness back to the model.
"""
import argparse
import asyncio
import inspect
import os
import sys
import traceback
import unicodedata

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in (REPO_ROOT, os.path.join(REPO_ROOT, "backend")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Must precede any import that reaches `core.config`, whose `get_settings()` is
# `@lru_cache`d — the first caller freezes the value.
os.environ.setdefault("HARNESS_CORPUS_ENABLED", "true")

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"
_results: list[tuple[str, str, str]] = []


def _width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in text)


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - _width(text))


def record(stage_name: str, status: str, detail: str = "") -> None:
    mark = {PASS: "✓", FAIL: "✗", SKIP: "–"}[status]
    _results.append((stage_name, status, detail))
    print(f"  {mark} {_pad(stage_name, 26)}  {detail}")


async def stage(name: str, fn) -> bool:
    """Run one check. Awaits what `fn` *produces*, not `fn` itself.

    A lambda wrapping an async call is not a coroutine function, so testing
    `fn` would report such a stage as passing without ever running it — the
    same trap `tools/harness_check.py` documents.
    """
    try:
        detail = fn()
        if inspect.isawaitable(detail):
            detail = await detail
        record(name, PASS, detail or "")
        return True
    except Exception as e:                       # noqa: BLE001 - report, never abort
        record(name, FAIL, f"{e.__class__.__name__}: {e}")
        if "--trace" in sys.argv:
            traceback.print_exc()
        return False


# ── stages ────────────────────────────────────────────────────────

def check_corpus() -> str:
    from core.config import get_settings
    from harness.corpus.store import CorpusStore

    corpus_dir = get_settings().harness_corpus_dir
    store = CorpusStore.load(corpus_dir, check_hash=True)      # re-hashes on purpose
    if len(store) == 0:
        raise AssertionError("语料为空")
    return f"{store.manifest.describe()} · 哈希校验通过"


def check_index_determinism() -> str:
    """The same query must always return the same ids, in the same order.

    Determinism of the *environment* is the claim this project can honestly
    make (the policy is stochastic). It rests on BM25 ties breaking on corpus
    order, so this asserts it rather than trusting it.
    """
    from core.config import get_settings
    from harness.corpus.store import load_cached

    store = load_cached(get_settings().harness_corpus_dir, False)
    probes = ["nationality of film directors", "flowering plant", "national park spain"]
    for query in probes:
        first = [h.doc_id for h in store.search(query, k=5)]
        second = [h.doc_id for h in store.search(query, k=5)]
        if first != second:
            raise AssertionError(f"检索不确定：{query!r} 两次结果不同")
        if not first:
            raise AssertionError(f"检索无结果：{query!r}")
    return f"{len(probes)} 个固定查询重复可复现"


def check_preset() -> str:
    from harness.tools.registry import ToolRegistry
    from rl.env.build import ENV_TOOLS, PRESET

    registry = ToolRegistry(PRESET)
    names = [s["function"]["name"] for s in registry.schemas()]
    if sorted(names) != sorted(ENV_TOOLS):
        raise AssertionError(f"动作空间不符：{names}，应为 {list(ENV_TOOLS)}")
    if not registry.get("corpus_answer").stops_turn:
        raise AssertionError("corpus_answer 必须 stops_turn，否则回合不会结束")
    for name in ENV_TOOLS:
        if registry.get(name).permission != "read":
            raise AssertionError(f"{name} 不是 read 权限，rollout 会停下等人审批")
    return f"{len(names)} 个动作 · max_steps {registry.max_steps} · 全部 read 权限"


def check_module_gate() -> str:
    """With the gate off, the production preset must be exactly what it was."""
    from harness.tools.registry import ToolRegistry, _gated_off

    if not _gated_off("corpus"):
        # `HARNESS_CORPUS_ENABLED=true` is set at the top of this file, so the
        # gate is open here; simulate the deployment default instead.
        import core.config as config
        settings = config.get_settings()
        original = settings.harness_corpus_enabled
        try:
            object.__setattr__(settings, "harness_corpus_enabled", False)
            names = {s["function"]["name"] for s in ToolRegistry("standard").schemas()}
        finally:
            object.__setattr__(settings, "harness_corpus_enabled", original)
        leaked = {n for n in names if n.startswith("corpus_")}
        if leaked:
            raise AssertionError(f"门禁关闭时 standard 仍包含 {sorted(leaked)}")
        return f"门禁关闭时 standard = {len(names)} 个工具，无 corpus_*"
    return "门禁生效"


def check_store_equivalence() -> str:
    """The in-memory store and SQLite must project to identical messages.

    Rollouts skip the database for speed. That is only safe while the two
    stores are interchangeable at the level `derive_messages` sees, so the
    equivalence is asserted rather than assumed.
    """
    import models  # noqa: F401  - registers the mappers
    from core.database import Base, engine
    from harness import events as ev
    from harness.session.projection import derive_messages
    from harness.session.sqlite_store import SqliteSessionStore
    from rl.env.memory_store import MemorySessionStore

    Base.metadata.create_all(bind=engine)

    script = [
        (ev.CONFIG_CHANGE, {"system_prompt": "你是一个研究检索助手。"}),
        (ev.USER_MESSAGE, {"content": "Who set Nietzche's novel to music?"}),
        (ev.ASSISTANT_MESSAGE, {"content": "", "tool_calls": [
            {"id": "c1", "type": "function",
             "function": {"name": "corpus_search", "arguments": '{"query": "Nietzsche"}'}}
        ]}),
        (ev.TOOL_RESULT, {"tool_call_id": "c1", "name": "corpus_search", "content": "1. [abc123def456] Also sprach Zarathustra"}),
        (ev.ASSISTANT_MESSAGE, {"content": "Richard Strauss", "tool_calls": []}),
    ]

    # `harness_events.session_id` is a foreign key, so SQLite needs a real
    # session row — and `manager.create` needs a real user. The repo's existing
    # convention for this is a dedicated disabled account (see
    # `tools/harness_probe.py::_probe_user`), reused here rather than reinvented.
    from harness.session import manager
    from models.user import User
    from core.database import SessionLocal

    with SessionLocal() as db:
        user = db.query(User).filter(User.username == "__harness_probe__").first()
        if user is None:
            user = User(username="__harness_probe__", email="probe@localhost",
                        password_hash="!", role="user", is_disabled=True)
            db.add(user)
            db.commit()
            db.refresh(user)
        user_id = user.id

    session = manager.create(user_id, "minimal", "rl-check")
    session_id = session.id

    memory = MemorySessionStore()
    sqlite = SqliteSessionStore()
    try:
        for etype, data in script:
            memory.append(session_id, etype, data)
            sqlite.append(session_id, etype, data)

        mem_msgs = derive_messages(memory.read(session_id), "fallback")
        sql_msgs = derive_messages(sqlite.read(session_id), "fallback")
        if mem_msgs != sql_msgs:
            raise AssertionError("两种 store 投影出的消息不一致——rollout 跳过数据库的前提不成立")
    finally:
        manager.delete(session_id, user_id)

    roles = [m["role"] for m in mem_msgs]
    return f"{len(script)} 条事件 → {len(mem_msgs)} 条消息 {roles} · 两种 store 逐字节相同"


def check_verifier_fixtures() -> str:
    """Hand-checked trajectories with known verdicts."""
    from rl.verifiers import reward as reward_verifier
    from rl.verifiers.trace import ToolCall, Trace

    def trace_for(answer, citations, seen, queries=(), contents=()):
        t = Trace(answer=answer, citations=list(citations), seen_doc_ids=set(seen),
                  queries=list(queries), steps=3, ended_by="answer")
        t.calls = [ToolCall(name="corpus_search", arguments={"query": q},
                            raw_arguments="", content=c)
                   for q, c in zip(queries, list(contents) + [""] * len(queries))]
        return t

    gold = ["aaa111bbb222", "ccc333ddd444"]
    cases = []

    # 1. Correct, well-cited, legitimate.
    task = {"answer": "Richard Strauss", "gold_doc_ids": gold, "question": "Who set it to music?"}
    r = reward_verifier.score(trace_for("Richard Strauss", gold, gold, ["nietzsche"]), task)
    cases.append(("正确且有据", r.correct and not r.voided and r.total > 1.0))

    # 2. Right string, but citing an id that never appeared — must be voided.
    r = reward_verifier.score(trace_for("Richard Strauss", ["fff999fff999"], gold), task)
    cases.append(("伪造引用被作废", r.voided and r.total == 0.0 and not r.correct))

    # 3. Wrong answer scores exactly zero, however tidy.
    r = reward_verifier.score(trace_for("Gustav Mahler", gold, gold), task)
    cases.append(("答错得 0", r.total == 0.0 and not r.correct))

    # 4. The regression that matters: a comparison question names its own
    #    answer, so querying for it is correct behaviour, not recall.
    comparison = {"answer": "Pleiospilos", "gold_doc_ids": gold,
                  "question": "Which is a flowering plant, Pueraria or Pleiospilos?"}
    r = reward_verifier.score(
        trace_for("Pleiospilos", gold, gold, queries=["Pleiospilos"]), comparison)
    cases.append(("对比题不误判为记忆", r.correct and not r.voided))

    # 5. A bridge question whose answer was typed before any evidence returned.
    bridge = {"answer": "Richard Strauss", "gold_doc_ids": gold,
              "question": "Who set Nietzche's philosophical novel to music?"}
    r = reward_verifier.score(
        trace_for("Richard Strauss", gold, gold, queries=["Richard Strauss"]), bridge)
    cases.append(("桥接题记忆作答被作废", r.voided))

    # 6. Never answered.
    r = reward_verifier.score(Trace(steps=12, ended_by="max-steps"), task)
    cases.append(("放弃得 0 且格式不合法", r.total == 0.0 and not r.fmt.valid))

    failed = [name for name, ok in cases if not ok]
    if failed:
        raise AssertionError(f"验证器 fixture 不符：{failed}")
    return f"{len(cases)} 条 fixture 全部符合预期"


async def check_answer_is_silent() -> str:
    """`corpus_answer` must reveal nothing about correctness.

    If the acknowledgement differed for a right and a wrong answer, the model
    could call it repeatedly and turn the verifier into an oracle.
    """
    from harness.tools.base import ToolContext
    from harness.tools.builtin.corpus import corpus_answer

    ctx = ToolContext(session_id="__rl_check__", sandbox=None, workspace=None)
    right = await corpus_answer(ctx, answer="Richard Strauss", citations=["aaa111bbb222"])
    wrong = await corpus_answer(ctx, answer="totally wrong", citations=["aaa111bbb222"])
    if right != wrong:
        raise AssertionError("corpus_answer 对正确与错误答案返回了不同内容——验证器被泄漏成了神谕")
    return f"正误返回完全相同：{right!r}"


def check_normalisation() -> str:
    from rl.verifiers.outcome import score

    cases = [
        ("Yes, both are American.", "yes", True),
        ("scott derrickson", "Scott Derrickson", True),
        ("Trenton-Mercer Airport", "Trenton–Mercer Airport", True),   # unicode dash
        ("Ed Wood (film)", "Ed Wood", False),
        ("no", "yes", False),
    ]
    bad = [(p, g) for p, g, want in cases if score(p, g).exact_match != want]
    if bad:
        raise AssertionError(f"归一化不符：{bad}")
    return f"{len(cases)} 条归一化用例通过（含 Unicode 破折号）"


def check_template_pinned() -> str:
    """The chat template must be the file in this repo, at a known hash."""
    from rl.env import template

    text = template.template_text()
    if "tool_call.arguments | tojson" not in text:
        raise AssertionError(
            "模板里没有 `tool_call.arguments | tojson`——模板变了，"
            "normalize() 的前提要重新确认"
        )
    return f"sha256 {template.template_sha256()[:16]}… · {len(text)} 字符 · 随仓库入库"


def check_arguments_normalisation() -> str:
    """Golden fixture for the single highest-risk detail in the pipeline.

    `derive_messages` hands over `arguments` as raw JSON *text*; the template
    pipes it through `tojson`. Without `normalize()` that double-encodes into a
    JSON string, so every tool call renders differently in training than it did
    in rollout — silently.
    """
    from rl.env import template

    tools = [{"type": "function", "function": {
        "name": "corpus_search", "description": "搜索",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
    }}]
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "", "tool_calls": [{
            "id": "c1", "type": "function",
            "function": {"name": "corpus_search", "arguments": '{"query": "nietzsche"}'},
        }]},
    ]
    rendered = template.render(messages, tools)
    want = '{"name": "corpus_search", "arguments": {"query": "nietzsche"}}'
    bad = '"arguments": "{'
    if want not in rendered:
        raise AssertionError(f"工具调用没有渲染成对象形式，期望片段：{want}")
    if bad in rendered:
        raise AssertionError("arguments 被双重编码成了 JSON 字符串——normalize() 失效")
    return "工具调用参数渲染为 JSON 对象，未双重编码"


def check_loss_mask() -> str:
    """The mask construction, over trajectories recorded by a real rollout."""
    import glob

    from harness.tools.registry import ToolRegistry
    from rl.env import template
    from rl.env.build import PRESET
    from rl.rollout.trajectory import read_jsonl

    paths = sorted(glob.glob(os.path.join(REPO_ROOT, "rl", "data", "trajectories", "*.jsonl")))
    if not paths:
        raise AssertionError(
            "没有录制好的轨迹可查（rl/data/trajectories/*.jsonl）。"
            "先跑 python -m rl.cli rollout -n 6 --out rl/data/trajectories/smoke.jsonl"
        )

    tools = ToolRegistry(PRESET).schemas()
    checked = spans = trainable = total = 0
    for path in paths:
        for traj in read_jsonl(path):
            if not traj.ok:
                continue
            packed = template.pack(traj.session_events(), tools)
            packed.check()
            eos = template.tokenizer().convert_tokens_to_ids(template.END_OF_TURN)
            for seg in packed.segments:
                if packed.token_ids[seg.end - 1] != eos:
                    raise AssertionError(f"{traj.task_id} 的 span {seg} 不以 EOS 结尾")
            checked += 1
            spans += len(packed.segments)
            trainable += packed.trainable_tokens
            total += len(packed.token_ids)

    if checked == 0:
        raise AssertionError("轨迹文件里没有成功的轨迹")
    return (f"{checked} 条轨迹 · {spans} 个 span · 前缀性质与 EOS 均成立 · "
            f"可训练 token 占 {trainable / max(total, 1):.0%}")


def check_policy_adapter() -> str:
    """The training-time adapter's parsing and self-checks, without a server.

    Everything here is what can be verified with no GPU: the tool-call parser,
    and the two guards that make a mismatch impossible to carry into training —
    one log-prob per sampled token, and recorded ids that decode back to the
    text the server returned.
    """
    import json as _json

    from rl.env import template
    from rl.env.policy_adapter import PolicyAdapter, SampledStep, parse_tool_calls

    raw = ('好。\n<tool_call>\n{"name": "corpus_search", '
           '"arguments": {"query": "nietzsche"}}\n</tool_call>')
    content, calls = parse_tool_calls(raw)
    if len(calls) != 1 or calls[0].name != "corpus_search":
        raise AssertionError(f"工具调用解析失败：{calls}")
    if _json.loads(calls[0].arguments) != {"query": "nietzsche"}:
        raise AssertionError("arguments 解析错误")
    if "<tool_call>" in content:
        raise AssertionError("content 里仍残留 tool_call 块")

    two = ('<tool_call>{"name":"corpus_open","arguments":{"doc_id":"a"}}</tool_call>'
           '<tool_call>{"name":"corpus_open","arguments":{"doc_id":"b"}}</tool_call>')
    _, pair = parse_tool_calls(two)
    if len(pair) != 2 or pair[0].id == pair[1].id:
        raise AssertionError("多个工具调用必须各自解析且 id 唯一")

    if parse_tool_calls('<tool_call>{not json}</tool_call>x')[1]:
        raise AssertionError("坏 JSON 不应产出工具调用")

    tok = template.tokenizer()
    text = "Richard Strauss" + template.END_OF_TURN
    ids = tok.encode(text, add_special_tokens=False)
    adapter = PolicyAdapter.__new__(PolicyAdapter)
    adapter._verify(SampledStep(completion_token_ids=ids,
                                logp_old=[-0.1] * len(ids), text=text))

    for broken, why in (
        (SampledStep(completion_token_ids=ids, logp_old=[-0.1] * (len(ids) - 1), text=text),
         "logprob 数量不一致"),
        (SampledStep(completion_token_ids=ids, logp_old=[], text="mismatch"),
         "解码文本不一致"),
    ):
        try:
            adapter._verify(broken)
        except RuntimeError:
            continue
        raise AssertionError(f"{why} 没有被拦下")

    return "工具调用解析（单/多/坏）· token-logprob 对齐与解码回环两道守卫均生效"


def check_grpo_loss() -> str:
    """The objective, on synthetic tensors. Needs torch; skipped without it.

    Pins three things that are silently wrong if broken: the `logp_old`
    alignment convention (a one-token shift trains on noise while looking
    healthy), the KL estimator, and that token-level normalisation genuinely
    differs from per-sequence averaging.
    """
    import torch

    from rl.train import grpo

    torch.manual_seed(0)

    rewards = torch.tensor([1., 0., 0., 0., 1., 1., 1., 1., 0., 0., 0., 0.])
    gids = torch.tensor([0, 0, 0, 0, 1, 1, 1, 1, 2, 2, 2, 2])
    adv, keep = grpo.group_advantages(rewards, gids)
    if keep[:4].sum() != 4 or keep[4:].any():
        raise AssertionError("退化组（全对/全错）必须被丢弃，非退化组必须保留")
    if abs(adv[:4].mean().item()) > 1e-5:
        raise AssertionError("组内 advantage 没有中心化")

    B, T, V = 2, 6, 11
    logits = torch.randn(B, T, V, requires_grad=True)
    ids = torch.randint(0, V, (B, T))
    mask = torch.zeros(B, T)
    mask[:, 3:] = 1
    advantages = torch.tensor([1.0, -1.0])

    # The shift convention lives in one place; see `grpo.token_logprobs`.
    logp_old = grpo.token_logprobs(logits.detach(), ids)

    loss, stats = grpo.masked_token_loss(
        logits, ids, mask, advantages, logp_old.clone(), ref_logp=logp_old.clone())
    if abs(stats["mean_ratio"].item() - 1.0) > 1e-6:
        raise AssertionError(
            f"策略与采样者相同时 ratio 应为 1，实际 {stats['mean_ratio'].item():.6f}"
            f"——logp_old 对齐方式错了"
        )
    if stats["clip_fraction"].item() > 1e-9 or stats["kl"].item() > 1e-9:
        raise AssertionError("同一策略下 clip_fraction 与 KL 都应为 0")

    loss.backward()
    if logits.grad is None or not torch.isfinite(logits.grad).all():
        raise AssertionError("梯度不存在或非有限")

    # Token-level must not equal per-sequence averaging.
    mask2 = torch.zeros(B, T)
    mask2[0, 1:] = 1
    mask2[1, 5:] = 1
    token_level, _ = grpo.masked_token_loss(
        logits, ids, mask2, advantages, logp_old.clone())
    per_seq = torch.stack([
        grpo.masked_token_loss(logits[i:i + 1], ids[i:i + 1], mask2[i:i + 1],
                               advantages[i:i + 1], logp_old[i:i + 1].clone())[0]
        for i in range(B)
    ]).mean()
    if abs(token_level.item() - per_seq.item()) < 1e-4:
        raise AssertionError("token 级与按序列归一化结果相同——归一化没生效")

    # SFT shares this shift and this normalisation, so a mask bug shows up in
    # both. A perfect predictor must score exactly 0, and an empty mask must be
    # 0 rather than NaN.
    perfect = torch.zeros(B, T, V)
    for b in range(B):
        for pos in range(T - 1):
            perfect[b, pos, ids[b, pos + 1]] = 50.0
    if grpo.masked_cross_entropy(perfect, ids, mask).item() > 1e-6:
        raise AssertionError("完美预测下 SFT 交叉熵应为 0——位移方向错了")
    if grpo.masked_cross_entropy(logits, ids, torch.zeros(B, T)).item() != 0.0:
        raise AssertionError("全零 mask 下交叉熵必须是 0，不能是 NaN")

    return (f"退化组丢弃 · ratio=1/KL=0 对齐正确 · "
            f"token 级 {token_level.item():+.3f} ≠ 按序列 {per_seq.item():+.3f} · "
            f"SFT 交叉熵共用同一位移")


def check_sft_selection() -> str:
    """Rejection sampling keeps only what the verifier passed."""
    import glob

    from rl.rollout.trajectory import read_jsonl
    from rl.train import sft

    paths = sorted(glob.glob(os.path.join(REPO_ROOT, "rl", "data", "trajectories", "*.jsonl")))
    if not paths:
        raise AssertionError("没有录制好的轨迹（rl/data/trajectories/*.jsonl）")
    trajectories = [t for p in paths for t in read_jsonl(p)]

    kept, funnel = sft.select(trajectories)
    if any(not t.reward.get("correct") for t in kept):
        raise AssertionError("SFT 选出了未通过验证器的轨迹")
    if funnel["kept"] + funnel["below_reward"] + funnel["errored"] + \
            funnel["ungrounded"] + funnel["duplicate"] + funnel["over_quota"] != funnel["total"]:
        raise AssertionError("SFT 漏斗各项之和不等于总数")

    # Raising the bar above any achievable reward must select nothing, and
    # lowering it must not select a wrong answer.
    strict, _ = sft.select(trajectories, sft.SFTConfig(min_reward=99.0))
    if strict:
        raise AssertionError("min_reward=99 时不应选出任何轨迹")

    dataset = sft.build_dataset(kept)
    if len(dataset) != len(kept):
        raise AssertionError(f"{len(kept)} 条入选但只有 {len(dataset)} 条可分词")
    trainable = sum(sum(s.mask) for s in dataset)
    if trainable == 0:
        raise AssertionError("SFT 数据里没有任何可训练 token")
    return (f"{funnel['total']} 条采样 → 保留 {funnel['kept']} 条"
            f"（{funnel['below_reward']} 条未过验证器）· 可训练 {trainable} token")


def check_attribution() -> str:
    """Context ablation removes the evidence, not just the id."""
    import glob

    from rl.attribution import loo
    from rl.rollout.trajectory import read_jsonl
    from rl.verifiers.trace import extract

    paths = sorted(glob.glob(os.path.join(REPO_ROOT, "rl", "data", "trajectories", "*.jsonl")))
    trajectories = [t for p in paths for t in read_jsonl(p) if t.ok]
    candidates = [t for t in trajectories if extract(t.session_events()).citations]
    if not candidates:
        raise AssertionError("没有带引用的轨迹可用于消融检查")

    trajectory = candidates[0]
    log = trajectory.session_events()
    trace = extract(log)
    target = trace.citations[0]

    before = sum(len(e.data.get("content") or "") for e in log if e.type == "tool/result")
    ablated = loo.ablate(log, target)
    after = sum(len(e.data.get("content") or "") for e in ablated if e.type == "tool/result")

    if any(target in (e.data.get("content") or "") for e in ablated):
        raise AssertionError(f"消融后 {target} 仍出现在工具输出里")
    if after >= before:
        raise AssertionError("消融没有移除任何内容——只删 id 不删正文等于没消融")
    if not any(loo.PLACEHOLDER in (e.data.get("content") or "") for e in ablated):
        raise AssertionError("没有插入等位占位符，位置会整体前移，测到的是位置敏感性")
    # Assistant turns must be untouched: rewriting them fabricates a trajectory.
    before_asst = [e.data for e in log if e.type == "assistant/message"]
    after_asst = [e.data for e in ablated if e.type == "assistant/message"]
    if before_asst != after_asst:
        raise AssertionError("消融改动了 assistant 消息——那是伪造轨迹而非消融观测")

    # A stub scorer that counts how much of the gold string survives gives the
    # ablated document a positive delta and leaves the rest at zero.
    gold = set(trajectory.gold_answer.lower().split())

    def score(candidate_log):
        text = " ".join((e.data.get("content") or "") for e in candidate_log
                        if e.type == "tool/result").lower()
        return -1.0 * sum(1 for w in gold if w not in text)

    result = loo.attribute(log, {"task_id": trajectory.task_id, "gold_doc_ids": []}, score)
    if not result.contributions:
        raise AssertionError("没有产生任何归因结果")
    loo.diagnose([result])
    return (f"消融 {target}：工具输出 {before}→{after} 字符 · assistant 轮未被改动 · "
            f"{len(result.contributions)} 篇文档得到归因")


def check_export() -> str:
    """Both export formats round-trip, and a mixed environment is rejected."""
    import glob
    import json as _json
    import tempfile

    from rl.rollout.trajectory import read_jsonl
    from rl.train import export_verl

    paths = sorted(glob.glob(os.path.join(REPO_ROOT, "rl", "data", "trajectories", "*.jsonl")))
    if not paths:
        raise AssertionError("没有录制好的轨迹（rl/data/trajectories/*.jsonl）")
    trajectories = [t for p in paths for t in read_jsonl(p)]

    summary = {}
    with tempfile.TemporaryDirectory() as tmp:
        for fmt in ("messages", "tokens"):
            out = os.path.join(tmp, f"{fmt}.jsonl")
            stats = export_verl.export(trajectories, out, fmt)
            if stats.written == 0:
                raise AssertionError(f"{fmt} 格式一条都没导出")
            summary[fmt] = export_verl.verify(out, fmt)

        # Trajectories from two different environments must not be trainable
        # together, and the export must say so rather than let it through.
        rows = [_json.loads(l) for l in open(os.path.join(tmp, "tokens.jsonl"), encoding="utf-8")]
        if len(rows) > 1:
            rows[0]["env"] = {**rows[0]["env"], "corpus_sha256": "deadbeef"}
            mixed = os.path.join(tmp, "mixed.jsonl")
            with open(mixed, "w", encoding="utf-8") as f:
                for row in rows:
                    f.write(_json.dumps(row, ensure_ascii=False) + "\n")
            try:
                export_verl.verify(mixed, "tokens")
            except AssertionError:
                pass
            else:
                raise AssertionError("混合环境指纹的导出没有被拒绝")

    return (f"messages {summary['messages']['rows']} 条 · "
            f"tokens {summary['tokens']['rows']} 条"
            f"（可训练 {summary['tokens']['trainable_fraction']:.0%}）· 混合环境被拒")


def check_lora_serve() -> str:
    """URL construction, adapter naming, and the swap protocol.

    Against a local stub rather than a real endpoint. The first version probed a
    public host expecting a 404 and got a 401 — its gateway authenticates before
    it routes — which tested nothing. A stub is hermetic and can exercise both
    branches: a server without the feature (404) and one with it.
    """
    import json as _json
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from rl.train.lora_serve import VLLMServer, adapter_dir, adapter_name

    for given in ("http://h:8000", "http://h:8000/", "http://h:8000/v1", "http://h:8000/v1/"):
        server = VLLMServer(given)
        if server.load_url != "http://h:8000/v1/load_lora_adapter":
            raise AssertionError(f"{given!r} 拼出了错误的 URL：{server.load_url}")

    if adapter_name(7) != "policy-step-000007":
        raise AssertionError(f"adapter 命名不对：{adapter_name(7)}")
    if not adapter_dir("/tmp/x", 7).endswith("policy-step-000007"):
        raise AssertionError("adapter 目录拼接不对")

    seen: list[tuple] = []

    def make_handler(lora_enabled: bool):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):            # keep the check output clean
                pass

            def do_GET(self):
                body = _json.dumps({"data": [{"id": "base"}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                payload = _json.loads(self.rfile.read(length) or b"{}")
                if not lora_enabled:
                    # No such route on a server started without the flag.
                    self.send_response(404)
                    self.end_headers()
                    return
                seen.append((self.path.rsplit("/", 1)[-1], payload.get("lora_name")))
                # An empty probe is malformed but the route exists: 400, not 404.
                self.send_response(400 if not payload.get("lora_name") else 200)
                self.end_headers()
        return Handler

    def serve(lora_enabled: bool):
        httpd = HTTPServer(("127.0.0.1", 0), make_handler(lora_enabled))
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        return httpd, f"http://127.0.0.1:{httpd.server_port}"

    # 1. A server WITHOUT runtime LoRA must be diagnosed, not shrugged at.
    httpd, url = serve(lora_enabled=False)
    try:
        try:
            VLLMServer(url).check_runtime_lora()
        except RuntimeError as e:
            if "VLLM_ALLOW_RUNTIME_LORA_UPDATING" not in str(e):
                raise AssertionError(f"404 应提示开启运行时 LoRA，实际：{str(e)[:90]}")
        else:
            raise AssertionError("404 没有被判为未开启运行时 LoRA")
    finally:
        httpd.shutdown()

    # 2. A server WITH it must pass the check, report its models, and swap.
    httpd, url = serve(lora_enabled=True)
    try:
        live = VLLMServer(url)
        live.check_runtime_lora()
        if live.wait_ready(deadline_seconds=10) != ["base"]:
            raise AssertionError("wait_ready 没有返回服务中的模型 id")

        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            live.load("policy-step-000001", tmp)
        # A swap must unload before loading, or a failed load leaves the old
        # adapter serving while the trainer believes the new one is live.
        named = [call for call in seen if call[1] == "policy-step-000001"]
        if [c[0] for c in named] != ["unload_lora_adapter", "load_lora_adapter"]:
            raise AssertionError(f"换权重顺序不对（应先卸后装）：{[c[0] for c in named]}")
    finally:
        httpd.shutdown()

    return "URL 归一化（4 种写法）· 步号命名 · 404 判为未开启 · 换权重先卸后装"


def check_training_loop() -> str:
    """The loop's orchestration, with stub hooks — no GPU involved.

    What this covers is the *order* things happen in, which is where the
    remaining mistakes live once every part works: that degenerate batches are
    skipped instead of stepped on, that the adapter is swapped on schedule (the
    thing that keeps the next step on-policy), that the curriculum sees every
    group's outcomes, and that the health warnings fire.
    """
    import copy
    import glob
    import random

    from rl.rollout.trajectory import read_jsonl
    from rl.tasks import split as split_mod
    from rl.train import metrics as metrics_mod
    from rl.train.loop import LoopConfig, all_zero_fraction, run

    paths = sorted(glob.glob(os.path.join(REPO_ROOT, "rl", "data", "trajectories", "*.jsonl")))
    if not paths:
        raise AssertionError("没有录制好的轨迹（rl/data/trajectories/*.jsonl）")
    real = [t for p in paths for t in read_jsonl(p) if t.ok]
    tasks = split_mod.load(os.path.join(REPO_ROOT, "rl", "data", "splits"), "train")[:30]

    calls = {"fb": 0, "publish": 0, "eval": 0}
    seen_advantages: list[list[float]] = []

    class Hooks:
        def forward_backward(self, batch, advantages, keep):
            calls["fb"] += 1
            seen_advantages.append(list(advantages))
            return {"loss": 0.5, "kl": 0.01, "entropy": 1.2,
                    "clip_fraction": 0.08, "mean_ratio": 1.0, "grad_norm": 0.3}

        def publish(self, step):
            calls["publish"] += 1
            return f"policy-step-{step:06d}"

        def evaluate(self, step):
            calls["eval"] += 1
            return {"pass@1": 0.3}

    rng = random.Random(0)

    def rollout(chosen, group_size):
        out = []
        for task in chosen:
            for _ in range(group_size):
                trajectory = copy.deepcopy(real[0])
                trajectory.task_id = task["task_id"]
                solved = rng.random() < 0.4
                trajectory.reward = {**trajectory.reward,
                                     "total": 1.0 if solved else 0.0, "correct": solved}
                out.append(trajectory)
        return out

    state = run(tasks, Hooks(), rollout,
                LoopConfig(steps=6, questions_per_step=3, group_size=4,
                           batch_size=8, save_every=2, eval_every=3))

    if calls["fb"] == 0:
        raise AssertionError("一步都没有进入 forward_backward")
    if calls["publish"] != 3:
        raise AssertionError(f"save_every=2 跑 6 步应换权重 3 次，实际 {calls['publish']}")
    if calls["eval"] != 2:
        raise AssertionError(f"eval_every=3 跑 6 步应评测 2 次，实际 {calls['eval']}")
    if state.adapter != "policy-step-000006":
        raise AssertionError(f"最后一次换权重的名字不对：{state.adapter}")
    if len(state.log.rows) != 6:
        raise AssertionError("指标行数与步数不一致")

    # Every batch handed to the hook must carry a non-degenerate advantage:
    # degenerate ones are skipped, not multiplied by zero.
    for advantages in seen_advantages:
        if all(abs(a) < 1e-9 for a in advantages):
            raise AssertionError("有一个批次的 advantage 全为 0 却仍然进了 forward_backward")

    # The curriculum must have observed outcomes, and the degenerate-group
    # fraction must actually vary — a constant would mean it is not measured.
    if state.pool.summary()["judged"] == 0:
        raise AssertionError("课程池没有累积任何观测")
    if len({r.all_zero_groups for r in state.log.rows}) == 1:
        raise AssertionError("全零组比例在整个运行中一成不变，说明没有真的在算")

    # The warnings must fire on the conditions they describe.
    log = metrics_mod.MetricLog()
    alarming = metrics_mod.StepMetrics(step=30, all_zero_groups=0.9, groups=4,
                                       clip_fraction=0.0, gave_up=0.5,
                                       voided=0.2, format_valid=0.1)
    fired = log.warnings(alarming)
    if len(fired) < 5:
        raise AssertionError(f"异常状态下应发出多条告警，实际只有 {len(fired)} 条")
    if not log.warnings(metrics_mod.StepMetrics(step=1, groups=2, clip_fraction=0.1,
                                                format_valid=1.0)) == []:
        raise AssertionError("健康状态下不应发出告警")

    if all_zero_fraction({}) != 0.0:
        raise AssertionError("空输入下全零组比例应为 0")

    return (f"6 步 · forward_backward {calls['fb']} 次 · 换权重 {calls['publish']} 次 · "
            f"评测 {calls['eval']} 次 · 退化批次被跳过 · 告警 {len(fired)} 条")


def check_advantage_equivalence() -> str:
    """The pure-Python and torch advantage implementations must agree.

    Two implementations exist because the loop has to run without a GPU. Two
    implementations that drift apart would mean the loop trains on different
    advantages than the loss was verified against — so the equivalence is
    asserted rather than assumed.
    """
    import random

    from rl.train import pack as packer

    try:
        import torch

        from rl.train import grpo
    except ImportError:
        raise AssertionError("需要 torch 才能对照两种实现")

    rng = random.Random(0)
    for trial in range(20):
        groups = rng.randint(1, 4)
        rewards, group_ids = [], []
        for gid in range(groups):
            for _ in range(rng.randint(2, 6)):
                rewards.append(float(rng.choice([0.0, 0.0, 1.0, 1.15])))
                group_ids.append(gid)

        py_adv, py_keep = packer.group_advantages(rewards, group_ids)
        tt_adv, tt_keep = grpo.group_advantages(
            torch.tensor(rewards), torch.tensor(group_ids))

        if py_keep != [bool(k) for k in tt_keep.tolist()]:
            raise AssertionError(f"trial {trial}: keep 掩码不一致")
        for a, b in zip(py_adv, tt_adv.tolist()):
            if abs(a - b) > 1e-5:
                raise AssertionError(f"trial {trial}: advantage 不一致 {a} vs {b}")

    return "20 组随机用例下，纯 Python 与 torch 两种实现的 advantage 与 keep 完全一致"


def check_logprob_placement() -> str:
    """The sampler's recorded log-probs must land on the right tokens.

    `logp_old` is matched to assistant spans by prompt prefix, not by position,
    because concurrent rollouts finish out of order. Getting this wrong pairs
    one generation's log-probs with another's tokens: the ratio is then garbage,
    the loss is still finite, and nothing reports it.
    """
    import glob

    from harness.tools.registry import ToolRegistry
    from rl.env import template
    from rl.env.build import PRESET
    from rl.rollout.trajectory import read_jsonl
    from rl.train import pack as packer

    paths = sorted(glob.glob(os.path.join(REPO_ROOT, "rl", "data", "trajectories", "*.jsonl")))
    if not paths:
        raise AssertionError("没有录制好的轨迹（rl/data/trajectories/*.jsonl）")
    trajectory = next(t for p in paths for t in read_jsonl(p) if t.ok)

    tools = ToolRegistry(PRESET).schemas()
    packed = template.pack(trajectory.session_events(), tools)
    if len(packed.segments) < 2:
        raise AssertionError("需要至少两个 assistant span 才能验出错配")

    # Records shaped exactly as `PolicyAdapter` produces them.
    records = [
        {
            "prompt_token_ids": packed.token_ids[: span.start],
            "completion_token_ids": packed.token_ids[span.start: span.end],
            "logp_old": [-(i + 1) * 0.1] * (span.end - span.start),
        }
        for i, span in enumerate(packed.segments)
    ]

    trajectory.sampled = records
    sample = packer.to_sample(trajectory, tools)
    if len(sample.logp_old) != len(sample.token_ids):
        raise AssertionError("logp_old 长度与序列长度不一致")
    for i, span in enumerate(packed.segments):
        expected = -(i + 1) * 0.1
        inside = sample.logp_old[span.start: span.end]
        if any(abs(v - expected) > 1e-9 for v in inside):
            raise AssertionError(f"span {i} 的 logp_old 放错了：{set(inside)}")
    covered = {j for span in packed.segments for j in range(span.start, span.end)}
    if any(v != 0.0 for j, v in enumerate(sample.logp_old) if j not in covered):
        raise AssertionError("span 之外出现了非零 logp_old")

    # Partial or absent coverage must drop the whole vector, so the trainer
    # falls back to a frozen pass rather than mixing real and fake ratios in
    # one sequence.
    trajectory.sampled = records[:1]
    if packer.to_sample(trajectory, tools).logp_old != []:
        raise AssertionError("只匹配部分 span 时应整体丢弃 logp_old")
    trajectory.sampled = [{"prompt_token_ids": [1, 2, 3], "logp_old": [-0.5]}]
    if packer.to_sample(trajectory, tools).logp_old != []:
        raise AssertionError("完全不匹配时应丢弃 logp_old")

    return (f"{len(packed.segments)} 个 span 各自对位正确 · span 外全零 · "
            f"部分覆盖与不匹配均整体丢弃")


async def check_engine_adapter_factory() -> str:
    """The engine must accept a factory and give each episode its own adapter.

    Async because the check driver already owns an event loop; `asyncio.run`
    inside it raises.
    """
    from rl.rollout import engine
    from rl.rollout.trajectory import EnvStamp

    built = []

    class FakeAdapter:
        def __init__(self):
            built.append(self)
            self.steps = []

        async def stream(self, messages, tools=None):
            # Wiring-only check: the episode is expected to fail here and be
            # recorded as an errored trajectory, which is itself the behaviour
            # `run_episode` promises (one bad episode never kills a run).
            raise RuntimeError("deliberate")
            yield  # pragma: no cover - makes this an async generator

    tasks = [
        {"task_id": "a", "question": "q1", "answer": "x"},
        {"task_id": "b", "question": "q2", "answer": "y"},
    ]
    stamp = EnvStamp(corpus_sha256="x", corpus_docs=1, preset="deepresearch",
                     max_steps=12, model="fake")

    trajectories, _ = await engine.run_many(
        tasks, FakeAdapter, stamp, concurrency=2, group_size=2, progress_every=0)
    # `FakeAdapter` is the factory here: the engine calls it per episode.

    if len(trajectories) != 4:
        raise AssertionError(f"G=2 × 2 题应得 4 条轨迹，实际 {len(trajectories)}")
    if len(built) != 4:
        raise AssertionError(f"每个 episode 应各建一个适配器，实际建了 {len(built)} 个")
    if len({id(a) for a in built}) != 4:
        raise AssertionError("适配器被复用了——生成记录会互相串台")
    # A passed instance (not a factory) must still work, for evaluation.
    before = len(built)
    single = FakeAdapter()
    trajectories, _ = await engine.run_many(
        tasks[:1], engine.shared(single), stamp, concurrency=1, progress_every=0)
    if len(trajectories) != 1:
        raise AssertionError("shared() 路径坏了")
    if len(built) != before + 1:
        raise AssertionError("shared() 不应额外构造适配器")

    return "工厂路径每 episode 独立建适配器（4 个互不相同）· shared() 复用单个实例"


async def check_one_rollout() -> str:
    """One real episode against the configured provider."""
    import random

    from harness.corpus.store import load_cached
    from core.config import get_settings
    from harness.tools.registry import ToolRegistry
    from rl.env.adapter import EvalAdapter
    from rl.env.build import PRESET
    from rl.rollout import engine
    from rl.rollout.engine import run_episode
    from rl.rollout.trajectory import EnvStamp

    store = load_cached(get_settings().harness_corpus_dir, False)
    task = random.Random(0).choice(store.tasks())
    llm = EvalAdapter.from_settings(temperature=0.0)
    stamp = EnvStamp(
        corpus_sha256=store.manifest.docs_sha256, corpus_docs=len(store),
        preset=PRESET, max_steps=ToolRegistry(PRESET).max_steps, model=llm.model,
    )

    trajectory = await run_episode(task, engine.shared(llm), stamp)
    if trajectory.error:
        raise AssertionError(f"rollout 失败：{trajectory.error}")
    if not trajectory.events:
        raise AssertionError("轨迹没有事件")

    # The whole training story rests on this: a stored trajectory must replay
    # into the exact messages the policy saw.
    from harness.session.projection import derive_messages
    messages = derive_messages(trajectory.session_events(), "")
    if not messages or messages[0]["role"] != "system":
        raise AssertionError("轨迹无法投影回消息")

    r = trajectory.reward
    return (f"{r.get('steps')} 步 · {r.get('tool_calls')} 次调用 · "
            f"correct={r.get('correct')} · 投影出 {len(messages)} 条消息")


# ── driver ────────────────────────────────────────────────────────

async def main() -> int:
    parser = argparse.ArgumentParser(description="天启·深研场 RL 自检")
    parser.add_argument("--offline", action="store_true", help="跳过真实 provider 调用")
    parser.add_argument("--trace", action="store_true", help="失败时打印完整堆栈")
    args = parser.parse_args()

    print("\n环境与语料")
    corpus_ok = await stage("语料与清单", check_corpus)
    if corpus_ok:
        await stage("检索确定性", check_index_determinism)
    else:
        record("检索确定性", SKIP, "语料不可用")
    await stage("动作空间与预设", check_preset)
    await stage("模块门禁", check_module_gate)

    print("\n轨迹与验证器")
    await stage("两种 store 投影一致", check_store_equivalence)
    await stage("答案归一化", check_normalisation)
    await stage("验证器 fixture", check_verifier_fixtures)
    await stage("作答不泄漏正确性", check_answer_is_silent)

    print("\n分词与 loss mask")
    tpl_ok = await stage("模板已入库并钉死", check_template_pinned)
    if tpl_ok:
        await stage("参数未双重编码", check_arguments_normalisation)
        await stage("前缀性质与 loss mask", check_loss_mask)
    else:
        record("参数未双重编码", SKIP, "模板不可用")
        record("前缀性质与 loss mask", SKIP, "模板不可用")

    print("\n训练通路")
    await stage("训练期适配器", check_policy_adapter)
    await stage("SFT 拒绝采样筛选", check_sft_selection)
    await stage("上下文消融归因", check_attribution)
    await stage("轨迹导出", check_export)
    await stage("LoRA 热插拔接线", check_lora_serve)
    await stage("训练循环编排", check_training_loop)
    await stage("logp_old 对位", check_logprob_placement)
    await stage("引擎适配器工厂", check_engine_adapter_factory)

    try:
        import torch  # noqa: F401
        await stage("GRPO 损失与 advantage", check_grpo_loss)
        await stage("两种 advantage 实现一致", check_advantage_equivalence)
    except ImportError:
        record("GRPO 损失与 advantage", SKIP, "torch 未安装（训练在 GPU 机上跑）")
        record("两种 advantage 实现一致", SKIP, "torch 未安装")

    print("\n真实调用")
    if args.offline:
        record("完整一轮 rollout", SKIP, "--offline")
    elif not corpus_ok:
        record("完整一轮 rollout", SKIP, "语料不可用")
    else:
        await stage("完整一轮 rollout", check_one_rollout)

    passed = sum(1 for _, s, _ in _results if s == PASS)
    failed = [n for n, s, _ in _results if s == FAIL]
    skipped = sum(1 for _, s, _ in _results if s == SKIP)

    print("\n" + "─" * 60)
    print(f"通过 {passed} · 失败 {len(failed)} · 跳过 {skipped}")
    if failed:
        print(f"失败：{', '.join(failed)}")
        if not args.trace:
            print("加 --trace 看完整堆栈")
        return 1
    print("链路完好。")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
