# 07 · 工具系统与审批策略

这一章讲两件事：**Function Calling（工具调用）在这个项目里是怎么落地的**，以及
**Human-in-the-loop（人工审批）机制怎么设计**。这是 Agent Infra 面试里最常被追问细节的部分。

## 7.1 数据与代码分离：工具的"契约"和"实现"是两个文件

> **严谨定义**：数据与代码分离（Data/Code Separation）指把"描述行为的元数据"（这里是工具名称、
> 描述、JSON Schema、权限级别）与"真正执行行为的代码逻辑"拆到两个独立的文件/系统里维护，
> 前者通常是配置文件或数据文件，后者是程序代码。
>
> **大白话**：一个工具在这个系统里的"说明书"（给模型看的名字和描述、参数长什么样）和"怎么干活"
> （Python 函数体）是分开放的。好处很直接——**改一句工具描述、调一下参数命名，不需要碰任何 Python
> 代码，也就不需要重新部署**。这对"给模型调优提示词措辞"这种高频、低风险的操作特别友好。

一个工具由两个文件组成：

```
harness/data/tools/<模块>.json    ← 契约：name/description/parameters(JSON Schema)/permission
harness/tools/builtin/<模块>.py   ← 实现：导出一个 HANDLERS = {"工具名": 函数, ...}
```

`ToolSpec`（`tools/base.py`）是两者合体后的最终形态：

```python
@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict        # 原样来自契约 JSON，直接就是 JSON Schema
    permission: str         # "read" | "write" | "exec"
    handler: Callable       # 绑定好的 Python 函数
    module: str
    stops_turn: bool = False

    def to_wire(self):
        # 渲染成 OpenAI 风格：{"type": "function", "function": {...}}
```

`.to_wire()` 就是真正塞进 `POST /chat/completions` 请求体 `tools` 字段里的那个 JSON——这也是
"Function Calling" 这个术语在工程上的真正含义：**不是模型自己会执行代码，而是模型被告知
"你可以以某种结构化格式请求调用某个函数"，服务端解析这个请求、真正执行、把结果重新喂回去**。

## 7.2 注册表：`load_specs()` 怎么发现所有工具

```python
def _discover():
    for path in sorted(TOOLS_DIR.glob("*.json")):     # 按文件名排序，不是目录列出的原始顺序
        module = import_module(f"harness.tools.builtin.{path.stem}")
        if path.stem not in loaded_modules and not hasattr(module, "HANDLERS"):
            raise ValidationError(...)                 # 契约有了，实现没有 → 启动直接报错
```

三条硬规则，全部在 CLAUDE.md 里被明确写出来，也是这个设计"缜密"的地方：

1. **按契约目录扫描发现**，不是靠某个地方写死一份"工具清单"——新增工具，只要放对两个文件，
   重启后自动生效（如果这个模块没被 `_MODULE_GATES` 挡住，见下文）。
2. **契约 `.json` 没有匹配的 `.py`** → 直接 `ValidationError`，**在应用启动时就报错**，
   不会等到某次真实对话跑到这个工具才炸——这是"快速失败（Fail Fast）"原则的典型应用：
   打包错误应该在部署阶段被发现，而不是留到用户真正触发它的那一刻。
3. **`.py` 里有 `HANDLERS` 但没有匹配的契约文件** → 只是记一条 info 日志，**这个模块被当成
   死代码**，不会加载、也不会报错——因为这不是"配置损坏"，只是"这份代码暂时没被引用"，
   不需要用错误级别去打扰。

### 为什么用 `sorted()`：Prompt 前缀缓存

```python
for path in sorted(TOOLS_DIR.glob("*.json")):
```

这行 `sorted()` 表面上只是"让输出有确定性顺序"，但代码注释里点出了更深的理由：**扫描得到的顺序
最终会变成请求里 `tools` 数组的顺序，而 LLM provider 的"前缀缓存（Prompt Prefix Caching）"只有
在请求前缀逐字节一致的情况下才能命中**。

