# 05 · 事件系统与会话存储

## 5.1 `SessionEvent`：一切的最小单位

`backend/harness/events.py` 定义了这个系统里唯一的持久化数据结构：

```python
@dataclass(frozen=True)
class SessionEvent:
    type: str      # 如 "user/message"、"tool/call"
    seq: int       # 会话内严格递增的序号
    time: int      # 毫秒级 Unix 时间戳
    data: dict     # 该事件类型专属的数据
```

`frozen=True` 让实例不可变（Immutable）——一旦创建就不能修改字段。

> **大白话**：为什么要设成不可变？因为它对应的正是日志"只能追加、不能修改"这条业务规则。
> 让数据结构本身在语言层面就"改不了"，比"大家记得别改它"这种口头约定可靠得多——这是"让非法状态
> 无法表示（Make illegal states unrepresentable）"这条设计原则的具体应用。

### 19 种事件类型，分两大阵营

| 阵营 | 事件类型（举例） | 作用 |
|---|---|---|
| **会投影进模型请求**（`SURFACE_TYPES`） | `user/message`、`assistant/message`、`tool/result` | 这三种，也只有这三种，会被 `derive_messages()` 转换成发给模型的消息 |
| **仅用于日志/重放/前端展示** | `session/start`、`turn/start`、`turn/end`、`step/start`、`step/end`、`assistant/chunk`（原始流式分片）、`tool/call`、`tool/approval`、`llm/usage`、`compaction/summary`、`subagent/start`、`subagent/end`、`agent/error`、`agent/interrupt`、`config/change` | 不占用模型上下文，但对调试、计费、轨迹回放、断线恢复都是刚需 |

还有一个 `IGNORABLE_TYPES` 集合，表示"一个不了解全部类型的读者可以安全跳过它们"，但 `config/change`
被**特意排除**在这个集合之外——因为它携带着系统提示词快照，跳过它会导致重放丢失关键上下文
（见 [04](04-harness-总览与两大设计原则.md) 的解释）。`KNOWN_TYPES` 是全部合法类型的封闭集合：
遇到不认识的类型，重放逻辑要直接报错中止，而不是悄悄忽略——因为"悄悄丢弃未知数据"本身就违反了
"全链路可追溯"的不变量。

## 5.2 存储层：`SessionStore` 协议与 SQLite 实现

```python
class SessionStore(Protocol):
    def append(self, session_id, type, data) -> SessionEvent: ...
    def append_many(self, session_id, events) -> list[SessionEvent]: ...
    def read(self, session_id, after_seq=-1) -> list[SessionEvent]: ...
    def next_seq(self, session_id) -> int: ...
```

默认实现 `SqliteSessionStore`：

- **每次调用都各自开关一次数据库会话**，不持有长连接。为什么？因为一次 Agent 对话可能跑好几分钟，
  如果一直占着一个数据库连接不放，在 SQLite 这种"写操作互斥"的引擎上会长时间锁住其他会话的写入。
- **`seq` 怎么保证递增不重复**：查 `MAX(seq)+1` 作为下一个序号，写入时靠数据库层面的
  `UNIQUE(session_id, seq)` 索引兜底——如果两个并发请求同时算出了同一个 `seq`，后写入的那个会触发
  `IntegrityError`，代码捕获后回滚重试（最多 5 次）。

  > **严谨定义**：这是一种乐观并发控制（Optimistic Concurrency Control）——不提前加锁阻止并发，
  > 而是允许冲突发生，靠数据库唯一约束在写入时兜底检测冲突，检测到就重试。
  >
  > **大白话**：不是"先派号再排队"，是"大家都猜自己该拿几号，撞了号就有一个人重新猜"——在冲突
  > 概率不高的场景下，这比每次都先加锁再操作效率更高。

- **`append_many` 一次性认领一段连续的 `seq` 区间，在一个事务里写完**——这正是流式回复分片批量落盘
  的优化基础（见 [06](06-harness-Agent执行循环.md) 的 `CHUNK_FLUSH_SIZE`）。
- 读取时如果某一行的 `data` 字段是损坏的 JSON，会捕获异常、用空字典 `{}` 代替，**不会让一条脏数据
  搞垮整个会话的重放**——这是"局部容错优于全局崩溃"的工程直觉。

### 数据表结构

```
harness_sessions
  id (uuid hex, PK) | user_id (FK) | title | preset | status
  forked_from | forked_at_seq | parent_id | agent | created_at | updated_at

harness_events
  id (自增PK) | session_id (FK, CASCADE) | seq | type | time | data(JSON文本)
  唯一索引 (session_id, seq)
```

