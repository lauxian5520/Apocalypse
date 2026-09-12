"""One entry point for the RL workbench.

    python -m rl.cli corpus build --target 50000
    python -m rl.cli corpus verify
    python -m rl.cli corpus stats

Run from the repository root. Operator-facing text is Chinese, matching the
rest of the project; identifiers and comments stay English.
"""
import argparse
import logging
import os
import sys
from datetime import date

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

# The corpus tools are gated off by default so the production website never sees
# them (`ToolRegistry._MODULE_GATES`). This CLI *is* the RL entry point, so it
# turns them on — and it has to happen before anything imports `core.config`,
# because `get_settings()` is `@lru_cache`d and the first caller freezes it.
os.environ.setdefault("HARNESS_CORPUS_ENABLED", "true")

from harness.corpus import store as corpus_store                 # noqa: E402
from harness.corpus.schema import DEFAULT_CATEGORIES             # noqa: E402
from rl.corpus import build as corpus_build                      # noqa: E402
from rl.corpus import fetch_arxiv                                # noqa: E402

DEFAULT_CORPUS_DIR = os.path.join(REPO_ROOT, "rl", "data", "corpus")


def _cmd_corpus_build(args: argparse.Namespace) -> int:
    since, until = date.fromisoformat(args.since), date.fromisoformat(args.until)
    categories = tuple(args.categories.split(",")) if args.categories else DEFAULT_CATEGORIES

    if args.snapshot:
        source = "snapshot"
        docs = fetch_arxiv.read_snapshot(args.snapshot, categories, since, until, args.target)
    else:
        source = "api"
        docs = fetch_arxiv.fetch_api(categories, since, until, args.target)

    print(f"来源 {source} · 类别 {'/'.join(categories)} · {since}..{until} · 目标 {args.target} 篇")
    manifest = corpus_build.write_corpus(
        args.corpus_dir, docs,
        source=f"arxiv-{source}",
        params={
            "categories": ",".join(categories),
            "since": since.isoformat(),
            "until": until.isoformat(),
            "snapshot_path": args.snapshot or "",
        },
    )
    print(f"完成：{manifest.describe()}")
    print(f"目录：{args.corpus_dir}")
    return 0


def _cmd_corpus_hotpot(args: argparse.Namespace) -> int:
    """Build the corpus and task set from HotpotQA's distractor split."""
    from rl.corpus import hotpot

    cache = os.path.join(REPO_ROOT, "rl", "data", "_downloads")
    print(f"下载 HotpotQA {args.split} 分片…")
    paths = hotpot.download(args.split, cache)
    print("解析中（10 段/题，跨题去重）…")
    docs, tasks = hotpot.read(paths)
    print(f"去重后 {len(docs)} 个段落 · {len(tasks)} 道题")

    manifest = corpus_build.write_corpus(
        args.corpus_dir,
        docs.values(),
        source="hotpotqa-distractor",
        params={"split": args.split, "licence": "CC BY-SA 4.0"},
        tasks=(t.to_dict() for t in tasks),
    )
    print(f"完成：{manifest.describe()}")
    print(f"目录：{args.corpus_dir}")
    return 0


def _cmd_corpus_verify(args: argparse.Namespace) -> int:
    manifest = corpus_store.verify(args.corpus_dir)
    print(f"✓ 语料校验通过：{manifest.describe()}")
    print(f"  构建于 {manifest.built_at}")
    return 0


