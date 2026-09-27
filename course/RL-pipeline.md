# RL-pipeline

## 1. 总体定位

当前项目的 `rl/` 不是单一训练脚本，而是一套围绕 Agent Runtime 构建的 Agentic RL 工作台，目标是让模型学习使用检索工具完成多跳问答。

核心链路：

```text
冻结语料与任务
    ↓
Harness RL 环境
    ↓
多轮 Agent rollout
    ↓
完整事件轨迹
    ↓
确定性验证器与奖励
    ↓
token 对齐与 loss mask
    ↓
SFT 冷启动 / GRPO
    ↓
LoRA adapter 发布到 vLLM
    ↓
下一轮 on-policy rollout
```

`rl/` 对 `backend/harness/` 是单向依赖：复用 Harness 的 Agent Loop、事件系统、工具注册和语料检索，但线上 `backend/` 不反向依赖 `rl/`。

## 2. 目录结构

```text
rl/
├── corpus/           语料构建与 HotpotQA 任务生成
├── tasks/            泄漏过滤、任务校准、任务切分
├── env/              RL 环境、消息投影、模板和 token 对齐
├── rollout/          多轮 Agent rollout 与轨迹记录
├── verifiers/        结果验证、过程验证、反作弊和奖励聚合
├── train/            SFT、GRPO、LoRA、课程学习和训练循环
├── eval/             评测报告与结果切片
├── attribution/      检索文档上下文消融归因
├── checks/           离线一致性和安全检查
└── cli.py            语料、任务和 rollout 命令行入口
```

## 3. 任务与数据层

### 3.1 语料构建：`rl/corpus/`

当前主环境使用 HotpotQA distractor split，早期的 arXiv 语料仍保留用于历史实验和对照。

#### `rl/corpus/hotpot.py`

核心数据结构是 `HotpotTask`，包含：

- `task_id`：任务 ID
- `question`：问题
- `answer`：标准答案
- `qtype`：`bridge` 或 `comparison`
- `level`：任务难度
- `gold_doc_ids`：标准支持文档
- `supporting`：文档及支持句子编号
- `context_doc_ids`：2 个 gold 文档加 8 个 distractor 文档

主要功能：

1. 下载 HotpotQA parquet 数据。
2. 将 Wikipedia 段落转换成 Harness 的 `Doc`。
3. 跨问题按标题去重段落。
4. 为文档生成稳定的 12 位 hash ID。
5. 保留人工标注的 supporting facts。

#### `rl/corpus/build.py`

负责将语料写入冻结目录：

```text
docs.jsonl
tasks.jsonl
manifest.json
```

`manifest.json` 记录文档数量、任务数量、文件 SHA-256、构建来源和参数，使训练结果可以绑定到确定的语料版本。

#### `rl/corpus/fetch_arxiv.py`

这是早期 arXiv 环境的构建器，支持 API 分日期窗口抓取、本地 snapshot 读取、Atom XML 解析以及作者、分类、摘要和版本信息提取。

项目通过测量发现 arXiv 文档之间过于容易区分，通常一次 BM25 查询即可命中目标，因此当前环境转向具有近似干扰文档的 HotpotQA。

### 3.2 泄漏过滤：`rl/tasks/leakage.py`

目标是避免模型凭预训练记忆直接答题。

```text
原始任务
  ├── deepseek-chat 闭卷测试
  └── deepseek-v4-flash 闭卷测试
             ↓
       取泄漏集合并集
             ↓
       删除闭卷可答任务
```

特别处理：

- 空回复表示未知，不能直接视为模型答不出来。
- 不同过滤器必须有足够覆盖范围。
- 覆盖不完整的任务可以被删除，避免过滤强度不一致。
- 泄漏是 `(task, model)` 级别，而不只是 task 级别。

### 3.3 一次查询可解校准：`rl/tasks/calibrate.py`

`one_query_solvable()` 判断把整道题直接作为 BM25 查询时，是否一次检索就能得到全部 gold 文档。

