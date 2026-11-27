# AMC 内观 2026-11-27：从三云检索功能到可独立验证的 Agent Work Continuity

> **结论不是完成认证。** 本文基于公开 main 的 User Story、核心源码、开放 Issue 和 Draft PR，以及 PR #71 的最新代码。所有状态按独立可复核的证据分级：SOURCE（代码已存在）/ CI（对应 SHA 的自动化）/ LIVE（真实厂商凭据或独立外部执行）/ INDEPENDENT（非作者外部复现）/ ADOPTED（非作者代码形成真实依赖）。

## 1. 我们到底是什么，不是什么

AMC 是 **Agent Work Continuity** 的候选基础设施：在 Agent A 离开后，Agent B 能在权威事实版本、双时态、来源与未决外部 Effect 上继续工作，并准确声明何处证据不足。

不是新的大模型、不是新的向量数据库、不是一个新的 Temporal，也不是“记忆检索分数高就是成功”。PG canonical 是唯一事实权威；FTS、pgvector、AGE、AWS、Alibaba、Azure 全是派生候选索引，不能自行声明当前事实；Temporal 管执行进程，不能凭空验证物理效果。

**已存在的相邻技术（不可声明原创）**：LangGraph checkpoint、OpenCheckpoint、Agent Memory Continuity Protocol、CONTINUUM 等已有跨 Agent state transfer 或 verifiable recovery 的工作。AMC 应验证一个更具体的差异：**source revision + snapshot freshness + external effect intent + unknown reconciliation + terminal outcome 的可移植、可独立判断合同**。参考：
- https://opencheckpoint.org/prior-art/
- https://github.com/nunchi-ai/amcp
- https://github.com/Under-Research-Labs/CONTINUUM
- https://aws.amazon.com/about-aws/whats-new/2026/11/s3-vectors-hybrid-search/ （S3 自带 hybrid，不能再当作 AMC 特有卖点）

## 2. 当前仓库事实（证据级别）

| 领域 | 已有 | 缺口与不能宣称 | 来源 |
|---|---|---|---|
| Canonical | PG 事实、显式修订、双时态、Outbox；真实 PG 测试 | 真实备份恢复、跨主机灾备不等于已有 | docs/USER-STORY-ACCEPTANCE.md |
| Processing | 去重、来源、Cardinality、否定候选 | 200+ 人工中文多轮真实模型 F1 还未达正式验收 | #65 #66 |
| Search | SQL/FTS/pgvector/AGE，多路 RRF，canonical tenant/times recheck | 真实 Recall@5/MRR@10 数据、成本/SLO 缺口；本地 simple FTS 不是中文分词 | hybrid.py |
| Identity | RLS principal 试验、OIDC/浏览器路径 | Worker/Temporal 的全局跨租户隔离与全部资源 RBAC 未验收 | #50 #10 |
| Handoff | handoff contract、实验验证、SIGKILL fixtures | 身份绑定、模型真实决策、任务终态、第三方执行证据仍未完整 | #62 #64 #68 |
| Source trust | Verified Learning, trust registry | #67 中核查到 source 复核后至注册 effect intent 的 TOCTOU | #63 #67 |
| External adoption | #69 独立标准库一致性检测器，12 vectors 可供复现 | #70 仍无已确认非作者独立实现 / 依赖 | #69 #70 |
| Packaging | public main cold install runner 已进入 main, CI | #57 仍未有第三方人工冷装后完整回放 | #57 |
| Multi-cloud | PR #71 AWS/Alibaba/Azure candidate adapters; opt-in HybridSearch | 没有 remote durable outbox、cloud live receipt、server activation | #71 |

**PR 风险**：#62/#63/#64/#65/#66 为多个长期 Draft 的重叠产品路径；#69 已尝试从大 PR 抽出 5 个文件；#71 的 3-cloud adapter 不应继续扩为另一个巨型平台。必须先按依赖边界收敛、确定单一当前 SHA 作为候选，防止不同 PR 的绿灯被当作同一版本的成功。

## 3. 逐项重做现有 US-01 的 DoD

- **S01 接入**：现有 API/SDK 证据属于代码/CI。终结需要 #57 独立开发者从 public main 无辅导完成。
- **S02 中文理解**：真实多轮纠正/否定/指代集 200+ 人工冻结判例；同 Qwen 端点与同权重指纹复测，禁止 rule/mock 分数冒充。
- **S03 原子写入**：保留同事务 canonical/outbox 逻辑；不能让云端 PUT 阻塞 canonical commit。
- **S04 加工修订**：反向测试 stale RETRACT、新旧版本并发、未知主体；验收最终事实与历史，而非工具执行次数。
- **S05 投影**：云端增量通过独立 lease/ack、retry/DLQ、remote readback、correction/deletion tombstone；故障注入 crash-after-put-before-ack 与 ACK 丢失；当前 #71 **未完成**。
- **S06 检索**：固定真实中文语料，Ablation（PG alone / 3-cloud shadow / active）比 Recall、MRR、时态违规、延迟、有效召回单价；跨云同一向量**只能算一个 semantic evidence family**，避免镜像多投票。
- **S07 修订**：云端陈旧候选不可违反 valid_at/known_at/supersession；旧索引删除不能擦除可审计 canonical history。
- **S08 事件**：外部 TLS/Webhook 证据与 receiver 幂等；不可只计 SDK 回执。
- **S09 恢复**：进程 SIGKILL、DB reboot、DLQ replay、备份恢复、真实 Provider UNKNOWN；独立记录 external-effect count。
- **S10 隔离**：身份来源必须来自认证 principal，而非模型、metadata filter 或自带 tenant 字符串。数据库 RLS 之外还需 Worker/Temporal/对象存储授权与跨租户敌意测试。