def _cmd_corpus_stats(args: argparse.Namespace) -> int:
    """Shape of the corpus: sizes, and whatever `meta` keys the source supplied."""
    from collections import Counter

    manifest = corpus_store.load_manifest(args.corpus_dir)
    meta_keys: Counter = Counter()
    title_chars = text_chars = n = 0
    shortest = 10 ** 9
    for doc in corpus_store.iter_docs(args.corpus_dir):
        n += 1
        title_chars += len(doc.title)
        text_chars += len(doc.text)
        shortest = min(shortest, len(doc.text))
        meta_keys.update(doc.meta.keys())

    print(f"清单       {manifest.describe()}")
    print(f"文档       {n}")
    print(f"标题均长   {title_chars / max(n, 1):.0f} 字符")
    print(f"正文均长   {text_chars / max(n, 1):.0f} 字符（最短 {shortest if n else 0}）")
    print(f"meta 字段  {', '.join(f'{k}×{v}' for k, v in meta_keys.most_common())}")

    tasks = corpus_store.CorpusStore.load(args.corpus_dir, check_hash=False).tasks() \
        if args.with_tasks else []
    if tasks:
        levels = Counter(t.get("level", "") for t in tasks)
        types = Counter(t.get("qtype", "") for t in tasks)
        print(f"任务       {len(tasks)}")
        print(f"  难度     {', '.join(f'{k}={v}' for k, v in levels.most_common())}")
        print(f"  题型     {', '.join(f'{k}={v}' for k, v in types.most_common())}")
    return 0


def _cmd_corpus_index(args: argparse.Namespace) -> int:
    from harness.corpus import index as corpus_index

    corpus_store.verify(args.corpus_dir)          # never index a drifted corpus
    idx = corpus_index.build_index(args.corpus_dir)
    path = corpus_index.index_path(args.corpus_dir)
    idx.save(path)
    size_mb = os.path.getsize(path) / 1e6
    print(f"✓ 索引完成：{len(idx.doc_ids)} 篇 · 词项 {len(idx.postings)} · "
          f"平均长度 {idx.avg_len:.0f} · {size_mb:.1f} MB")
    return 0


def _cmd_corpus_search(args: argparse.Namespace) -> int:
    from harness.corpus import index as corpus_index

    idx = corpus_index.BM25Index.load(corpus_index.index_path(args.corpus_dir))
    titles = {d.doc_id: d.title for d in corpus_store.iter_docs(args.corpus_dir)}
    for rank, hit in enumerate(idx.search(args.query, k=args.k), 1):
        print(f"{rank:2}. [{hit.score:7.3f}] {hit.doc_id}  {titles.get(hit.doc_id, '')[:90]}")
    return 0


def _cmd_tasks_calibrate(args: argparse.Namespace) -> int:
    from harness.corpus import index as corpus_index
    from rl.tasks import calibrate

    idx = corpus_index.BM25Index.load(corpus_index.index_path(args.corpus_dir))
    docs = list(corpus_store.iter_docs(args.corpus_dir))
    rows = calibrate.run(idx, docs, sample=args.sample, seed=args.seed)
    print(f"样本 {min(args.sample, len(docs))} 篇 · 语料 {len(docs)} 篇\n")
    print(calibrate.render(rows))
    print("\n读法：top-5 越高说明「一次检索就能找到源文档」，第一跳越假。")
    print("     『去罕见词』是改写效果的**下界**（真实 LLM 改写是替换而非删除，会更好）。")
    return 0


def _cmd_tasks_leakage(args: argparse.Namespace) -> int:
    """Measure how many questions a strong model answers with no tools at all."""
    import json

    from harness.corpus import CorpusStore
    from rl.tasks import leakage
    from rl.tasks.teacher import Teacher

    store = CorpusStore.load(args.corpus_dir, check_hash=False)
    tasks = store.tasks()
    if not tasks:
        print("语料里没有任务集——请先运行 corpus hotpot")
        return 1

    # A full sweep is hours long, so it appends as it goes and a re-run resumes
    # from whatever is already on disk rather than repaying for it.
    done: set[str] = set()
    # Questions another model already flagged as leaked are dropped from the
    # dataset regardless of this model's opinion, so asking about them buys
    # nothing for the union. Skipping them is the difference between checking
    # 4,834 questions and 7,405.
    for path in args.skip_leaked_from:
        if not os.path.isfile(path):
            print(f"⚠ 跳过清单不存在：{path}")
            continue
        with open(path, "r", encoding="utf-8") as f:
            already = {json.loads(line)["task_id"] for line in f
                       if line.strip() and json.loads(line).get("exact_match")}
        done |= already
        print(f"跳过 {os.path.basename(path)} 已判泄漏的 {len(already)} 题")

    if args.out and args.resume and os.path.isfile(args.out):
        with open(args.out, "r", encoding="utf-8") as f:
            done |= {json.loads(line)["task_id"] for line in f if line.strip()}
        print(f"续跑：{args.out} 里已有记录，跳过集合共 {len(done)} 题")

    teacher = Teacher(model=args.model, temperature=0.0)
    n = len(tasks) if args.all else args.n
    print(f"闭卷测试 {n} 题 · 模型 {teacher.model} · 并发 {args.concurrency}")

    handle = open(args.out, "a" if done else "w", encoding="utf-8") if args.out else None

    def on_chunk(fresh, so_far, total):
        if handle:
            for r in fresh:
                handle.write(json.dumps(r.__dict__, ensure_ascii=False) + "\n")
            handle.flush()
        rate = sum(1 for r in fresh if r.exact_match) / max(len(fresh), 1)
        print(f"  {so_far}/{total} · 本批泄漏 {rate:.1%} · 累计用量 {teacher.usage()['calls']} 次调用",
              flush=True)

    try:
        report = leakage.run(
            teacher, tasks, sample=n, seed=args.seed,
            concurrency=args.concurrency, chunk=args.chunk,
            on_chunk=on_chunk, skip_ids=done,
        )
    finally:
        if handle:
            handle.close()

    print()
    print(leakage.render(report))
    print()
    print(f"用量：{teacher.usage()}")
    if args.out:
        print(f"逐题结果写入 {args.out}")
    return 0