这类任务不会被删除，而是增加：

```json
"one_query_solvable": true
```

它们仍然需要查询、读文档、组织答案和引用，只是检索难度较低，后续由在线课程学习自动淘汰。

### 3.4 任务切分：`rl/tasks/split.py`

`build()` 完成：

1. 删除泄漏任务。
2. 删除未被全部过滤器覆盖的任务。
3. 标记 `one_query_solvable`。
4. 按 `bridge/comparison` 分层切分。
5. 写出 train/dev/test 和 SHA-256。

`rl/tasks/adversarial.py` 还用于发现问题中直接包含答案、描述复制目标文档稀有词等简单捷径。

## 4. RL 环境层

### 4.1 环境构建：`rl/env/build.py`

核心函数：

```python
build_env_context()
```

它将 Harness Runtime 组装成一个 RL episode：

```text
HarnessContext
├── session_id
├── MemorySessionStore
├── LLM policy
├── ToolRegistry("deepresearch")
├── shared workspace
├── pinned system prompt
├── max_steps
└── no-compaction context budget
```

当前动作空间固定为：

```python
ENV_TOOLS = (
    "corpus_search",
    "corpus_open",
    "corpus_answer",
)
```

| 工具 | 功能 |
|---|---|
| `corpus_search` | BM25 检索，返回标题、文档 ID 和摘要片段 |
| `corpus_open` | 打开文档，返回全文和元数据 |
| `corpus_answer` | 提交答案与引用，结束或继续当前 turn |

环境关闭网络搜索、shell、文件写入、动态技能、动态 agent 和上下文压缩。

compaction 会中途重写 system prompt，破坏采样 prompt 与训练 prompt 的一致性。因此环境提供：

```python
assert_no_compaction()
assert_single_system_prompt()
```

用于确保轨迹只有一个系统提示词快照。

### 4.2 内存 Session Store：`rl/env/memory_store.py`

网站使用 SQLite，但 RL rollout 使用内存存储，原因是：

- Agent Loop 每一步会重复读取完整事件日志。
- rollout 没有真实用户账号。
- 大量训练轨迹是临时数据。
- 逐事件持久化会产生不必要的 I/O。

它保持与 Harness `SessionStore` 相同的 `create/append/read/drop` 接口。关键不变量是：内存存储投影出的消息必须与 SQLite 存储投影出的消息一致。

## 5. 模型适配器

### 5.1 评测适配器：`rl/env/adapter.py`

类名：`EvalAdapter`。

用于普通 API 模型评测、vLLM chat completion 评测和 SFT 教师 rollout：

```text
messages
   ↓
/v1/chat/completions
   ↓
服务端套 chat template
```

它适合评测和教师数据收集，但不记录每个采样 token 的 ID 和旧策略 logprob，因此不适合严格的 on-policy GRPO 采样。

### 5.2 训练适配器：`rl/env/policy_adapter.py`

类名：`PolicyAdapter`。

GRPO rollout 使用它：

```text
messages
   ↓
本地 pinned chat template
   ↓
prompt_token_ids
   ↓
/v1/completions
   ↓
vLLM 不再重新套模板
```

每次生成记录为 `SampledStep`：

```text
prompt_token_ids
completion_token_ids
logp_old
text
finish_reason
```

它负责解析 `<tool_call>`、恢复 JSON 参数、校验 logprob 数量，并验证 token 解码结果与服务端文本一致。

## 6. 模板与 Loss Mask

### `rl/env/template.py`

数据流：

```text
事件日志
  ↓ derive_messages()
消息列表
  ↓ normalize()
工具参数 JSON 归一化
  ↓ pinned chat template
token ids
  ↓ pack()
loss mask
```

模板固定在：

```text
rl/env/qwen2.5_tool.jinja
```

模板 SHA-256 写入 `EnvStamp.template_sha256`，防止模板漂移。

loss mask 规则：

```text
system prompt        mask=0
user question        mask=0
tool result          mask=0
assistant generation  mask=1
```

