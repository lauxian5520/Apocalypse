# 天启·深研场 — Deep Research 的 Agentic RL 环境

在自研 Agent Runtime（[`backend/harness/`](../backend/harness)）之上构建的一个**可复现、离线、
确定性**的多跳检索 RL 环境：任务集、精确验证器、并发多轮 rollout 引擎、token 级 loss mask，
以及 GRPO 目标函数。

与「拿公开数据集跑一遍 baseline」的区别是：**环境、奖励和验证器本身是产出物**，
而且每一个设计决定都有实测数字支撑——包括推翻我自己第一版设计的那一组。

---

## 1. 一个被实测推翻的设计（这是本项目最值得看的部分）

第一版环境建在**自建的 50k 篇 arXiv 语料**上：让模型根据一段改写过的方法描述找到源论文，
再顺着作者/日期/分类跳到答案。答案来自结构化元数据，所以验证器精确且免费。

验证器那一半是对的。**难度那一半是错的**，而且错得很彻底：

| 测量（50k arXiv，200 样本） | 结果 |
|---|---|
| 取摘要里最罕见的 8 个词去检索它自己 | **top-1 100%**，0.7 ms |
| 先删掉**全部**语料罕见词，再取剩下最罕见的 8 个词 | **top-1 仍然 100%** |
| 第二名 / 第一名 的 BM25 得分比 | 中位 **0.28**，200 例中无一超过 0.8 |
| 把语料收窄到单一主题（LLM / diffusion / RL） | **更糟**，比值降到 0.19 |

收窄语料反而更糟，是因为 IDF：主题词在窄语料里变常见，论文自身的特异词相对更突出。

**结论：在任何能从 arXiv 构建出来的语料上，「按描述找到那篇论文」都是一步到位的**——
因为附近根本没有可混淆的文档。难度必须来自**语料本身存在近似文档**，
这是语料的性质，改措辞改不动。

换成 **HotpotQA distractor split** 后，同样的测量：

| | arXiv 50k | HotpotQA 66.6k |
|---|---|---|
| 第二名/第一名 得分比（中位） | 0.28 | **0.84** |
| 存在 >0.8 强竞争者的比例 | 0% | **58%** |
| 单次检索 top-5 同时命中两篇 gold | — | 48.3% |
| 单次检索 top-5 命中至少一篇 gold | — | 95.7% |

每道 HotpotQA 题自带 2 段 gold + 8 段**专门检索出来的可混淆干扰段**，
这正是 arXiv 语料缺的那个性质。arXiv 的语料与构建器仍保留在仓库里
（`rl/corpus/fetch_arxiv.py`），它是这条结论的证据。

---

## 2. 环境

**语料**：HotpotQA distractor dev split 的 66,581 个去重维基段落，`sha256` 写进清单，
rollout 期零网络。BM25 索引手写（`backend/harness/corpus/index.py`），零依赖，
Raspberry Pi 5 上索引 40 MB、载入 2.7 s、短查询约 0.7 ms。

> 不用 `bm25s` / Pyserini 是刻意的：索引是**环境定义的一部分**，
> 一次库升级悄悄改掉分词或打分，就会悄悄改掉任务，让之前所有数字失效。

**三个动作**，全部 `permission: "read"`，因此审批策略自动放行，rollout 不会停下等人：

| 动作 | 返回 |
|---|---|
| `corpus_search(query, k≤10)` | 编号 + 标题 + 正文前 200 字，**不含 `meta`** |
| `corpus_open(doc_id)` | 标题、`meta`、正文全文 |
| `corpus_answer(answer, citations[])` | `stops_turn`，返回**不含正确性**的固定串 |

`search` 与 `open` 的信息分割**就是**第二跳。`corpus_answer` 的回执不透露对错，
否则模型可以反复调用把验证器变成神谕。

**系统提示词**（`backend/harness/data/prompts/deepresearch.md`）也是环境定义的一部分：它告诉模型
任务是什么，而工具 schema 管不到它。它写明两类题（桥接、比较）怎么拆，并把判分契约直说出来：
答案要**从段落原文照抄**（判分是归一化后的 EM，标准答案常用段落里的全称，而题面用的是简称），
每跳引用一段、不超过 6 个，编号必须来自工具结果，不能先凭记忆猜答案再去搜。这些都是验证器
真实执行的规则，写进提示词不是泄题，而是让「任务」和「判分」说的是同一件事。

