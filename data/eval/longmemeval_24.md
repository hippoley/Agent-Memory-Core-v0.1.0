# LongMemEval（oracle）首份基线

- 日期：2026-11-12　模型：Qwen2.5-7B-Instruct（本地，抽取/作答/判分同一个）
- 样本：24 题分层抽样（6 类各 4 题），灌入 258 轮用户发言 → 477 条 claims，抽取失败 3 轮
- 总准确率：**20.8%**（5/24），耗时 882s

| 题型 | 准确率 | 题数 | 平均召回 |
|---|---|---|---|
| knowledge-update | 25% | 4 | 3.2 |
| multi-session | 0% | 4 | 5.5 |
| single-session-assistant | 0% | 4 | 1.0 |
| single-session-preference | 0% | 4 | 2.0 |
| single-session-user | 75% | 4 | 1.8 |
| temporal-reasoning | 25% | 4 | 8.5 |

## 19 道错题的归因

- **检索空手而归 6 题**：题面与任何 claim 无可匹配词，模型无从作答（其中 assistant 类 3 题属设计边界：只灌用户侧发言）
- **有记忆却拒答 6 题**：检索到了相关 claim，作答阶段仍回 "Not in memory"（7B 在长上下文中过度保守）
- **答了但值不对 7 题**：多会话计数（3 题，答 1/2 而 gold 是 3/5/8）与知识更新（旧值当新值）为主，根因在抽取粒度：逐轮抽取丢上下文

## 复现

```bash
python scripts/eval/run_longmemeval.py --base-url http://127.0.0.1:8767 \
  --instances 'http://127.0.0.1:8768|lme-eval-2,http://127.0.0.1:8769|lme-eval-3' \
  --key "$MEMORY_API_KEY" --dsn "$MEMORY_DSN" \
  --llm-url http://127.0.0.1:8101/v1/chat/completions --limit 24 --workers 3
```