另立 **US-C02** 为产品闭环：A(已认证) -> A 消失 -> B(独立身份) -> 有效版本 source -> durable fenced effect -> 独立 provider readback -> canonical terminal task receipt -> C 第三方读审计。必须同一 task/effect/receipt 关联。#68 的 12 个负例是规格，不是通过证据。

## 4. 本轮真实增量：PR #71 不能只叫云兼容

实际发现并修正的两类 correctness 问题：
1. **无效 UUID 候选**：云返回恶意/脏 memory_id 可以进入 canonical SQL 的 `ANY(%s::uuid[])` 导致整次检索失败。现在仅对云端候选先做严格 canonical UUID 校验，并标记该云降级；本地旧检索语义不变。
2. **镜像重复投票**：三个云和本地 pgvector 同时命中同一个 Memory 并不是四份独立事实证据。RRF 只计算一次最佳 vector-family 贡献，同时保留所有命中通道作为诊断标签；同样的语义候选不因镜像数量上升排序。

两者有离线回归测试。需要最新 HEAD 的 Pytest/PG/产品 E2E 结果；尚无真实云端收据。未完成不能标为三云产品闭环。

## 5. 下一次 Action 由事实驱动，而不是功能驱动

**阻塞一（产品安全）**：#67 TOCTOU source 撤销与 effect admission 必须在同一信任边界重新检查；`plan` 不可自动升级为 `execute`；有确定性 barrier 测试、独立 Provider POST=0 的负例才算完成。

**阻塞二（产品可用）**：#57 独立 public-main 冷装。若 1 位非作者不能按 README 运行，继续添加新云功能的边际收益应下调。

**阻塞三（知识正确性）**：S02/S06 多轮真实中文 Benchmark，冻结数据、模型版本与两臂差异，保存失败病例。没有质量差值就不声称更好的记忆。

**阻塞四（可移植 cloud index）**：三云按相同 384/512d Embedding/TTL/correction/deletion 收据测试，先 shadow，再灰度；每一个外部写入独立回执，绝不假设跨云事务。

**阻塞五（外部采用）**：优先以 #69 的纯标准库、可独立重实现的 12 frozen failure vectors 做最小公开接口；到 #70 收到非作者报告/独立实现引用，才可以从“内部成果”升级为“外部依赖”。

## 6. Pareto 纪律：停止/转向条件

1. 任何一个新增组件若不能为端到端真实 User Story 增添可独立观察的结果，不进入核心运行时。
2. 每个功能必须有 **反例/故障注入**，而不是只有 happy path；报告 PASS / FAIL / UNKNOWN。
3. 每个新 cloud backend 先影子检索、账单采样，再决定真实流量：不能只凭价目表迁移。
4. 任何“外部影响力”只由非作者 issue、可执行引用、依赖版本、独立实验或付费集成来证明；stars 与 AI 自生成推文不能代替采用。
5. 若下一个冻结 Benchmark 显示没有明显 future-task success 提升，减少抽取/记忆策略研发，转向经过真实用例证实更有价值的 effect admission conformance。
6. 若三云迁移只增加成本/延迟而无真实灾备或复用用户，则只保留一个默认索引和可替换适配器，不默认镜像。

## 7. 对外身份凭证与第三方入口（不是宣传标语）

最小可移植资产：`amc.continuity-receipt.v1`、12 个 failure vectors、一个可复现 CLI、独立实现指南以及每个 PASS **不证明什么**。其次才是云投影互操作性、现场失败收据和每 1000 次有效检索的可核对成本。

对外要让第三方在没有 AMC 运行时和没有作者协助的情况下，仅凭冻结 JSON，给出一致的 PASS/FAIL/UNKNOWN；否则对方使用的是我们的软件，不是依赖我们的公开协议。

6–12 个月衡量：非作者复现次数、引用具体版本、issue bug 最小复现、外部 adapter 的持续 CI 依赖、upstream 讨论或合并。5–10 年的价值更取决于可核对的外部效果标准，而不是任何特定 LLM、数据库或云 API。

## 8. 内观裁决

**保留**：PG canonical/bitemporal/outbox；检索结果权威回读；外部效果接收与 UNKNOWN 语义；轻量 conformance 资产。

**限制**：云索引、OpenTelemetry、Temporal、AGE、RAG query planner 只在明确故事需要时纳入。

**禁止**：把云检索当作 fact truth；把 receipt existence 当作 physical causality；把 CI 全绿当独立采用；把多云镜像重复命中当多份事实；把不同 SHA 的测试拼成一个虚假 release。

下一次闭环以真实失败事件、独立复现与发布门槛选择方向；高质量负信息比新概念数量更有价值。