它漂移过一次：第一版环境建在 arXiv 上，换成 HotpotQA 之后提示词没跟着改，一直让模型「在论文
语料里」调用 `corpus_open(arxiv_id)`，而那个参数根本不存在。所以现在有两道防线：提示词的
sha256 写进每条轨迹的环境指纹（`EnvStamp.prompt_sha256`），不同提示词下采集的轨迹不能混在一起
导出；`rl_check` 的「提示词与工具契约一致」阶段逐一核对提示词里写出的每个工具签名。

**对 `backend/` 的改动**：全是新增——语料包 `harness/corpus/`、一个工具模块、一个预设及其提示词与
工具契约——外加两个配置字段和工具注册表门禁里的一行。
`agent.py` / `projection.py` / `events.py` / `context.py` / `approval.py` **一行未改**。
`HARNESS_CORPUS_ENABLED` 默认 `false`，线上站点行为完全不变。

---

## 3. 任务集

7,405 道题 → 两道泄漏过滤器取并集 → 冻结带 hash 的 train/dev/test。

```
原始题目                7405
泄漏过滤器              chat ∪ v4-flash
− 闭卷可答（泄漏）丢弃   3684  (49.8%)
− 覆盖不全丢弃           897  (12.1%)
= 保留                  2824
  其中「一次检索即可解」  1158  (41.0%)  —— 只打标签，不丢弃

train 1908 · dev 457 · test 459    按 qtype 分层，各自记 sha256
```

**泄漏过滤是这个环境最关键的一道。** HotpotQA 问的是维基实体，模型大多背过。
不过滤的话，策略不检索也能拿奖励，于是学会跳过工具，所有数字都在测背诵而不是检索。

实测闭卷可答率（无工具、无上下文、提示词刻意鼓励猜而不是拒答，所以是**上界**）：

| 模型 | 有效判定 | 泄漏率 | bridge | comparison |
|---|---|---|---|---|
| `deepseek-chat` | 7404 | 34.7% | 27.2% | 64.8% |
| `deepseek-v4-flash` | 3937 | 28.3% | 27.7% | 29.6% |
| **并集** | — | **49.8%** | — | — |

**近一半的 HotpotQA dev 能被其中一个模型闭卷答对。** comparison 题尤其严重——
题面就把两个候选都写出来了。

**一个模型不够。** 在同样 300 题上对比两个模型：

| | |
|---|---|
| 两者一致率 | 76.7% |
| 弱模型覆盖强模型的比例 | 58.2% |
| 弱模型的泄漏集是强模型的子集吗 | **否**——它知道 19 道对方不知道的 |

所以泄漏是 **(题目, 模型)** 的性质，不是题目单独的性质，正确的过滤是**取并集**。

### 两条容易做错的处理

**空回复是「没有结论」，不是「答不出来」。** 思考型模型会把输出预算全烧在推理上：
`v4-flash` 平均每题 1,291 个 completion token，尾部超过 4,096 上限，**18.6% 拿不到正文**。
把这些算成「模型不知道」会让真正泄漏的题溜进数据集——正是这道过滤器要防的事。
它们记为「未知」，既不计入泄漏率分母，也不进入覆盖集。

**覆盖不全就丢弃。** 于是产生第二个问题：一部分题被两个模型复核过，一部分只被一个复核过，
过滤强度不一致，而弱过滤的那部分会悄悄抬高在它上面测出的任何指标。
`--require-coverage` 只保留**所有过滤器都实际复核过**的题（集合上就是
`(泄漏并集) ∪ (各过滤器有效集的交集)`），代价是少 897 道题，换来过滤强度一致。

### 「一次检索即可解」只打标签不丢弃

它们是**简单**，不是**空洞**——仍然需要构造查询、读结果、综合作答。丢掉会白扔四成数据，
而在线难度课程本来就会在策略变强后把它们自然淘汰（组内奖励方差归零 ⇒ advantage 恒为 0）。
评测时按这个标签分片报告，避免用总体数字掩盖是哪一半在动。

> 副作用值得记一笔：comparison 题从原始的 20.1% 降到切分后的 12.0%，因为它的泄漏率
> 远高于 bridge。这是过滤器的**正确**后果，不是分层的 bug。

---

## 4. 奖励与验证器