> **严谨定义**：Prompt 前缀缓存是主流 LLM API 提供商（DeepSeek、OpenAI 等）的一项计费优化——
> 如果本次请求的开头一段 token 序列和上一次请求完全相同，provider 可以复用之前计算过的
> 中间状态（KV Cache），只对新增部分做计算，这部分"命中缓存"的输入 token 按更低的价格计费。
>
> **大白话**：模型每次收到请求，开头那一大段"系统提示词 + 所有工具的定义"其实每次都长得一样。
> 如果这段东西**每个字节都分毫不差**，provider 就能"抄近道"少算一遍，价格也更便宜。但只要
> 工具在数组里的顺序换了一下——哪怕工具本身内容没变——对 provider 来说这就是"不一样的输入"，
> 缓存直接失效。`sorted()` 就是为了保证"文件系统目录列出的顺序不保证稳定"这个问题不会悄悄
> 把这份缓存打碎。README 里提了一个具体数字：这项优化能省下大约 8000～9900 个输入 token
> （具体针对系统提示词只带日期、不带精确时间戳这项优化，但同一个原理也适用于工具顺序）。

`compose_prompt()`（`context.py`）在拼系统提示词时也遵循同一个原则——把"稳定不变的部分"
（工具目录说明、技能目录、子代理目录）放前面，把"易变的部分"（`runtime_context()`，
只带日期）**放在最后**，最大化能命中缓存的前缀长度。

### `expand_tools()`：预设怎么选工具

预设文件（`data/presets/standard.json` / `minimal.json`）和子代理角色定义（`data/agents/*.json`）
共享同一套 `tools` 字段语法：

- 直接写工具名：`"read"`；
- `"*"`：全部可用工具；
- `"<模块>:*"`：某个模块导出的全部工具（比如 `fs.py` 一个模块导出了 `read/write/edit/glob/grep`
  五个工具，`"fs:*"` 一次性全要）。

`standard` 模式用 `"*"`，代码注释解释了这背后的动机：这样"新工具丢进去、重启后立刻自动可用"，
不需要手动维护一份清单。`minimal` 模式反过来，故意保留一份显式清单——它的定位是"可复现的对照基准"，
新加了什么工具都不该悄悄改变 `minimal` 模式的行为。

### 部署级"总闸"：`_MODULE_GATES`

```python
_MODULE_GATES = {
    "shell": lambda settings: settings.harness_shell_enabled,
    "subagent": lambda settings: settings.harness_subagent_enabled,
}
```

这个逻辑**在预设的 `"*"` 通配符展开之后才应用**——意味着即使某个预设写了 `"*"`，如果部署方
在 `.env` 里没打开 `HARNESS_SHELL_ENABLED`，`bash` 工具依然不会出现。这是一条很重要的
"权限层级"设计：**用户/角色级别的配置（预设文件）永远不能覆盖部署级别的开关（环境变量）**——
后者才是最终的安全边界。

### 工具失败是"值"，不是"异常"

```python
def execute(self, name, arguments_json, ctx):
    try:
        args = parse_arguments(arguments_json)
        _reject_unknown_arguments(handler, args)   # 用 inspect.signature 检查参数名是否匹配
        return handler(ctx, **args), False          # (结果文本, is_error=False)
    except AppError as e:
        return f"错误：{e.message}", True
    except TypeError as e:
        return f"参数不匹配：...", True
    except Exception:
        logger.exception(...)
        return "工具执行时发生未知错误", True
```

> **大白话**：工具执行失败不会让整个 Turn 崩掉、也不会往上抛异常打断循环——而是把"失败"本身
> 变成一段文本，正常走 `tool/result` 这条通路喂回给模型。这样模型能"看到"自己刚才这次调用
> 失败了、失败原因是什么，然后**自己决定要不要换个方式重试**——这正是"让模型自己纠错"这种
> Agent 常见模式的工程基础：错误信息本身也是模型推理链条里的一环输入，不该被基础设施层直接吞掉
> 变成一次程序崩溃。