def _cmd_tasks_split(args: argparse.Namespace) -> int:
    """Freeze train/dev/test, dropping leaked questions and labelling easy ones."""
    import json

    from harness.corpus import CorpusStore
    from harness.corpus.index import BM25Index, index_path
    from rl.tasks import adversarial, split as split_mod

    store = CorpusStore.load(args.corpus_dir, check_hash=False)
    tasks = store.tasks()

    # A question is leaked if *any* model answered it closed-book. Measured on
    # 300 questions, deepseek-chat and deepseek-v4-pro agreed only 76.7% of the
    # time and neither set contained the other — chat knew 19 things pro did
    # not. Leakage is a property of (question, model), so a single-model filter
    # under-reports it, and the union is the conservative definition a benchmark
    # wants.
    # A question is leaked if *any* model answered it closed-book. Measured on
    # 300 questions, two DeepSeek models agreed only 76.7% of the time and
    # neither set contained the other, so leakage is a property of
    # (question, model) and the union is the conservative definition.
    #
    # `covered` tracks what every filter actually looked at. A sweep can stop
    # early (budget, rate limit), and a question only one filter reached is
    # more weakly filtered than its neighbours — see `split.build`.
    leaked: set[str] = set()
    covered: set[str] | None = None
    per_file: list[str] = []
    names: list[str] = []
    for path in args.leakage_file:
        if not os.path.isfile(path):
            print(f"⚠ 泄漏清单不存在，跳过：{path}")
            continue
        found, seen, unknown = set(), set(), 0
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                row = json.loads(line)
                # An empty prediction is a failed measurement, not a negative
                # verdict — see `leakage.LeakageResult.answered`. It counts as
                # neither leaked nor covered.
                if not (row.get("predicted") or "").strip():
                    unknown += 1
                    continue
                seen.add(row["task_id"])
                if row.get("exact_match"):
                    found.add(row["task_id"])
        leaked |= found
        # A filter run with --skip-leaked-from never saw the questions an
        # earlier filter already condemned; those count as covered, since they
        # are dropped either way.
        covered = seen if covered is None else (covered | leaked) & (seen | leaked)
        note = f"，另有 {unknown} 题未拿到答案（未知）" if unknown else ""
        per_file.append(f"{os.path.basename(path)}: 有效 {len(seen)}，判泄漏 {len(found)}{note}")
        names.append(os.path.basename(path).replace("leak_full_", "").replace(".jsonl", ""))

    if per_file:
        for line in per_file:
            print(f"  {line}")
        print(f"  → 泄漏并集 {len(leaked)} 题 · 全部过滤器共同覆盖 {len(covered or set())} 题")
    else:
        print("⚠ 未提供任何泄漏清单（--leakage-file），本次不丢弃任何题目")

    if not args.require_coverage:
        covered = None

    # The adversarial filter is a pure function of (question, corpus, index), so
    # its verdicts are cached by corpus hash. Recomputing 7,405 BM25 queries over
    # 66k documents takes ~10 minutes, and `tasks split` gets re-run every time a
    # leakage sweep finishes.
    cache_path = os.path.join(args.corpus_dir, f"one_query.{store.manifest.docs_sha256[:16]}.json")
    if os.path.isfile(cache_path):
        with open(cache_path, "r", encoding="utf-8") as f:
            one_query = set(json.load(f))
        print(f"对抗性过滤器：命中缓存 {os.path.basename(cache_path)}（{len(one_query)} 题一次可解）")
    else:
        print("跑对抗性一次查询过滤器…（首次会慢，之后按语料哈希缓存）")
        idx = BM25Index.load(index_path(args.corpus_dir))
        one_query = {
            row["task_id"] for row in tasks
            if adversarial.one_query_solvable(idx, row["question"], row.get("gold_doc_ids") or [])
        }
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(sorted(one_query), f)
        print(f"  已缓存到 {os.path.basename(cache_path)}")

    splits, funnel = split_mod.build(tasks, leaked, one_query, seed=args.seed,
                                     covered_ids=covered, filters=names)
    hashes = split_mod.write(args.out_dir, splits)
    print()
    print(funnel.render())
    print()
    for name, h in sorted(hashes.items()):
        print(f"  {name:<6} sha256 {h[:16]}…")
    print(f"\n写入 {args.out_dir}")
    return 0