结果奖励是**精确匹配**（SQuAD/HotpotQA 归一化），不用 LLM judge：答案都是短跨度、
年份、人名或 yes/no，精确匹配确定且免费，而 judge 会引入一个会漂移的第二模型。
F1 一并报告但**不进入训练信号**——部分给分等于鼓励模型加词对冲。

所有塑形项**门控在正确性之后**。理由很具体：稀疏奖励下，1.5B 策略训练早期几乎从不答对，
此时 outcome 恒为 0，效率惩罚就成了唯一有梯度的项，而最大化它的最省力办法是
**立刻空答**。门控让塑形只在「已经答对的轨迹之间」做区分。

反作弊命中即**作废**（不是打折，打折在 outcome 更大时仍然有利可图）：

- **编号出处校验**——引用的编号必须在此前某条 `tool/result` 里真的出现过。
- **记忆作答**——答案在任何含它的文档返回之前就被写进检索词。
- **引用灌水**——引用条数超过阈值。

### 一个在 n=3 就抓到的假阳性

> *"Which is a flowering plant, Pueraria or **Pleiospilos**?"*

答案就写在题面上，所以检索它是**正确行为**，不是记忆。这条轨迹引用完全正确
（grounding F1 = 1.0）却被判作废。**comparison 题占 HotpotQA 的 20%**——
这条规则会在训练中悄悄把五分之一的任务分布判成零分，而且看起来会像
「策略就是不会做对比题」。修法是豁免题面里已出现的答案，并写成固定用例钉死。

---

## 5. 分词对齐：训练与 rollout 必须逐 token 相同

多轮 RL 最容易错、也最难发现的一环。Qwen2.5 的 chat template 里是：

```jinja
{{- '", "arguments": ' }}{{- tool_call.arguments | tojson }}
```

而 `derive_messages()` 交出的 `arguments` 是**模型写的原始 JSON 字符串**。
对字符串做 `tojson` 会双重编码——实测：

```
按存储形态（字符串）-> "arguments": "{\"query\": \"nietzsche\"}"
解析成对象后       -> "arguments": {"query": "nietzsche"}
```

vLLM 服务端会先归一化再套模板，所以 **rollout 产出第二种、本地重建产出第一种**。
每一次工具调用都会差若干 token，不报错、不告警，梯度就算在了从未被采样的文本上。

另外两条钳制：

- **`<|im_end|>` 必须进 loss mask**，否则模型学会永不停止。
- **chat template 作为文件入库**（`env/qwen2.5_tool.jinja`，sha256 随轨迹一起盖章），
  因为 HF 会原地更新模板。

### 真正需要成立的前缀性质

我一开始断言「每个消息边界处前缀都稳定」，六条真实轨迹**全部失败**。
原因不是 bug：Qwen 会把**连续的 tool 响应合并进同一个 `<|im_start|>user` 块**，
所以加第二条 tool 消息会移动第一条的收尾标记。中间前缀确实不稳定。

但 mask 需要的是更弱的条件——**每个 assistant 轮的 prompt 是完整序列的 token 前缀**。
没有任何 assistant 轮落在 tool 块内部，所以这条**精确成立**，实测六条轨迹全绿。

> 顺带一个值得记的数字：**每条轨迹只有 7–12% 的 token 是可训练的**，
> 其余都是工具观测。这就是多轮场景必须用 token 级归一化的直接理由。

---

## 6. GRPO

无 value head，组内相对 advantage，**token 级归一化**（`sum / batch 内 mask token 总数`）。

合成张量上的实测，说明为什么不能按序列取平均：

```
一条 5 token 的轨迹（advantage +1）+ 一条 1 token 的轨迹（advantage −1）
  token 级归一化 : -0.667
  按序列取平均   : +0.000      ← 两条完全抵消
```

按序列平均会系统性地低估长轨迹，而长程行为正是这个环境要训练的东西。

`logp_old` **在 rollout 时就记录**，不是事后重算——重算得到的是当前策略的数字，
ratio 恒为 1、裁剪永不触发，现象看起来还像「训练很稳」。
对齐约定（`logp_old[b,t]` 是 `input_ids[b,t]` 自身的对数概率）由自检用例钉死：
策略与采样者相同时 ratio 必须**精确等于 1**。

---

## 7. 训练循环

一步做七件事：从课程池的**活跃**子集采题 → 每题 rollout G 条 → 验证器打分并回灌课程 →
打包成带 loss mask 的 token 序列 → 组内归一化成 advantage（退化组直接跳过）→
一步裁剪的 token 级损失 → 存 LoRA adapter 并换进服务。

