"""Human-facing API documentation: service narrative, tag metadata, user stories.

The OpenAPI schema FastAPI generates is accurate but impersonal: a path, a
schema, a status code. A reviewer — an integrator, an auditor, an investor in
the room — first needs to know *whose problem this endpoint solves*. So every
operation in this module carries one user story: persona, goal, value, the
concrete steps, and a runnable example.

Stories are injected into the schema rather than pasted into each decorator so
that ``tests/test_api_docs.py`` can prove, mechanically, that no endpoint ships
without one. If you add a route, add its story here; the test fails otherwise.

Keys are ``(METHOD, path_template)`` exactly as FastAPI registers them.
"""
from __future__ import annotations

from dataclasses import dataclass, field

SERVICE_DESCRIPTION = """\
**本地优先、证据可追溯的 Agent 记忆服务。** PostgreSQL 是唯一事实源；pgvector 与
Apache AGE 只是可选的召回索引；对象存储与 Temporal 是彼此独立的可选适配器。

## 五分钟上手

```bash
export API=http://127.0.0.1:8000
export KEY=$MEMORY_API_KEY

curl -s $API/v1/health
curl -s -H "Authorization: Bearer $KEY" $API/v1/capabilities
curl -s -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \\
  -d '{"text":"我不喜欢卧室太吵","source_id":"demo:1"}' \\
  $API/v1/memories/process
curl -s -H "Authorization: Bearer $KEY" "$API/v1/memories?limit=20"
```

浏览器打开 `GET /dashboard/` 可以看到同一套接口的可视化台账。

## 三条必须先理解的设计约束

| 约束 | 含义 | 违反了会怎样 |
|---|---|---|
| **双时态** | `valid_from/valid_to` 是事实在真实世界生效的区间；`recorded_at` 是系统什么时候相信它。两者不混用。 | 时间戳缺时区 → 422 |
| **可信基数** | 一个 `subject+predicate` 是否允许多条并存，由租户策略表决定，默认 `one`。模型/调用方**不能**自己声明多值。 | 与策略冲突 → 422；策略已有历史时改策略 → 409 |
| **软遗忘** | `DELETE` 只是撤回相信，行与审计链保留，可通过 `known_at` 回看。 | 需要物理删除请走独立的合规流程 |

## 认证与租户

所有 `/v1/*`（除 `/v1/health`）都要求 `Authorization: Bearer <MEMORY_API_KEY>`。
**租户身份来自凭据，永远不从请求体读取**，因此客户端无法通过改 JSON 越权。

## 响应与错误约定

- 成功：`2xx` + JSON；`POST /v1/memories` 返回 `201`，写回执含 `id`。
- `401/403`：凭据缺失或错误（不会提示"key 错在哪"）。
- `404`：按租户作用域查找不到；不会泄露其他租户是否存在该资源。
- `409`：幂等键冲突、观测事件冲突、策略变更有历史依赖。
- `422`：字段校验失败、时间戳缺时区、路径 id 非 UUID。
- `503`：可选能力未启用（向量、图谱、对象存储、Temporal、搜索适配器）。**先查 `/v1/capabilities`，不要靠试错。**

## 事实关系五分类

`POST /v1/memories/process` 对每条候选事实给出且只给出一种判定：

`CREATED` 新增 · `DUPLICATE` 完全重复（只补来源） · `SUPERSEDES` 修正旧值 ·
`CONTRADICTION` 同时段冲突（需人工/上层裁决） · `MERGED` 合并为一条更强的表述。

## 相关入口

- `/dashboard/` 可视化台账（同源、无需 CORS）
- `/docs/swagger` 标准 Swagger UI（需要能访问 CDN）
"""

