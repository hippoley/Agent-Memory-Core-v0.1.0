# Agent Memory Core · Swagger / OpenAPI 第三方接入手册

> HTTP Contract 1.0.0。当前是可运行、可测试的研究型服务原型，不是已经完成安全加固的多租户 SaaS。文档按当前代码更新；所有可选能力应先查询运行时能力，不要把 SDK 原型误称为生产能力。

## 一、当前已经暴露的能力

本服务采用 PostgreSQL 作为权威事实库，pgvector 与 Apache AGE 作为可重建检索索引，对象字节存放在本地或 S3 兼容存储，时序观测与事件日志保存在 PostgreSQL。

当前 HTTP 路由：

| 方法 | 路径 | 能力 |
|---|---|---|
| GET | `/v1/health` | 进程存活 |
| GET | `/v1/ready` | PostgreSQL 就绪 |
| GET | `/v1/capabilities` | 能力发现 |
| POST | `/v1/memories` | 写入事实 |
| GET | `/v1/memories/{subject_id}` | 双时间事实查询 |
| POST | `/v1/memories/process` | 结构化候选记忆加工 |
| GET | `/v1/memories/{memory_id}/sources` | 来源追溯 |
| PUT | `/v1/memories/{memory_id}/embedding` | 写入调用方生成的向量 |
| POST | `/v1/search` | SQL + Vector + Graph 混合检索 |
| PUT | `/v1/objects` | 上传证据对象 |
| GET | `/v1/objects/{digest}` | 下载并校验对象 |
| POST | `/v1/observations` | 写入设备时序观测 |
| GET | `/v1/observations/{device_id}` | 查询观测 |
| GET | `/v1/observations/{device_id}/aggregate` | 时间范围聚合 |
| POST | `/v1/entities` | 创建/更新实体 |
| GET | `/v1/entities/resolve` | 实体别名解析 |
| POST | `/v1/edges` | 创建关系并投影 AGE |
| GET | `/v1/events` | 事件游标回放 |
| GET | `/v1/events/stream` | SSE 实时事件流 |
| POST | `/v1/workflows/memory-verifications` | 启动记忆核验工作流 |
| GET | `/v1/workflows/{workflow_id}` | 查询工作流状态 |
| POST | `/v1/workflows/{workflow_id}/cancel` | 请求取消工作流 |

Temporal 目前提供一个有限的记忆验证 Workflow REST 生命周期（启动、状态查询、取消请求）；Worker 仍需独立部署，不是通用工作流编排平台。

## 二、安装与启动

要求：Docker Compose、Python 3.10+。

```bash
git clone https://github.com/hippoley/Agent-Memory-Core-v0.1.0.git
cd Agent-Memory-Core-v0.1.0
docker compose up -d --build --wait
python -m pip install -e '.[dev,postgres,api,s3]'

export MEMORY_DSN='postgresql://memory:local-dev-only@127.0.0.1:55432/memory'
python -c "import os; from agent_memory_core.postgres import PostgresMemory; db=PostgresMemory(os.environ['MEMORY_DSN']); db.migrate(); db.close()"

export MEMORY_API_KEY='replace-with-a-random-secret-at-least-24-characters'
export MEMORY_TENANT_ID='demo-home'
export MEMORY_ENABLE_VECTOR='1'
export MEMORY_ENABLE_AGE='1'
export MEMORY_OBJECT_BACKEND='local'
uvicorn agent_memory_core.server:app --host 127.0.0.1 --port 8765
```

Windows PowerShell 请使用 `$env:NAME='value'` 设置环境变量。

打开：

- Swagger UI： http://127.0.0.1:8765/docs
- ReDoc： http://127.0.0.1:8765/redoc
- OpenAPI JSON： http://127.0.0.1:8765/openapi.json

在 Swagger 右上角 Authorize 中输入 API Key，不要加 `Bearer ` 前缀。

## 三、认证和租户边界

除 `GET /v1/health` 外，所有接口都需要：

```http
Authorization: Bearer <MEMORY_API_KEY>
```

每个 API 进程绑定一个 `MEMORY_TENANT_ID`。请求体不能指定租户。当前不是完整的多租户身份系统；数据库 RLS、密钥轮换/撤销、TLS、限流和审计仍是生产部署前必须完成的工作。对象下载增加了 PostgreSQL 租户所有权登记；多租户部署仍应使用独立对象存储命名空间。

## 四、最常用的操作

### 1. 写入记忆

```bash
curl -X POST http://127.0.0.1:8765/v1/memories \
  -H "Authorization: Bearer $MEMORY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "subject_id":"bedroom-1",
    "predicate":"night_preference",
    "value":{"noise":"quiet","temperature_c":23},
    "source_id":"conversation:session-001",
    "valid_from":"2026-11-09T00:00:00+08:00"
  }'
```

返回新建 claim UUID。事实必须带来源标识。用 `replaces_id` 显式更正同一主体和谓词的旧事实。

### 2. 双时间读取

