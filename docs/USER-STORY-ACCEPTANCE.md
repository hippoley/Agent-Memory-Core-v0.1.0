# Agent Memory Core — User Story 与可执行验收契约

> 本文件定义“产品完成”而不是“代码提交成功”。每个 Gate 必须在同一个发布候选 SHA 上运行；
> CI 跳过测试、使用替身模型、未提供环境或缺少数值，均不能视为通过。

## 最终用户

第三方家庭 Agent：通过 HTTP/SDK 记住用户的中文偏好，承认纠正，查询历史，
订阅事件，并在重启、重复写入和跨租户访问时保持一致性。

## US-01 / 十个执行步骤

| Step | 用户行为与成功后的可观察结果 | 自动证据 | 现阶段验收 |
|---|---|---|---|
| S01 接入 | 无凭证拒绝，有效 key 访问 readiness | `test_user_story_e2e.py`, `test_sdk_contract.py` | 已有真实 API 测试 |
| S02 中文理解 | 口语→本地模型→有主体/谓词/值/来源的候选，不虚构 | `test_local_extractor.py` | 协议已测；真实模型质量**未测** |
| S03 原子写入 | 请求结束后新连接能读到事实、来源、时间和 outbox | `test_user_story_e2e.py`, `test_pg_runtime.py` | 真 PostgreSQL 测试 |
| S04 加工纠正 | 去重、否定、冲突、实体消歧、历史版本均正确 | `test_processing.py`, `test_user_story_e2e.py` | 仅结构化候选纠正；复杂否定**未完成** |
| S05 投影 | FTS、向量、AGE 独立任务，成功状态不能互相冒充 | `test_concrete_projections.py`, `test_projection_deliveries.py` | 真实数据库路径，质量另测 |
| S06 检索 | 正确当前记忆、证据、双时间，跨渠道排序可解释 | `test_user_story_e2e.py`, `test_hybrid.py` | 机制可测；中文 Recall@k **未测** |
| S07 纠正 | 当前仅新事实；known_at=纠正前仍返回旧事实 | `test_user_story_e2e.py` | 真实数据库测试 |
| S08 事件 | 分页、游标重连、SSE；远端 Webhook 持久重投 | `test_webhooks_integration.py`, `test_webhook_api_integration.py`, `test_sdk_contract.py` | Webhook 订阅、签名、退避、死信、重放已实现；真实 PG 集成 CI 通过；外部真实接收端尚未实测 |
| S09 故障恢复 | DB/Worker 故障后任务可重试、DLQ 重放、无静默丢数据 | `test_projection_deliveries.py`, `test_outbox_runtime.py` | 故障注入尚不完整 |
| S10 隔离 | 不同租户不可读、写、引用、订阅他人资源 | `test_user_story_e2e.py`, `test_api_postgres_runtime.py` | API 层已测；DB RLS **未实现** |

## Release Gate — 明确区分目标与实测

以下数值是拟议的内网单机验收门槛，**不是当前实测结果**。

| 类别 | 测试负载 / 输入 | 拟议门槛 |
|---|---|---|
| 可靠性 | 真实 PostgreSQL，故障注入后重新开启连接 | 0 条已确认写入丢失；0 次跨租户泄漏 |
| 幂等 | 相同请求、同一个事件重复交付 100 次 | canonical 结果和副作用仅产生一次 |
| 并发 | 100 个并发 Agent，各执行写入/读取/纠正 | 无数据丢失、无错租户、请求成功率 ≥99.9% |
| API 延迟 | 1 万次本地网内纯事实 recall，预热后 | p50 < 100 ms，p95 < 500 ms |
| 写入延迟 | 1 万次 canonical write，不含 LLM 推理 | p95 < 800 ms |
| 检索质量 | 至少 200 条有标准答案的中文多轮样本 | Recall@5 ≥0.90，MRR@10 ≥0.85 |
| 抽取质量 | 200 条纠正/否定/指代/实体歧义中文样本 | 事实 F1 ≥0.90；纠正准确率 ≥0.95 |
| 错误控制 | 100 次不支持的工具/越权/错误证据请求 | 100% 拒绝；无错误执行 |
| 重连 | SSE 断线后从原游标恢复 | 0 条缺失（按不可变事件日志） |
| 运维恢复 | 杀死 Worker、重启 DB、积压后重放 | 已确认数据 0 丢失、积压最终清空 |

> 若在 AMD 395 家庭主机上运行，还必须单独记录模型参数、量化、token/s、
> CPU/RAM/功耗及推理 P95。纯数据库延迟不能与含模型推理的 E2E 延迟混为一谈。

## 已存在可运行测试命令

```bash
python -m pip install -e '.[dev,postgres,api,temporal]'
docker compose up -d --build --wait
export MEMORY_TEST_DSN=postgresql://memory:local-dev-only@127.0.0.1:55432/memory
pytest -q -m integration tests/test_user_story_e2e.py tests/test_projection_deliveries.py tests/test_concrete_projections.py
pytest -q tests/test_sdk_contract.py tests/test_local_extractor.py
```

任何关键集成测试未运行或被 skip，都不计入完成。不得因为短期 CI 全绿就宣称
Webhook、中文本地模型质量、DB RLS、备份恢复或 100 Agent 负载验收完成。

## 发布裁决

只有 S01–S10 全部真实通过，且故障、质量、性能、安全 Gate 有可审计原始指标时，
才可以发布为“第三方可依赖的完整版本”。当前阶段仍是未达该门槛的候选实现。
