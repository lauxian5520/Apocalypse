# 18 · 分词对齐与 loss mask

这一章是全课程技术含量最高、也最该在面试里主动讲的一章。

它解决的问题只有一句话：**训练时算梯度的那些 token，必须和采样时模型真正生成的那些
token 逐一对应。** 听起来像废话。实际上在多轮 Agent RL 里，这件事默认是**不成立**的，
而且不成立的时候**没有任何报错**。

---

## 18.1 失败长什么样

> **严谨定义**：train/rollout 分词不一致，指训练时重建的 token 序列与采样时模型实际
> 产生的序列不完全相同。
>
> **大白话**：模型说了 A，你按 B 去算「它说 A 的概率」。数字都算得出来，
> loss 是有限的，梯度是有限的，曲线还挺好看——但你在用噪声训练。

为什么它比一般 bug 难发现：

| | 普通 bug | 分词不一致 |
|---|---|---|
| 报错 | 有 | **没有** |
| loss | NaN / 爆炸 | 正常范围 |
| 曲线 | 明显异常 | 平滑下降 |
| 发现方式 | 看日志 | **只能主动断言** |

所以这一章的产出不是「写对了」，而是「**写了断言来证明写对了**」。
`rl/checks/rl_check.py` 里三个阶段专门守这件事。

---

## 18.2 根因一：`arguments` 是字符串，模板以为它是对象

Qwen2.5 的 chat template 里渲染工具调用的那几行：

```jinja
{{- '\n<tool_call>\n{"name": "' }}
{{- tool_call.name }}
{{- '", "arguments": ' }}
{{- tool_call.arguments | tojson }}
```

而 `derive_messages()` 交出的 `arguments` 是**模型写的原始 JSON 字符串**
（`harness/llm/base.py` 里 `LLMToolCall.arguments` 的注释写得很明确：
"raw JSON text, as the model wrote it"）。

对一个**字符串**做 `tojson`，得到的是一个 **JSON 字符串**，不是对象。实测：

```
按存储形态（字符串）-> "arguments": "{\"query\": \"nietzsche\"}"
解析成对象后       -> "arguments": {"query": "nietzsche"}
```

而 vLLM 的服务端**会先归一化再套模板**。于是：

> **rollout 产出第二种，本地重建产出第一种。每一次工具调用都差若干 token。**

一条 3-4 步的轨迹有 3-4 次工具调用，每次都错。不报错、不告警。

### 修法与钉死

`rl/env/template.py::normalize()` 在套模板前把 `arguments` 从 JSON 文本解析成对象：

```python
if isinstance(raw, str):
    try:
        fn["arguments"] = json.loads(raw or "{}")
    except json.JSONDecodeError:
        pass          # 模型真的写了坏 JSON，保持原样才是忠实的
```

注意那个 `except` 里的 `pass`：模型确实会写出不合法的 JSON，
registry 也确实把它当错误返回了。**轨迹应该分词成「当时发生的事」，而不是分词成修好的样子。**

然后用一个 golden fixture 钉死（`check_arguments_normalisation`）：
渲染结果里必须出现 `"arguments": {"query": ...}`，且必须**不**出现 `"arguments": "{`。

---

## 18.3 根因二：停止符不在 mask 里

`add_generation_prompt=True` 渲染出的 prompt 结尾是 `<|im_start|>assistant\n`。
而 `finish_reason == "stop"` 时，vLLM **默认不把 `<|im_end|>` 放进 completion**。

> **大白话**：如果 loss mask 不覆盖停止符，模型就永远学不到「该在这里停」。
> 训出来的结果是它不停地说下去，直到撞上 max_tokens。
> 这个 bug 安静、昂贵，而且很经典。

所以 `template.pack()` 把 span 的右边界往回收到 `<|im_end|>` 那一格**为止（含）**：

```python
while end > start and full[end - 1] != eos_id:
    end -= 1
```

`check_prefix_property` 里专门断言每个 span 的最后一个 token 就是 `<|im_end|>`。

---

## 18.4 方案：让服务端彻底不碰模板

两条路：

**A. rollout 走 `/chat/completions`，训练时本地重建。**
问题是模板在服务端套用——包括它自己对工具调用参数的归一化，而那段代码你不控制，
版本之间还会变。你没有任何办法检查两边是否一致，它们就是有时候不一致。

**B. 把服务端从「套模板」这件事里彻底摘出去。**（本项目的选择）

1. `derive_messages()` 构造消息列表——仍然是唯一投影函数。
2. `template.encode()` 用**入库的**模板在**本地**套，拿到 `prompt_token_ids`。
3. 把这串**整数**发给 vLLM 的 `/v1/completions`——它不做任何模板处理。
4. `logprobs` 把采样到的 token id 和对数概率一起带回来，
   所以 `logp_old` 是**采样那一刻记录的**，不是事后重算的。
5. 工具调用由我们自己按 `<tool_call>{...}</tool_call>` 解析
   （`rl/env/policy_adapter.py::parse_tool_calls`）。

> **大白话**：一个分词器、一个模板、一条代码路径，两边共用。
> 不一致就**不再是「要小心的事」，而是结构上不可能发生的事**。

代价是要自己写工具调用解析（约 30 行）。这是这个项目里**每行代码价值最高的一段**。

### 两条配套的钳制

**chat template 作为文件入库**（`rl/env/qwen2.5_tool.jinja`，2507 字符，
sha256 `cd8e9439f0570856`）。因为 HF 会**原地更新**模板文件，
一次静默更新就能让之前收集的所有轨迹失效。它的 sha256 和语料 hash 一起盖进每条轨迹。

