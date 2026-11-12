# 评估数据（data/eval）

这里有两类东西，用途完全不同，不要混着看。

## 一、LongMemEval（外部基准，已跑通）

`longmemeval_oracle.json` — [LongMemEval](https://github.com/xiaowu0162/LongMemEval)（ICLR 2025）oracle 版本，
500 题，每题带证据会话与 `question_date`。它是"给一个用户的聊天史，问一个关于他的问题"，正好压这套服务的核心：
记得住、改得对、算得清、答得出。

### 跑法

```bash
# 1) 服务器上：三个独立分片实例（各自租户 + 端口），共享同一个真实抽取器
bash start_eval.sh 8767 lme-eval
bash start_eval.sh 8768 lme-eval-2
bash start_eval.sh 8769 lme-eval-3

# 2) 跑分（--dsn 用于每题之间清空该分片租户，保证题目互不污染）
python scripts/eval/run_longmemeval.py \
  --base-url http://127.0.0.1:8767 \
  --instances 'http://127.0.0.1:8768|lme-eval-2,http://127.0.0.1:8769|lme-eval-3' \
  --key "$MEMORY_API_KEY" --dsn "$MEMORY_DSN" \
  --llm-url http://127.0.0.1:8101/v1/chat/completions \
  --limit 24 --workers 3 --out data/eval/longmemeval_24.json
```

四步全走产品自己的代码路径，没有为评测另写一套：

| 阶段 | 用的东西 |
|---|---|
| 灌入 | `POST /v1/memories/process`，每轮用户发言一次，`observed_at` = 该会话日期（真实抽取器：Qwen2.5-7B） |
| 回忆 | `POST /v1/search`，`known_at`/`valid_at` = `question_date`，取 30 条 |
| 作答 | 同一模型，只给检索到的记忆，要求一句话给具体值 |
| 判分 | 同一模型对照 gold answer 判 correct/incorrect（LongMemEval 官方也用 LLM judge） |

### 为什么要三个实例

租户是绑在进程上的（`MEMORY_TENANT_ID`），而每题之间要清库保证隔离。
一个进程一个租户 → 并发 worker 会互相把对方的数据清掉（早期版本就踩了这个坑，recall 时有时无）。
三个进程三个租户，worker 按题号取模分配，才能真并行。

### 已知边界（读数字前先看）

- **只灌用户发言**，不灌助手回复。因此 `single-session-assistant`（问"你上次说过的…"）本质答不了，
  这类分数低是设计边界，不是检索坏了。
- 抽取器是本地 7B，不是 GPT-4o 级模型：数值改口、隐含因果经常抽歪（例如把"我跑进了 25:50"抽成
  "想跑到 25:50"）。答错多半错在这一步，报告里可逐题复核。
- 判分用一个 7B 模型，粗于官方 rubric grader；偏好类（rubric 型 gold）会偏严。

### 2026-11-12 基线（24 题分层抽样）

```
准确率 20.8% (5/24)   灌入 258 轮 → 477 claims   抽取失败 3 轮   882s
  single-session-user      75%   知识更新 25%   时序推理 25%
  multi-session             0%   助手侧回忆 0%   偏好跟随 0%
```

19 道错题的归因（明细见 `longmemeval_24.md`）：
6 题检索空手而归（题面词与 claim 对不上，其中 3 题是"只灌用户侧"的设计边界）、
6 题检索到了却仍拒答（7B 长上下文过度保守）、7 题答了但值不对（多会话计数与知识更新为主，
根因是逐轮抽取丢上下文）。

这份数字衡量的是**整条链路**：本地 7B 抽取 + 词法检索 + 7B 作答。它不是这套服务的能力上限，
而是"小模型 + 纯词法 + 逐轮抽取"这套组合的上限；换抽取器、换检索通道（向量）、或按会话整体灌入，
都会直接改变这个数——所以每次换都要重跑，不要拿这个数当产品结论。

## 二、中文黄金集（本服务自己能跑出数字的部分）

`gold_home_zh.jsonl` —— 200 条，按 `home-zh` 规则包的**契约**（不是它的正则）手写，分五类：

| kind | 条数 | 内容 | 怎么计分 |
|---|---|---|---|
| A | 90 | 词表内正例（温度 / 安静 / 灯光亮暗） | 计入 P/R/F1 |
| B | 20 | 否定陷阱（"别太亮""不要吵""不要开到28度"） | 计入 P/R/F1，抽反即 FP |
| C | 30 | 与记忆无关的日常噪声 | 抽出任何事实即 FP |
| D | 30 | 领域内但词表外（网速、窗帘、漏水…） | **不计入 F1**，单独报覆盖率（词表缺口） |
| E | 30（15 对） | 改口对（同一 subject+predicate 换值） | 单独报改口判定率 |

D 类不计入 F1 是有意的：规则包本来就没有这些谓词，混进去会让分数虚高或虚低，
掩盖真正要回答的问题——"现有词表覆盖多少真实家居话语"。

### 跑分

```bash
# 1) 在服务器上起一个一次性租户实例（切勿指向 demo-home，脚本会删数据）
MEMORY_TENANT_ID=eval-gold ... uvicorn agent_memory_core.server:app --port 8767

# 2) 本地
python scripts/eval/build_gold_home_zh.py          # 重新生成黄金集（可选）
python scripts/eval/run_eval.py \
  --base-url http://127.0.0.1:18767 --key "$MEMORY_API_KEY"
```

输出 `data/eval/report_home_zh.json`，含每类明细与失败样例。

### 2026-11-12 基线（A100，rule 抽取器）

```
extraction  P=0.960  R=0.882  F1=0.919   (tp=97 fp=4 fn=13)
  kind A  n=90  tp=83 fp=0 fn=7      # 词表内正例
  kind B  n=20  tp=14 fp=4 fn=6      # 否定陷阱最弱
  kind C  n=30  tp=0  fp=0 fn=0      # 噪声零误抽
coverage    0/30 = 0.0%              # 词表外领域事实完全抽不出
updates     11/15 = 73.3%            # 4 对改口未关闭旧值
retrieval   subject queries   Recall@5=1.000  MRR=1.000
            composite queries Recall@5=0.000  MRR=0.000
```

已知损失点（均可复现，见 report 的 `errors` / `misses`）：

1. `灯` 相关的两个正则要求"名词+形容词"成对出现：`次卧开灯`、`书房开灯`、`主卧亮度调高`、`厨房照明暗一些`、`阳台照明亮一些` 抽不出。
2. 否定变体覆盖不足：`不要那么亮`、`别出声`、`不要有声音` 漏抽；`我不喜欢吵闹` 被抽成 `dislikes` 而非 `noise_preference`（极性判断仍会误伤）。
3. 带纠正的温度句取错数字：`不要开到28度，26度就够了` 抽成 28（应取 26）。
4. 撤回类表达（`不用静音了`、`正常模式就行`）被当作 DUPLICATE，旧值不关闭——系统目前没有"取消偏好"的语义。
5. 复合查询（`主卧温度`）在纯词法通道下召回为 0：词法只做整句子串匹配；需要分词通道或向量通道。