```bash
curl -G http://127.0.0.1:8765/v1/memories/bedroom-1 \
  -H "Authorization: Bearer $MEMORY_API_KEY" \
  --data-urlencode 'predicate=night_preference' \
  --data-urlencode 'valid_at=2026-11-01T20:00:00+08:00' \
  --data-urlencode 'known_at=2026-11-05T12:00:00+08:00'
```

- `valid_at`：现实世界中的事实有效时间。
- `known_at`：系统在什么时间已经知道该事实。

### 3. 记忆加工

`POST /v1/memories/process` 接收调用方本地模型或规则引擎生成的结构化候选，不会假装自动理解任意原始文本。

```json
{
  "text":"我希望卧室晚上保持安静。",
  "source_id":"conversation:session-002",
  "confidence_threshold":0.7,
  "candidates":[{
    "subject_id":"bedroom-1",
    "predicate":"night_preference",
    "value":{"noise":"quiet"},
    "confidence":0.96
  }]
}
```

确定性加工包括字段验证、置信度过滤、精确事实去重、来源链接、实体别名解析和事实版本替换。尚未完成的能力包括模糊/跨语言实体消歧、矛盾分类、自动摘要整合、遗忘策略和中文评测集。

### 4. 上传和下载证据对象

```bash
curl -X PUT http://127.0.0.1:8765/v1/objects \
  -H "Authorization: Bearer $MEMORY_API_KEY" \
  -H "Content-Type: application/octet-stream" \
  --data-binary @floorplan.png
```

返回 SHA-256，写入记忆时可将它放入 `object_sha256`。下载：

```bash
curl http://127.0.0.1:8765/v1/objects/<SHA256> \
  -H "Authorization: Bearer $MEMORY_API_KEY" --output evidence.bin
```

上传限制为 10 MiB。默认本地对象存储；S3 兼容适配器配置：

```bash
export MEMORY_OBJECT_BACKEND=s3
export MEMORY_S3_BUCKET=your-private-bucket
export MEMORY_S3_REGION=us-east-1
export MEMORY_S3_ENDPOINT_URL=https://s3-compatible-endpoint
export MEMORY_S3_ACCESS_KEY_ID=...
export MEMORY_S3_SECRET_ACCESS_KEY=...
```

安装 `.[s3]`。当前 S3 适配器已通过模拟客户端的内容寻址和哈希校验测试，尚未声称通过真实 AWS/MinIO 部署验收。

### 5. 向量写入

```http
PUT /v1/memories/<MEMORY_UUID>/embedding
Authorization: Bearer <MEMORY_API_KEY>
Content-Type: application/json
```

```json
{
  "model_id":"BAAI/bge-small-zh-v1.5",
  "embedding":[0.01,0.02,0.03]
}
```

上面仅展示请求结构。真实向量必须是模型生成的完整 512 维数组；当前支持 384D/512D profile。服务本身不托管 Embedding 模型。

### 6. 实体和关系

先通过 `POST /v1/entities` 创建实体，再通过 `POST /v1/edges` 创建关系。PostgreSQL 保存权威关系，启用 AGE 后由 API 投影到图索引。

### 7. 时序观测

```json
{
  "device_id":"window-1",
  "source_event_id":"gateway-event-001",
  "observed_at":"2026-11-09T10:00:00+08:00",
  "property":"angle",
  "value":17.5,
  "unit":"degree",
  "source_id":"window-gateway"
}
```

发送到 `POST /v1/observations`。重复的相同事件会幂等确认；同一事件 ID 携带不同数据会被拒绝。使用 GET 查询时间范围，或使用 `/aggregate` 获取 count/min/max/avg。当前是 PostgreSQL 时序观测子系统，不是完整专业 TSDB；自动分区、保留策略、降采样和高吞吐压测仍待完成。

### 8. 实时事件

轮询回放：

```bash
curl 'http://127.0.0.1:8765/v1/events?limit=100' \
  -H "Authorization: Bearer $MEMORY_API_KEY"
```

使用响应中的 `next_cursor` 作为下一页 `after`。SSE：

```bash
curl -N 'http://127.0.0.1:8765/v1/events/stream?max_seconds=30' \
  -H "Authorization: Bearer $MEMORY_API_KEY"
```

连接最长 30 秒；断线后使用最后收到的 `id:` 作为 `Last-Event-ID` 或 `after`。当前实现是基于持久事件日志的轮询式 SSE，不是消息代理；多副本扇出、背压和吞吐 SLA 尚未验证。浏览器原生 EventSource 不能设置自定义 Authorization Header，请使用 fetch streaming 或可信网关，不要把 API Key 放入 URL。

## 五、Temporal 工作流接入

当前对外开放的是一个经过真实 Temporal SDK + PostgreSQL 集成测试的窄工作流：核验某条记忆是否存在、是否仍有效，并写入可幂等重放的核验回执。

API 与 Worker 是两个独立进程。先配置 API：

