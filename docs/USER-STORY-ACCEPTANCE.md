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
| S02 中文理解 | 口语→本地模型→有主体/谓词/值/来源的候选，不虚构 | `test_local_extractor.py`, `scripts/evaluate_local_extractor.py` | 本地 Ollama 适配器与严格候选 Schema 已测；200 条人工裁定语料上的真实模型质量**未测**，不得标 Done |
| S03 原子写入 | 请求结束后新连接能读到事实、来源、时间和 outbox | `test_user_story_e2e.py`, `test_pg_runtime.py` | 真 PostgreSQL 测试 |
| S04 加工纠正 | 去重、否定、冲突、实体消歧、历史版本均正确 | `test_processing.py`, `test_processing_concurrency.py`, `test_user_story_e2e.py` | PostgreSQL 同键候选现以 advisory lock 原子去重/版本化，并保留多来源证据；真实模型复杂否定、指代及歧义质量**未完成** |
| S05 投影 | FTS、向量、AGE 独立任务，成功状态不能互相冒充 | `test_concrete_projections.py`, `test_projection_deliveries.py` | 真实数据库路径，质量另测 |
| S06 检索 | 正确当前记忆、证据、双时间，跨渠道排序可解释 | `test_user_story_e2e.py`, `test_hybrid.py`, `scripts/evaluate_retrieval.py` | SQL/FTS/vector/AGE 机制可测；200 条中文查询集上的 Recall@5、MRR@10 **未测** |
| S07 纠正 | 当前仅新事实；known_at=纠正前仍返回旧事实 | `test_user_story_e2e.py` | 真实数据库测试 |
| S08 事件 | 分页、游标重连、SSE；远端 Webhook 持久重投 | `test_webhooks_integration.py`, `test_webhook_api_integration.py`, `test_s08_real_tls_receiver.py`, `test_sdk_contract.py` | 订阅、签名、退避、死信、重放已实现；独立进程 HTTPS 接收端与篡改签名的 401 见 `docs/S08-REAL-TLS-ACCEPTANCE.md`；真实公网接收端、证书轮换与接收端幂等**未验收** |
| S09 故障恢复 | DB/Worker 故障后任务可重试、DLQ 重放、无静默丢数据 | `test_projection_deliveries.py`, `test_outbox_runtime.py`, `test_outbox_api_integration.py` | 投影与事务 Outbox 有界重试、死信、事务回滚和租户级 API 重放已有真实 PG 测试；真实进程杀死、数据库重启、积压恢复与备份恢复**未完成** |
| S10 隔离 | 不同租户不可读、写、引用、订阅他人资源 | `test_user_story_e2e.py`, `test_api_postgres_runtime.py` | API 层已测；DB RLS **未实现** |

## Provider 通道实测记录（2026-11-12，A100 单机）

模型通道从“只支持本地 Ollama”扩展为可配置 provider 后的实测证据，运行于真实
PostgreSQL + pgvector + AGE 实例，抽取与向量化均通过 OpenAI-compatible 端点完成
（端点为可控 mock，因此本节只证明**接线与契约正确**，不证明模型质量）。

| 检查项 | 命令 | 实测结果 |
|---|---|---|
| 能力自描述 | `GET /v1/capabilities` | `memory.extraction` → `text extractor provider: openai-compat`；`memory.embedding` → `server-side embedding model: openai-compat` |
| 免费文本抽取 | `POST /v1/memories/process`（仅 `text`，无 `candidates`） | `created=1`，`relations[0].relation=CREATED`，`embedding_failures=[]` |
| 精确去重 | 同句重放 | `duplicates=1`，不为同一事实新建 canonical 事件 |
| 矛盾替换 | 改成相反偏好后重放 | `relation=CONTRADICTION`，`replaced=[旧 memory_id]`；`/v1/search` 只返回新值 |
| 服务端向量化写入 | `POST /v1/memories` | `{"embedded": true}` |
| 查询自动向量化 | `POST /v1/search`（仅 `query`） | `channels_used=["vector"]`，命中带 `score` |
| 无模型降级 | 未配置 provider 的实例 | 文本路径 503，其余端点正常，`memory.embedding=false` |

仍未完成、不得标记 Done 的门槛：S02 的 200 条人工裁定中文语料上真实模型
F1 ≥0.90、S06 的 Recall@5 ≥0.90 / MRR@10 ≥0.85（见 Release Gate），以及 S10
DB RLS 之外的跨租户压测。provider 配置只解决“能不能接模型”，不替代“模型好不好”。

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
pytest -q tests/test_sdk_contract.py tests/test_local_extractor.py tests/test_benchmark_metrics.py tests/test_retrieval_metrics.py
# Real local-model evaluation: requires a 200+ case human-adjudicated JSONL corpus and installed Ollama model
MEMORY_LOCAL_OLLAMA_MODEL=<local-model> python scripts/evaluate_local_extractor.py --dataset <zh-corpus.jsonl>
# Real HTTP retrieval evaluation: requires 200+ judged queries against an isolated local service
MEMORY_API_KEY=<test-key> python scripts/evaluate_retrieval.py --dataset <retrieval-corpus.jsonl>
```

任何关键集成测试未运行或被 skip，都不计入完成。不得因为短期 CI 全绿就宣称
Webhook、中文本地模型质量、DB RLS、备份恢复或 100 Agent 负载验收完成。

## 发布裁决

只有 S01–S10 全部真实通过，且故障、质量、性能、安全 Gate 有可审计原始指标时，
才可以发布为“第三方可依赖的完整版本”。当前阶段仍是未达该门槛的候选实现。


## Additional S04/S05 domain-policy acceptance

- Predicate cardinality is tenant-scoped and defaults to `one`.
- `many` predicates retain distinct simultaneous facts; exact duplicates
  attach provenance without creating another canonical event.
- The model cannot select or upgrade cardinality. A mismatch with trusted
  database policy is rejected.
- Single-valued overlapping writes fail closed unless they explicitly replace
  the only overlapping active claim.
- Real PostgreSQL checks live in `tests/test_predicate_cardinality.py`.
- This domain-policy implementation does not replace the separate 200+ case
  Chinese extraction or retrieval-quality gates.
