# Agent Memory Core · 中文导览

> **Agents change their minds. Memory should keep the evidence.**
>
> Agent 会改口；记忆不仅要跟上变化，还要留下证据。

**Agent Memory Core** 是一个面向任意行业 Agent 的开源、Local-first、模型无关、证据驱动的记忆运行时研究原型。

不是另一个聊天记录列表，也不是在向量数据库外面包一个 `remember()`。我们的核心问题是：**当 Agent 记住了旧事实，也接收了新纠正，谁来保证下一次行动依据的是有效、可追溯的版本？**

## 视觉系统：Bsph Blue

现在 README 的三张 SVG 配图采用一致的视觉规范：**白底、宝蓝 `#125CFF`、深海军蓝字体、细网格、证据标注与版本线索**。这与实验性 **Memory Observatory** 的视觉方向一致；它不是独立的新记忆数据库。

- [首屏记忆演化图](assets/hero-belief-lifecycle.svg)：用月结 → 季结这个**示意例子**表达“更新事实，但保留历史证据”。
- [运行时架构图](assets/architecture.svg)：PostgreSQL 是唯一权威事实源，pgvector 和 AGE 是检索视图，Temporal 是可选持久执行适配器。
- [记忆生命周期图](assets/memory-lifecycle.svg)：区分写入、派生投影与带规范事实复核的召回。
- [Memory Observatory](../src/agent_memory_core/resources/dashboard/observatory.html)：现有前端包含时间筛选、来源检查、合成 Demo 与 Live API；**PR #56 的写入回读核验和一键 Demo 只有进入 `main` 后才算正式发布**，不能把 PR 环境测试当作公开版本能力。

这里的蓝色装饰线并不表示 AGE 图边、Embedding 距离或真实验证结果；新版 Observatory 的 Live 读取有限页的认证事实，不代表整个租户数据。前端截图需要真实浏览器验收之后才能放入首页，不能用视觉效果代替测量。

参见 [完整配色与视觉语义规范](DESIGN-SYSTEM.md)。

## 20 秒理解一个真实问题

某供应商原先按月结算，合同后来改成按季度结算。

普通向量检索可能同时返回“月结”和“季结”；Agent 选择哪一个，取决于相似度、Prompt 和碰运气。我们要让系统能够区分：

- 当前应该生效的是哪个版本？
- 旧结论来自什么证据？何时被替换？
- 如果查询上个月，系统当时知道什么？
- 另一个 Agent 是否可以在不复制整个聊天记录的前提下安全接续？

**核心原则：模型提出事实候选，Embedding 和 AGE 帮助找到相关证据，PostgreSQL 决定可用的当前状态；Temporal 负责需要持久执行的验证与恢复。**

## 新版前端：Memory Observatory

**Bsph Blue × 计算美学**。新版 Observatory 不是第二个记忆引擎，而是让访客看到已加载记忆的主题分布、记录时间、来源和事件。

| 体验入口 | 可操作内容 | 数据边界 |
| --- | --- | --- |
| **[Memory Observatory](../src/agent_memory_core/resources/dashboard/observatory.html)** · 新预览 | 蓝色细线束、领域筛选、时间滑杆、来源检查、文本筛选、快照导出、五步演示 | Demo 为明确标示的合成数据；Live 使用经过认证的同源 API |
| [经典 Memory Console](../src/agent_memory_core/resources/dashboard/index.html) | 原有记忆台账与管理交互 | 真实 API |
| [Memory Atlas](demo/index.html) | 静态产品故事与架构导览 | 浏览器内合成数据 |

**注意：** 蓝色曲线不代表 AGE 真实关系边、Embedding 距离、因果关系或置信度；时间滑杆仅过滤已加载的记录时间，搜索仅匹配浏览器内的文本，不能替代 PostgreSQL 的双时间查询与混合检索。Live 首次最多读取 500 条事实和 100 条事件，不等于全部数据。

```bash
python scripts/launch_local.py up --profile lite
# main 的本地 Observatory：http://127.0.0.1:8765/dashboard/observatory.html
```

Demo 无需密钥；Live 只应连接受保护的同源 API。**第三方只应从 `main` 安装。完整 P0-1 外部验收必须以检出的 `main` 确实包含 `scripts/demo_local.py` 和完整写入／修订／软遗忘验证为前提；若缺失则记录 BLOCKED，不能指导测试者切换功能分支绕过发布门槛。** [产品设计与隐私说明](memory-observatory-preview.md)。

## 为什么有 Apache AGE 和 Temporal？

我们要做“轻量记忆框架”，但**绝不会因为轻量化而放弃图关系和可靠的长任务执行**。

| 组件 | 工程角色 | 是否所有请求必需 |
| --- | --- | --- |
| OpenAI-compatible LLM / 本地 Ollama | 跨行业事实抽取、指代与修订候选 | 可选，由调用者配置 |
| Embedding + pgvector | 跨措辞语义候选发现 | 可选，需要真实模型 |
| Apache AGE | 实体关系与图邻域候选，服务多跳召回 | 可选图通道；重要扩展能力 |
| PostgreSQL | 权威事实、双时间版本、来源、事务及 Outbox | 当前所有部署档位必需 |
| Temporal | 长周期核验、失败恢复、Activity/Workflow | 可选执行层；重要扩展能力 |

