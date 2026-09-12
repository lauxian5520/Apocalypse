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

# Python 3.11 或 3.12。不要用 3.13+：torch 的轮子还没跟上
conda create -n rl python=3.12 -y && conda activate rl

pip install -r rl/requirements.txt -r rl/requirements-trainer.txt
```

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

期望 **20 个阶段全绿**（GPU 机上 torch 可用，所以两个 torch 阶段不会跳过）。
任何一个红的都先解决再往下走——这些断言存在的理由就是它们守的那些错都是静默的。

## 4. 冒烟：先花 5 分钟，别直接烧几小时

```bash
# 起 vLLM（独立进程）。VLLM_ALLOW_RUNTIME_LORA_UPDATING 必须设
VLLM_ALLOW_RUNTIME_LORA_UPDATING=1 vllm serve Qwen/Qwen2.5-0.5B-Instruct \
    --enable-lora --max-lora-rank 32 --max-loras 1 --port 8000 &

# 另开一个终端
python -m rl.train.run_grpo --smoke --base Qwen/Qwen2.5-0.5B-Instruct
```

`--smoke` 是 2 题 · G=2 · 1 步，每步都换权重都评测——**把全链路走一遍**：
rollout → 打分 → 分词打 mask → advantage → 前向反向 → 存 adapter → 热插拔 → 评测。

不设 `VLLM_ALLOW_RUNTIME_LORA_UPDATING=1` 会怎样：热插拔端点返回 404，
每次换权重**静默失效**，整个训练全程都在采样基座模型——loss 曲线会动，reward 不会动，
而原因完全看不见。`run_grpo` 在加载模型**之前**就检查这一条并报错退出。

## 5. 冷启动 SFT

GRPO 的 advantage 是「奖励减去同题其他 rollout 的均值」。如果一道题 8 条 rollout 全是 0 分，
组内标准差为 0，**每条的 advantage 精确为 0，梯度精确为 0**。1.5B 模型直接上多跳检索
就是这个状态：烧几小时 GPU，reward 一条直线，而 loss 曲线看起来很平静。

```bash
# 采集：教师模型走同一个环境、同一套工具（需要 provider，不需要 GPU）
python -m rl.train.run_sft collect --split train -n 400 -G 4 \
    --model deepseek-chat --out rl/data/sft/teacher.jsonl

# 训练
python -m rl.train.run_sft train \
    --trajectories rl/data/sft/teacher.jsonl \
    --base Qwen/Qwen2.5-1.5B-Instruct --out rl/data/adapters/sft
```

`collect` 不需要 GPU，可以在别的机器上提前跑好再拷过来——它要花数小时，
让一张卡空等是浪费。

**闸门**：SFT 之后在 dev 上 pass@1 没有大致翻倍、格式合法率没上 95%，
就停下来查，别碰 GRPO。

## 6. GRPO

```bash
VLLM_ALLOW_RUNTIME_LORA_UPDATING=1 vllm serve Qwen/Qwen2.5-1.5B-Instruct \
    --enable-lora --max-lora-rank 32 --max-loras 1 --port 8000 &

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

按这个顺序调，前面的代价小：

1. `--micro-batch-size 1`（默认就是）
2. `--max-tokens 768`（默认 1024）
3. `--batch-size 4`（默认 8）
4. `-G 4`（默认 8）—— 最后才动它，G 越小组内基线方差越大
5. 换 0.5B 基座先把流程跑通

trainer 和 vLLM 是**两个进程**，各自一个显存分配器。24 GB 卡上这是装得下与装不下的差别，
代价是权重通过 LoRA 热插拔而不是直接拷贝——也就是第 4 步那个环境变量必须设的原因。

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
