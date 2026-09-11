# 06 · Agent 执行循环

`backend/harness/loop/agent.py` 是整个 Harness 的心脏——`run_turn()` 驱动一整轮对话，
是 [04](04-harness-总览与两大设计原则.md) 里那张流程图的真正实现。这一章逐层拆解它的控制流。

## 6.1 入口：`run_turn()` 和 `resume_turn()`

```python
async def run_turn(hctx, user_text):
    interrupt.begin(hctx.session_id)
    if user_text:
        hctx.emit(USER_MESSAGE, {"text": user_text})
    hctx.emit(TURN_START, {})
    try:
        async for event in _drive(hctx):
            yield event
    finally:
        interrupt.finish(hctx.session_id)
```

**两个入口，对应两种场景**：

- `run_turn()`：用户发一条新消息，开启新的一轮。
- `resume_turn(hctx, call_id, approved)`：某个工具调用之前卡在"等待人工审批"，现在人给出了
  批准/拒绝的答复，继续跑下去。

`resume_turn` 的分支逻辑：

```python
if approved:
    await _execute(hctx, call)          # 跳过审批检查，直接执行——已经问过了
else:
    hctx.emit(TOOL_RESULT, {..., "denied": True, "is_error": True})
# 同一条 assistant 消息里可能还有其他待处理的 tool_call，一并处理完
await _run_pending_calls(hctx)
async for event in _drive(hctx):        # 继续走主循环
    yield event
```

## 6.2 主循环：`_drive()` 一步步拆解

这是全文件最核心的函数，`for step in range(hctx.max_steps)` 循环体依次做以下事：

### 第 1 步：检查中断

```python
if interrupt.is_set(hctx.session_id):
    hctx.emit(AGENT_INTERRUPT, {})
    break
```

