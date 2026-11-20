# Agent Memory Core · 本地产品体验

这份指南把仓库已有的 API、PostgreSQL、Python SDK、Console 和 Observatory 串成一条可重复的体验路径。它启动的是**本机真实服务**，不是静态截图，也不使用云端模型。

## 1. 环境要求

- Git
- Docker Desktop / Docker Engine，支持 Docker Compose v2
- Python 3.10+
- 可用的本机浏览器

建议先从仓库根目录执行：

```bash
python -m pip install -e '.[api,postgres]'
```

## 2. 一条命令启动并体验

```bash
python scripts/demo_local.py
```

默认使用 `lite` 本地配置。脚本会：

1. 通过 `scripts/launch_local.py` 启动 PostgreSQL + FastAPI。
2. 等待真实 API readiness。
3. 通过公开 Python SDK 执行真实写入、修订、当前态读取、历史读取、检索、证据对象往返和事件读取。
4. 调用仓库原有的 `scripts/seed_demo.py --seed`，通过公开文本处理 API 写入七轮中文示例故事；不调用 `--clean`，不会自动软删除任何记录。
5. 运行成功后打开 Console、Observatory 和 API 手册三个页面。

成功标志：

```text
LOCAL_MEMORY_RUNTIME_ACCEPTED
AGENT_MEMORY_CORE_LOCAL_DEMO_READY
```

如果七轮中文故事中有任何一轮 HTTP 请求失败，启动脚本现在会以非零状态退出，并且**不会**打印 `AGENT_MEMORY_CORE_LOCAL_DEMO_READY`。HTTP 200 但未抽取事实则仅显示 `no facts`，不会伪装成写入成功。

这条体验路径会在本地默认租户中写入带随机 subject 前缀的 smoke 记录，以及 source ID 以 `demo:script:` 开头的中文示例故事，并保留在本地数据库供界面检查；不会自动删除。是否成功抽取事实以 seeder 的逐轮输出为准，不把没有抽取出来的文本伪装成记忆。它不调用云端模型，也不代表中文抽取质量、生产安全或负载验收已经通过。

## 3. 打开哪些页面

| 页面 | 地址 | 用途 |
|---|---|---|
| Memory Console | http://127.0.0.1:8765/dashboard/ | 自然语言入账、事实台账、历史版本、混合检索、实例能力 |
| Memory Observatory | http://127.0.0.1:8765/dashboard/observatory.html | 记忆领域可视化、时间回放、证据检查、Live API 读写体验 |
| API 手册 | http://127.0.0.1:8765/docs | API 契约与交互式接口说明 |
| Swagger UI | http://127.0.0.1:8765/docs/swagger | OpenAPI 操作入口 |

Console 首次连接时，从仓库根目录的 `.env.local` 读取 `MEMORY_API_KEY`，粘贴到本机页面。不要把密钥放进 URL、截图、Issue 或 Git 提交。

Observatory 首次打开默认是 **Demo**，显示明确标记的合成样本。点击右上角连接按钮，再输入同一个本地 API key，才会切换到 **Live**。Demo 数据不是数据库记录；Live 数据才来自当前本机 API。

## 4. 建议按这个顺序体验前端

1. **Console：** 输入 `.env.local` 中的本地 key，进入「记忆台账」。先查看七轮中文故事写入的事实，再用页面内置示例提交一句新陈述。
2. **纠正与历史：** 对同一主体、同一谓词提交修正，切换「有效 / 含历史」，打开一条记录检查来源、替换关系和双时态。
3. **Observatory：** 默认先看 SYNTHETIC Demo；连接本地 key 后切换到 LIVE。尝试领域筛选、搜索、选择事实查看证据、拖动时间轴、导出快照。
4. **Live 写操作：** Observatory 的 Live 模式提供写入、修订、软遗忘和可选 Temporal 验证入口。每次操作都应观察 API 返回与重新读取结果，不要把画面动画当作事实或检索质量指标。
5. **API：** 打开 `/docs`，对照相同操作的 REST 请求和响应。

Lite profile 默认使用本地 `rule` 抽取器。它适合让界面和 API 跑通，不代表本地 LLM 的理解质量；模型质量应使用独立标注语料和评测脚本测量。

## 5. 想体验身份和长任务

```bash
python scripts/launch_local.py down
python scripts/launch_local.py up --profile extended --identity
```

这会增加本地 Keycloak 身份服务和 Temporal 开发环境。它适合体验 OIDC、Workspace 和长任务工作流；不是生产身份部署模板。

## 6. 查看运行状态与停止

```bash
python scripts/launch_local.py status
python scripts/launch_local.py down
```

如果启动失败：

```bash
docker compose ps
docker compose logs --tail=150
```

## 7. 安全边界

- 默认 API 仅绑定 `127.0.0.1`，不应直接映射到公网。
- `.env.local` 是本机生成的私密配置，不要提交。
- 不要将真实租户凭据输入 GitHub Pages 静态演示。公开静态演示只用于合成数据浏览。
- Lite 是减少可选检索通道的开发配置，不是资源占用或生产安全认证。
- smoke 通过只证明本地 API/SDK 生命周期可用，不证明真实模型抽取质量、跨租户生产隔离、故障恢复或高并发能力。
