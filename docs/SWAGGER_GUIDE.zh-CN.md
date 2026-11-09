# Agent Memory Core · Swagger / OpenAPI 第三方接入手册

> 版本：HTTP Contract 1.0.0；实现仍属本地研究原型。文档只描述**已经实现**的 HTTP 接口。PostgreSQL RLS、TLS、限流、审计、数据删除、长期运行 SLO 尚未完成。**不要把开发服务直接暴露在公网，不要用真实敏感个人数据作公开测试。**

## 一、你到底获得了什么？

一个本地 REST 网关，背后是**唯一权威事实库 PostgreSQL**。pgvector/Apache AGE 是该实例内部的检索索引；Temporal 是**单独运行且可选**的可靠执行服务；ObjectStore 默认是 SHA-256 本地文件存储。

**HTTP 已实现：** 健康、数据库就绪、能力发现、事实写入/更正、按主体双时间读取、可选混合检索。

**仅 Python 模块已实现（目前没有独立 HTTP 路由）：** 关系创建/退役、AGE 投影/多跳、向量写入、设备时序观测、SHA-256 对象读写、Outbox 消费、Temporal 验证 Workflow。

**尚未实现：** 自动文档解析建知识库、中文 LLM 事实提取、自动向量生成、对象上传/下载 HTTP、公开图查询 HTTP、Workflow 启停 HTTP、SSE/WebSocket 实时订阅、生产级多租户隔离、真实外部用户认证。

因此，这个版本的「知识库制作」指**从调用方提供的结构化事实形成可追溯知识条目**，不是把 PDF 拖进去就自动完成解析/切块/嵌入的完整 RAG SaaS。

## 二、安装（本地单机）

仓库： https://github.com/hippoley/Agent-Memory-Core-v0.1.0

要求：Python 3.11+、Docker Compose、可用的本地端口 55432 与 8765。此处数据库凭据只是开发示例。

~~~bash
git clone https://github.com/hippoley/Agent-Memory-Core-v0.1.0.git
cd Agent-Memory-Core-v0.1.0
docker compose up -d --build --wait
python -m pip install -e '.[dev,postgres,api]'
~~~

**第一次启动必须执行数据库迁移（API 不会自动建表）：**

~~~bash
export MEMORY_DSN='postgresql://memory:local-dev-only@127.0.0.1:55432/memory'
python - <<'PY'
import os
from agent_memory_core.postgres import PostgresMemory
db=PostgresMemory(os.environ["MEMORY_DSN"])
try:
    db.migrate()
    print("Migration OK")
finally:
    db.close()
PY
~~~

设置服务端租户和 Bearer 凭据：

~~~bash
export MEMORY_TENANT_ID='demo-home'
export MEMORY_API_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
# 开始时不开启可选图投影；要启用时设 MEMORY_ENABLE_AGE=1
export MEMORY_ENABLE_AGE=0
# 只有确认调用方能提供模型匹配的 Embedding 且已存储向量才设 1
export MEMORY_ENABLE_VECTOR=0
uvicorn agent_memory_core.server:app --host 127.0.0.1 --port 8765
~~~