每个 assistant span 必须包含 `<|im_end|>`，否则模型可能学会持续生成而不停止。

项目验证的是“每个 assistant prompt 是完整序列的前缀”，而不是每个消息边界都是前缀，因为 Qwen 模板会合并连续 tool response。

## 7. Rollout 与轨迹

### 7.1 轨迹结构：`rl/rollout/trajectory.py`

`Trajectory` 包含：

```text
task_id
question
gold_answer
events
reward
stamp
seconds
generation_seconds
sampled
error
```

保存完整事件日志而不是摘要，因此之后可以在没有模型、网络和语料的情况下重新构造消息、运行验证器、计算奖励和执行消融分析。

`EnvStamp` 包含：

```text
corpus_sha256
corpus_docs
preset
max_steps
model
template_sha256
prompt_sha256
```

用于阻止不同环境下的轨迹混合训练。

### 7.2 并发 rollout：`rl/rollout/engine.py`

单条 episode 流程：

```text
创建 episode
  ↓
构造 HarnessContext
  ↓
run_turn()
  ↓
读取事件日志
  ↓
检查 compaction
  ↓
extract Trace
  ↓
reward.score()
  ↓
写入 Trajectory
```

`run_many()` 支持并发执行、refill/batch 调度、每题 G 次 rollout、provider 错误隔离以及吞吐统计。

GRPO 的 G 条同题轨迹通过相同的 `task_id` 分组。任务复制时保持相邻，以便利用服务端 prefix cache。

## 8. 轨迹解析与验证器

### 8.1 Trace：`rl/verifiers/trace.py`

这是唯一直接理解 Harness event vocabulary 的验证模块，输出：

```text
Trace
├── calls
├── steps
├── ended_by
├── queries
├── opened
├── seen_doc_ids
├── answer
├── citations
└── failed_answer_attempts
```

可以区分：

- `answer`
- `max-steps`
- `no-tool-call`
- `error`
- `interrupt`

多次调用 `corpus_answer` 时，失败调用会计数，最后一次成功调用作为最终答案。

### 8.2 结果验证：`rl/verifiers/outcome.py`

使用 HotpotQA/SQuAD 风格归一化：

- 小写
- 去 Unicode 标点
- 删除冠词
- 合并空白

训练奖励使用 Exact Match：

```text
正确 = 1
错误 = 0
```

F1 只用于报告和诊断，不作为训练奖励，避免通过添加额外词语提高部分匹配分数。

### 8.3 过程验证：`rl/verifiers/process.py`

分为：

#### Format

- 是否作答。
- 是否提供引用。
- 是否存在失败的 answer 尝试。

#### Grounding

- 引用是否命中 gold 文档。
- grounding precision、recall 和 F1。
- 打开的 gold 文档数量。

#### AntiGaming

以下任意情况都会作废奖励：

1. 引用了模型从未见过的文档 ID。
2. 引用数量超过 6 个。
3. 在证据返回前就把标准答案写进了搜索查询。

comparison 题有专门豁免：如果答案已出现在问题中，搜索该答案不算记忆作弊。

## 9. 奖励系统

### `rl/verifiers/reward.py`

默认权重：

```python
Weights(
    grounding=0.15,
    efficiency=0.05,
    failed_attempt_penalty=0.05,
)
```

只有答对时才启用 shaping：

```text
total =
    outcome
    + 0.15 * grounding_f1
    - 0.05 * redundant_query_fraction
    - 0.05 * failed_answer_attempts
```

如果反作弊失败：

```text
total = 0
voided = true
```

这种设计避免早期策略通过“立即放弃”最大化效率奖励，也避免通过刷引用获得收益。

## 10. SFT 冷启动

### `rl/train/sft.py`

如果同一道题的 G 条 rollout 奖励完全相同，则：

```text
group mean = 每条样本 reward
advantage = 0
gradient = 0
```

因此先进行 rejection-sampling SFT：

