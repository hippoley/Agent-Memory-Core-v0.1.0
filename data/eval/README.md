# 评估数据（data/eval）

这里有两类东西，用途完全不同，不要混着看。

## 一、外部基准（英文，会话级，暂不能直接给本服务打分）

| 文件 | 来源 | 规模 | 许可 | 它测什么 |
|---|---|---|---|---|
| `longmemeval_oracle.json` | [LongMemEval](https://github.com/xiaowu0162/LongMemEval)（ICLR 2025） | 500 题，每题若干证据会话 + turn 级 `has_answer` 标注 | MIT | 五种长期记忆能力：信息抽取、跨会话推理、时序推理、知识更新、拒答 |
| `locomo10.json` | [LoCoMo](https://github.com/snap-research/locomo)（ACL 2024） | 10 段超长对话，1986 道 QA，evidence 指向对话轮 | CC BY-NC 4.0（**仅限非商用**） | 单跳/时序/多跳/开放域/对抗问答 |

**为什么现在跑不了分**：两者都是「整段英文对话 → 自然语言问答」，评测要靠 LLM 生成答案 + LLM judge。
本服务存的是**中文原子事实**（subject / predicate / value JSON），当前部署用的是 `home-zh` 规则抽取器，
不识别英文、也不做问答生成。硬灌进去只能得到 0 分，那是测法的问题，不是系统的水平。

**什么时候能用**：配置 `MEMORY_EXTRACTOR_PROVIDER=openai-compat` 接上真实抽取模型后，
把每个 `has_answer: true` 的 turn 抽成 claims 入库，再用 `answer_session_ids` 算 Recall@k，
用答案生成 + LLM judge 算准确率。这条路子要写一个 loader，属于下一步工作。

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
