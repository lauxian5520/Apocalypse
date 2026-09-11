# 10 · 子代理与多智能体协作

## 10.1 为什么需要子代理

> **严谨定义**：多智能体系统（Multi-Agent System）中的"委派（Delegation）"模式，指一个 Agent
> （主 Agent / Orchestrator）把某个子任务的完整执行过程交给另一个独立运行的 Agent（子 Agent /
> Subagent）负责，主 Agent 只关心子 Agent 最终返回的结论，不需要（也不应该）介入子任务内部的
> 每一步细节。
>
> **大白话**：主 Agent 好比一个项目经理，遇到"帮我把这三个文件都按新规范改一遍"这种明确、
> 独立、不需要频繁汇报进度的子任务时，不用亲自一步步做，而是"派活"给一个专门的执行者——
> 执行者自己决定怎么读文件、怎么改、改完什么样算完成，最后只需要给项目经理一句"做完了，
> 结果是这样"的汇报。这样做的好处：**主 Agent 的对话上下文不会被子任务的几十步中间过程
> 淹没**——如果不委派，这些中间步骤（读了什么文件、每一步的思考过程）全都会挤进主对话
> 的上下文窗口，让主对话越来越臃肿、越来越贵。

## 10.2 角色定义：和预设长得一样，但读者不同

```json
// harness/data/agents/coder.json
{
  "label": "工程员",
  "tools": ["read", "write", "edit", "glob", "grep", "load_skill", "current_time"],
  "max_steps": 8,
  "description": "适合需要读写工作区文件、按现有代码风格修改多个文件的任务"
}
```

角色定义文件（`data/agents/<名字>.json` + 同名 `.md` 提示词）在结构上和预设文件
（`data/presets/*.json`）**完全一样**——都有 `tools`、`max_steps`、系统提示词——共用同一套
`ToolRegistry` 加载逻辑。**但两者的"读者"不一样**：预设是给**人**在界面上选的（"这次对话
要跑 `standard` 还是 `minimal` 模式"），角色定义的 `description` 字段是给**模型**看的
（"遇到什么样的任务应该委派给这个角色"）——所以措辞风格必须是"何时该用我"，而不是一句
UI 上的展示标签。

内置两个角色：

- **`researcher`**（查证员）：只读权限，用来做信息检索/事实核查类子任务；
- **`coder`**（工程员）：能读写工作区，**没有 `shell`、没有 `subagent`**——不能再往下派发，
  这既是产品设计的选择，也和下面讲的深度限制机制互相印证。

## 10.3 委派入口：`subagent` 工具

```python
async def subagent(ctx, task: str, agent: str, context: str = "") -> str:
    if ctx.depth >= settings.harness_subagent_max_depth:      # 第 1 道闸
        return "已达到委派深度上限，不能再往下派发"
    if len(task) > MAX_TASK_CHARS:                             # 4000 字符
        return "任务描述过长"
    if agent not in agents.names():
        return f"未知的子代理角色：{agent}"
    if manager.count_children(ctx.session_id) >= settings.harness_subagent_max_per_session:  # 第 2 道闸
        return "本会话委派次数已达上限"

    child = manager.create_subagent(ctx.session_id, agent, task)
    child_ctx = build_subagent_context(child.id, agent, ctx.workspace, ctx.sandbox, ctx.depth)
    ctx.emit(SUBAGENT_START, {"child_id": child.id, "agent": agent})
    try:
        answer = await _run(child_ctx, task, context)
    finally:
        manager.finalize_turn(child_ctx.session_id)
    ctx.emit(SUBAGENT_END, {"child_id": child.id, ...})
    return _report(answer, ...)
```

**四道花钱的闸门，全部检查在"真正开始跑之前"**：

| 闸门 | 环境变量 | 默认值 | 检查什么 |
|---|---|---|---|
| 委派深度 | `HARNESS_SUBAGENT_MAX_DEPTH` | `1` | 子代理不能再派子代理（`ctx.depth` 只会是 0 或 1） |
| 单会话次数 | `HARNESS_SUBAGENT_MAX_PER_SESSION` | `16` | 累计计数，即使跨多次重启也依然准确（存数据库，不是内存计数器） |
| 单次步数 | `HARNESS_SUBAGENT_MAX_STEPS` | `8` | 一次子运行内部最多跑几步 |
| 墙钟超时 | `HARNESS_SUBAGENT_TIMEOUT_SECONDS` | `300` | 一次子运行最多跑多久 |