AGE 不是第二个事实权威，Temporal 也不是第二个记忆数据库。二者是**一等公民的扩展路径**，并非日常每条 `remember()` 都要经过的服务。

现状边界也要讲清：AGE 在特定 RLS 模式下主动禁用；仓库的 Temporal 已有验证工作流与 PostgreSQL 收据测试，却不是完整的批处理/人工审核引擎。见 [AGE + Temporal 发展路线](AGE-TEMPORAL-STRATEGY.md)。

## 先跑起来

**正式安装入口只认 `main`。** PR 和功能分支只用来内部开发；如果文档要求的文件在 `main` 缺失，属于发布阻断，不能让第三方临时切换分支。`main` 按下面的命令运行基础服务；完整 Demo 是否已发布以本地 `main` 检出后文件是否存在为准，而非 PR 的 CI 状态。

前提：Git、Python 3.10+，Docker Compose v2。

```bash
git clone --branch main --single-branch https://github.com/hippoley/Agent-Memory-Core-v0.1.0.git
cd Agent-Memory-Core-v0.1.0
git branch --show-current  # 必须显示 main
python scripts/launch_local.py up --profile lite
```

- 控制台：<http://127.0.0.1:8765/dashboard/>
- Swagger：<http://127.0.0.1:8765/docs/swagger>
- OpenAPI：<http://127.0.0.1:8765/openapi.json>

命令会创建仅限本地的开发 API 与随机密钥。**不要把开发数据库和控制台直接暴露到公网。** 当前 Lite 依然使用 PostgreSQL 混合扩展镜像，不是 SQLite 单进程版；Standard/Full 可以启用向量、图查询开关，但不自动启动模型服务或 Temporal 集群。

## 四个真正有价值的机制

**01 · Belief Revision — 保留历史，改变当前事实**

旧事实不能简单被新的文本覆盖。模型的撤回建议必须引用具体旧记忆 ID 和预期旧值，数据库核对后才能执行。完整的 `RETRACT + UPSERT` 多操作原子性仍属于下一道验收关卡。

**02 · Recall with Receipts — 不让相似度充当真理**

SQL/FTS、Embedding/pgvector、AGE 可以共同发现候选，但最后必须回到权威 PostgreSQL 检查租户、时间和状态。来源 ID 能追溯依据，但不能证明原始来源一定真实。

**03 · Bitemporal Memory — 同时回答“何时有效”和“何时得知”**

使用 `valid_at` 与 `known_at` 区分现实世界生效时间和系统知识时间。未来的纠正不能篡改过去的认知历史。

**04 · Durable Verification — 记忆校验可恢复**

当证据核验、批量索引、人工批准或定期复验需要在多个进程与时间段中继续，就使用 Temporal。现有实现包括 `VerifyMemoryWorkflow` 和幂等核验收据；通用端到端工作流仍需扩展。

## 现在已经做到哪里？

| 已有代码和测试 | 仍需第三方验收 |
| --- | --- |
| 通用 OpenAI-compatible/Ollama 模型适配与记忆候选契约 | 不同真实模型的跨行业理解正确率 |
| 结构化修订、双时间历史、来源和 Outbox | 完整多操作原子修订、生产级审计 |
| PostgreSQL / pgvector / AGE 混合检索与集成测试 | AGE/RLS 安全一致性、真实多跳质量 |
| Temporal 验证 Workflows 与 Activity 测试 | 批处理/审批/失败恢复的产品级流程 |
| SDK、REST、Swagger、本地交互页 | 公网多租户生产部署验收 |

原有 200 条中文规则测试的抽取 F1 94.34% **不能**被写成全链路记忆准确率；30 条词表外精确覆盖为零，说明规则路线不是最终的跨行业解决方案。

## 从哪里进入项目？

| 你想了解什么 | 入口 |
| --- | --- |
| 20 秒理解项目价值 | [README 首页](../README.md) |
| 想直接看操作过程 | [交互式 Memory Atlas](demo/index.html) |
| 了解全部真实边界 | [架构审计](ARCHITECTURE-REVIEW.md) |
| 看证据驱动修订链路 | [Evidence-Grounded Revision](EVIDENCE-GROUNDED-REVISION.md) |
| 看 AGE 与 Temporal 战略 | [专项路线与验收](AGE-TEMPORAL-STRATEGY.md) |
| 看真实接口 | [API Reference](API-REFERENCE.md) |
| 看哪些功能还没闭环 | [User Story 验收](USER-STORY-ACCEPTANCE.md) |

## 我们希望社区贡献什么？

不要求开发者写一篇宏大的架构提案。一个可以复现的错误就很有价值：旧事实误命中、跨租户图泄漏、Temporal 重试产生重复事件、模型撤回错误的旧记忆、相同数据集上的真实性能差异。

**真正的定位：让任何 Agent 可以从对话走向长期行动，同时有能力解释、纠正和恢复自己的记忆。**

Apache-2.0 · Research prototype · 欢迎以可复现 User Story、测试与真实 benchmark 参与建设。
