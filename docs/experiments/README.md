# 已有 Qwen3-8B 实验成果

已有本地记录：SFT 710 步、GRPO 500 步；本轮交付未重新训练。
主指标使用完整选定题目数，错误与缺失按失败计。有效轨迹指标单独列出。

- dev：基座 97/457，SFT 170/457，GRPO-500 187/457。
- test：SFT 162/459，GRPO-500 190/459。配对 bootstrap 区间见 test-comparison.md。
- test 格式合法数由 405 降至 393，错误轨迹由 2 增至 6；正确率提升不代表执行稳定性同步改善。

历史运行没有足以核验的代码提交记录；manifest 中将训练提交明确设为 null。当前依赖快照是交付环境记录，不冒充历史训练环境。生成参数来自已有报告，原始运行未保留新的 .run.json 清单，所以旧目录只用于核验，不能直接续评。

重算与打包：`python tools/package_rl.py`。完整产物位于 `artifacts/rl-delivery/`，不提交 Git。使用同目录 sha256 文件检验压缩包；解压后按 manifest 核验每个文件。包中包含 SFT/GRPO-500 adapter、原始评测轨迹、日志与冻结切分，不包含模型基座、密钥或站点数据。

新评测：`python -m rl.eval.run_policy --base Qwen/Qwen3-8B --candidate GRPO-500=rl/data/adapters/grpo-qwen3-12steps/policy-step-000500 --split test --n 0 --out-dir NEW_DIRECTORY`。预先启动带运行时 LoRA 支持的 vLLM。续评必须使用同一目录、参数和 adapter；清单不匹配时程序拒绝混用。

本次没有新增多随机种子实验或信用分配消融；不能据单次训练声称跨种子稳定提升。

可核验的历史训练信息：日志显示 RTX 4090、torch 2.13.0+cu130；SFT 分词后 1419 条训练样本，GRPO 训练切分 1908 题，两份 LoRA 配置随包提供。完整优化器参数和启动命令没有可核验记录，manifest 保留为未知；本包支持结果核验，不宣称逐位复现历史训练。