**关掉 compaction。** `maybe_compact` 每一步都跑（`agent.py` 的 `_request_model`），
一旦触发就会**改写 system 消息**——那是序列的最前面，前缀性质当场破掉，而且静默。
做法是把 `context_budget` 设成不可达，再用 `assert_no_compaction()` 查日志。
**查日志而不是信配置。**

---

## 18.5 真正需要成立的前缀性质

这一节是我犯错又改对的过程，比结论更有价值。

### 我一开始断言错了

我最初的断言是：**每个消息边界处，前缀都稳定**。
即 `tokenize(messages[:i])` 必须是 `tokenize(messages[:i+1])` 的前缀，对每个 `i` 成立。

在六条真实轨迹上**全部失败**。

### 原因不是 bug

Qwen2.5 的模板会把**连续的 tool 响应合并进同一个 `<|im_start|>user` 块**：

```
一条 tool:   <|im_start|>user\n<tool_response>A</tool_response><|im_end|>\n
两条 tool:   <|im_start|>user\n<tool_response>A</tool_response>\n
                              <tool_response>B</tool_response><|im_end|>\n
```

加上第二条 tool 消息，**第一条的收尾标记被移动了**。所以中间前缀确实不稳定——
这是模板的正常行为，我的断言测的是一个不该成立的条件。

### 正确的条件更弱

mask 构造真正需要的是：

> **每个 assistant 轮的 prompt，是完整序列的 token 前缀。**

而**没有任何 assistant 轮落在 tool 块内部**——tool 块里只有工具响应。
所以这条**精确成立**，实测六条轨迹全绿：

```
assistant@2: prompt  911 · prefix-of-full=True
assistant@4: prompt 1776 · prefix-of-full=True
assistant@7: prompt 2124 · prefix-of-full=True
```

> **教训**：断言太强和断言太弱一样危险。太弱抓不到 bug；
> 太强会在正确的代码上报警，把人引去修不该修的地方。
> 想清楚「构造真正依赖什么」，再写断言。

顺带说：Qwen3 会**删掉除最后一轮以外所有 assistant 消息里的 `<think>` 块**，
那会破坏**真正的**前缀性质。本项目的断言能抓住这种情况——
这也是为什么断言要留在自检里，而不是验证一次就删掉。

---

## 18.6 mask 怎么建

`rl/env/template.py::pack()`：

```python
full = encode(messages)                                    # 整条对话一次分词
for i, message in enumerate(messages):
    if message["role"] != "assistant":  continue
    prompt  = encode(messages[:i],     add_generation_prompt=True)   # 当时的 prompt
    through = encode(messages[:i+1])                                  # 加上这一轮
    assert full[:len(prompt)]  == prompt                              # 两条断言
    assert full[:len(through)] == through
    start, end = len(prompt), len(through)
    while end > start and full[end-1] != eos_id:  end -= 1            # 收到 EOS 为止
    mask[start:end] = 1
```

系统提示词、用户轮、**工具观测全部 mask = 0**。

> **大白话**：模仿工具返回的内容，等于教模型**凭空编造检索结果**，
> 而不是教它去请求检索结果。这是多轮 RL 里另一个经典错误。

### 一个值得记住的数字

实测：**每条轨迹只有 7–12% 的 token 是可训练的**，其余都是工具观测和提示词。

```
6 条轨迹 · 20 个 span · 前缀性质与 EOS 均成立 · 可训练 token 占 8%
```

这个数字直接推出下一章的一个结论：多轮场景**必须**用 token 级归一化，
按序列取平均会系统性地低估长轨迹。

---

## 18.7 位移约定：`logp_old[b, t]` 是谁的概率

最后一个坑，我在写测试时**连着搞反三次**。

`logits[b, t]` 预测的是 token `t+1`。所以 token `t` 自身的对数概率来自 `logits[b, t-1]`。
约定定成：

> **`logp_old[b, t]` 是 `input_ids[b, t]` 自身的对数概率**（token 对齐，不是 logit 对齐），
> 位置 0 不用（没有东西预测第一个 token）。

搞反一格的后果：ratio 算在错误的 token 上，loss 有限、梯度有限、模型学噪声。

因为我反复搞错，最后把它收进了**一个有名字的函数**
（`rl/train/grpo.py::token_logprobs`），而且训练时算参考模型的 logprob 也正好需要它。

> **大白话**：一个约定被同一个人搞错三次，就不该再靠「记住」来维持，
> 而该变成一个函数 + 一条断言。断言是：**策略与采样者相同时，ratio 必须精确等于 1**。
> 这一条能抓住所有位移错误。

---

## 18.8 本章小结

- 分词不一致是唯一一种「看起来在训练、其实在学噪声」的失败，只能靠主动断言发现。
- `arguments` 是原始 JSON 字符串而模板会 `tojson`，双重编码让每次工具调用都错位。
- 停止符必须进 mask，否则模型学会永不停止。
- 解法是让服务端彻底不碰模板：本地套模板、传 token id、自己解析工具调用，
  不一致从「要小心」变成「结构上不可能」。
- 模板作为文件入库并记 sha256；compaction 必须关，且查日志不信配置。
- 需要成立的前缀性质是**每 assistant 轮**的，不是每消息的——断言太强和太弱一样危险。
- 可训练 token 只占 7–12%，这直接决定了下一章的归一化方式。
- 位移约定收进 `token_logprobs()` 一个函数，用「ratio 恰好等于 1」钉死。

下一章讲 GRPO 与训练循环。