第七步是保持 on-policy 的关键，**漏掉它是这里最贵的静默故障**：训练照常进行、loss 照常下降，
但第 1 步之后的每一条 rollout 都来自基座模型。

**需要 GPU 的只有三个 hook**（forward_backward / publish / evaluate），其余编排——采样、
课程更新、指标累积、健康告警——全部是纯 Python，所以在没有卡的机器上用 stub 就能完整跑一遍。
自检里正是这么验的：断言退化批次被跳过而不是乘零、断言换权重按期发生、断言告警在该响的时候响。

### 必须盯的曲线

reward 曲线单独看不出健康与否。这些各自对应一种它掩盖不住的失败：

| 指标 | 它的失败长什么样 |
|---|---|
| `all_zero_groups` | 完全没有梯度；reward 走平而损失函数里**一切正常** |
| `clip_fraction` | ≈0 说明这一步啥也没更新；≈1 说明策略已跑离采样分布 |
| `kl` | 持续上升＝正在漂离参考模型，通常是崩塌前兆 |
| `entropy` | 快速下降＝探索已死，reward 随后必然见顶 |
| `format_valid` | 下降＝策略在丢掉工具协议，不是在变聪明 |
| `gave_up` | 上升＝它学会了「不答比检索更省」 |
| `voided` | 上升＝它找到了刷验证器的办法 |

最后两个对这个环境最要紧，因为 reward hacking 和奖励崩塌就是这样显形的——
而且**它们可以和平均 reward 同时上升**。

### 在线难度课程

组内奖励全相同的题贡献恰好为零的梯度，却照样要花 G 条 rollout。但静态过滤错两次：
**标定错了模型**（教师 4/8 的题在 1.5B 上可能 0/8），**只标定一次**（今天 0/8 的题
明天会变成 3/8，那正是那时最该练的）。所以通过率用运行中已有的 rollout 持续重估，
退休也是可逆的。奖励是现成的——它们本来就是为训练算的。

> 两份 `group_advantages` 实现（纯 Python 给循环、torch 给损失）由自检断言**数值一致**，
> 因为悄悄漂开就意味着循环训练用的 advantage 和损失验证过的不是同一个。

---

## 8. 权重怎么进 rollout 服务

不写权重同步代码：vLLM 跑在**独立进程**（`--enable-lora`），trainer 存下 LoRA adapter，
`lora_serve.py` 让服务自己去读。基座权重从不移动，所以没有东西需要同步。

- 单卡不争显存：trainer 拿着优化器状态，服务拿着 KV cache，各自一个进程。
- 训练期和评测期走**同一个** `PolicyAdapter`，checkpoint 的评测路径与训练路径一致。
- 每一步的 adapter 按步号命名（`policy-step-000123`）并盖进轨迹的 `stamp`，
  混进离策略数据这种难查的 bug 变成可核对的。

**必须给服务设 `VLLM_ALLOW_RUNTIME_LORA_UPDATING=1`。** 不设的话热插拔端点返回 404，
每次换权重静默失效，整个训练全程都在采样基座模型——loss 曲线会动，reward 不会动，
而原因完全看不见。所以 `check_runtime_lora()` 在开跑前就问一次。
换权重是**先卸后装**：装一个服务已知的名字在某些版本上会报错，而半完成的交换会留下旧
adapter 继续服务，于是训练的版本和采样的版本不是同一个。

---

## 9. 复现