> **面试话术**：这是"在真正花钱之前，先把所有能提前判断的限制条件挨个检查一遍"的具体实践——
> 类似于数据库事务里的"乐观锁校验放在真正写入之前"，或者支付系统里"先做风控规则判断，
> 再真正扣款"。深度检查排最前面，因为它"检查代价最低、能拦下最大范围的滥用"（防止指数级
> 递归委派）。

### 委派深度为什么恰好是 1（而不是允许更深的递归）

`coder`/`researcher` 这两个内置角色的工具清单里**都不包含 `subagent`**——不是靠深度检查
"运行时兜底拦截"，而是从工具可用性上**直接不给这个能力**。深度限制本身是"防御性的第二层"，
即使未来有人手写一个角色定义、不小心把 `subagent` 塞进了它的工具清单，深度检查依然能兜底。
这是"权限最小化"和"纵深防御"两条原则叠加的一个具体例子。

## 10.4 `build_subagent_context()`：为什么不直接传父 context

```python
def build_subagent_context(session_id, agent, parent_workspace, parent_sandbox, depth):
    return HarnessContext(
        session_id=session_id,
        store=SqliteSessionStore(),          # 全新的、独立的
        llm=build_adapter(...),               # 全新的、独立的
        tools=ToolRegistry(agent_definition),
        workspace=parent_workspace,            # 直接复用父会话的 workspace 对象
        sandbox=parent_sandbox,                # 直接复用父会话的 sandbox 对象
        hooks=build_hooks(strict=True),        # 严格审批策略
        depth=depth + 1,
        max_steps=min(agent_definition.max_steps, settings.harness_subagent_max_steps),
    )
```

参数列表里**故意**传的是 `parent_workspace`/`parent_sandbox` 这两个具体对象，而不是
"把整个父 `HarnessContext` 传进去"。这个细节值得多想一层：

> **大白话**：如果传的是整个父 context，子代理的工具处理函数（handler）理论上就有能力
> "顺着这根线"摸到父会话的一切——父会话的存储实例、父会话的 LLM 适配器、甚至父会话专属的
> 钩子配置。这些东西子代理**根本不需要**，也**不应该**碰得到。只传"真正需要共享的两个东西"
> （工作区目录、沙箱执行器——子代理要能操作和父 Agent 同一份文件），其余全部重新构建一份
> 独立实例——这是"最小知识原则（Principle of Least Knowledge，也叫 Law of Demeter）"
> 的具体体现：一个组件只应该知道它真正需要协作的那一部分，不该拿到"能通到整个系统"的引用。

### 为什么工作区和沙箱要共享，其他都不共享

子代理和父会话**共用同一个工作区目录**——这是有意为之，而不是疏漏。README 明确解释：
"这样'把这三个文件改一遍'这类任务能直接落地"——如果子代理有自己独立的工作区，那它改的
文件父会话根本看不到，委派就失去了意义。而 `store`（事件日志存储）和 `llm`（模型适配器）
是无状态的、每次会话独立构建代价很低的东西，没有必要共享，独立一份反而让子代理的日志
和主会话的日志边界清晰（各自的完整轨迹分别保存，方便追溯到底哪一步是子代理干的）。

## 10.5 `_run()`：驱动子代理，同时监听两个"该停下来"的信号

```python
async def _run(child_ctx, task, context):
    deadline = time.monotonic() + settings.harness_subagent_timeout_seconds
    steps, answer = 0, ""
    try:
        async for event in run_turn(child_ctx, prompt):
            if event.type == STEP_START:
                steps += 1
            if event.type == ASSISTANT_MESSAGE and event.data.get("content"):
                answer = event.data["content"]     # 只留"最新的非空回复"，让最终总结覆盖前面的碎片叙述
            if interrupt.is_set(ctx.session_id):     # 父会话被打断了
                interrupt.request(child_ctx.session_id)
                break
            if time.monotonic() > deadline:          # 子运行自己超时了
                interrupt.request(child_ctx.session_id)
                break
    except Exception:
        answer = answer or "子代理没有给出结论"       # 任何异常都不能往上抛，只能变成一句话
    return answer, steps
```

