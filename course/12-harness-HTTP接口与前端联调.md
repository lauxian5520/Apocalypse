# 12 · HTTP 接口与前端联调

这一章把前面几章讲的"内部机制"和"外部可见的接口/界面"接起来——`harness/loop/agent.py`
产生的是纯 Python 异步生成器，它是怎么变成浏览器能消费的 SSE 流的？前端"轨迹检查器"
又是怎么把一串串事件渲染成可交互的对话界面的？

## 12.1 权限门槛：`require_harness_user()`

```python
def require_harness_user(user = Depends(require_user)):
    if not settings.harness_enabled:
        raise NotFoundError(...)     # 404，不是 403 —— 直接隐藏这个功能存在过
    if settings.harness_require_admin and user.role != "admin":
        raise PermissionError_(...)  # 403
    return user
```

值得注意的细节：**功能整体关闭时返回 404，而不是 403**。403 会告诉一个未授权的访问者
"这个东西存在，只是你没权限"，404 则是"这里什么都没有"——如果部署方压根没打开 Harness
功能，没必要通过状态码差异暴露"这个系统其实内置了一个 Agent 工作台"这个信息本身。
这是一个很小但体现安全意识的细节：**错误响应本身也可能泄露信息，要按"最少暴露"的
原则来设计**。

## 12.2 SSE 流式接口的构造

### 路由层：只负责把异步生成器包装成响应

```python
@router.post("/sessions/{id}/messages")
async def send_message(id: str, body: MessageIn, user=Depends(require_harness_user)):
    if manager.is_busy(id):
        raise ValidationError("这个会话正有一轮对话在进行")
    hctx = build_context(id, row.preset)
    return _stream(id, run_turn(hctx, body.text))

def _stream(session_id, event_iter):
    manager.set_status(session_id, "running")
    async def generator():
        try:
            async for event in event_iter:
                yield sse(event.to_dict())
            yield SSE_DONE
        except AppError as e:
            yield sse({"error": e.message})
        except Exception:
            logger.exception(...)
            yield sse({"error": "服务器内部错误"})
    return StreamingResponse(
        generator(),
        headers=SSE_HEADERS,
        background=BackgroundTask(_finish_turn, session_id),   # 见 05/10 章
    )
```

三个关键设计，前面章节各自埋过一个点，这里汇总串起来看：

1. **异常必须在生成器内部捕获，转换成"数据帧里的一个错误字段"，而不是让它继续往上抛**——
   因为一旦 `StreamingResponse` 已经开始往浏览器发送数据（HTTP 响应头早已发出），
   这个 HTTP 请求就**不可能再改变状态码**了；这时候唯一能传递"出错了"这个信息的方式，
   就是在流里再吐一帧带 `error` 字段的 JSON，前端自己解析这一帧、判断这是一次错误。
   这也是 [02](02-后端分层架构.md) 里提到的 `core/sse.py` 那条规则的直接来源——
   "Errors after headers are flushed travel in-band as `{"error": …}`, never as a
   status code"。
2. **`background=BackgroundTask(...)`，不是 `try/finally` 包在生成器里**——原因在
   [05](05-harness-事件系统与会话存储.md) 详细讲过，这里再强调一次：Starlette 保证
   `BackgroundTask` 在响应结束后一定会被调度执行（无论是正常结束还是客户端断连异常结束），
   这个保证比依赖异步生成器自身的 `finally` 更可靠。
3. **状态先设成 `"running"` 再开始迭代**——这样即使客户端还没收到任何事件，只是刚发出
   请求，其他并发请求去查这个会话状态也能立刻看到"正忙"，`manager.is_busy()` 的检查
   才能生效（防止同一个会话被两个并发请求同时驱动，产生交叉写乱的事件序列）。

### `/messages/derived`：可以现场核对的"透明度承诺"