TAG_METADATA = [
    {"name": "System",
     "description": "探活、就绪与能力发现。接入方应当先调用 `/v1/capabilities`，"
                    "再决定是否使用向量、图谱、Temporal 等可选通道。"},
    {"name": "Memory",
     "description": "记忆的主体：写入、浏览、回忆、软遗忘，以及控制"
                    "“一个主体一个谓词能存几条”的可信基数策略。"},
    {"name": "Retrieval",
     "description": "混合召回：SQL 词法、pgvector 语义、AGE 图邻域。通道可降级，"
                    "降级会显式写在 `degraded_channels` 里。"},
    {"name": "Knowledge Graph",
     "description": "租户内的规范实体注册表与关系写入；AGE 投影是可选的派生索引。"},
    {"name": "Objects",
     "description": "按内容寻址的证据对象（录音、截图、原文）。SHA-256 即地址。"},
    {"name": "Observations",
     "description": "设备时序观测：幂等写入、事件时间查询与窗口聚合。"},
    {"name": "Events",
     "description": "持久化事件日志：游标轮询、SSE 实时流、签名 webhook 与死信重放。"},
    {"name": "Workflows",
     "description": "可选的 Temporal 工作流生命周期；未配置时统一返回 503。"},
]


@dataclass(frozen=True)
class Story:
    """One endpoint, told from the caller's point of view."""

    persona: str
    goal: str
    value: str
    steps: tuple[str, ...] = field(default_factory=tuple)
    example: str = ""

    def as_dict(self) -> dict:
        return {
            "persona": self.persona,
            "goal": self.goal,
            "value": self.value,
            "steps": list(self.steps),
            "example": self.example,
        }

    def markdown(self) -> str:
        lines = [
            f"**User story** — 作为{self.persona}，我希望{self.goal}，"
            f"以便{self.value}。",
        ]
        if self.steps:
            lines.append("")
            for index, step in enumerate(self.steps, start=1):
                lines.append(f"{index}. {step}")
        if self.example:
            lines += ["", "```bash", self.example.strip(), "```"]
        return "\n".join(lines)


CURL = 'curl -s -H "Authorization: Bearer $KEY" -H "Content-Type: application/json"'