def _cmd_rollout(args: argparse.Namespace) -> int:
    """Roll out N tasks against a model and report the aggregate."""
    import asyncio
    import random

    from harness.corpus import CorpusStore
    from rl.env.adapter import EvalAdapter
    from rl.env.build import PRESET
    from rl.rollout import engine
    from rl.rollout.trajectory import EnvStamp, write_jsonl
    from rl.verifiers import reward as reward_verifier

    store = CorpusStore.load(args.corpus_dir, check_hash=False)
    tasks = store.tasks()
    if not tasks:
        print("语料里没有任务集——请先运行 corpus hotpot")
        return 1

    if args.split:
        # A frozen split is the only thing a reported number may be conditioned
        # on; sampling the raw task set is for smoke tests only.
        from rl.tasks import split as split_mod

        tasks = split_mod.load(args.splits_dir, args.split)
        print(f"使用冻结切分 {args.split}（{len(tasks)} 题）")

    rng = random.Random(args.seed)
    chosen = rng.sample(tasks, min(args.n, len(tasks)))

    if args.base_url:
        llm = EvalAdapter.for_vllm(args.base_url, args.model, temperature=args.temperature)
    else:
        llm = EvalAdapter.from_settings(args.model, temperature=args.temperature)

    from harness.tools.registry import ToolRegistry
    registry = ToolRegistry(PRESET)
    stamp = EnvStamp(
        corpus_sha256=store.manifest.docs_sha256,
        corpus_docs=len(store),
        preset=PRESET,
        max_steps=registry.max_steps,
        model=llm.model,
    )

    weights = reward_verifier.Weights.outcome_only() if args.outcome_only else reward_verifier.Weights()
    print(f"rollout {len(chosen)} 题 · 模型 {llm.model} · 并发 {args.concurrency} · 调度 {args.schedule}")
    print(f"环境 {stamp.preset} · max_steps {stamp.max_steps} · 语料 {stamp.corpus_docs} 段 @ {stamp.corpus_sha256[:12]}…")
    print()

    trajectories, stats = asyncio.run(engine.run_many(
        chosen, engine.shared(llm), stamp,
        concurrency=args.concurrency, weights=weights, schedule=args.schedule,
        group_size=args.group_size,
    ))

    ok = [t for t in trajectories if t.ok]
    failed = len(trajectories) - len(ok)

    print()
    print(_render_rollout(ok, failed, stats))

    if args.out:
        write_jsonl(args.out, trajectories)
        print(f"\n轨迹写入 {args.out}（{len(trajectories)} 条）")

    if args.report:
        from rl.eval import report as report_mod

        by_id = {t["task_id"]: t for t in chosen}
        markdown = report_mod.render(
            trajectories, by_id, stats,
            title=f"评测报告 · {llm.model} · {len(chosen)} 题",
        )
        with open(args.report, "w", encoding="utf-8") as f:
            f.write(markdown)
        print(f"报告写入 {args.report}")
    return 0


