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

所有 `/v1/*`（除 `/v1/health`）都必须通过认证。兼容模式可使用
`Authorization: Bearer <MEMORY_API_KEY>`；身份模式使用 Opaque Session 或有 Scope 的
API Credential，并通过 `X-Memory-Tenant` 选择已加入的租户。
**租户身份必须由服务端验证当前 Membership，永远不从请求体读取**。

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
    {"name": "Identity",
     "description": "当前身份、租户 Membership、Opaque Session 撤销与 API Credential 授权。"},
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
    {"name": "Durable Tasks",
     "description": "PostgreSQL 规范任务状态、版本化检查点、幂等副作用意图与事务性 Outbox。"},
    {"name": "Work Continuity",
     "description": "Agent 交接：预算内的来源化上下文、不可变交接收据、接手时版本与副作用安全校验。"},
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
    ("GET", "/v1/tasks/{task_id}/effects"): Story(
        persona="独立任务审计员",
        goal="在不接触设备凭据和原始请求体的情况下核对任务副作用状态",
        value="区分账本提交、UNKNOWN 和真实物理效果，不把自报执行当成已验证的因果结果",
        steps=("只能读取当前已授权租户的任务；不存在或跨租户的 task_id 返回 404。",
               "按 effect_key 返回请求哈希、状态、尝试次数和时间，不返回原始请求或 Provider 敏感信息。",
               "明确标记物理独立观察和因果验证未完成；GET 不会重试任何外部操作。"),
        example=CURL + " $API/v1/tasks/<uuid>/effects",
    ),
    ("POST", "/v1/context/prepare"): Story(
        persona="接力研究的 Agent A",
        goal="用任务和明确的主体集合生成有版本来源的上下文包",
        value="Agent B 无需逐字重放历史就能定位已完成事实与未解决任务",
        steps=("提供真实任务 UUID、1..20 个主体以及 256..8192 估算 Token 预算。",
               "返回版本化事实、来源、任务检查点和未决副作用，显式列出任何省略的事实 ID。",
               "超过预算、任务不属于当前租户或必需内容无法容纳时拒绝成功。"),
        example=CURL + """ -d '{"task_id":"<uuid>","subject_ids":["supplier-a"],"token_budget":4096}' $API/v1/context/prepare""",
    ),
    ("POST", "/v1/handoffs"): Story(
        persona="准备退出会话的 Agent A",
        goal="把交接上下文冻结为租户限定的不可变收据",
        value="下一位 Agent 能以固定内容哈希验证接手材料而非相信会话缓存",
        steps=("使用当前租户的公开任务与事实读路径生成 Context Pack。",
               "保存不可变 JSON 与 SHA-256；source_agent/target_agent 是描述标签而非独立授权身份。",
               "任何已知未决副作用都会保留在快照中，不能静默变成成功。"),
        example=CURL + """ -d '{"task_id":"<uuid>","subject_ids":["supplier-a"],"token_budget":4096,"source_agent":"A","target_agent":"B"}' $API/v1/handoffs""",
    ),
    ("POST", "/v1/handoffs/{handoff_id}/accept"): Story(
        persona="全新会话的 Agent B",
        goal="验证交接任务与事实是否仍可安全延续",
        value="陈旧事实、错误目标、裁剪信息和未决外部副作用不会被误当成完成交接",
        steps=("必须通过当前租户权限验证；其他租户的收据返回 404。",
               "在同一数据库事务检查快照哈希、Task checkpoint、事实版本和未决副作用。",
               "冲突返回 409；接受不会触发外部执行，重复接受具有幂等语义。"),
        example=CURL + """ -d '{"target_agent":"B"}' $API/v1/handoffs/<uuid>/accept""",
    ),

    # --------------------------------------------------------------- Identity
    ("GET", "/v1/auth/oidc/login"): Story(
        persona="首次使用的开发者",
        goal="通过已配置的 OpenID Connect 身份提供商安全登录",
        value="不需要把密码交给记忆服务，且登录事务由短期 State、Nonce 与 PKCE 保护",
        steps=("服务端从固定的 MEMORY_OIDC_ISSUER 获取 Discovery 元数据。",
               "浏览器被重定向到授权端点，使用 Authorization Code + PKCE S256。",
               "事务 Cookie 为 HttpOnly、签名保护且最多存活 10 分钟。"),
        example='打开 $API/v1/auth/oidc/login',
    ),
    ("GET", "/v1/auth/oidc/callback"): Story(
        persona="完成身份认证的开发者",
        goal="验证 OIDC 回调并建立自己的记忆工作区",
        value="首次登录可原子创建 Principal、External Identity、Owner Membership 和 Session",
        steps=("服务端校验签名、Issuer、Audience、Expiry、Nonce 与 State。",
               "账号关联只使用 issuer + subject，不使用未经验证的 Email 自动合并账号。",
               "仅在 MEMORY_OIDC_ALLOW_SIGNUP=1 时允许新身份注册；新工作区与 Session 在同一数据库事务内创建。",
               "Session 使用 HttpOnly Cookie，CSRF Token 单独设置；登录后进入 Observatory。"),
        example='由身份提供商回调 $API/v1/auth/oidc/callback',
    ),
    ("GET", "/v1/identity/tenants"): Story(
        persona="已登录开发者",
        goal="列出当前 Session 能访问的全部租户及角色",
        value="登录后可以选择工作区，不必把租户 ID 写死在客户端配置里",
        steps=("只返回当前有效 Session 的 active Membership。",
               "已撤销、暂停的 Membership 和租户不会出现在列表中。",
               "API Credential 只返回它绑定的单个租户。"),
        example='curl -H "Authorization: Bearer $SESSION" $API/v1/identity/tenants',
    ),
    ("POST", "/v1/identity/tenants"): Story(
        persona="已登录开发者",
        goal="创建一个新的独立记忆工作区",
        value="新工作区由当前 Principal 拥有，并在创建后立即切换过去",
        steps=("仅浏览器 Session 可以创建工作区；Legacy Key 和 API Credential 不可调用。",
               "新 Tenant 与 Owner Membership 在控制面事务中创建。",
               "服务端设置当前浏览器的工作区 Cookie；后续请求仍逐次验证 Membership。"),
        example='curl -X POST -H "X-Memory-Tenant: $TENANT" -H "X-CSRF-Token: $CSRF" -H "Content-Type: application/json" -b cookies.txt -d \'{"display_name":"Research"}\' $API/v1/identity/tenants',
    ),
    ("POST", "/v1/identity/select-tenant"): Story(
        persona="属于多个工作区的开发者",
        goal="切换当前浏览器 Session 使用的工作区",
        value="切换只改变当前选择，不改变 Principal、Session 或其他成员的状态",
        steps=("仅浏览器 Session 可以切换；每个写请求必须通过 CSRF 校验。",
               "目标 Tenant 必须存在于该 Session 的 active Membership 列表中。",
               "切换成功后服务端更新工作区 Cookie；伪造或已撤销的 Membership 返回 403。"),
        example='curl -X POST -H "X-Memory-Tenant: $CURRENT_TENANT" -H "X-CSRF-Token: $CSRF" -H "Content-Type: application/json" -b cookies.txt -d \'{"tenant_id":"$TARGET_TENANT"}\' $API/v1/identity/select-tenant',
    ),
    ("POST", "/v1/identity/api-credentials"): Story(
        persona="已注册工作区的 Owner 或 Admin",
        goal="通过受 CSRF 保护的浏览器 Session 创建租户限定的开发者 API 凭据",
        value="第三方 SDK 可以按当前 Workspace 权限调用记忆和异步工作流",
        steps=("必须使用 Owner/Admin Session，API Credential 不能再次签发 API Credential。",
               "支持 memory:read、memory:write、memory:* 与专用 tasks:execute；Worker 权限必须显式签发。默认 90 天有效。",
               "secret 只在创建响应中返回一次，服务端只保存哈希；使用时携带 X-Memory-Tenant。"),
        example='curl -X POST -b cookies.txt -H "X-Memory-Tenant: $TENANT" -H "X-CSRF-Token: $CSRF" -H "Content-Type: application/json" -d \'{"name":"my-agent","scopes":["memory:read","memory:write"]}\' $API/v1/identity/api-credentials',
    ),
    ("DELETE", "/v1/identity/api-credentials/{credential_id}"): Story(
        persona="工作区开发者",
        goal="立即撤销自己签发的第三方访问凭据",
        value="旧 SDK Token 和由它排队的后续任务都不能再产生新的授权副作用",
        steps=("必须使用有效的浏览器 Session，不能用 API Credential 自行撤权。",
               "只能撤销当前 Principal 签发的 Credential；不存在返回 404。",
               "下次请求和后台执行会重新校验凭据状态。"),
        example='curl -X DELETE -b cookies.txt -H "X-Memory-Tenant: $TENANT" -H "X-CSRF-Token: $CSRF" $API/v1/identity/api-credentials/<uuid>',
    ),
    ("GET", "/v1/identity/me"): Story(
        persona="已登录开发者",
        goal="读取当前请求实际生效的 Principal、租户和角色",
        value="客户端能展示当前身份与权限，不需要信任本地缓存的角色",
        steps=("每次请求都重新检查 Session/API Credential 与当前 Membership。",
               "租户通过 X-Memory-Tenant 选择，必须是该 Principal 当前加入的租户。",
               "兼容旧 Bearer Key 时返回 legacy 身份，不伪装成多用户 Principal。"),
        example='curl -H "Authorization: Bearer $SESSION" -H "X-Memory-Tenant: $TENANT" $API/v1/identity/me',
    ),
    ("POST", "/v1/auth/logout"): Story(
        persona="已登录开发者",
        goal="立即撤销当前登录 Session",
        value="退出后旧 Session 不能继续访问任何租户资源",
        steps=("Session 通过服务端存储的 Token Hash 定位并撤销。",
               "Cookie 模式的写请求必须提供匹配的 CSRF Cookie 与 X-CSRF-Token。",
               "该接口不撤销独立签发的 API Credential。"),
        example='curl -X POST -H "Authorization: Bearer $SESSION" -H "X-Memory-Tenant: $TENANT" $API/v1/auth/logout',
    ),
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
    ("GET", "/v1/evaluation/search-config"): Story(
        persona="中文记忆质量评测工程师",
        goal="在开始写入冻结测试集之前核实实例实际启用的检索别名",
        value="消融实验的检索配置由运行中服务证明，不会被命令行开关误标为关闭",
        steps=("必须带有效的 Bearer 凭据；租户由服务端授权决定。",
               "响应包含 aliases_enabled、alias_count 与规范化映射的 SHA-256，不直接泄露映射内容。",
               "搜索适配器不能证明有效映射时返回 409；未启用搜索时返回 503。",
               "在同一实例上更换别名应产生不同哈希，评测报告必须保存该哈希。"),
        example=CURL + " $API/v1/evaluation/search-config",
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
    ("GET", "/v1/workflows/{workflow_id}/progress"): Story(
        persona="任务发起者",
        goal="读取持久化的工作阶段、已完成步骤和进度百分比",
        value="服务重启后仍能从工作流历史还原任务进度，而不是依赖浏览器内存",
        steps=("工作流 id 必须带本租户前缀，否则 404。",
               "当前 VerifyMemoryWorkflow 暴露阶段和两步完成计数；这不是任意 Activity 的字节级检查点。"),
        example=CURL + " $API/v1/workflows/t:memory-verify-<uuid>/progress",
    ),
    ("POST", "/v1/workflows/{workflow_id}/pause"): Story(
        persona="任务发起者",
        goal="请求工作流在下一个安全阶段边界暂停",
        value="维护人员可以暂缓后续步骤而不丢失已经完成的工作",
        steps=("暂停通过 Temporal Signal 持久化。",
               "正在执行的 Activity 不会被强制中断；暂停在下一个阶段边界生效。"),
        example=CURL + " -X POST $API/v1/workflows/t:memory-verify-<uuid>/pause",
    ),
    ("POST", "/v1/workflows/{workflow_id}/resume"): Story(
        persona="任务发起者",
        goal="继续执行此前暂停的工作流",
        value="从 Temporal 已记录的阶段继续，而不是创建一条新的任务",
        steps=("工作流 id 必须带本租户前缀，否则 404。",
               "恢复通过 Temporal Signal 发送；重复请求不会创建新的 Workflow ID。"),
        example=CURL + " -X POST $API/v1/workflows/t:memory-verify-<uuid>/resume",
    ),
    # ----------------------------------------------------------- Durable Tasks
    ("POST", "/v1/tasks"): Story(
        persona="第三方 Agent 开发者",
        goal="创建一条有租户归属、输入快照和幂等键的长期业务任务",
        value="HTTP 请求结束后任务仍有独立身份，可以被查询、恢复和审计",
        steps=("tenant_id 从当前认证上下文取得，不接受请求体指定租户。",
               "相同幂等键和相同请求返回原任务；相同键不同请求返回 409。",
               "任务创建与 memory.task.created Outbox 事件在同一事务提交。"),
        example=CURL + " -d '{\"task_type\":\"memory.batch.organize\",\"input\":{\"batch_size\":100},\"idempotency_key\":\"organize-001\"}' $API/v1/tasks",
    ),
    ("GET", "/v1/tasks"): Story(
        persona="任务控制台",
        goal="分页列出当前工作区的长期任务",
        value="不访问数据库也能检查任务状态、进度和执行历史入口",
        steps=("结果只包含当前认证租户的任务。",
               "limit 为 1–100，offset 从 0 开始。"),
        example=CURL + " '$API/v1/tasks?limit=50&offset=0'",
    ),
    ("GET", "/v1/tasks/{task_id}"): Story(
        persona="任务发起者",
        goal="读取任务的规范状态和最新业务检查点",
        value="Worker 更换或 Temporal 重放后仍可从 PostgreSQL 还原业务游标",
        steps=("task_id 必须是 UUID；跨租户资源统一返回 404。",
               "响应包含 checkpoint_version、progress 与最新 checkpoint。"),
        example=CURL + " $API/v1/tasks/<task-uuid>",
    ),
    ("POST", "/v1/tasks/worker/claim"): Story(
        persona="独立业务 Worker",
        goal="使用专用 tasks:execute 凭据原子领取当前租户的一条待执行任务",
        value="多个 Worker 可以安全竞争同一队列，只有一个取得当前 Lease Epoch",
        steps=("必须使用身份模式签发的专用 API Credential；memory:write 或 memory:* 不会自动获得任务执行权。",
               "租户来自 Credential 的服务端身份，不接受请求体指定租户。",
               "成功响应返回 worker_id、lease_epoch、lease_expires_at 与最新 checkpoint；无任务时 task 为 null。"),
        example=CURL + " -d '{\"worker_id\":\"worker-01\",\"lease_seconds\":30}' $API/v1/tasks/worker/claim",
    ),
    ("POST", "/v1/tasks/{task_id}/lease/renew"): Story(
        persona="持有任务租约的 Worker",
        goal="在任务执行期间续租，并证明自己仍持有当前 Epoch",
        value="长任务不会因正常执行时间超过初始租约而被其他 Worker 抢占",
        steps=("每次续租都必须提交 task_id、worker_id 与 lease_epoch。",
               "只允许当前且未过期的租约续期；旧 Epoch 或过期租约返回 409。",
               "租约时间由 PostgreSQL clock_timestamp() 判定，不信任 Worker 本机时钟。"),
        example=CURL + " -d '{\"worker_id\":\"worker-01\",\"lease_epoch\":1,\"lease_seconds\":30}' $API/v1/tasks/<task-uuid>/lease/renew",
    ),
    ("POST", "/v1/tasks/{task_id}/checkpoints"): Story(
        persona="长期 Agent Worker",
        goal="以预期版本提交检查点、进度和可选副作用意图",
        value="检查点与 Outbox 事件原子提交，旧 Worker 不能覆盖新 Worker 的进度",
        steps=("必须使用专用 tasks:execute API Credential，并同时提交 worker_id 与 lease_epoch。",
               "服务端在同一事务内校验当前租约、Epoch 和 expected_version；旧 Worker 返回 409。",
               "相同幂等键与完全相同内容重放返回原版本；内容不同则 409。",
               "effect_key 只记录副作用意图，不代表外部系统已经执行成功。"),
        example=CURL + " -d '{\"worker_id\":\"worker-01\",\"lease_epoch\":1,\"expected_version\":0,\"checkpoint\":{\"last_completed\":100},\"idempotency_key\":\"cp-001\",\"progress_completed\":100,\"progress_total\":1000}' $API/v1/tasks/<task-uuid>/checkpoints",
    ),
    ("POST", "/v1/tasks/{task_id}/status"): Story(
        persona="任务执行器",
        goal="更新任务生命周期并留下可重放的状态变更事件",
        value="暂停、运行、完成和失败等状态能被独立观察与审计",
        steps=("必须使用专用 tasks:execute API Credential，并同时提交 worker_id 与 lease_epoch。",
               "服务端通过当前租约与 Epoch 校验后才能完成、失败、暂停或取消任务。",
               "状态更新与 memory.task.status.changed Outbox 事件在同一事务提交。"),
        example=CURL + " -d '{\"worker_id\":\"worker-01\",\"lease_epoch\":1,\"status\":\"completed\",\"result\":{\"organized\":1000}}' $API/v1/tasks/<task-uuid>/status",
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