STORIES: dict[tuple[str, str], Story] = {
    # ---------------------------------------------------------------- System
    ("GET", "/v1/health"): Story(
        persona="平台运维",
        goal="在不带任何凭据的情况下确认进程还活着",
        value="负载均衡器能在 200ms 内摘除坏实例，而不是把 401 当成故障",
        steps=("只探进程，不碰数据库；它返回 200 不代表依赖就绪。",
               "需要判断“能不能真正服务请求”时改用 `/v1/ready`。"),
        example="curl -s $API/v1/health",
    ),
    ("GET", "/v1/ready"): Story(
        persona="值班工程师",
        goal="用一个受鉴权的探针确认 PostgreSQL 真能执行查询",
        value="发布后 30 秒内就能区分“进程起来了”和“数据库连不上”",
        steps=("探针只验证规范库，不验证 AGE、向量模型、对象存储或 Temporal。",
               "返回 503 时看服务日志中的 DSN/连接池错误，而不是重启进程。"),
        example=CURL + " $API/v1/ready",
    ),
    ("GET", "/v1/capabilities"): Story(
        persona="接入方工程师",
        goal="在写第一行业务代码前知道这个实例到底开了哪些能力",
        value="不会把 503 当成 bug，也不会为一个未启用的向量通道写降级逻辑到深夜",
        steps=("读取 `capabilities[]`，按 `name` 建索引。",
               "`memory.extraction` 的 `reason` 会带当前抽取 provider，可直接展示在控制台。",
               "能力是部署态，不是租户态；多租户共享同一份。"),
        example=CURL + " $API/v1/capabilities",
    ),
    # ---------------------------------------------------------------- Memory
    ("POST", "/v1/memories"): Story(
        persona="设备网关开发者",
        goal="把已经结构化的事实（比如传感器读数）直接写成一条带来源的记忆",
        value="不必为了入库而伪造一段自然语言，也不必自己算幂等",
        steps=("`valid_from` 必须是带时区的 ISO-8601；它是事实生效时间，不是写入时间。",
               "重复投递时带上 `Idempotency-Key`，同键同体会返回同一条 `id`，不同体会 409。",
               "要修正旧值就填 `replaces_id`，且 `subject_id`/`predicate` 必须与旧值一致。",
               "返回值里的 `embedded=false` 只表示向量化失败，事实已经落库。"),
        example=CURL + """ \\
  -H 'Idempotency-Key: gw-2026-11-12-0001' \\
  -d '{"subject_id":"bedroom","predicate":"current_temperature",
       "value":{"celsius":22.5},"source_id":"sensor:bedroom-01",
       "valid_from":"2026-11-12T09:00:00+08:00"}' \\
  $API/v1/memories""",
    ),
    ("GET", "/v1/memories"): Story(
        persona="数据审计员",
        goal="按时间顺序翻看这个租户写过的每一条事实，包括已经被替换的",
        value="能回答“这条结论是什么时候、被哪次写入改掉的”，而不是只看当前快照",
        steps=("默认只返回当前有效事实；勾选 `include_superseded=true` 才看到历史。",
               "`known_at` 是“当时我们相信什么”，`valid_at` 是“当时世界上是什么”。",
               "分页用 `limit`（≤500）+ `offset`，按 `recorded_at` 倒序。"),
        example=CURL + """ \\
  "$API/v1/memories?include_superseded=true&limit=50" """.rstrip(),
    ),
    ("GET", "/v1/predicate-policies"): Story(
        persona="租户管理员",
        goal="看清哪些谓词被声明为多值，哪些还在默认的单值规则下",
        value="不会因为“一个房间出现两个温度”而被质疑数据质量",
        steps=("返回 `default: one`，表示未列出的谓词都只允许一条有效事实。",
               "策略是可信配置，写在 `memory_predicate_policies` 表里，模型无权修改。"),
        example=CURL + " $API/v1/predicate-policies",
    ),
    ("PUT", "/v1/predicate-policies/{predicate}"): Story(
        persona="租户管理员",
        goal="声明某个谓词天生就是多值的（例如联系电话），或把多值收紧为单值",
        value="基数由数据库策略决定，而不是交给模型在每次抽取时即兴判断",
        steps=("`predicate` 必须是短 snake_case（`a-z0-9_`），否则 422。",
               "该谓词已经有历史事实时，改策略返回 409：先清理/归档旧事实再改，避免历史被静默重解释。",
               "放宽为 `many` 不会删除任何数据；收紧为 `one` 只影响后续写入。"),
        example=CURL + """ -X PUT \\
  -d '{"cardinality":"many"}' \\
  $API/v1/predicate-policies/contact_phone""",
    ),
    ("GET", "/v1/memories/{subject_id}"): Story(
        persona="对话助手",
        goal="在回答前回忆某个主体在某个时刻的事实",
        value="回答能对齐用户当时的世界状态，而不是拿最新值硬套过去的问题",
        steps=("只传 `subject_id` 时，两个时间维度都取“现在”。",
               "问“上周三卧室多少度” → 传 `valid_at=上周三`、`known_at=现在`。",
               "问“上周三我们以为卧室多少度” → 两个参数都传上周三。"),
        example=CURL + """ \\
  "$API/v1/memories/bedroom?valid_at=2026-11-05T21:00:00%2B08:00" """.rstrip(),
    ),
    ("DELETE", "/v1/memories/{memory_id}"): Story(
        persona="终端用户（通过助手）",
        goal="让系统忘掉一条不想再被提起的事实",
        value="隐私诉求被立刻满足，同时审计链仍然完整可查",
        steps=("这是软遗忘：事实从所有有效读取与检索中消失，但行和事件保留。",
               "随后会发出 `memory.claim.forgotten` 事件，下游投影可据此清理。",
               "重复删除同一条会返回 404。"),
        example=CURL + " -X DELETE $API/v1/memories/0f6c2f5e-3b1a-4c9d-9a1e-6d2b7c8f9a01",
    ),
    ("DELETE", "/v1/memories"): Story(
        persona="合规负责人",
        goal="按主体或谓词批量撤回一类事实",
        value="用户行使删除权时不必逐条点，且每条撤回都有独立事件留痕",
        steps=("`subject_id` 与 `predicate` 至少给一个，否则 422。",
               "返回 `forgotten[]` 列表与 `count`；没有匹配项时返回空列表而非报错。"),
        example=CURL + """ -X DELETE \\
  "$API/v1/memories?subject_id=bedroom&predicate=current_temperature" """.rstrip(),
    ),
    ("POST", "/v1/memories/process"): Story(
        persona="产品经理（演示现场）",
        goal="输入一句自然语言，看系统如何抽取事实、判重、判矛盾并落库",
        value="同一句话重复说不会产生两条记忆，改口会生成新版本而不是脏数据",
        steps=("`candidates` 为空时使用服务端配置的本地抽取器；两者都没有则 503。",
               "每条候选事实会被判定为 CREATED / DUPLICATE / SUPERSEDES / CONTRADICTION / MERGED 之一。",
               "`confidence_threshold` 以下的候选被计入 `rejected`，不落库。",
               "返回值里的 `relations[]` 就是演示时应当逐条讲给观众听的结果。"),
        example=CURL + """ \\
  -d '{"text":"其实卧室 23 度更合适","source_id":"demo:2"}' \\
  $API/v1/memories/process""",
    ),
    ("GET", "/v1/memories/{memory_id}/sources"): Story(
        persona="数据审计员",
        goal="顺着一条事实回查它由哪些来源支撑",
        value="结论可以被证伪：每个来源都带 trust_level 与 confidence",
        steps=("来源是独立表，一条事实可以有多条来源（重复写入只会补来源，不会新建事实）。",
               "如果写入了 `object_sha256`，可再用 `/v1/objects/{digest}` 取回原始证据。"),
        example=CURL + " $API/v1/memories/0f6c2f5e-3b1a-4c9d-9a1e-6d2b7c8f9a01/sources",
    ),
    # --------------------------------------------------------------- Objects
    ("PUT", "/v1/objects"): Story(
        persona="证据采集端",
        goal="把录音、截图或原文按内容寻址存起来，并在事实里引用它",
        value="事实和证据分离存放，删记忆不会误删证据，证据也不会被悄悄替换",
        steps=("请求体就是原始字节，`Content-Type` 会被记录。",
               "空体或重复内容都返回 201，响应头 `X-Content-SHA256` 是地址。",
               "超过 10 MiB 返回 413；对象存储未启用返回 503。"),
        example='curl -s -X PUT -H "Authorization: Bearer $KEY" \\\n'
                '  --data-binary @note.txt $API/v1/objects',
    ),
    ("GET", "/v1/objects/{digest}"): Story(
        persona="审计员",
        goal="凭 SHA-256 取回当初那条事实背后的原始证据",
        value="任何结论都能落到一份不可篡改的原始材料上",
        steps=("只返回本租户登记过的对象，跨租户一律 404。",
               "响应头同样带 `X-Content-SHA256`，客户端应自行校验。"),
        example=CURL + " $API/v1/objects/<sha256> --output evidence.bin",
    ),
    # ---------------------------------------------------------- Observations
    ("POST", "/v1/observations"): Story(
        persona="传感器网关",
        goal="把设备读数幂等地上报，重试不会产生重复点",
        value="网络抖动导致的重复上报不会污染聚合结果",
        steps=("`source_event_id` 是设备侧唯一事件 id；重复上报返回 200 与 `duplicate:true`。",
               "同一个 `source_event_id` 携带不同读数会 409，说明设备侧时钟或 id 生成有问题。",
               "`observed_at` 是物理事件时间，不是上报时间。"),
        example=CURL + """ \\
  -d '{"device_id":"bedroom-01","source_event_id":"evt-8812",
       "observed_at":"2026-11-12T09:15:00+08:00","property":"temperature",
       "value":22.4,"unit":"celsius","source_id":"sensor:bedroom-01"}' \\
  $API/v1/observations""",
    ),
    ("GET", "/v1/observations/{device_id}/aggregate"): Story(
        persona="能效分析师",
        goal="拿一个设备在某段时间的平均/极值，而不是自己拉全量原始点",
        value="一次查询就能支撑“昨晚卧室平均温度”这类结论",
        steps=("`property` 必填；`start_at`/`end_at` 必须带时区。",
               "区间按事件时间半开，聚合在数据库内完成。"),
        example=CURL + """ \\
  "$API/v1/observations/bedroom-01/aggregate?property=temperature
   &start_at=2026-11-11T00:00:00%2B08:00&end_at=2026-11-12T00:00:00%2B08:00" """.rstrip(),
    ),
    ("GET", "/v1/observations/{device_id}"): Story(
        persona="现场排障工程师",
        goal="按设备与属性拉最近一段原始时序，定位异常点",
        value="不用登进数据库就能看到设备到底报了什么",
        steps=("`property` 必填，`limit` 默认 100。",
               "可选 `start_at`/`end_at` 收窄窗口，时间必须带时区。"),
        example=CURL + """ \\
  "$API/v1/observations/bedroom-01?property=temperature&limit=200" """.rstrip(),
    ),
    # ------------------------------------------------------------- Retrieval
    ("PUT", "/v1/memories/{memory_id}/embedding"): Story(
        persona="自带模型的接入方",
        goal="把自己算好的向量挂到已有事实上，不把模型部署进本服务",
        value="向量模型可以随业务迭代，记忆库仍然只做存储与检索",
        steps=("实例未启用向量能力时返回 503；先查 `/v1/capabilities`。",
               "维度必须是 384–512，且与后续查询用的模型一致。",
               "同一事实重复挂载会覆盖旧向量。"),
        example=CURL + """ -X PUT \\
  -d '{"model_id":"bge-m3","embedding":[0.01,0.02,"…"]}' \\
  $API/v1/memories/<uuid>/embedding""",
    ),
    ("POST", "/v1/search"): Story(
        persona="助手运行时",
        goal="用一句话同时走词法、语义和图邻域，拿回可解释的命中",
        value="召回不再依赖单一通道，某一路挂了也能继续服务",
        steps=("不传 `embedding` 且服务端配了 embedding provider 时，会自动向量化查询串。",
               "返回的每个命中都带 `channels[]`（sql/vector/graph），可直接展示“为什么召回它”。",
               "`degraded_channels` 非空表示某通道降级，结果仍可用但应提示。",
               "未配置搜索适配器返回 503。"),
        example=CURL + """ \\
  -d '{"query":"卧室温度偏好","limit":10}' \\
  $API/v1/search""",
    ),
    # -------------------------------------------------------- Knowledge Graph
    ("POST", "/v1/entities"): Story(
        persona="领域建模者",
        goal="注册规范实体并维护别名，让不同说法收敛到同一个主体",
        value="“主卧”“主卧室”“bedroom”不再各写一条互不相干的记忆",
        steps=("`entity_id` 由调用方给定且租户内唯一，重复提交即更新。",
               "`aliases` 用于后续 `/v1/entities/resolve` 的模糊匹配。"),
        example=CURL + """ \\
  -d '{"entity_id":"bedroom","entity_type":"room","canonical_name":"主卧",
       "aliases":["主卧室","bedroom"],"attributes":{"floor":2}}' \\
  $API/v1/entities""",
    ),
    ("GET", "/v1/entities/resolve"): Story(
        persona="抽取器",
        goal="把用户口语里的说法解析成规范实体 id",
        value="记忆写入前就能对齐主体，避免同义实体撕裂事实",
        steps=("参数名是 `name`，不是 `query`。",
               "命中别名返回规范实体；找不到 404；同名歧义 409，需要先消歧。"),
        example=CURL + ' "$API/v1/entities/resolve?name=主卧室"',
    ),
    ("POST", "/v1/edges"): Story(
        persona="领域建模者",
        goal="写入实体之间的有向关系，并让它进入图索引参与召回",
        value="“主卧 属于 二层”这类结构信息能被图通道用上",
        steps=("关系先写规范表，再由 AGE 投影异步写入图；`projected_to_age` 是本次投影条数。",
               "图谱能力未启用时照常写入规范表，`projected_to_age` 为 0。"),
        example=CURL + """ \\
  -d '{"from_entity":"bedroom","predicate":"located_on","to_entity":"floor_2"}' \\
  $API/v1/edges""",
    ),
    # ---------------------------------------------------------------- Events
    ("GET", "/v1/events"): Story(
        persona="下游同步服务",
        goal="从上次的游标继续拉事件，不漏不重",
        value="外部系统可以在不订阅 webhook 的情况下保持一致",
        steps=("把上一页的 `next_cursor` 作为下一次的 `after`。",
               "`has_more=true` 表示可能还有下一页，应继续拉。",
               "事件按写入顺序单调递增，游标即最后一条的 `event_id`。"),
        example=CURL + ' "$API/v1/events?limit=100&after=<cursor>"',
    ),
    ("GET", "/v1/events/stream"): Story(
        persona="实时看板",
        goal="保持一条长连接接收事件，断线后从上次位置续上",
        value="演示与告警都能做到秒级可见，而不是轮询等待",
        steps=("标准 SSE：`id:` 是事件 id，`event:` 是事件类型，`data:` 是 JSON。",
               "断线重连时带 `Last-Event-ID` 或 `?after=` 即可续传。",
               "单连接最长 30 秒（`max_seconds` 1–30），到点正常结束，客户端重连即可。"),
        example='curl -N -H "Authorization: Bearer $KEY" $API/v1/events/stream',
    ),
    ("POST", "/v1/webhooks"): Story(
        persona="集成工程师",
        goal="注册一个带签名密钥的持久化 webhook，让外部系统收到记忆变更",
        value="投递失败会自动重试并落死信，不会因为对方暂时宕机而丢事件",
        steps=("目标地址必须通过主机白名单校验，且必须是 https。",
               "响应里的 `signing_secret` 只返回这一次，请立即保存。",
               "签名算法：`sha256=HMAC-SHA256(secret, timestamp + '.' + raw_body)`。",
               "需要更新签名密钥或白名单时，由运维通过环境变量完成。"),
        example=CURL + """ \\
  -d '{"target_url":"https://example.com/hooks/memory",
       "event_types":["memory.claim.created","memory.claim.forgotten"]}' \\
  $API/v1/webhooks""",
    ),
    ("GET", "/v1/webhooks"): Story(
        persona="集成工程师",
        goal="确认这个租户当前有哪些订阅、是否还活着",
        value="上线前能核对订阅，而不是靠猜为什么收不到事件",
        steps=("只返回本租户的订阅；不含签名密钥。",
               "已撤销的订阅保留在列表里，`active` 为 false。"),
        example=CURL + " $API/v1/webhooks",
    ),
    ("GET", "/v1/webhooks/{subscription_id}/deliveries"): Story(
        persona="值班工程师",
        goal="查看某次投递的尝试记录与失败原因",
        value="能在对方说“没收到”时给出具体状态码与重试轨迹",
        steps=("`subscription_id` 必须是 UUID，否则 422。",
               "按时间倒序返回尝试记录，含响应码、尝试次数与下次重试时间。"),
        example=CURL + " $API/v1/webhooks/<uuid>/deliveries?limit=50",
    ),
    ("POST", "/v1/webhooks/deliveries/{delivery_id}/replay"): Story(
        persona="值班工程师",
        goal="把一条已进入死信的投递重新排队",
        value="对方修好接收端后可以立刻补投，不必手工造事件",
        steps=("只能重放本租户的死信投递，其他一律 404。",
               "重放不会改变事件内容，只重置投递状态。"),
        example=CURL + " -X POST $API/v1/webhooks/deliveries/<uuid>/replay",
    ),
    ("DELETE", "/v1/webhooks/{subscription_id}"): Story(
        persona="集成工程师",
        goal="撤销一个不再需要的订阅",
        value="停止投递的同时保留历史投递记录以便追溯",
        steps=("撤销是幂等的：已撤销的订阅再删返回 404。",
               "撤销后不再产生新投递，历史 `deliveries` 仍可查询。"),
        example=CURL + " -X DELETE $API/v1/webhooks/<uuid>",
    ),
    ("POST", "/v1/events/outbox/{event_id}/replay"): Story(
        persona="平台运维",
        goal="把一条卡在死信里的出站事件重新投递",
        value="下游恢复后能补上投影，不必手工改库",
        steps=("只处理本租户的死信事件，其他 404。",
               "与 webhook 重放的区别：这里重放的是事务性 outbox 事件，通常用于内部投影。"),
        example=CURL + " -X POST $API/v1/events/outbox/<uuid>/replay",
    ),
    # ------------------------------------------------------------- Workflows
    ("POST", "/v1/workflows/memory-verifications"): Story(
        persona="质量工程师",
        goal="为一条记忆启动可恢复的校验工作流",
        value="长耗时校验不占请求线程，进程重启也不会丢进度",
        steps=("需要实例启用 Temporal 且 worker 在线，否则 503。",
               "工作流 id 以租户前缀命名，跨租户无法查询或取消。",
               "`memory_id` 必须是 UUID。"),
        example=CURL + """ \\
  -d '{"memory_id":"0f6c2f5e-3b1a-4c9d-9a1e-6d2b7c8f9a01"}' \\
  $API/v1/workflows/memory-verifications""",
    ),
    ("GET", "/v1/workflows/{workflow_id}"): Story(
        persona="质量工程师",
        goal="查询校验工作流跑到哪一步了",
        value="失败与超时可以被区分，而不是笼统地“没结果”",
        steps=("工作流 id 必须带本租户前缀，否则 404。",
               "返回 `status`、`run_id` 与 `workflow_type`。"),
        example=CURL + " $API/v1/workflows/t:memory-verify-<uuid>",
    ),
    ("POST", "/v1/workflows/{workflow_id}/cancel"): Story(
        persona="质量工程师",
        goal="中止一次跑错方向的校验",
        value="错误的工作流不会继续消耗 worker 配额",
        steps=("取消是请求式的，返回 `cancel_requested:true` 不代表已终止。",
               "随后用 GET 同一路径确认最终状态。"),
        example=CURL + " -X POST $API/v1/workflows/t:memory-verify-<uuid>/cancel",
    ),
}


def install(app) -> None:
    """Inject stories into the generated schema (and keep it cached)."""
    original = app.openapi

    def openapi():
        if app.openapi_schema:
            return app.openapi_schema
        schema = original()
        for path, operations in schema.get("paths", {}).items():
            for method, operation in operations.items():
                if method.lower() not in {"get", "post", "put", "delete", "patch"}:
                    continue
                story = STORIES.get((method.upper(), path))
                if story is None:
                    continue
                operation["x-user-story"] = story.as_dict()
                existing = operation.get("description", "").strip()
                operation["description"] = story.markdown() + (
                    "\n\n---\n\n" + existing if existing else "")
        app.openapi_schema = schema
        return schema

    app.openapi = openapi
