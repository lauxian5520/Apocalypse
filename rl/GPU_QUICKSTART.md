# GPU 机上从 `git clone` 到开始训练

这份文档假设你刚租到一张卡，什么都没装。全程大约 20 分钟，其中大部分是下载。

前置阅读不是必需的，但 [`course/18`](../course/18-rl-分词对齐与loss-mask.md) 解释了
为什么第 4 步的冒烟测试不能跳过。

---

## 0. 这个仓库带了什么、没带什么

| | 随仓库提供 | 为什么 |
|---|---|---|
| 冻结的 train/dev/test（2,824 题，带 sha256） | ✅ | 一个数字只在某个具体切分上有意义 |
| 两个模型的闭卷泄漏判定（约 11k 条） | ✅ | 花了真钱和数小时；provider 下个月未必给同样答案 |
| 语料清单 `manifest.json` | ✅ | 用来校验你重建出的语料是不是同一份 |
| chat template（`env/qwen2.5_tool.jinja`） | ✅ | HF 会原地更新模板，改了会让已有轨迹全部失效 |
| 语料正文 + BM25 索引（119 MB） | ❌ | 3 分钟可确定性重建，见第 2 步 |
| tokenizer 二进制（11 MB） | ❌ | 从 Hub 拉，词表不会像模板那样漂 |
| 模型权重 | ❌ | 从 Hub 拉 |

---

## 1. 装依赖

```bash
git clone https://github.com/lauxian5520/Apocalypse.git
cd Apocalypse

# 用 Python 3.12：下面的依赖解析就是在 3.12 上实测通过的
conda create -n rl python=3.12 -y && conda activate rl

pip install -r rl/requirements.txt -r rl/requirements-trainer.txt
```

实测（2026-09-21）：在 x86_64 · Python 3.12 · glibc 2.35（Ubuntu 22.04）下，这两个文件一起能解析，
得到 `vllm 0.29.0`、`torch 2.13.0`、`transformers 5.17.0`、`peft 0.21.0`。vLLM 会**精确锁定** torch
版本，所以 torch 的版本由它决定，文件里只写下限。

**装之前确认三件事**，每一件都能让第一步就失败：

| | 要求 | 不满足会怎样 |
|---|---|---|
| 系统 | glibc ≥ 2.31，即 Ubuntu 20.04 及以上 | vLLM 依赖的 `llguidance` 只发了 `manylinux_2_31` 的轮子，pip 报「找不到版本」 |
| 网络 | 国内机器先 `export HF_ENDPOINT=https://hf-mirror.com` | 语料、tokenizer、权重全部从 Hub 下载；这个变量对 transformers、vLLM 和第 2 步的语料下载都生效 |
| 磁盘 | 系统盘小的机器，`export HF_HOME=<数据盘>/hf` | vLLM 与 torch 的轮子加上 1.5B / 7B 权重有二三十 GB |

建议把这两个 `export` 写进 `~/.bashrc`，第 4 步另开的终端里也需要它们。

`rl/requirements.txt` 里的 `sqlalchemy` / `pydantic-settings` / `httpx` 不是可选的：
`rl/` 以库的方式 import `backend/harness`，而 `harness/__init__.py` 是一个 eager facade，
导入任何子模块都会牵出配置与数据库层。文件里逐条注明了每个依赖是被哪个模块拉进来的。

## 2. 准备数据

```bash
python -m rl.cli setup
```

它做四件事：重建语料（HotpotQA distractor，约 3 分钟，**不花 API**）、建 BM25 索引、
预取 tokenizer、**把重建结果的 sha256 和仓库里的清单比对**。

最后一步很重要：

> 如果上游数据集变了，重建出的语料就不是所有已报告数字所基于的那一份。
> 这时 `setup` 会**失败并说明**，而不是让你在一份不同的语料上继续跑、
> 最后拿到一组无法与 README 对比的数字。

## 3. 自检

```bash
python -m rl.checks.rl_check --offline
```

期望 **通过 21 · 跳过 1**：GPU 机上 torch 可用，两个 torch 阶段不再跳过；跳过的那一个是
需要 API 的真实 rollout，`--offline` 本来就不跑它。
任何一个红的都先解决再往下走——这些断言存在的理由就是它们守的那些错都是静默的。

## 4. 冒烟：先花 5 分钟，别直接烧几小时

```bash
# 起 vLLM（独立进程）。VLLM_ALLOW_RUNTIME_LORA_UPDATING 必须设
VLLM_ALLOW_RUNTIME_LORA_UPDATING=1 vllm serve Qwen/Qwen2.5-0.5B-Instruct \
    --enable-lora --max-lora-rank 32 --max-loras 1 --port 8000 \
    --gpu-memory-utilization 0.35 &

# 另开一个终端
python -m rl.train.run_grpo --smoke --base Qwen/Qwen2.5-0.5B-Instruct
```

