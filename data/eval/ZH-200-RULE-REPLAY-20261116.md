# 中文 200 条规则黄金集：2026-11-16 实际复评

**评测范围：** `RuleFactExtractor.from_preset("home-zh")` 直接运行；使用原始 `data/eval/gold_home_zh.jsonl`（200 条，SHA-256: `f7aec900c5cebdd61b8c6c0637c9f0e3332451acb1b2a2ab05bd5a187f37453e`）。这是模型之外、没有数据库写入的**规则抽取**复评，不是完整产品质量数据。

- 代码提交：`b954d9bac45e18ad32261b4c10fef0443397a4e1`
- 运行：GitHub Actions [#43791844945](https://github.com/hippoley/Agent-Memory-Core-v0.1.0/actions/runs/43791844945)
- 自动运行环境：Python 3.11，在 CI 环境安装当前仓库代码
- 运行结果：`200` 条；A/B/C 的精确主体 + 谓词 + 值 + 操作 F1 **0.943396**（TP=100, FP=2, FN=10）；Precision **0.980392**；Recall **0.909091**。
- A 词表内正例：90 条，TP=83, FP=0, FN=7，F1 **0.959538**。
- B 否定陷阱：20 条，TP=17, FP=2, FN=3，F1 **0.871795**。
- D 词表外精确事实覆盖：**0 / 30**。
- 每条预测与错误写进工作流 artifact（在保留期内可下载）；摘要日志记录了数据集哈希和全部统计。
- E 类的第二句话只是单句候选提取，**不等同于**数据库成功替换历史事实；本报告不计算任何记忆修订成功率。
- **未执行：** 真实本地模型抽取、PostgreSQL 记忆修订的 200 条端到端复评、复合检索、最终回答以及模型/上下文/别名消融。

> 历史报告 `report_home_zh.json` 的 2026-11-12 F1=0.9194 来自不同的 HTTP 端到端候选计量方法；本次单独离线运行不能据此直接宣称产品性能提高了 2.4 个百分点。

## 如何手动复现（仅在需要时触发）

```bash
python -m pip install -e .
python scripts/eval/diagnose_zh_200_rules.py --json-out /tmp/zh-200-rules.json

# 不要每次 push 都触发质量基准，避免浪费 CI
gh workflow run zh-rule-audit.yml --repo hippoley/Agent-Memory-Core-v0.1.0
```

生产环境的独立验收必须另行在隔离租户中执行 `scripts/eval/capture_zh_frozen_http.py`，由本地模型提供真实回答。这里不填造任何模型分数。