def _render_rollout(ok, failed, stats) -> str:
    """Aggregate straight off the stored reward dicts."""
    n = max(len(ok), 1)
    def mean(key, default=0.0):
        return sum(t.reward.get(key, default) or 0 for t in ok) / n
    def frac(pred):
        return sum(1 for t in ok if pred(t.reward)) / n

    lines = [
        f"完成 {len(ok)} 条" + (f"（另有 {failed} 条因错误失败）" if failed else ""),
        "",
        f"  pass@1（正确且非作弊）   {frac(lambda r: r.get('correct')):.1%}",
        f"  exact match              {frac(lambda r: r.get('exact_match')):.1%}",
        f"  token F1                 {mean('f1'):.3f}",
        f"  格式合法                 {frac(lambda r: r.get('format_valid')):.1%}",
        f"  被判作弊作废             {frac(lambda r: r.get('voided')):.1%}",
        f"  放弃/超步                {frac(lambda r: r.get('ended_by') in ('no-tool-call', 'max-steps')):.1%}",
        f"  平均步数                 {mean('steps'):.1f}",
        f"  平均工具调用             {mean('tool_calls'):.1f}",
        f"  引用扎实度 F1            {mean('grounding_f1'):.3f}",
        f"  重复检索比例             {mean('redundant_query_fraction'):.1%}",
        "",
        "吞吐：",
    ]
    for k, v in stats.to_dict().items():
        lines.append(f"  {k:<22} {v}")
    return "\n".join(lines)


def _cmd_export(args: argparse.Namespace) -> int:
    """Export trajectories for an external trainer (verl / TRL / anything)."""
    from rl.rollout.trajectory import read_jsonl
    from rl.train import export_verl

    trajectories = read_jsonl(args.trajectories)
    stats = export_verl.export(trajectories, args.out, args.format)
    print(stats.render())
    print(f"校验：{export_verl.verify(args.out, args.format)}")
    print(f"写入 {args.out}")
    return 0