`--smoke` 是 2 题 · G=2 · 1 步，每步都换权重都评测——**把全链路走一遍**：
rollout → 打分 → 分词打 mask → advantage → 前向反向 → 存 adapter → 热插拔 → 评测。

**`--gpu-memory-utilization 0.35` 不能省。** vLLM 会按这个比例预占**整张卡**给 KV cache，默认值是
0.9。它先启动、训练进程后启动，在同一张 24 GB 的卡上训练只剩约 2.4 GB，比 1.5B 模型的 bf16
权重（约 3 GB）还小，于是 OOM，而报错里没有任何一处指向 vLLM。0.35 给 vLLM 约 8 GB，对 1.5B 的
rollout 足够。训练入口启动时会打印空闲显存，低于 6 GB 会给出这条提示；真 OOM 时也会给出同样
的提示，而不是一段堆栈。有两张卡的话，更干净的做法是 `CUDA_VISIBLE_DEVICES=1 vllm serve …`，
训练留在 0 号卡。

不设 `VLLM_ALLOW_RUNTIME_LORA_UPDATING=1` 会怎样：热插拔端点返回 404，
每次换权重**静默失效**，整个训练全程都在采样基座模型——loss 曲线会动，reward 不会动，
而原因完全看不见。`run_grpo` 在加载模型**之前**就检查这一条并报错退出。

## 5. 冷启动 SFT

GRPO 的 advantage 是「奖励减去同题其他 rollout 的均值」。如果一道题 8 条 rollout 全是 0 分，
组内标准差为 0，**每条的 advantage 精确为 0，梯度精确为 0**。1.5B 模型直接上多跳检索
就是这个状态：烧几小时 GPU，reward 一条直线，而 loss 曲线看起来很平静。

教师模型走 API，所以先配 key（`.env` 不随仓库，新克隆里没有）：

```bash
cp .env.example .env      # 然后填 AI_PROVIDER=deepseek 与 DEEPSEEK_API_KEY
```

```bash
# 采集：教师模型走同一个环境、同一套工具（需要 provider，不需要 GPU）
# -n 0 表示整个 train 切分（1908 题）
python -m rl.train.run_sft collect --split train -n 0 -G 4 --concurrency 8 \
    --model deepseek-chat --out rl/data/sft/teacher.jsonl

# 训练
python -m rl.train.run_sft train \
    --trajectories rl/data/sft/teacher.jsonl \
    --base Qwen/Qwen2.5-1.5B-Instruct --out rl/data/adapters/sft
```

**在租卡之前做这一步。** `collect` 不需要 GPU，在任何能跑 rollout 的机器上（包括开发用的 Pi）
提前跑好，再把 `teacher.jsonl` 拷过来。按仓库里 6 条真实轨迹实测：每条平均 3.3 次模型调用、
约 14 秒、约 $0.0009（deepseek flash 档费率）。

| 规模 | rollout 数 | 费用 | 墙钟（并发 8） | 最多可训练条数 |
|---|---|---|---|---|
| `-n 400 -G 4` | 1,600 | 约 $1.4 | 约 45 分钟 | 800 |
| `-n 0 -G 4`（全部 1908 题） | 7,632 | 约 $7 | 约 3.7 小时 | 3,816 |

「最多」是因为每题最多保留 2 条（`MAX_PER_QUESTION`，防止几道简单题占满数据），实际再乘上
教师的通过率。计划要 2–5k 条，所以用全量；400 题大概只能得到几百条。
让一张卡空等几个小时的采集是纯浪费。

**闸门**：SFT 之后在 dev 上 pass@1 没有大致翻倍、格式合法率没上 95%，
就停下来查，别碰 GRPO。

## 6. GRPO

```bash
VLLM_ALLOW_RUNTIME_LORA_UPDATING=1 vllm serve Qwen/Qwen2.5-1.5B-Instruct \
    --enable-lora --max-lora-rank 32 --max-loras 1 --port 8000 \
    --gpu-memory-utilization 0.35 &

python -m rl.train.run_grpo \
    --base Qwen/Qwen2.5-1.5B-Instruct \
    --adapter rl/data/adapters/sft \
    --steps 500 -G 8 --questions-per-step 8 \
    --metrics rl/data/metrics/grpo.jsonl
```

指标按步追加写入 JSONL（每步 flush），所以跑到第 400 步挂掉也留得下 400 步的曲线。

### 出问题时先看这三个数