```text
教师模型 rollout
  ↓
同一套环境与工具
  ↓
同一套 verifier
  ↓
保留正确且有引用支撑的轨迹
  ↓
去重和每题限额
  ↓
masked cross entropy
  ↓
LoRA adapter
```

SFT 和 GRPO 共享 `template.pack()`、loss mask、token shift 和 token-level normalization，因此 SFT 也是训练对齐问题的早期检查器。

## 11. GRPO 实现

### 11.1 Advantage：`rl/train/grpo.py`

同一道题的 G 条 rollout 使用组内均值作为 baseline：

```text
A_i = (r_i - mean(group_rewards))
      / (std(group_rewards) + eps)
```

不使用 value head。奖励完全相同的组默认丢弃，包括全部答错和全部答对的情况。

### 11.2 Token-level clipped loss

输入张量：

```text
logits      (B, T, V)
input_ids   (B, T)
mask        (B, T)
advantages  (B,)
logp_old    (B, T)
ref_logp    (B, T)
```

核心计算：

```text
ratio = exp(logp_new - logp_old)
```

随后进行 PPO 风格 clipping，只在 assistant token 上计算损失，分母是整个 batch 的可训练 token 数量，而不是每条序列 loss 的平均值。

参考模型是关闭 LoRA adapter 后的 base model，不需要额外复制一份模型到显存。

## 12. Batch、Padding 与 logprob 对齐

### `rl/train/pack.py`

`Sample` 包含 `task_id`、`token_ids`、`mask`、`reward` 和 `logp_old`。

关键规则：

- Padding 位置 `mask=0)，不参与 loss。
- GRPO 分组依据是 `task_id)，不能按 batch 位置分组。
- `logp_old` 按 assistant span 之前的 `prompt_token_ids` 匹配，而不能按并发完成顺序匹配。
- 如果只有部分 span 匹配成功，则放弃整条样本的旧 logprob，避免一条序列中混合真实 ratio 和 ratio=1。

## 13. 训练模型与 LoRA

### 13.1 `rl/train/policy_model.py`

默认基座模型：

```text
Qwen/Qwen2.5-1.5B-Instruct
```

LoRA 目标模块：

```text
q_proj, k_proj, v_proj, o_proj,
gate_proj, up_proj, down_proj
```

默认配置：

```text
LoRA rank = 32
alpha = 64
dropout = 0
dtype = bfloat16
gradient checkpointing = true
```

提供 `grpo_step()`、`sft_step()`、reference logprob、sampler logprob、梯度裁剪、token 级梯度累积和 adapter 保存。

### 13.2 `rl/train/lora_serve.py`

训练进程和 vLLM 推理进程分离：

```text
PolicyModel 训练进程
      ↓ 保存 LoRA adapter
      ↓ HTTP load_lora_adapter
vLLM 推理进程
```

服务端必须开启：

```text
VLLM_ALLOW_RUNTIME_LORA_UPDATING=1
--enable-lora
```

## 14. GRPO 训练循环

### `rl/train/loop.py`

一次训练 step 包含：

```text
1. 从 active curriculum pool 采样题目
2. 每题 rollout G 次
3. 运行 verifier 并计算 reward
4. 更新课程统计
5. 转换成 token/mask batch
6. 计算 group advantage
7. 执行 GRPO optimizer step
8. 保存并发布 LoRA adapter
9. 定期执行 dev evaluation
10. 写入 metrics JSONL
```

`TrainHooks` 将 GPU 相关逻辑隔离为：

```python
forward_backward()
publish()
evaluate()
```

因此 rollout、课程、指标和告警逻辑可以在没有 GPU 的机器上用 stub 测试。

## 15. 在线课程学习

### `rl/train/curriculum.py`

每道题维护最近 16 条 rollout 的成功记录，默认活跃区间为：

```text
0.05 < pass_rate < 0.95
```

通过率接近 0 的题目暂时太难，通过率接近 1 的题目暂时太简单，只有中间区域继续训练。题目不是永久删除的，模型变强后可以重新进入 active pool。

## 16. 评测与归因