```python
@router.get("/sessions/{id}/messages/derived")
async def get_derived_messages(id: str, ...):
    hctx = build_context(id, row.preset)
    log = store.read(id)
    return derive_messages(log, hctx.system_prompt)
```

这个接口的注释非常直接："exactly what the next request would send"——它调用的
**跟循环内部真正要用来发请求的，是同一个 `derive_messages()` 函数**，不是另外单独写
一份"展示用"的近似逻辑。这意味着 [04](04-harness-总览与两大设计原则.md) 里"全链路
可追溯"这条承诺，**不是一句自我标榜，而是一个用户随时能在界面上点开'消息'这个页签、
当场核对的可验证事实**——右栏展示的内容，和真正发给模型的内容，物理上就是同一段代码
算出来的同一份结果。

## 12.3 文件下载：两套 Header 编码兼容中文文件名

```python
def _content_disposition(filename):
    ascii_fallback = _ascii_fallback(filename)   # 去掉所有非 ASCII 字符和引号/反斜杠
    encoded = urllib.parse.quote(filename)        # RFC 5987 百分号编码
    return f'attachment; filename="{ascii_fallback}"; filename*=UTF-8\'\'{encoded}'
```

> **严谨定义**：`Content-Disposition` 响应头的 `filename` 参数只能安全地承载 ASCII
> 字符，`filename*=UTF-8''...`（RFC 5987/6266）参数则允许用百分号编码承载任意 Unicode
> 文件名；同时提供两者是为了兼容"支持新语法的现代浏览器"和"只认旧语法的老客户端/某些
> 下载管理器"。
>
> **大白话**：中文文件名不能直接塞进 HTTP 响应头（响应头传统上只允许 ASCII），
> 但用户显然希望下载下来的文件名是"报告.docx"而不是一串乱码或者 "untitled.docx"。
> 解决方式是"两手准备"——同时给一个"退化成能表示的部分"的 ASCII 版本（万一浏览器
> 只认这个老写法），再给一个用规定编码方式表达完整中文名字的新写法（现代浏览器都认
> 这个、会优先使用它）。

下载路由（单文件、打包 zip 两个接口）都复用**和 Agent 自己 `read`/`glob` 工具完全同一份**
`Workspace(session_id).resolve()` 逻辑——CLAUDE.md 里专门点出"one containment
implementation, not a second one to keep in sync"，这和 [08](08-harness-沙箱与安全边界.md)
讲的"路径收敛只有一处实现"是同一条原则在 HTTP 层的延续。

## 12.4 前端三栏布局与状态驱动渲染

`harness.js` 维护一个简单的状态对象：

```js
const state = { sessionId, events: [], registry, tab, running, search };
```

没有引入任何前端框架的响应式系统，靠"改状态 → 手动调用对应的渲染函数重新构建 DOM"
这种最朴素的模式。中间栏对话区域的渲染逻辑（`buildNode()`）是一个按事件类型分发的
`switch`：

| 事件类型 | 渲染成什么 |
|---|---|
| `user/message` / `assistant/message` | 聊天气泡 |
| `tool/result` | 可折叠的工具卡片（`todo_write` 的结果默认展开，因为"输出本身就是进度展示"） |
| `tool/approval` | 批准/拒绝按钮卡片——**但如果日志里已经能找到这个 `tool_call_id` 对应的
    `tool/result`**（说明已经被处理过了），改成只读展示，不渲染可点击按钮（否则按钮点了
    也没有意义，还容易让用户误以为"点了就能重新触发一次") |
| `subagent/end` | 展示步数/花费，附一个"查看它的完整轨迹"按钮，点击跳转打开子会话（
    见 [10](10-harness-子代理多智能体.md) 里"子代理不在侧栏但可追溯"的设计） |
| 其余（`step/*`、`llm/usage`、`config/change`…） | 返回 `null`，中间栏不显示——
    这些"基础设施事件"属于右栏的检查器，不属于对话本身 |