def _cmd_setup(args: argparse.Namespace) -> int:
    """Bootstrap a fresh clone: corpus, index, tokenizer, and a hash check.

    The repository commits what is expensive or irreproducible (leakage
    verdicts, frozen splits, the chat template, the corpus manifest) and leaves
    out what a few minutes of compute regenerates (the corpus itself, its index,
    the tokenizer binaries). This command closes that gap in one step, and
    **verifies the rebuild matches the manifest** — otherwise every number in
    the README would be measured against a different corpus than the one you
    just built.
    """
    import json

    from harness.corpus import index as corpus_index

    expected = None
    manifest_path = os.path.join(args.corpus_dir, "manifest.json")
    if os.path.isfile(manifest_path):
        with open(manifest_path, "r", encoding="utf-8") as f:
            expected = json.load(f).get("docs_sha256")

    docs = os.path.join(args.corpus_dir, "docs.jsonl")
    if os.path.isfile(docs) and not args.force:
        print(f"语料已存在，跳过构建（--force 可强制重建）")
    else:
        print("构建语料（HotpotQA distractor，约 3 分钟，不需要 API）…")
        _cmd_corpus_hotpot(argparse.Namespace(
            split="validation", corpus_dir=args.corpus_dir))

    if expected:
        actual = corpus_store.sha256_file(docs)
        if actual != expected:
            print(f"\n⚠ 重建出的语料与仓库清单不一致：")
            print(f"    清单 {expected[:16]}…")
            print(f"    实际 {actual[:16]}…")
            print("  上游数据集可能变了。冻结的 split 与泄漏清单是针对清单那份语料的，")
            print("  在这份语料上测出的数字不能与 README 里的直接比较。")
            return 1
        print(f"✓ 语料哈希与仓库清单一致（{expected[:16]}…）")

    idx_path = corpus_index.index_path(args.corpus_dir)
    if os.path.isfile(idx_path) and not args.force:
        print("索引已存在，跳过")
    else:
        print("建立 BM25 索引…")
        _cmd_corpus_index(argparse.Namespace(corpus_dir=args.corpus_dir))

    if args.tokenizer:
        print("预取 tokenizer（避免训练时才发现拿不到）…")
        from rl.env import template

        tok = template.tokenizer()
        print(f"✓ tokenizer 就绪：{tok.__class__.__name__} · "
              f"模板 sha256 {template.template_sha256()[:16]}…")

    splits_dir = os.path.join(REPO_ROOT, "rl", "data", "splits")
    if os.path.isfile(os.path.join(splits_dir, "splits.json")):
        with open(os.path.join(splits_dir, "splits.json"), "r", encoding="utf-8") as f:
            info = json.load(f)
        print(f"✓ 冻结切分已随仓库提供：{info.get('sizes')}")
    else:
        print("⚠ 没有找到冻结切分。可用仓库里的泄漏清单重建：")
        print("    python -m rl.cli tasks split --require-coverage \\")
        print("        --leakage-file rl/data/leakage/chat.jsonl \\")
        print("        --leakage-file rl/data/leakage/v4-flash.jsonl")

    print("\n下一步：python -m rl.checks.rl_check --offline")
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    # httpx logs one INFO line per request; a 500-episode rollout makes several
    # thousand of them and buries the actual report.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    parser = argparse.ArgumentParser(prog="rl.cli", description="天启·深研场 RL 工作台")
    sub = parser.add_subparsers(dest="group", required=True)

    corpus = sub.add_parser("corpus", help="语料构建与校验").add_subparsers(dest="action", required=True)

    common = dict(corpus_dir=DEFAULT_CORPUS_DIR)
    b = corpus.add_parser("build", help="拉取并冻结语料")
    b.add_argument("--target", type=int, default=50000, help="目标文档数")
    b.add_argument("--since", default="2023-01-01", help="最早提交日期")
    b.add_argument("--until", default=date.today().isoformat(), help="最晚提交日期")
    b.add_argument("--categories", default="", help="逗号分隔，默认 cs.AI,cs.CL,cs.CV,cs.LG")
    b.add_argument("--snapshot", default="", help="本地 arXiv 元数据快照 JSONL 路径；留空则走 API")
    b.add_argument("--corpus-dir", default=common["corpus_dir"])
    b.set_defaults(func=_cmd_corpus_build)

    h = corpus.add_parser("hotpot", help="用 HotpotQA 构建语料与任务集")
    h.add_argument("--split", default="validation", choices=["validation", "train"])
    h.add_argument("--corpus-dir", default=common["corpus_dir"])
    h.set_defaults(func=_cmd_corpus_hotpot)

    v = corpus.add_parser("verify", help="校验语料哈希与清单一致")
    v.add_argument("--corpus-dir", default=common["corpus_dir"])
    v.set_defaults(func=_cmd_corpus_verify)

    s = corpus.add_parser("stats", help="语料规模与字段统计")
    s.add_argument("--with-tasks", action="store_true", help="一并统计任务集难度/题型分布")
    s.add_argument("--corpus-dir", default=common["corpus_dir"])
    s.set_defaults(func=_cmd_corpus_stats)

    i = corpus.add_parser("index", help="建立 BM25 检索索引")
    i.add_argument("--corpus-dir", default=common["corpus_dir"])
    i.set_defaults(func=_cmd_corpus_index)

    q = corpus.add_parser("search", help="对索引做一次检索（人工抽查用）")
    q.add_argument("query")
    q.add_argument("-k", type=int, default=5)
    q.add_argument("--corpus-dir", default=common["corpus_dir"])
    q.set_defaults(func=_cmd_corpus_search)

    tasks = sub.add_parser("tasks", help="任务合成与过滤").add_subparsers(dest="action", required=True)
    c = tasks.add_parser("calibrate", help="测量第一跳需要多强的改写（无需模型）")
    c.add_argument("--sample", type=int, default=200)
    c.add_argument("--seed", type=int, default=0)
    c.add_argument("--corpus-dir", default=common["corpus_dir"])
    c.set_defaults(func=_cmd_tasks_calibrate)

    lk = tasks.add_parser("leakage", help="测量闭卷可答率（环境是否空转的头号风险）")
    lk.add_argument("-n", type=int, default=300, help="抽样题数")
    lk.add_argument("--model", default="", help="留空用 provider 默认模型")
    lk.add_argument("--all", action="store_true", help="跑全部题目，忽略 -n")
    lk.add_argument("--seed", type=int, default=0)
    lk.add_argument("--concurrency", type=int, default=8)
    lk.add_argument("--out", default="", help="逐题结果写到这个 jsonl（分批追加）")
    lk.add_argument("--chunk", type=int, default=250, help="每批题数，跑完一批就落盘")
    lk.add_argument("--resume", action="store_true", help="跳过 --out 里已有的题")
    lk.add_argument("--skip-leaked-from", action="append", default=[],
                    help="另一个模型的泄漏清单；其中已判泄漏的题不再复核（可重复传）")
    lk.add_argument("--corpus-dir", default=common["corpus_dir"])
    lk.set_defaults(func=_cmd_tasks_leakage)

    sp = tasks.add_parser("split", help="切分并冻结 train/dev/test")
    sp.add_argument("--leakage-file", action="append", default=[],
                    help="tasks leakage --out 产生的 jsonl；可重复传，按并集过滤")
    sp.add_argument("--seed", type=int, default=0)
    sp.add_argument("--require-coverage", action="store_true",
                    help="只保留被所有泄漏过滤器都复核过的题（过滤强度一致，但题数更少）")
    sp.add_argument("--out-dir", default=os.path.join(REPO_ROOT, "rl", "data", "splits"))
    sp.add_argument("--corpus-dir", default=DEFAULT_CORPUS_DIR)
    sp.set_defaults(func=_cmd_tasks_split)

    ro = sub.add_parser("rollout", help="对若干题跑 rollout 并评分")
    ro.add_argument("-n", type=int, default=20)
    ro.add_argument("--seed", type=int, default=0)
    ro.add_argument("--concurrency", type=int, default=8)
    ro.add_argument("--schedule", default="refill", choices=["refill", "batch"])
    ro.add_argument("--model", default="", help="留空则用 AI_PROVIDER 的默认模型")
    ro.add_argument("--base-url", default="", help="vLLM 地址；留空走配置的 provider")
    ro.add_argument("--temperature", type=float, default=0.7)
    ro.add_argument("--group-size", "-G", type=int, default=1,
                    help="每题采样几条轨迹。训练用 G>1（GRPO 的组），评测用 1")
    ro.add_argument("--outcome-only", action="store_true", help="只用结果奖励（消融基线）")
    ro.add_argument("--out", default="", help="轨迹写到这个 jsonl")
    ro.add_argument("--report", default="", help="Markdown 报告写到这个文件")
    ro.add_argument("--split", default="", help="用冻结的切分而不是整个任务集（train/dev/test）")
    ro.add_argument("--splits-dir", default=os.path.join(REPO_ROOT, "rl", "data", "splits"))
    ro.add_argument("--corpus-dir", default=DEFAULT_CORPUS_DIR)
    ro.set_defaults(func=_cmd_rollout)

    st = sub.add_parser("setup", help="新克隆的一键准备：语料 + 索引 + tokenizer + 哈希校验")
    st.add_argument("--force", action="store_true", help="已存在也重建")
    st.add_argument("--no-tokenizer", dest="tokenizer", action="store_false",
                    help="跳过 tokenizer 预取（离线环境用）")
    st.add_argument("--corpus-dir", default=DEFAULT_CORPUS_DIR)
    st.set_defaults(func=_cmd_setup)

    ex = sub.add_parser("export", help="把轨迹导出给外部训练框架")
    ex.add_argument("trajectories", help="rollout --out 产生的 jsonl")
    ex.add_argument("--format", default="tokens", choices=["messages", "tokens"],
                    help="tokens 保留 loss mask（推荐）；messages 是通用会话格式")
    ex.add_argument("--out", required=True)
    ex.set_defaults(func=_cmd_export)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