| 指标 | 它在说什么 |
|---|---|
| `all_zero_groups` > 0.7 | 组内奖励全一样，**梯度为零而算力照付**。回到第 5 步 |
| `clip_fraction` ≈ 0 | 这一步几乎没更新。检查 `logp_old` 是不是被事后重算了 |
| `gave_up` 上升 | 学会了「不答比检索更省」。确认塑形项门控在正确性之后 |

`gave_up` 和 `voided` 最要紧，因为**它们可以和平均 reward 同时上升**——
奖励在涨而任务在退化，这是唯一靠 reward 曲线看不出来的一类失败。

## 7. 显存不够时

先确认 vLLM 带了 `--gpu-memory-utilization 0.35`（见第 4 步）——那是最常见的原因，
而且和下面这些参数都无关。然后按这个顺序调，前面的代价小：

1. `--micro-batch-size 1`（默认就是）
2. `--max-tokens 768`（默认 1024）
3. `--batch-size 4`（默认 8）
4. `-G 4`（默认 8）—— 最后才动它，G 越小组内基线方差越大
5. 换 0.5B 基座先把流程跑通

trainer 和 vLLM 是**两个进程**，各自一个显存分配器。24 GB 卡上这是装得下与装不下的差别，
代价是权重通过 LoRA 热插拔而不是直接拷贝——也就是第 4 步那个环境变量必须设的原因。

### 有两张卡：一个进程一张

显存压力来自 vLLM 和训练挤在同一张卡上，所以第二张卡最有效的用法是**把两者分开**。
不用改代码：

```bash
# 1 号卡只给 vLLM。独占一张卡时不需要再压 --gpu-memory-utilization
CUDA_VISIBLE_DEVICES=1 VLLM_ALLOW_RUNTIME_LORA_UPDATING=1 vllm serve Qwen/Qwen2.5-1.5B-Instruct \
    --enable-lora --max-lora-rank 32 --max-loras 1 --port 8000 &

# 0 号卡只给训练
CUDA_VISIBLE_DEVICES=0 python -m rl.train.run_grpo --base Qwen/Qwen2.5-1.5B-Instruct …
```

每个进程只看得见自己那张卡，在进程内都是 `cuda:0`，所以训练的 `--device` 保持默认即可。

**多卡数据并行训练（DDP / `torchrun`）目前不支持**，而且它也不解决显存问题：数据并行是在
每张卡上各放一份**完整**模型，省的是时间，不是单卡显存。真要降单卡显存得做分片（FSDP /
ZeRO），对 1.5B 来说杀鸡用牛刀。vLLM 自己的 `--tensor-parallel-size` 同理，1.5B 的权重只有 3 GB，
切开只会增加卡间通信。

### 显存峰值在哪

训练一步里最大的张量是整条序列 × 整个词表（151,936）的 logits 和它的 `log_softmax`，
随轨迹长度线性增长。按仓库里的真实轨迹：平均约 2,000 token，而 `MAX_SEQUENCE_TOKENS`
把单条上限卡在 8,192。一份这样的 bf16 张量在 2,000 token 时约 0.6 GB，在 8,192 时约 2.5 GB；
一步里同时存在的有好几份（logits、`log_softmax`、熵、反向时的梯度），模型若把 logits 升成
fp32 还要再翻倍。所以常规长度的轨迹在 24 GB 卡上与 vLLM 共存问题不大，接近上限的长轨迹才是
风险。以冒烟测试时 `nvidia-smi` 的实测为准。

真到了不够的时候，比加卡更划算的是**只在需要的位置算 log-prob**：计入 loss 的 token 只占约
10%（其余是工具返回的检索结果，本来就被 mask 掉），现在却对全部位置都算了全词表的
`log_softmax`。只对计入 loss 的位置取 hidden state 再过 `lm_head`，这部分峰值能降一个数量级。
这个优化还没做——训练路径没在真 GPU 上跑过，先让冒烟测试告诉我们它是否必要。

---

## 附：如果想自己重跑泄漏过滤

仓库里的判定是拿 `deepseek-chat` 和 `deepseek-v4-flash` 跑出来的（约 ¥15、数小时）。
想换模型或复现：

```bash
python -m rl.cli tasks leakage --all --model <模型> \
    --concurrency 8 --chunk 250 --resume --out rl/data/leakage/<名字>.jsonl

python -m rl.cli tasks split --require-coverage \
    --leakage-file rl/data/leakage/chat.jsonl \
    --leakage-file rl/data/leakage/<名字>.jsonl
```

`--resume` 分批落盘可续跑；第二个模型加 `--skip-leaked-from` 可以跳过前一个已判泄漏的题
（并集里它们无论如何都要丢，再问一遍是白花钱）。

**注意这会覆盖 `rl/data/splits/`，让你的数字不再与 README 可比。**
要对比的话，先把原来的切分留一份。