每一步开始前先看看用户是不是点了"停止生成"——这是[中断机制](#66-中断机制为什么只能在边界处生效)
的检查点之一。

### 第 2 步：`PRE_STEP` 钩子

一个可挂载的"步骤开始前"检查点，如果某个监听器返回"拒绝"，直接结束这一步并记一条 `agent/error`。
标准部署没有挂东西在这个点上，但它是一个天然的扩展位——比如后续要加"单会话每日 step 数硬上限"，
只需要在这里挂一个监听器，不用碰循环本身的代码。

### 第 3 步：请求模型（流式）

```python
hctx.emit(STEP_START, {})
result = None
async for delta in _request_model(hctx):
    if isinstance(delta, LLMResult):
        result = delta
    # 否则是流式分片，已经在 _request_model 内部处理并 emit 了
```

`_request_model()` 内部会先跑一次[上下文压缩检查](11-harness-成本上下文压缩与工程细节.md)，
再调用 `derive_messages()` 拿到要发的消息，然后调用 `ModelAdapter.stream()`。

如果 `result is None`（意味着流式过程中被打断，还没等到模型说完），emit 一个
`step/end{stopped: true}` 然后 `break`——这一轮直接结束，不算完整的一步。

### 第 4 步：落盘模型的回复

```python
hctx.emit(ASSISTANT_MESSAGE, {
    "content": result.content,
    "reasoning": result.reasoning,
    "tool_calls": [tc.to_wire() for tc in result.tool_calls],
    "finish_reason": result.finish_reason,
})
hctx.emit(LLM_USAGE, result.usage.__dict__)
```

### 第 5 步：判断要不要继续

```python
if not result.tool_calls:
    hctx.emit(STEP_END, {})
    break  # 模型给出了不带工具调用的最终回复，Turn 结束
```

模型这一步没有请求调用任何工具，说明它认为可以给出最终答案了——循环结束。

### 第 6 步：执行工具调用

如果有 `tool_calls`，调用 `_run_pending_calls(hctx)`，它会依次处理每一个待处理的调用，
并且返回两个布尔标记：

- `awaiting`：这一步过程中遇到了需要人工审批的调用（发出了 `tool/approval` 事件）；
- `halted`：遇到了中断，或者某个工具结果标记了 `stops_turn=True`（比如 `exit_plan_mode`，
  一旦调用就应该结束当前 Turn，把方案交给用户确认）。

### 第 7 步：`step/end` 的发出时机——一个必须记住的细节

```python
hctx.emit(STEP_END, {"stopped": awaiting or halted})
```

**注意：`step/end` 是在 `tool/approval` 事件之后才发出的**。这不是随手写的顺序，而是刻意的——
如果反过来（先发 `step/end` 再发 `tool/approval`），会给下游（比如 [05](05-harness-事件系统与会话存储.md)
里讲的会话状态推导逻辑）造成误判风险。CLAUDE.md 原文明确指出这一点，也是
`SessionManager._turn_outcome()` 为什么不能简单地"只看最后一条事件"、而要在多种候选类型里找
"时间最新"的那条的根本原因——两件事互为因果，理解了其中一个就理解了另一个。

### 第 8 步：三种收尾方式

```python
if awaiting:
    return                       # 直接 return，不发 turn/end——这轮对话还"开着"，等人来批
if halted:
    break                        # 跳出循环，走到下面统一的 turn/end
# 否则回到 for 循环开头，进行下一步
else:                            # for...else：循环耗尽 max_steps 都没跳出
    hctx.emit(AGENT_ERROR, {"stage": "max-steps"})

hctx.emit(TURN_END, {})          # 唯一没有触发这行的路径是 awaiting 的 return
```

> **大白话**：`return` 和 `break` 在这里承载了不同的业务语义——`return` 意味着"这轮对话还没死，
> 只是卡在等人点头"，`break` 意味着"这轮对话该收尾了，不管是正常结束、被打断，还是跑满了步数上限"。
> 只有走到收尾流程才会发 `turn/end`，所以前端只要看到没有 `turn/end`，就知道这个 Turn 处于"待审批"
> 或者"仍在流式生成"的状态，不需要额外维护一个专门的"是否结束"标志位。

## 6.3 `_request_model()`：流式分片的批量落盘

```python
async def _request_model(hctx):
    log = maybe_compact(hctx, hctx.store.read(hctx.session_id), hctx.context_budget)
    _snapshot_prompt_if_changed(hctx, log)          # config/change，仅内容变化时才写
    messages = derive_messages(log, hctx.system_prompt)

    buffer = []
    async for delta in hctx.llm.stream(messages, tools=hctx.tools.schemas()):
        if interrupt.is_set(hctx.session_id):
            flush(buffer); hctx.emit(AGENT_INTERRUPT, {}); return
        buffer.append(delta)
        if len(buffer) >= CHUNK_FLUSH_SIZE or elapsed >= CHUNK_FLUSH_SECONDS:
            flush(buffer)                            # 批量写一次 assistant/chunk
        if delta.result is not None:
            flush(buffer)                            # 关键：必须先把缓冲区清空
            yield delta.result                        # 再把最终结果吐出去
```

### 为什么要"批量刷新"而不是每个 token 都落盘一次

代码注释给出了一个具体数字：**如果每个 token 都单独触发一次数据库写入（append），实测这部分开销
能占掉一次长对话总耗时的约五分之一**。所以设置了两个刷新条件——攒够 `CHUNK_FLUSH_SIZE=32` 个分片，
或者超过 `CHUNK_FLUSH_SECONDS=0.15` 秒，**任一条件先满足就刷新一次**（借助
[05](05-harness-事件系统与会话存储.md) 里 `append_many` 一次事务写入一批事件的能力）。

> **面试话术**：这是一个典型的"吞吐量 vs 实时性"权衡——完全不攒批，每个 token 都立即持久化，
> 实时性最好但吞吐量最差；攒太大批次，吞吐量好但用户会感觉界面"卡顿式"地一大段一大段往外蹦字，
> 体验变差。`0.15` 秒这个时间阈值就是"人眼感知不到延迟，但已经能攒下不少 token"的经验取舍点。

### 为什么最终结果必须在缓冲区清空**之后**才 yield

如果反过来，先把最终的 `assistant/message` 事件发出去，再补发缓冲区里剩下没刷新的分片，
就会出现"完整消息已经落盘了，但拼出这条消息的部分原始分片还没落盘"的乱序——重放时时间线会
自相矛盾。这是"全链路可追溯"这条系统级不变量，在一个具体函数内部的微观体现。

## 6.4 工具调用的执行链路

```
_run_pending_calls()
  └─ 对每一个还没有 tool/result 或 tool/approval 的 tool_call：
       _run_call()
         emit(TOOL_CALL, ...)
         如果工具名不存在于 registry → 直接返回错误的 tool/result
         否则：跑 PRE_EXECUTE 钩子（=审批策略，见 07 章）
           DENY → 直接返回错误的 tool/result
           ASK  → emit(TOOL_APPROVAL, ...)，函数直接 return（还没结果）
           否则 → _execute()
                    result = await hctx.tools.execute(...)
                    emit(TOOL_RESULT, {..., "stops_turn": spec.stops_turn and not is_error})
```

`_run_pending_calls()` **一次只处理一个调用，处理到 `TOOL_APPROVAL` 就立刻停止**——一条 assistant
消息可能带了多个并行的 `tool_calls`，如果其中第 2 个需要审批，第 3、4 个不会被抢跑执行，必须等
第 2 个的审批结果出来之后（`resume_turn` 里的 `_run_pending_calls` 才会继续跑剩下的）。

### `_pending_calls()`/`_find_call()`：状态完全从日志重建，没有内存里的临时状态

```python
def _pending_calls(log):
    settled_ids = {e.data["call_id"] for e in log if e.type in (TOOL_RESULT, TOOL_APPROVAL)}
    return [tc for tc in latest_assistant_message(log).tool_calls if tc.id not in settled_ids]
```

这一段也是"全链路可追溯"不变量的又一处体现——**"哪些调用还没处理完"这个信息，不是维护一个内存里
的 Python 变量去追踪，而是每次都从日志里现查**。这意味着即使进程重启、即使是在
`resume_turn()`（一次全新的函数调用，没有任何跨调用共享的内存状态）里，逻辑也完全一致，
不需要专门做"状态恢复"——**日志本身就是唯一的状态**。

## 6.5 钩子系统：`HookBus`

`harness/loop/hooks.py` 提供两个挂载点：

- `PRE_STEP`：每一步开始前；
- `PRE_EXECUTE`：每次真正执行一个工具调用前（审批策略就挂在这里，见 [07](07-harness-工具系统与审批.md)）。

```python
class HookBus:
    def on(self, point, name, listener): ...
    def emit(self, point, *args):
        for listener in self._listeners[point]:
            answer = listener(*args)
            if answer is not None:
                return answer     # 第一个给出明确答案的监听器胜出，短路后面的
        return None
```

单个监听器抛异常会被记录日志后**跳过**，不会让整个 Turn 崩掉——钩子系统被设计成"锦上添花"而不是
"单点故障源"。`describe()` 能把当前挂载的监听器列表暴露出来，正是前端"插件面板"数据来源之一。

## 6.6 中断机制：为什么只能在"边界处"生效

`harness/loop/interrupt.py` 用一个进程内字典 `{session_id: asyncio.Event}` 记录中断请求，
**故意不做成跨进程/持久化的**（CLAUDE.md 明确写了"deliberately"）。

```python
def begin(session_id): _tokens[session_id] = asyncio.Event()
def request(session_id) -> bool:
    event = _tokens.get(session_id)
    if event is None: return False   # 没有正在跑的 Turn，报告"没人接收到这次请求"
    event.set(); return True
def is_set(session_id) -> bool: ...
def finish(session_id): _tokens.pop(session_id, None)
```

**中断只在两个检查点生效**：每一步开始前、流式过程中每收到一个分片时。这意味着中断**不能**打断
一个"正在执行中的工具调用"——如果一个 `bash` 命令正在跑，用户点了停止，这次调用会跑完（或者被
沙箱自己的超时机制杀掉），下一次到达检查点时才会真正停下来。

> **大白话**：这不是偷懒，是诚实——"立即取消一个正在执行的系统调用"在通用场景下本来就很难做到
> 干净（可能留下一半写完的文件、一个僵尸子进程），与其假装能做到"瞬间打断"，不如老老实实说清楚
> "中断信号会在下一个安全点生效"，把"这次调用到底该不该被强制杀掉"的责任交给沙箱自己的超时机制
> （见 [08](08-harness-沙箱与安全边界.md)）。

`finish()` 是"尽力而为"的清理——如果客户端直接断连，可能永远不会被调用到，这也是为什么
[05](05-harness-事件系统与会话存储.md) 讲到的 `reconcile_status` 心跳检测必须作为独立的兜底存在，
不能依赖 `interrupt.finish()` 一定会执行。

## 6.7 本章小结：从流程图到代码的完整映射

把 [04](04-harness-总览与两大设计原则.md) 的流程图和这一章对照：

```
turn/start                      → run_turn() 里 emit(TURN_START)
  step/start                    → _drive() 循环体开头 emit(STEP_START)
  assistant/chunk…               → _request_model() 里批量刷新的流式分片
  assistant/message + llm/usage → 流式结束后落盘完整回复与用量
  tool/call → [审批] → tool/result → _run_pending_calls()/_run_call()/_execute()
  step/end                      → 在 tool/approval 之后才发，标记这一步是否"停住了"
turn/end                        → _drive() 的收尾路径（awaiting 除外）
```

下一章讲工具调用的另一半——工具契约怎么定义、审批策略的三档判定逻辑，以及每个内置工具的实现细节。