三个值得单独拆开说的设计点：

### 1. 父会话被打断，要连带打断正在跑的子代理

`ctx.depth`/父子关系意味着"父 Turn 的这一次工具调用（也就是这次委派）正在等子代理跑完
才能返回"——如果用户点了"停止生成"，中断信号打在**父会话**的 `interrupt` 令牌上，
但父 Agent 的主循环此刻正阻塞在 `await _run(...)` 这一行代码里，它自己根本走不到下一次
"检查是否被打断"的时机点（见 [06](06-harness-Agent执行循环.md) 的中断机制一节）。
`subagent.py` 里主动做了这一层"信号转发"——每一轮事件都顺手检查一下父会话是否被打断了，
一旦发现，就主动把中断请求也打在**子会话**自己的令牌上，让子代理的循环在它自己的下一个
检查点尽快停下来。这不是"框架自动帮你做的事"，是委派这个功能自己额外补上的一层信号传递。

### 2. 子代理内部的异常，绝不允许往上传播

任何在子代理运行过程中抛出的异常都被吞掉，转换成一句"子代理没有给出结论"，作为**正常的
返回值**交还给主 Agent，**不会**变成一次未捕获的异常打断主 Agent 的整个 Turn。这和
[07](07-harness-工具系统与审批.md) 讲的"工具失败是值，不是异常"是完全一致的设计哲学——
子代理本身也可以被看作一次"很复杂的工具调用"，遵循同一套"失败要以模型能读懂的文本形式
喂回去，而不是让基础设施层面的异常打断整条推理链"的原则。

### 3. `finally: manager.finalize_turn(child_ctx.session_id)`

即使子代理这一次运行抛出了异常、或者被中途打断，也要保证它对应的会话状态最终被"收尾"，
不会永远卡在 `running`——这正是 [05](05-harness-事件系统与会话存储.md) 里讲的
"流式清理为什么不能只依赖生成器的 `finally`"同一个道理在这里的又一次应用。

## 10.6 费用汇总：为什么不能"分开算"

[05](05-harness-事件系统与会话存储.md) 已经提过 `usage_summary()` 会递归汇总所有子会话
的用量，这里补充一个真实的观察（README 原文）：

> "实测一次派发里子代理花掉的 token 比主 Agent 还多，分开算就会严重低估。"

这是一个很实用的经验：**委派并不是"免费"的**——子代理内部要独立跑完整的思考-工具调用循环
（可能好几步），累计消耗的 token 完全可能超过主 Agent 本身直接完成同样任务所花的 token。
委派换来的是"主对话上下文更干净"，不是"总花费更低"——如果只统计主 Agent 自己那部分的
`llm/usage` 事件，会严重低估这次委派的真实成本，这也是为什么费用统计必须做递归汇总的
真正动机所在。

## 10.7 子代理不出现在侧栏，但可以从轨迹里点进去看

[05](05-harness-事件系统与会话存储.md) 提到 `list_for_user()` 会过滤掉所有
`parent_id` 非空的会话——子代理不会挤占用户在侧边栏看到的会话列表。但完整的中间过程
**没有丢失**——子代理有自己独立、完整的事件日志，前端轨迹检查器提供"从父会话的
`subagent/end` 事件卡片，点进去查看子代理的完整轨迹"的导航路径（详见
[12](12-harness-HTTP接口与前端联调.md)）。这是"减少界面噪音"和"保留完整可追溯性"
两个目标同时达成的一个具体设计——不是简单粗暴地"不展示等于不记录"。

## 10.8 本章小结

- 委派的核心价值：把复杂子任务的中间过程从主对话上下文里"搬出去"，只让最终结论回流。
- 四道花钱闸门（深度/次数/步数/墙钟）全部在真正发起前检查，深度限制同时也体现在
  内置角色的工具清单里，是两层独立的防护。
- 子代理只共享工作区和沙箱，其余组件独立重建，遵循最小知识原则。
- 中断需要主动转发给子会话；异常绝不允许向上传播，一律转换成文本结论；费用必须递归汇总。

下一章会讲一批贯穿整个 Harness 系统的"工程细节"——上下文压缩的真实触发条件、流式分片的
批处理、思考型模型的踩坑——这些往往是面试官"往细节里追问"时最能体现你是否真的读懂了
这套系统的地方。