`_reject_unknown_arguments` 这一步特别值得注意——它用 `inspect.signature` 反查 handler
函数真实的参数列表，主动拒绝契约 JSON Schema 之外、模型却硬塞进来的多余参数。这能在**第一时间**
捕获"契约文件和 handler 函数签名不一致"这类打包漂移问题，而不是让多余参数被 Python 的
`**kwargs` 悄悄吞掉、制造出一个看起来正常但其实没生效的调用。

## 7.3 审批策略：`ApprovalPolicy`

```python
class Decision:
    verdict: Literal["allow", "ask", "deny"]
    reason: str

def decide(spec, arguments) -> Decision:
    if spec.permission == PERMISSION_EXEC:
        return _judge_command(arguments)
    return Decision(ALLOW)   # read/write 权限一律直接放行
```

### 为什么 `write` 权限也直接放行

`fs.py` 里的 `write`/`edit` 工具权限标的是 `write`，但审批策略对它一律 `ALLOW`。原因是
**路径已经被强制收敛在会话专属工作区内**（下一章细讲 `Workspace.resolve()`）——不管模型
想写什么文件名，最终都只能落在自己的沙箱目录里，写坏了顶多是自己的工作区，不影响系统其他
任何地方，所以不需要人再多确认一次。**真正需要人来判断的，只有"可能执行任意代码或联网"
这一类操作**——也就是 `exec` 权限的命令。

### `_judge_command()`：三档判定

```python
def _judge_command(command):
    argv = parse_command(command)     # 解析失败 → ASK（不能判定就问人，不默认拒绝）
    name = Path(argv[0]).name          # 取 basename，"/usr/bin/python3" 也能匹配到 "python3"
    allowlist = _allowlist()           # 读 data/shell_allowlist.json，读失败则"全部转人工"
    if name in allowlist.denied:       return Decision(DENY)
    if name in allowlist.auto_approved: return Decision(ALLOW)
    return Decision(ASK)               # 默认档：既不在白名单也不在黑名单 → 问人
```

`data/shell_allowlist.json` 的分档标准，文件自己的注释写得非常明确：**自动放行的前提是
这个命令既不能执行任意代码，也不能打开网络连接**。

| 分档 | 举例 | 为什么 |
|---|---|---|
| `denied`（直接拒绝） | `sudo`、`ssh`、`docker`、`chmod`、`kill` | 提权/远程访问/容器/进程控制类，风险确定且高 |
| `auto_approved`（自动放行） | `ls`、`cat`、`grep`、`mkdir`、`cp`、`sha256sum` | 纯读取/检视/改名类工具，无法执行任意代码也无法联网 |
| 其余全部（转人工） | `python`、`node`、`git`、`curl`、`find`、`awk` | **能**执行任意代码（`python -c`）或联网（`curl`），即使大多数正常用法是安全的 |

CLAUDE.md 原文明确说这是"功能而不是缺口"（"deliberately require approval — that is the
feature working, not a gap to close"）——`git`、`curl` 这些工具在 99% 的场景下都是无害操作，
但"能执行任意代码/能联网"这个属性本身就足够危险，宁可让人多点几次"批准"，也不把这类命令
一次性划入自动放行的白名单。

### `StrictApprovalPolicy`：为子代理准备的"严格版"

```python
class StrictApprovalPolicy(ApprovalPolicy):
    def decide(self, spec, arguments):
        decision = super().decide(spec, arguments)
        if decision.verdict == ASK:
            return Decision(DENY, reason=decision.reason + "，但子代理无法请求人工批准，请改用别的办法")
        return decision
```

只把 `ASK` 改写成 `DENY`，`ALLOW`/`DENY` 原样保留。这是这一章最值得记住的一个设计——
**为什么不能让子代理也走一遍"弹出审批卡片"的流程**？

> **大白话**：想象一下，主 Agent 的这一整个 Turn，正卡在"等待子代理返回结论"这一步的执行过程中
> （`subagent` 工具调用本身还没执行完）。如果子代理内部又碰到一个需要审批的命令，它会弹出一张
> 审批卡片——但这张卡片要交给谁点"批准"？主 Turn 都还没轮到它能继续往下走、把控制权交还给
> 用户界面，压根没有人能看到、也没有人能响应这张卡片。它只会静静地永远挂在那里，主 Agent
> 的这个调用永远不会返回。