```bash
# 语料与任务（约 3 分钟，不需要 GPU，不需要 API）
python -m rl.cli corpus hotpot --split validation
python -m rl.cli corpus index
python -m rl.cli corpus verify

# 泄漏测量（需要 API）。分批落盘、可续跑；第二个模型用 --skip-leaked-from
# 跳过前一个已判泄漏的题——并集里它们无论如何都要丢，复核是白花钱。
python -m rl.cli tasks leakage --all --model deepseek-chat \
    --concurrency 8 --chunk 500 --resume --out data/leak_chat.jsonl
python -m rl.cli tasks leakage --all --model deepseek-v4-flash \
    --concurrency 8 --chunk 250 --resume \
    --skip-leaked-from data/leak_chat.jsonl --out data/leak_flash.jsonl

# 冻结切分：多清单取并集，--require-coverage 保证过滤强度一致
python -m rl.cli tasks split --require-coverage \
    --leakage-file data/leak_chat.jsonl --leakage-file data/leak_flash.jsonl

# rollout 与评测
python -m rl.cli rollout --split dev -n 50 --concurrency 8 \
    --out data/traj.jsonl --report data/report.md

# 导出给外部训练框架（verl / TRL / 任意）
python -m rl.cli export data/traj.jsonl --format tokens --out data/verl.jsonl

# ── 训练（需要 GPU；下面两步之外的一切都不需要）────────────────────
# 1. 冷启动数据：教师模型走同一个环境、同一套工具，只保留过验证器的轨迹
python -m rl.train.run_sft collect --split train -n 400 -G 4 \
    --model deepseek-chat --out rl/data/sft/teacher.jsonl

# 2. SFT
python -m rl.train.run_sft train --trajectories rl/data/sft/teacher.jsonl \
    --base Qwen/Qwen2.5-1.5B-Instruct --out rl/data/adapters/sft

# 3. 起 vLLM（独立进程，必须开运行时 LoRA 热插拔）
VLLM_ALLOW_RUNTIME_LORA_UPDATING=1 vllm serve Qwen/Qwen2.5-1.5B-Instruct \
    --enable-lora --max-lora-rank 32 --max-loras 1 --port 8000

# 4. 先冒烟（2 题 · G=2 · 1 步），再正式跑
python -m rl.train.run_grpo --smoke --base Qwen/Qwen2.5-0.5B-Instruct
python -m rl.train.run_grpo --base Qwen/Qwen2.5-1.5B-Instruct \
    --adapter rl/data/adapters/sft --steps 500 -G 8 --questions-per-step 8

# 自检：21 个离线阶段 + 1 个真实 rollout（其中 2 个阶段需要 torch，没装时跳过）
python -m rl.checks.rl_check --offline
```

环境自检遵循仓库既有约定（无 pytest，`check_*` 成功返回描述串、失败抛异常、
非零退出），可直接接进部署脚本。

---

## 10. 现在能跑到哪一步

**不需要 GPU、现在就能完整跑通**（已在 Raspberry Pi 5 上逐条验证）：

| | 验证情况 |
|---|---|
| 语料构建 / BM25 索引 / 哈希校验 | ✅ 66,581 段，`corpus verify` 通过 |
| 泄漏测量与冻结切分 | ✅ chat ∪ v4-flash 并集，覆盖一致 |
| 三个动作 + preset + 模块门禁 | ✅ 门禁关闭时线上工具集不变 |
| 验证器 / 反作弊 / 评测报告 | ✅ 含 golden fixture |
| 并发 rollout（连续补位 vs 批同步） | ✅ 打真实 provider 跑通 |
| 分词对齐与 loss mask | ✅ 真实轨迹上前缀性质与 EOS 全绿 |
| 轨迹导出（messages / tokens） | ✅ 混合环境指纹被拒 |
| **SFT 数据采集 → 筛选 → 分词** | ✅ 真跑：4 条采样 → 过验证器 2 → 去重后 1 条可训练 |
| GRPO 损失 / advantage / SFT 交叉熵 | ✅ CPU 上（py311）数值验证 |
| 训练循环编排（课程 / 指标 / 告警） | ✅ stub hooks 全路径 |
| LoRA 热插拔接线 | ✅ 本地 stub 服务验证两条分支 |

`python -m rl.checks.rl_check` —— **20 个阶段全绿**（2 个 torch 阶段在默认解释器上跳过，
在 py311 里单独验过）。

**需要 GPU 才能跑**（代码已写完，缺的是卡）：

| | |
|---|---|
| `run_sft train` | LoRA 微调 —— 需要 torch + peft |
| `run_grpo` | 正式训练 —— 需要 torch + peft + 一个 vLLM 进程 |
| 信用分配三方案消融 | 依赖上面两步的产物 |

没有卡时这两个入口会**提前退出并给出可操作的提示**（缺哪个包、怎么装、为什么开发机的
3.14 解释器装不了 torch），而不是从某个用户没提过的模块里抛 `ModuleNotFoundError`。

## 致谢与许可

任务与语料来自 **HotpotQA**（Yang et al., 2018），许可 **CC BY-SA 4.0**。
chat template 来自 **Qwen2.5-1.5B-Instruct**（Apache-2.0）。
本目录代码随本仓库许可。