`parent_id` 字段**故意没有加索引**——代码注释解释这是因为它是后来用 `ALTER TABLE` 补上去的列
（见 [02](02-后端分层架构.md) 的 `_ADDED_COLUMNS` 机制），如果在模型定义里声明索引，会和"这张表
实际部署环境里的真实结构"（没索引）产生偏差描述。这是个很小但很真实的细节：**代码里的声明应该
反映数据库的真实状态，不能只反映"理想状态"**。

## 5.3 投影函数：`derive_messages()`

这是"全链路可追溯"这条不变量在代码里唯一的落地入口，位于 `harness/session/projection.py`。

### 逻辑拆解

1. **找系统提示词**：`logged_system_prompt(log)` 从后往前扫描日志，找最近一条 `config/change`
   事件里的 `system_prompt` 字段。`derive_messages()` **优先用这个快照**，只有找不到（早期会话，
   还没有这套快照机制时创建的）才退回用传进来的 `system_prompt` 参数（当前配置现算的版本）。
2. **叠加压缩摘要**（如果存在）：如果日志里有 `compaction/summary` 事件，把摘要文本追加到系统消息
   的一个固定中文小标题下面，同时**跳过所有 `seq <= covered_to` 的事件**——已经被摘要覆盖的原始
   事件不再参与投影（但原始事件本身**一条都不删**，只是不再参与"造消息"这一步，见
   [11](11-harness-成本上下文压缩与工程细节.md)）。
3. **逐条转换**：
   - `user/message` → 一条 user 消息；
   - `assistant/message` → 一条 assistant 消息（带上 `content` 和 `tool_calls`，如果有的话）；
     **紧接着**，对这条消息里的每一个 `tool_call`，立刻在结果列表里查找并插入它对应的
     `tool/result` 事件（按 `call_id` 匹配，而不是按这个 `tool/result` 事件在日志里出现的
     位置）——因为 OpenAI 协议要求"一个 assistant 消息如果带了 tool_calls，紧跟着的每个
     tool 消息必须严格对应"。
   - 如果某个 `tool_call` 在日志里**找不到匹配的结果**（比如对话在审批环节被中断、或者被压缩
     切掉了），会插入一条占位内容：`"[未完成：该工具调用被中断或取消]"`——这是**必须做**的，
     因为大多数 provider 会直接拒绝一个"带了 tool_calls 但缺少对应 tool 结果"的请求，
     不补这个占位符会导致整个请求报错。

> **面试话术**：`derive_messages()` 这个函数名字本身就体现了"投影（Projection）"这个词在事件溯源
> 语境下的含义——**当前请求要发的消息列表，是从历史事件"派生（derive）"出来的一个视图，而不是
> 一份独立维护的状态**。这意味着"历史消息"这个概念在系统里根本不单独存在一份，它每次都是
> 现算的——这保证了"模型看到的历史"和"日志记录的历史"永远是同一个东西，不可能出现同步不一致。

### `compaction_boundary()`：压缩边界只能切在 user 消息之前

```python
def compaction_boundary(log, keep_recent_turns=2) -> int:
    # 返回倒数第 keep_recent_turns 条 user/message 的 seq 减一
```

为什么一定要切在紧挨着一条 `user/message` 之前？因为这是**唯一不会把某个 `tool_call` 从它对应的
`tool_result` 中间切断的位置**——一个 Turn 内部，`assistant/message`（带 tool_calls）和它的
`tool/result` 总是紧紧挨在一起，只有"两个 Turn 之间"（也就是一条新 user 消息发出之前）才是安全的
切割点。

### `estimate_tokens()`：一个"仅用来做决策，不用来做真实计费"的粗估算

```python
def estimate_tokens(messages) -> int:
    return total_chars / 2.5
```

这个 `/2.5` 是经验系数——中文大约 1 个字对应 1 个 token，英文大约 4 个字符对应 1 个 token，
两者各占一半时取个折中值。**这个估算只用来"要不要触发压缩"这个决策**，真正的计费和"是否达到
压缩阈值"的判断，用的是下一章会讲到的、provider 真实返回的 token 数。

## 5.4 会话状态管理：`SessionManager`

`harness/session/manager.py` 负责会话的生命周期，几个值得细讲的设计：

### 状态推导为什么不能只看"最后一条事件"

```python
STATUSES = (idle, running, awaiting_approval, error)
_TERMINAL_STATUS = {
    TOOL_APPROVAL: "awaiting_approval",
    AGENT_ERROR:   "error",
    TURN_END:      "idle",
    AGENT_INTERRUPT: "idle",
}
```