`StrictApprovalPolicy` 把这个"死锁"从源头掐断：需要人来判断的操作，在子代理里直接判定为
"拒绝"，同时把原因文本带出去（这个原因本身也会作为 `tool/result` 喂回子代理，让子代理的模型
"知道"自己被拒绝了、可以换一种思路重试）。**这没有放宽任何安全边界**——原本要人批的东西，
现在依然跑不了，只是从"永远挂起"变成了"立即失败、附带原因"。

## 7.4 内置工具速览

| 工具 | 权限 | 一句话 |
|---|---|---|
| `read`/`write`/`edit`/`glob`/`grep` | read/write | 文件操作，所有路径强制走 `Workspace.resolve()` |
| `bash` | exec | 单条命令，`shlex.split` 解析，禁止管道/重定向等 shell 操作符 |
| `web_fetch`/`web_search` | read | 抓正文（去脚本样式导航栏）/ DuckDuckGo HTML 检索 |
| `current_time` | read | 本地读取服务器时间，可转指定时区，不联网 |
| `todo_write`/`exit_plan_mode` | read | 任务清单/方案确认，纯粹靠日志本身当状态，服务端不额外存一份 |
| `load_skill` | read | 按需把一份技能正文读进当前对话（见 [09](09-harness-技能系统.md)） |
| `subagent` | write | 派发子任务给独立子代理（见 [10](10-harness-子代理多智能体.md)） |

### `fs.py` 的几个具体设计

- `read`：分页读取，单次最多 400 行、文件最大 2MB，超限截断并明确提示。
- `edit`：精确字符串替换，**要求匹配唯一**（除非传 `replace_all=True`），0 次匹配或多于 1 次
  匹配（未显式允许全部替换）都直接报错——宁可拒绝执行也不猜模型到底想改哪一处。
- `glob`：每一个匹配结果都要**重新**过一遍 `workspace.resolve()`（哪怕 glob 本身理论上已经
  被限定在工作区内搜索）——这是"纵深防御"的一个小例子：不完全信任上一层过滤的结果，
  在下一层再校验一次。
- `grep`：正则搜索，跳过所有点号开头的目录（如 `.git`、`.skills`），限制最多 100 条匹配。

### `web.py` 的一处小细节：错误原因追溯

```python
def _reason(exc):
    # 沿着 exc.__cause__ / exc.__context__ 链条一直往下找
    # 把 "ConnectError" 这种笼统的异常类型，还原成 "Connection refused" 这种具体的操作系统级原因
```

这体现了一个容易被忽视的工程细节——**异常链（Exception Chaining）**：Python 的异常在多层
`raise ... from ...` 包裹后，最外层看到的往往是一个语义模糊的高层异常（比如 httpx 的
`ConnectError`），而真正有用的信息（操作系统返回的 "Connection refused"）藏在 `__cause__`
链条更深的地方。主动往下挖，能给模型（以及最终看到报错的人）提供真正可操作的信息，
而不是一句"连接失败"了事。

## 7.5 本章小结

- 工具的"契约"和"实现"物理上分成两个文件，注册表按契约目录扫描发现，缺一不可，缺了立即报错。
- `sorted()` 顺序不是随手写的，关系到 provider 侧的前缀缓存能不能命中。
- 审批策略是三档判定（放行/询问/拒绝），只对"可能执行任意代码或联网"的操作转人工，
  写文件天然安全（已经被路径收敛保护），读操作天然安全。
- 子代理用严格版策略把"该问人却没人能答"的死锁场景，转换成"直接拒绝、给出原因"——
  安全边界没有放宽，只是换了一种失败方式。

下一章讲"路径收敛"和"沙箱执行"具体怎么实现——也就是审批策略敢把 `write` 权限直接放行、
`fs.py` 敢让模型随便传路径的底气从哪里来。