这一条分发规则，其实就是 [05](05-harness-事件系统与会话存储.md) 里
`SURFACE_TYPES`（哪些事件会投影进模型请求）在**前端展示层**的一个近亲——虽然前端展示
决定"人看到什么"和后端投影决定"模型看到什么"是两个独立的判断，但两者背后的直觉是
一致的：并不是所有落盘的事件都需要摆在"正在对话"这个语境里。

### 四个页签

- **events**：原始事件日志，逐条展示（"从 seq N 分支"的分叉按钮只出现在
  `turn/end`/`step/end`/`assistant/message` 这几种类型上——理由前面 [05](05-harness-事件系统与会话存储.md)
  的压缩边界讨论已经讲过：分叉如果切在一个 `tool_call` 和它对应的 `tool_result` 中间，
  会制造出一个不完整、无法正确投影的历史）。
- **messages**：实时请求 `/messages/derived`，展示"如果现在发下一条消息，模型实际会
  看到什么"——上一节讲过的透明度承诺。
- **files**：会话工作区的文件列表，下载走"裸 `fetch` + Blob"（见下一节）。
- **plugins**：`GET /harness/tools` 返回的注册表数据——当前预设启用了哪些工具、
  技能、子代理角色、挂载了哪些钩子。

### 流式过程中的一个体验细节：分片气泡会被"替换"，不是"追加"

```js
// assistant/chunk 事件持续追加进一个临时气泡
// 一旦收到真正的 assistant/message 事件，这个临时气泡被丢弃，用最终消息重新渲染一份
```

代码注释直接写出这条逻辑背后的原因："The final message supersedes the chunks it was
assembled from"——最终消息取代了组装它的那些分片。这样即使流式过程中出现了乱序、
或者中间某个分片渲染出了一点小瑕疵，最终展示给用户的都是"权威、完整"的那一份，
不会残留任何"半成品"痕迹。

## 12.5 文件下载为什么走裸 `fetch` + Blob，而不是 `<a href>`

[03](03-前端架构.md) 已经讲过流式接口为什么不能用 `apiFetch`，这里是同一个限制在
"下载文件"这个具体场景下的应用：Harness 的文件下载接口需要 Cookie 认证 + CSRF 头，
而普通的 `<a href="...">` 标签**没有办法自定义请求头**（浏览器发起这类导航请求时，
没有 JS 介入的机会去附加一个自定义的 `X-CSRF-Token`）。解决方式：

```js
const res = await fetch('/api' + url, {
  credentials: 'include',
  headers: { 'X-CSRF-Token': Auth.csrfToken() },
});
const filename = parseContentDisposition(res.headers.get('Content-Disposition'));
const blob = await res.blob();
const objectUrl = URL.createObjectURL(blob);
const a = document.createElement('a');
a.href = objectUrl; a.download = filename;
a.click();
URL.revokeObjectURL(objectUrl);
```

**先用带凭证的 `fetch` 换来完整的文件内容（Blob），再用一个"程序创建、立即点击"的
`<a download>` 触发浏览器原生的保存对话框**——这是"需要认证的下载"在纯前端场景下的
标准解法。文件名解析还要优先取上一节讲的 `filename*=UTF-8''...` 编码字段，
兼容中文文件名不乱码。

## 12.6 小结

这一章把前面章节讲的"内部机制"串成了一条完整的链路：

```
loop/agent.py 产出的异步生成器
  → routers/harness.py 的 _stream() 包装成 StreamingResponse
    → core/sse.py 定义帧格式，nginx 关闭代理缓冲保证不被中间层攒批延迟
      → 前端裸 fetch 逐帧解析
        → harness.js 按事件类型分发渲染到中间栏对话 / 右栏四个页签
```

到这里，Harness 子系统从底层数据结构（`SessionEvent`）到顶层用户界面的完整链路
就讲完了。下一章讲部署形态和这个项目"没有 pytest 测试框架"情况下真实采用的质量保障方式。