### 16.1 评测报告：`rl/eval/report.py`

报告按以下维度切片：

- 全部任务。
- 一次检索可解 / 需多步检索。
- bridge / comparison。
- 结果正确性。
- grounding。
- 工具调用。
- 放弃率。
- 反作弊作废率。
- rollout 吞吐和长尾。

主要指标：

```text
pass@1
EM
F1
format_valid
voided
gave_up
grounding_f1
mean_steps
mean_tool_calls
```

### 16.2 上下文消融：`rl/attribution/loo.py`

这是 context ablation，不是严格的 action-level causal credit assignment。

```text
完整轨迹
  ↓
删除一个检索文档的上下文
  ↓
计算 gold answer log-prob 的变化
```

```text
delta = log P(gold | full context)
        - log P(gold | ablated context)
```

用于判断 gold 文档是否真正帮助答案、模型是否主要凭记忆作答、distractor 是否比 gold 更重要，以及泄漏过滤是否存在遗漏。

## 17. 检查系统

### `rl/checks/rl_check.py`

检查内容包括：

1. 语料和 manifest hash。
2. BM25 检索确定性。
3. 动作空间和 preset。
4. 系统提示词与工具契约一致性。
5. 模块门禁。
6. Memory Store 和 SQLite 投影一致性。
7. 验证器 fixture。
8. 作答接口不泄漏正确性。
9. chat template hash。
10. tool arguments 不双重编码。
11. assistant prefix property。
12. PolicyAdapter token round-trip。
13. SFT 拒绝采样筛选。
14. GRPO loss。
15. 两种 advantage 实现的一致性。
16. `logp_old` 对齐。
17. 轨迹导出。
18. LoRA 热插拔。
19. 训练循环编排。
20. rollout adapter 工厂。

当前环境运行离线检查时，主要由于缺少：

```text
pydantic
sqlalchemy
httpx
torch
```

因此无法完整通过。已经通过的是答案归一化；其余失败项多数是 import dependency 缺失，不能直接等同于 RL 逻辑错误。

## 18. 当前实现中的风险点

### 18.1 初始 SFT adapter 可能没有加载到 vLLM

在 `rl/train/run_grpo.py` 中，`--adapter` 明确加载到了训练侧 `PolicyModel`，但初始 `served_name` 仍然是 base model。第一次 `publish()` 之前，代码中没有明显将该 adapter 加载到 vLLM 的步骤。

如果外部没有提前手动加载 SFT adapter，可能出现：

```text
训练模型：base + SFT adapter
rollout 模型：base model
```

这会破坏预期的 SFT 冷启动链路。正式训练前应显式将 `args.adapter` 加载到 vLLM，并同步服务端模型名。

### 18.2 `trainable_tokens` 统计可能包含被跳过的退化组

训练循环会跳过奖励完全相同的退化组，但 `loop.py` 的 `trainable_tokens` 统计看起来包含所有 packed samples，而不是只统计实际送入 GPU hook 的样本。

这通常不影响 loss，但会使 metrics 中的训练 token 数高于实际参与反向传播的 token 数。

### 18.3 错误轨迹没有直接进入训练指标分母

provider 错误会被保存到 `trajectory.error`，但训练循环主要使用 `usable` 轨迹统计 reward 和过程指标。如果错误率上升，部分训练曲线仍可能看起来正常。

建议额外记录：

```text
episode_error_rate
provider_error_rate
```

## 19. 总结

当前 RL 子系统的核心价值不只是实现了 GRPO，而是将以下一致性固定在一条闭环中：

```text
模型看到的内容
= 事件日志记录的内容
= 轨迹重放的内容
= verifier 评估的内容
= tokenizer 训练的内容
= 下一轮 rollout 使用的策略版本
```

从工程结构看，项目已经覆盖 Agentic RL 的主要组成部分：环境定义、数据防泄漏、完整轨迹、确定性奖励、token 对齐、SFT 冷启动、GRPO、课程学习、LoRA 热更新、评测切片和归因诊断。

