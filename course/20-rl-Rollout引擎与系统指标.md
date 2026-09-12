# 20 · Rollout 引擎与系统指标

MiniMax 在介绍自研 RL 系统 Forge 时指出：**rollout 往往占 RL 循环墙钟时间的大部分**，
他们关注多轮交互、工具调用、复杂环境的支持，以及怎样降低长尾延迟。
美团 LongCat 的 RL 框架岗要打通策略更新、环境交互和奖励建模的端到端流程。

这一章讲的就是这一块，也是整个项目里最容易被算法向的人跳过、但最容易量化出成果的一块。

---

## 20.1 先看一个数字

第一次跑 6 条轨迹时的实测：

```
p50_seconds   9.06
p95_seconds   235.71
```

> **中位数 9 秒，p95 是 236 秒。** 一条轨迹跑了 4 分钟，而一半的轨迹 9 秒就结束了。

这就是长尾。理解它为什么出现，以及它怎么吃掉你的算力，是这一章的全部内容。

为什么这里的长尾特别重：轨迹长度天然差异巨大。一道题三步就解出来，
另一道题把 `max_steps=12` 全烧完；每一步还要等一次模型生成。
两者的墙钟差一个数量级是常态，不是异常。

---

## 20.2 批同步 vs 连续补位

> **严谨定义**：批同步（batch-synchronous）调度指启动 N 个任务、等全部完成、再启动下一批。
> 连续补位（continuous refill）指始终保持 N 个任务在飞，任何一个结束就立刻补进下一个。
>
> **大白话**：批同步像**一起过红灯**——最快的那辆车也得等最慢的那辆。
> 连续补位像**车位管理**——空出一个立刻放一辆进来。

批同步就是 `asyncio.gather` 套在分块上，很自然会写出来：

```python
for i in range(0, len(tasks), concurrency):
    chunk = tasks[i:i + concurrency]
    done = await asyncio.gather(*(run_episode(t, ...) for t in chunk))
```

它的上界是**每批最慢那条**。结合 20.1 的数字：一批 16 条里只要有一条跑 236 秒，
其余 15 条在 9 秒就做完了，**剩下 227 秒里只有 1/16 的并发在工作**。

连续补位是一个信号量加一个 per-episode 的 task，大约三十行
（`rl/rollout/engine.py::run_many`）：

```python
semaphore = asyncio.Semaphore(concurrency)

async def guarded(task):
    # 信号量就是「补位」的全部：一个 episode 结束的瞬间释放槽位，
    # 排队中的下一个立刻拿到，而不是等它那一批对齐。
    async with semaphore:
        return await run_episode(task, policy, stamp, weights)

pending = [asyncio.create_task(guarded(t)) for t in tasks]
for coro in asyncio.as_completed(pending):
    results.append(await coro)
```

**`--schedule batch` 被刻意保留下来**，专门用来产出对照数字。

> **一个方法论习惯**：不要只实现好的那个方案。把差的那个也留着、
> 用同一套指标量出来，你就有了「优化前后」而不是「我认为这样更好」。

---

## 20.3 怎么把长尾量出来

`RunStats.to_dict()`（`rl/rollout/engine.py`）输出的几个数字，
每一个都对应一个能采取的行动：

| 指标 | 读法 |
|---|---|
| `throughput_per_min` | 最终成果数字 |
| `p50 / p95 / p99` | 分布形状；p95/p50 的比值就是长尾有多重 |
| `slowest_5pct_share` | **最慢 5% 占了总 episode 时长的多大比例** |
| `utilisation` | 并发占用率 = 总 episode 时长 /（墙钟 × 并发） |

`utilisation` 是最直接的浪费度量：

> **大白话**：并发开了 16，但如果占用率只有 33%，就等于有 10 个多的槽位全程空着。
> 批同步调度的占用率天花板就是被长尾压死的。

实测那次 6 条轨迹的 `utilisation` 是 0.33——因为 concurrency=6、任务只有 6 条，
根本没有补位的机会。这也说明一件事：**补位的收益需要任务数远大于并发数才体现**。

`generation_seconds` 单独统计（在 provider 里花的时间），
于是墙钟可以拆成「生成 / 环境 / 奖励 / 开销」。

> 这里有个我自己引入又修掉的 bug 值得记：为了读 `generation_seconds`，
> 我在 `run_many` 结尾**多调了一次策略工厂**，结果每轮多造一个适配器。
> 改成按 episode 量增量（前后差值），对「共用一个适配器」和「每 episode 一个」都正确。

---

## 20.4 一条轨迹失败，不能带走一整轮

```python
except Exception as e:      # noqa: BLE001 - one episode must not kill a run
    trajectory.error = f"{type(e).__name__}: {e}"
```

`run_episode` **永不抛出**。provider 抽风、某道题触发一个边界情况——
坏的那一条被记下 `error` 并计入失败率，其余几百条继续。

> **大白话**：跑 500 条轨迹跑到第 300 条挂了，不能让前 299 条白费。
> 而且失败的那条要**留下来、算进分母**，不能悄悄消失——
> 否则失败率会伪装成「样本量少一点」。