```bash
export MEMORY_TEMPORAL_ADDRESS=localhost:7233
export MEMORY_TEMPORAL_NAMESPACE=default
export MEMORY_TEMPORAL_TASK_QUEUE=agent-memory-core
```

Worker 进程需要配置数据库连接，并启动：

```bash
python -m pip install -e '.[temporal,postgres]'
export MEMORY_DSN='postgresql://...'
python -m agent_memory_core.temporal_worker
```

启动工作流：

```bash
curl -X POST http://127.0.0.1:8765/v1/workflows/memory-verifications \
  -H "Authorization: Bearer $MEMORY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"memory_id":"<existing-claim-uuid>","workflow_id":"verify-claim-001"}'
```

服务端会将 workflow ID 加上租户前缀。后续使用返回的完整 ID 查询：

```bash
curl http://127.0.0.1:8765/v1/workflows/<tenant-prefixed-workflow-id> \
  -H "Authorization: Bearer $MEMORY_API_KEY"
```

请求取消：

```bash
curl -X POST http://127.0.0.1:8765/v1/workflows/<tenant-prefixed-workflow-id>/cancel \
  -H "Authorization: Bearer $MEMORY_API_KEY"
```

跨租户查询/取消返回 404。Temporal 服务不可用返回 503；不能将基础设施故障误报为工作流不存在。取消是向 Temporal 发出请求，不代表已经回滚任何外部副作用。

当前尚未提供通用 Workflow 定义、Signal/Update、任意任务编排、Worker 自动扩缩容或生产集群治理。

## 六、能力边界

| 能力 | 当前状态 |
|---|---|
| PostgreSQL 事实与双时间记忆 | 已实现，有真实数据库集成测试 |
| pgvector 384D/512D | 已实现，有真实数据库集成测试 |
| Apache AGE 投影与遍历 | 已实现，有真实数据库集成测试 |
| SQL/Vector/Graph 混合检索 | 已实现，有真实数据库集成测试 |
| Time Series 写入/查询/基础聚合 | 已实现，有真实数据库集成测试 |
| 本地对象存储 | 已实现，有完整性测试 |
| S3 兼容适配器 | 已实现，模拟客户端测试；真实云/MinIO 部署待验收 |
| 事件回放和 SSE | 已实现，API 合约测试；生产级负载待验收 |
| Transactional Outbox | 已实现内部消费者与重试，真实 PostgreSQL 测试 |
| Mem0 式确定性加工第一阶段 | 已实现，单元测试与真实 PostgreSQL API 端到端测试 |
| 自动 LLM 抽取/摘要整合/遗忘 | 未完成 |
| Temporal SDK Workflow | 已有记忆核验 REST start/status/cancel 与独立 Worker；真实 Temporal + PostgreSQL CI |
| 通用 Temporal Workflow 定义/Signal/Update | 未完成 |
| 多租户生产安全 | 未完成 |

## 七、CI 与验收

本地运行：

```bash
python -m pip install -e '.[dev,postgres,api,temporal,s3]'
pytest -q
docker compose up -d --build --wait
export MEMORY_TEST_DSN='postgresql://memory:local-dev-only@127.0.0.1:55432/memory'
pytest -q -m integration
```

检查 GitHub Actions 时，必须确认全部相关工作流针对**同一个 commit SHA**通过。不能将不同提交上的绿灯拼成一个发布验收结果。

## 八、生产部署前仍需完成

数据库 RLS、密钥轮换/撤销、TLS、速率限制、审计、对象保留与删除、备份恢复、Outbox 死信与投影任务拆分、Temporal Worker 运行治理、记忆加工并发幂等、中文检索/抽取评测，以及 p50/p95 和故障恢复测试。


## 九、Temporal 记忆验证工作流

API 与 Worker 分开运行。配置 API：

```bash
export MEMORY_TEMPORAL_ADDRESS=localhost:7233
export MEMORY_TEMPORAL_NAMESPACE=default
export MEMORY_TEMPORAL_TASK_QUEUE=agent-memory-core
```

另起一个 Worker 进程：

```bash
python -m pip install -e '.[temporal,postgres]'
export MEMORY_DSN='postgresql://...'
python -m agent_memory_core.temporal_worker
```

启动记忆验证：

```bash
curl -X POST http://127.0.0.1:8765/v1/workflows/memory-verifications \
  -H "Authorization: Bearer $MEMORY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"memory_id":"<existing-claim-uuid>","workflow_id":"verify-claim-001"}'
```

响应中的 workflow_id 已由服务端加上租户前缀。后续使用完整 ID 调用 `GET /v1/workflows/{workflow_id}` 查询，或调用 `POST /v1/workflows/{workflow_id}/cancel` 请求取消。跨租户 workflow ID 会返回 404。

目前只注册了 VerifyMemoryWorkflow。尚未实现任意 Workflow 注册、Signal/Update、通用长期任务调度、Worker 自动扩缩容和生产 Temporal 集群运维。取消工作流也不等于回滚已发生的外部副作用。