天真的做法是"看日志里最后一条事件的类型来判断当前状态"，但这在这个系统里**是错的**——因为
[06](06-harness-Agent执行循环.md) 会讲到，循环里 `step/end` 事件是在 `tool/approval` **之后**才
发出的（记录"这一步因为等待审批而暂停"）。如果只看最后一条，会把"正在等人审批"误判成"这一步已经
正常结束"。

正确做法：`_turn_outcome()` 在"所有终态候选事件类型 + `turn/start`"里找**时间最新**的那一条——
因为同一个 Turn 内，`turn/start` 必然出现在它对应的终态事件之前，如果 `turn/start` 是最新的，
说明这个 Turn 还没走到任何终态，仍然"进行中"。

### 断线保护：`reconcile_status` + 心跳窗口

```python
def reconcile_status(row):
    if row.status != "running":
        return row
    healed = _turn_outcome(row.id)
    if healed.status is None:  # 日志里还没有终态事件
        if now_ms() - healed.last_time <= 180_000:  # 3 分钟心跳窗口
            return row  # 相信它还在正常运行
        else:
            row.status = "idle"  # 判定为失联的僵尸会话，强制拉回空闲
    ...
```

> **大白话**：如果一个会话标记为"运行中"，但日志里最后一条事件已经是 3 分钟前的了，大概率是
> 后端进程重启、或者流式连接异常中断导致这次对话"烂尾"了，永远不会再有新事件写进来。
> 与其让它在数据库里永远显示"运行中"、把用户卡在一个转圈的界面上，不如主动"体检"一次、
> 拉回空闲状态。

### 为什么清理逻辑放在 `BackgroundTask` 而不是流式响应的 `finally` 里

这是一个极其容易被误解、但在真实生产系统里非常关键的坑：

> **反直觉但正确的做法**：一个 `async def generator(): yield ...` 形式的流式响应生成器，
> 如果客户端中途关闭了连接（比如用户直接关掉浏览器标签页），Python/Starlette **不保证**这个
> 生成器的 `finally` 块会被立即执行、甚至**不保证它一定会被执行**——挂起的异步生成器可能被垃圾回收器
> 在任意延迟之后才清理，也可能因为某些实现细节永远悬挂在那里。

正确做法是把清理逻辑（`interrupt.finish()` + `manager.finalize_turn()`）作为 Starlette 的
`BackgroundTask` 传给 `StreamingResponse`——**它会在响应流结束（不管是正常结束还是客户端断连
导致异常结束）之后被调度执行**，这是框架层面提供的保证，比依赖生成器自己的 `finally` 可靠得多。
再叠加上面那层 `reconcile_status` 的心跳检测作为最后一道兜底，双保险确保"会话不会永远卡在
运行中"。

### 子代理的费用汇总：`usage_summary()`

```python
def usage_summary(session_id):
    events = llm_usage_events_of(session_id)
    events += llm_usage_events_of_all_children(session_id)  # parent_id 指向自己的会话
    return pricing.summarize(events)
```

主 Agent 委派一次子任务，子代理内部可能会跑十几步、花费比主 Agent 本身还多的 token——这是项目
注释里明确提到的真实观察（"实测一次派发里子代理花掉的 token 比主 Agent 还多，分开算就会严重低估"）。
所以费用统计必须递归汇总所有子会话，这一点在 [10](10-harness-子代理多智能体.md) 里会结合具体的
委派机制再展开。

### 其他值得记住的细节

- `fork(session_id, user_id, seq)`：从任意事件序号"分叉"出一个新会话——复制历史日志到该序号为止，
  同时用 `shutil.copytree` 复制整个工作区目录，新会话打上 `session/end-seed` 标记表示这是一个
  分叉的起点。这天然支持"试了一条思路走错了，从某一步重新分叉再试另一条路"的调试场景。
- `list_for_user()` **排除**所有 `parent_id` 非空的会话（子代理会话）——避免侧边栏被大量子会话
  淹没，同时搜索只匹配标题和用户消息文本，不搜 assistant 回复或工具输出内容。

## 5.5 本章小结

- 一切都是 `SessionEvent`，写日志（`emit`）先于一切下游动作。
- 只有三种事件类型会真正进入模型的上下文，其余全部是"旁路信息"。
- `derive_messages()` 是唯一的日志→请求转换器，压缩摘要和系统提示词快照都在这一层被正确拼接。
- 会话"当前状态"是**推导**出来的（从日志里算），不是一个可以被随意直接赋值、可能和日志脱节的
  独立字段——这是把"事件溯源"思想贯彻到状态管理层面的体现。

下一章拆解真正驱动这一切运转的 Agent 执行循环。