在另一个终端复用相同的 \`MEMORY_API_KEY\`。

- Swagger UI： http://127.0.0.1:8765/docs
- ReDoc： http://127.0.0.1:8765/redoc
- 运行时 OpenAPI JSON： http://127.0.0.1:8765/openapi.json
- 仓库内的 OpenAPI 规范： \`docs/openapi.json\`（由接口契约对照测试检查）
- 生成当前实现对应的规范文件： \`python scripts/export_openapi.py docs/openapi.generated.json\`

Swagger UI 中点击 **Authorize**，粘贴 \`MEMORY_API_KEY\`，可直接执行所有鉴权端点。不要把真实 API key 提交到 GitHub。

## 三、HTTP 接口逐个使用

认证方式：除 \`GET /v1/health\` 外，一律：

~~~http
Authorization: Bearer <your-MEMORY_API_KEY>
Content-Type: application/json
~~~

请求体**禁止传入 tenant_id**；服务端从启动配置确定租户。同一个 API key 只应关联其配置的单租户。

### 1. GET /v1/health — 进程存活

~~~bash
curl -s http://127.0.0.1:8765/v1/health
~~~

~~~json
{"status":"alive","contract_version":"1.0.0"}
~~~

只证明 HTTP 进程活着，**不证明数据库、AGE、Temporal 或对象存储可用**。

### 2. GET /v1/ready — PostgreSQL 就绪

~~~bash
curl -s -H "Authorization: Bearer $MEMORY_API_KEY" \
  http://127.0.0.1:8765/v1/ready
~~~

成功返回 \`200\` 和 \`{"status":"ready","checks":{"postgresql":"ready"},"contract_version":"1.0.0"}\`。数据库不可用返回 \`503\`，不会暴露内部连接错误。此接口**不检查 AGE/Temporal**。

### 3. GET /v1/capabilities — 功能发现

~~~bash
curl -s -H "Authorization: Bearer $MEMORY_API_KEY" \
  http://127.0.0.1:8765/v1/capabilities
~~~

结果包含名称如 \`memory.claims\`、\`memory.bitemporal\`、\`memory.vector\`、\`memory.graph\`、\`memory.objects\`、\`workflow.temporal\` 的 \`available\` 布尔值。当前语义主要是**部署配置声明**，并非所有可选后端的实时探活。返回 \`false\` 时不要尝试依赖该能力。

### 4. POST /v1/memories — 写入事实 / 创建知识条目

~~~bash
curl -s -X POST http://127.0.0.1:8765/v1/memories \
  -H "Authorization: Bearer $MEMORY_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{
    "subject_id":"bedroom-1",
    "predicate":"night_preference",
    "value":{"description":"晚上保持安静","source_language":"zh"},
    "source_id":"utterance-20261109-001",
    "valid_from":"2026-11-09T00:00:00+08:00"
  }'
~~~

\`201\` 示例：

~~~json
{"id":"<new-claim-uuid>","tenant_id":"demo-home"}
~~~

字段说明：

| 字段 | 类型 | 作用 |
|---|---|---|
| subject_id | string，必填 | 人/设备/空间/项目等主体的稳定标识 |
| predicate | string，必填 | 属性或关系语义名称 |
| value | JSON object，必填 | 结构化事实；不是原始文档全文解析 |
| source_id | string，必填 | 输入来源/证据标识，必须由调用方提供 |
| valid_from | ISO-8601，必填 | 事实现实中开始有效的时间（含时区） |
| valid_to | ISO-8601 或 null | 事实有效截止时间，**开区间** |
| replaces_id | UUID 或 null | 纠正同主体、同 predicate 的已有事实 |
| object_sha256 | SHA-256 或 null | 指向**此前已存储**的原始文件；API 不负责上传 |

重要：如果调用方重复发送相同的 POST，而**没有**显式的幂等机制，可能创建两条事实。当前 \`/v1/memories\` **尚未提供 Idempotency-Key 保障**。不能把 Outbox 的幂等性误认为本端点具有幂等写入。

### 5. GET /v1/memories/{subject_id} — 双时间历史查询

~~~bash
curl -sG http://127.0.0.1:8765/v1/memories/bedroom-1 \
  -H "Authorization: Bearer $MEMORY_API_KEY" \
  --data-urlencode "predicate=night_preference" \
  --data-urlencode "valid_at=2026-11-09T20:00:00+08:00" \
  --data-urlencode "known_at=2026-11-09T21:00:00+08:00"
~~~

- \`valid_at\`：要问**事实当时是否有效**。
- \`known_at\`：要问**系统在那个时间已经知道什么**。
- 不传则以当前时间为默认查询时点。
- 结果中的每条记录包括 id、主体、谓词、JSON 值、来源、有效时间、记录时间、被更正时间和对象引用（如有）。

**更正**：先从 GET 找出旧 \`id\`，再 POST 同样的 \`subject_id\`、\`predicate\`，提供新 \`value\`、新 \`source_id\` 和 \`replaces_id\`。旧事实不会被静默删除；系统保留其知识时间线。注意：完整的有效期拆分尚未实现。

### 6. POST /v1/search — 混合检索

~~~bash
curl -s -X POST http://127.0.0.1:8765/v1/search \
  -H "Authorization: Bearer $MEMORY_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{
    "query":"晚上保持安静",
    "anchor_entity":"bedroom-1",
    "limit":10
  }'
~~~

返回体包含：

~~~json
{
  "contract_version":"1.0.0",
  "items":[
    {
      "memory_id":"<claim-uuid>",
      "subject_id":"bedroom-1",
      "predicate":"night_preference",
      "value":{"description":"晚上保持安静","source_language":"zh"},
      "source_id":"utterance-20261109-001",
      "score":0.0164,
      "channels":["sql"]
    }
  ],
  "channels_used":["sql"],
  "degraded_channels":{}
}
~~~

这是**示意响应，具体评分和结果取决于已写入的真实数据**。目前 SQL 字符串查找采用 JSON 文本字面子串匹配，不是成熟的全文搜索/BM25。图查询依赖 AGE 投影已经完成；Vector 需要调用方提供匹配维度的 \`embedding\` 和 \`model_id\`，且目标记忆已经通过 Python 后端写入向量。

当可选通道不可用时，\`degraded_channels\` 报告异常；SQL 权威事实库不可用时不会伪造结果。若没有配置搜索适配器，返回 \`503\`。

## 四、只通过 Python SDK 使用的现成能力

下列功能不在当前 Swagger HTTP 路由中。要调用它们，请在可信任的本地 Python 进程中使用模块接口，未来才能通过正式 API 网关暴露给第三方。

### 关系图：PostgreSQL 事实边 + Apache AGE

~~~python
from agent_memory_core.postgres import PostgresMemory
from agent_memory_core.age_graph import AgeProjection
import os

db=PostgresMemory(os.environ["MEMORY_DSN"])
try:
    edge_id=db.add_edge(
        tenant_id="demo-home",from_entity="bedroom-1",
        predicate="HAS_WINDOW",to_entity="window-1")
    age=AgeProjection(db.conn)
    age.initialize()
    age.project_pending(tenant_id="demo-home")
    print(age.neighbors(tenant_id="demo-home",from_entity="bedroom-1",hops=1))
finally:
    db.close()
~~~

AGE 是可重建的图投影；不应直接在 AGE 中新增权威业务事实。

### PGVector：写入本地生成的向量

~~~python
db.store_embedding(
    tenant_id="demo-home",
    memory_id="<existing-claim-uuid>",
    model_id="BAAI/bge-small-zh-v1.5",
    vector=[1.0] + [0.0]*511,  # 仅为接口形状示例，不是有意义的模型向量
)
~~~

模型 \`BAAI/bge-small-zh-v1.5\` 的实际向量维度为 512。示例常量向量只能测试存储形状，**不能用于语义质量评估**；必须由本地 Embedding 模型真正生成文本向量。项目不包含该模型权重。

### 时序观测

~~~python
from datetime import datetime, timezone
db.record_observation(
    tenant_id="demo-home", device_id="co2-1",
    source_event_id="reading-001", observed_at=datetime.now(timezone.utc),
    property="co2", value=780.0, unit="ppm", source_id="sensor-gateway")
recent=db.observations(
    tenant_id="demo-home", device_id="co2-1", property="co2", limit=20)
~~~

这里记录的是传感器**报告**，不是独立证明物理状态真实无误。

### 对象存储：SHA-256 内容寻址

~~~python
from agent_memory_core.objects import ObjectStore
objects=ObjectStore("./.memory-objects")
sha256=objects.put(b"original-evidence")
assert objects.get(sha256)==b"original-evidence"
~~~

### Temporal

代码路径：\`src/agent_memory_core/temporal_adapter.py\`；测试：\`tests/test_temporal_integration.py\`。Temporal 的真实 SDK Workflow、Worker、Activity 和 PostgreSQL 验证事件已在 CI 的本地 Temporal 开发服务器上运行。**尚无 /v1/workflows HTTP API，也没有生产级 Temporal 集群部署**。它不参与普通同步记忆读写。

## 五、HTTP 状态与运维规则

| HTTP 状态 | 意义 |
|---|---|
| 200 | 查询成功 |
| 201 | 已创建事实 |
| 401 | 未提供 Bearer |
| 403 | Bearer 不正确 |
| 422 | 请求字段/时间格式/更正条件错误 |
| 503 | PostgreSQL 就绪检查失败或搜索适配器未配置 |
| 500 | 未处理的内部异常；目前生产级错误治理尚未完成 |

部署守则：只绑定 \`127.0.0.1\`；生产应用必须先补 RLS、TLS、密钥管理、限流、完整删除/保留治理、备份恢复和监控。不要通过公网隧道直接发布当前开发服务。后续文档所有承诺以自动化测试与实际源代码为准。

## 六、第三方独立验收

执行：

~~~bash
python -m pip install -e '.[dev,api,postgres]'
pytest -q tests/test_public_contract.py tests/test_openapi_contract.py
python scripts/export_openapi.py docs/openapi.generated.json
MEMORY_TEST_DSN="$MEMORY_DSN" pytest -q tests/test_api_postgres_runtime.py
~~~

完整三合一测试还需要 Docker 数据库：

~~~bash
MEMORY_TEST_DSN="$MEMORY_DSN" pytest -q -m integration
~~~

最后请到 GitHub Actions 检查**同一个 commit**的全部通过记录；历史不同提交的绿色 CI 不应合并为某一新版本的正式验收证明。
