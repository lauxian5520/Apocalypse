# 自检用的固定轨迹

六条真实轨迹（deepseek-chat 跑 HotpotQA dev），约 80 KB，**随仓库提供**。

为什么committed：分词、loss mask、SFT 筛选、消融、导出这几个自检阶段都需要
真实轨迹才能验。没有它们的话，一个新克隆会看到六个红色失败——而用户无法
分辨「仓库坏了」和「还差一步」。带上 80 KB，这些阶段在 `git clone` 之后
立刻就能跑。

它们是**夹具，不是数据**：`rl/data/trajectories/` 下的运行产出不入库，
而自检会优先读那里——有新录的就用新的，没有就用这份。

重新录制：

```bash
python -m rl.cli rollout -n 6 --concurrency 6 \
    --out rl/checks/fixtures/trajectories.jsonl
```