这一条在实测里立刻救过场：第一次 smoke rollout 时
`ToolRegistry object is not iterable` 让三条轨迹全失败，
但引擎照常跑完、照常出报告、错误照常被记下来——我一眼就看到了原因。

---

## 20.5 一个便宜的吞吐优化：分组相邻

GRPO 要每题 G 条 rollout。`run_many` 的 `group_size` 参数把任务列表展开成：

```python
tasks = [task for task in tasks for _ in range(group_size)]
# → [a, a, a, a, b, b, b, b]
```

**同一道题的 G 份刻意相邻**：provider 的前缀缓存按共享前缀计费，
而这 G 条共享从 system prompt 到第一个采样 token 之前的**全部内容**。

第 11 章量过前缀缓存的效果（约省下 8000/9900 的输入 token）。这里是同一个机制，
只是免费——只需要把展开顺序写对。

> 面试时这条可以这样讲：「G=8 分组时前缀缓存命中率 vs 打散时的命中率」
> 是一个可以直接量的数字，而且优化成本是零。

---

## 20.6 为什么 rollout 走非流式

`ModelAdapter.stream()` 只要求返回一个异步迭代器，且最后一个 delta 带着
组装好的 `LLMResult`。**只 yield 一个这样的 delta 是完全合法的**
（`rl/env/adapter.py::EvalAdapter.stream`）。

读 `agent.py` 的 `_request_model` 就知道循环会怎么做：第一个 delta 的
`result is not None`，于是 chunk 缓冲区排空、直接 return。结果是：

- 轨迹里**零条 `assistant/chunk` 事件**
- 循环不做逐 token 的生成器跳转

> **大白话**：rollout 要的是「说完的那一整段话」，不是打字机效果。
> 而且日志小了之后，循环每步重读整个日志的代价也就无所谓了。

这是「可替换接缝」在 RL 场景下的第三次兑现（前两次是 store 和 adapter）——
改一个实现，不动循环。

---

## 20.7 内存 store 为什么是必需的，不只是方便

`agent.py` 的循环有两个地方会重读整个日志：
`_request_model`（每步一次投影）和 `_run_pending_calls`（每个待处理工具调用一次）。

一条 12 步的轨迹会有几十次完整读取。

| | SQLite | 内存 list |
|---|---|---|
| 每次读取 | 一次 DB 查询 | 一次切片 |
| 需要 user 外键 | **要**（`manager.create()`） | 不要 |
| 持久化 | 每条事件 fsync | 无 |

第二行是硬阻塞：`manager.create()` 需要一个真实的 `users.id`，
而一条 rollout 没有用户。`tools/harness_probe.py` 的绕法是建一个专用的禁用账号——
对探针可以，对训练不行。

`rl/env/memory_store.py` 实现 `SessionStore` 的四个方法就解决了。
**代价是必须证明两者等价**：自检里有一个阶段断言两种 store 产生的
`derive_messages()` 结果**逐字节相同**，因为「rollout 可以跳过数据库」这个前提就靠它。

---

## 20.8 本章小结

- 实测 p50=9s / p95=236s：多轮 Agent rollout 的长尾是常态，不是异常。
- 批同步调度的上界是每批最慢那条；连续补位是一个信号量加三十行。
  **把差的方案也留着**，才能量出「优化前后」。
- `slowest_5pct_share` 和 `utilisation` 是两个能直接对应行动的浪费度量。
- 一条轨迹失败不能带走一整轮，且失败要留在分母里。
- G 份相邻是零成本的前缀缓存优化。
- 非流式适配器让轨迹零 chunk 事件——接缝的第三次兑现。
- 内存 store 不只是快：它绕开了 `manager.create()` 的用户外键这个硬阻塞，
  代价是必须断言与 SQLite 的投影逐字节相同。

---

## 20.9 这一部分的整体产出（给面试用）

RL 这六章（15–20）合起来能拿出去的东西：

**环境与数据**
- 语料 66,581 段、`sha256` 可校验、Pi 上约 3 分钟可复现
- 任务漏斗：7405 → 泄漏丢 3684 → 覆盖不全丢 897 → **2824**
- arXiv→HotpotQA 的实测推翻（BM25 第二名/第一名 0.28 → 0.84）

**奖励与验证器**
- 精确匹配 + 三条反作弊，命中即作废
- 塑形项门控在正确性之后（附「立刻空答」的失败机制）
- n=3 抓到的 comparison 假阳性

**分词与算法**
- `arguments | tojson` 双重编码，本地套模板 + 传 token id 的解法
- 可训练 token 只占 7–12%
- token 级 vs 按序列归一化：`-0.667 vs +0.000`

**系统**
- p50/p95 长尾、`utilisation`、连续补位 vs 批同步
- LoRA 热插拔省掉全部权重同步代码

**验证**
- `rl_check` 20 个阶段，含前缀性质、EOS、两种 store 投影一致、两份 advantage 实现一致

按第 14 章的用法：不要背，确保每一条你能用自己的话说出**为什么这样做，以及不这样做会怎么错**。
这一部分的每一章都刻意把「错法」写在了「对法」旁边，就是为了这个。
