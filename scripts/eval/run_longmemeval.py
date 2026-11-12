"""Run LongMemEval (ICLR 2025) end to end against a live memory service.

The benchmark asks one question about a user's chat history. Running it here
exercises the product, not a re-implementation:

  1. ingest   every user turn of the question's sessions goes through
              ``POST /v1/memories/process`` with the real server-side extractor
              (a cloud Qwen via the OpenAI-compatible adapter) and with
              ``observed_at`` set to the session date, so the store keeps the
              same knowledge-time order the user lived through;
  2. recall   ``POST /v1/search`` with the question, evaluated at the question
              date, returns the claims the memory store actually holds;
  3. answer   one model call turns the recalled claims into a short answer;
  4. judge    one model call compares that answer with the gold answer.

Reporting separates accuracy per question type because the five abilities
(single-session recall, multi-session aggregation, temporal reasoning,
knowledge update, preference following) fail for different reasons.

Design notes
  * Tenants are isolated by wiping the evaluation tenant before each question,
    so no fact from question A can leak into question B. The wipe needs a DSN.
  * Ingestion runs in chronological order inside a question; questions run
    concurrently because they share nothing.
  * Every question's transcript can be dumped with --trace for inspection.

Usage
  export DASHSCOPE_API_KEY=sk-...
  python scripts/eval/run_longmemeval.py --base-url http://127.0.0.1:8767 \
      --key "$KEY" --dsn "$MEMORY_DSN" --limit 40 --workers 3
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data" / "eval" / "longmemeval_oracle.json"

ANSWER_INSTRUCTION = (
    "You answer questions about a user from the facts they told you earlier. "
    "Each fact is one remembered record: subject | predicate = value, with the "
    "date it was learned. Records come newest first; when two records disagree "
    "about the same thing, the newest one is what the user currently holds. "
    "Answer in one short sentence with the concrete value, no preamble. When the "
    "question asks how many, silently list every matching record first, then "
    "answer with the total or the summed quantity (sum durations and counts; do "
    "not just count records when each record carries a number). Only if no "
    "record mentions anything relevant, reply exactly: "
    "Not in memory.")

JUDGE_INSTRUCTION = (
    "You grade an answer against a reference answer. "
    "Mark it correct when it conveys the same key information as the reference; "
    "minor wording, extra detail, or a different phrasing is still correct. "
    "For preference questions, mark it correct when the answer uses the user's "
    "stated preference, even if the reference lists it as a rubric. "
    "Mark it incorrect when it contradicts the reference, answers a different "
    "question, or says it does not know while the reference has an answer. "
    'Reply with JSON only: {"correct": true|false, "reason": "<= 15 words"}.')


# ---------------------------------------------------------------- utilities
_DATE = re.compile(r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})(?:\s*\([^)]*\))?(?:\s+(\d{1,2}):(\d{2}))?")


def parse_date(raw: str) -> datetime:
    """LongMemEval dates look like '2023/03/15 (Wed) 10:00'."""
    match = _DATE.search(raw or "")
    if not match:
        raise ValueError(f"unparsable date: {raw!r}")
    year, month, day, hour, minute = match.groups()
    return datetime(int(year), int(month), int(day),
                    int(hour or 0), int(minute or 0), tzinfo=timezone.utc)


class Api:
    def __init__(self, base: str, key: str):
        self.client = httpx.Client(base_url=base.rstrip("/"), timeout=180,
                                   trust_env=False,
                                   headers={"Authorization": f"Bearer {key}",
                                            "Content-Type": "application/json"})

    def process(self, *, text: str, source_id: str, observed_at: datetime) -> dict:
        response = self.client.post("/v1/memories/process", json={
            "text": text, "source_id": source_id[:255],
            "observed_at": observed_at.isoformat()})
        if response.status_code >= 400:
            raise RuntimeError(f"process {response.status_code}: {response.text[:200]}")
        return response.json()

    def search(self, query: str, *, known_at: datetime, limit: int = 20) -> list[dict]:
        response = self.client.post("/v1/search", json={
            "query": query[:2000], "limit": limit,
            "known_at": known_at.isoformat(), "valid_at": known_at.isoformat()})
        response.raise_for_status()
        return response.json()["items"]

    def forget_tenant(self, dsn: str, tenant_id: str) -> None:
        """Wipe one tenant so the next question starts from an empty store.

        Each statement runs in its own transaction: an older schema may lack a
        column, and one failed table must not poison the rest of the wipe.
        """
        import psycopg
        statements = [
            ("DELETE FROM memory_claim_search WHERE tenant_id=%s", (tenant_id,)),
            ("DELETE FROM memory_embeddings WHERE memory_id NOT IN "
             "(SELECT id FROM memory_claims)", ()),
            ("DELETE FROM memory_embeddings_512 WHERE memory_id NOT IN "
             "(SELECT id FROM memory_claims)", ()),
            ("DELETE FROM memory_claim_sources WHERE tenant_id=%s", (tenant_id,)),
            ("DELETE FROM memory_outbox WHERE tenant_id=%s", (tenant_id,)),
            ("DELETE FROM memory_events WHERE tenant_id=%s", (tenant_id,)),
            ("DELETE FROM memory_entities WHERE tenant_id=%s "
             "AND starts_with(entity_id,'auto-')", (tenant_id,)),
            ("DELETE FROM memory_claims WHERE tenant_id=%s", (tenant_id,)),
        ]
        with psycopg.connect(dsn, autocommit=True) as conn:
            for sql, params in statements:
                with conn.transaction():
                    try:
                        conn.execute(sql, params)
                    except Exception as exc:  # table shape varies by version
                        print(f"  wipe skipped: {exc.__class__.__name__}: "
                              f"{str(exc)[:80]}", file=sys.stderr)


class Llm:
    """Chat client for the same endpoint the extractor uses.

    Cloud endpoints throttle, so 429/5xx back off exponentially while a 4xx
    such as a bad key fails immediately instead of burning retries.
    """

    RETRYABLE = {408, 409, 425, 429, 500, 502, 503, 504}

    def __init__(self, url: str, model: str, api_key: str | None = None,
                 timeout: float = 180.0):
        self.url = url
        self.model = model
        self.api_key = api_key
        self.requests = 0
        self.client = httpx.Client(timeout=timeout, trust_env=False)

    def chat(self, system: str, user: str, *, max_tokens: int = 400,
             retries: int = 4) -> str:
        payload = {"model": self.model, "temperature": 0,
                   "max_tokens": max_tokens,
                   "messages": [{"role": "system", "content": system},
                                {"role": "user", "content": user}]}
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        last = None
        for attempt in range(retries + 1):
            try:
                response = self.client.post(self.url, json=payload, headers=headers)
                if response.status_code >= 400:
                    snippet = response.text[:200].replace("\n", " ")
                    if response.status_code not in self.RETRYABLE:
                        raise RuntimeError(
                            f"chat {response.status_code}: {snippet}")
                    raise httpx.HTTPStatusError(
                        f"{response.status_code}: {snippet}",
                        request=response.request, response=response)
                self.requests += 1
                return response.json()["choices"][0]["message"]["content"].strip()
            except RuntimeError:
                raise
            except Exception as exc:  # transient network or throttle
                last = exc
                if attempt < retries:
                    time.sleep(min(2 ** attempt * 2, 20))
        raise RuntimeError(f"chat failed after {retries + 1} tries: {last}")


def parse_json(text: str) -> dict:
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise ValueError(f"no JSON object in: {text[:120]!r}")
    return json.loads(match.group(0))


# ---------------------------------------------------------------- pipeline
def ingest(api: Api, item: dict, *, include_assistant: bool = False,
           context_turns: int = 0) -> dict:
    """Feed every user turn of this question's sessions, oldest session first.

    Assistant turns are optional because a memory product can legitimately
    decide that its own replies are not the user's memory — but a benchmark
    category (single-session-assistant) asks exactly about them, so the harness
    must be able to index them when asked.

    A rolling window of recent turns is prepended to each extraction call so
    coreference ("the one in Bandung") resolves against what came before;
    exact-fact deduplication absorbs the overlaps.
    """
    dates = [parse_date(d) for d in item["haystack_dates"]]
    order = sorted(range(len(item["haystack_sessions"])), key=lambda i: dates[i])
    stats = {"turns": 0, "created": 0, "duplicates": 0, "rejected": 0,
             "replaced": 0, "errors": 0}
    for index in order:
        session_date = dates[index]
        window: list[tuple[str, str]] = []
        for turn_index, turn in enumerate(item["haystack_sessions"][index]):
            role = turn.get("role")
            if role != "user" and not (include_assistant and role == "assistant"):
                continue  # the assistant's own words are not the user's memory
            text = (turn.get("content") or "").strip()
            if not text:
                continue
            payload = text
            if context_turns and window:
                context = "\n".join(
                    f"[earlier] {past_role}: {past_text}"
                    for past_role, past_text in window[-context_turns:])
                payload = f"{context}\n[current] {role}: {text}"
            try:
                report = api.process(
                    text=payload,
                    source_id=f"lme:{item['question_id']}:{index}:{turn_index}",
                    observed_at=session_date)
                stats["turns"] += 1
                stats["created"] += len(report.get("created") or [])
                stats["duplicates"] += report.get("duplicates", 0)
                stats["rejected"] += report.get("rejected", 0)
                stats["replaced"] += len(report.get("replaced") or [])
            except Exception:
                stats["errors"] += 1
            window.append((role, text))
    return stats


def render_claims(claims: list[dict]) -> str:
    """Newest-first rendering: a later record supersedes an earlier one."""
    ordered = sorted(claims, key=lambda c: str(c.get("valid_from") or ""), reverse=True)
    lines = []
    for index, claim in enumerate(ordered, 1):
        value = json.dumps(claim.get("value"), ensure_ascii=False, sort_keys=True)
        learned = str(claim.get("valid_from") or "")[:10]
        lines.append(f"{index}. {claim.get('subject_id')} | {claim.get('predicate')}"
                     f" = {value} [{learned}]")
    return "\n".join(lines) if lines else "(no records)"


def answer(llm: Llm, item: dict, claims: list[dict]) -> str:
    prompt = (f"Memory records:\n{render_claims(claims)}\n\n"
              f"Question: {item['question']}\n"
              f"Answer:")
    return llm.chat(ANSWER_INSTRUCTION, prompt, max_tokens=200)


def judge(llm: Llm, item: dict, model_answer: str) -> dict:
    prompt = (f"Question: {item['question']}\n"
              f"Reference answer: {item['answer']}\n"
              f"Answer to grade: {model_answer}\n")
    raw = llm.chat(JUDGE_INSTRUCTION, prompt, max_tokens=120)
    try:
        verdict = parse_json(raw)
        return {"correct": bool(verdict.get("correct")),
                "reason": str(verdict.get("reason", ""))[:120]}
    except Exception:
        lowered = raw.lower()
        return {"correct": ("true" in lowered and "false" not in lowered),
                "reason": raw[:120]}


def run_question(api: Api, llm: Llm, item: dict, *, dsn: str | None,
                 tenant: str, trace: bool, judge_llm: Llm | None = None,
                 settle: float = 0.0, search_limit: int = 30,
                 include_assistant: bool = False, context_turns: int = 0) -> dict:
    started = time.time()
    if dsn:
        api.forget_tenant(dsn, tenant)
    ingest_stats = ingest(api, item, include_assistant=include_assistant,
                          context_turns=context_turns)
    if settle:
        time.sleep(settle)  # let the outbox worker materialise the FTS projection
    question_date = parse_date(item["question_date"])
    claims = api.search(item["question"], known_at=question_date,
                        limit=search_limit)
    model_answer = answer(llm, item, claims)
    verdict = judge(judge_llm or llm, item, model_answer)
    result = {
        "question_id": item["question_id"],
        "question_type": item["question_type"],
        "question": item["question"],
        "gold": item["answer"],
        "answer": model_answer,
        "correct": verdict["correct"],
        "reason": verdict["reason"],
        "recalled": len(claims),
        "ingest": ingest_stats,
        "seconds": round(time.time() - started, 1),
    }
    if trace:
        result["claims"] = [
            {"subject": c.get("subject_id"), "predicate": c.get("predicate"),
             "value": c.get("value"), "valid_from": c.get("valid_from")}
            for c in claims]
    return result


def isolation_error(workers: int, shard_count: int, allow_shared: bool) -> str | None:
    """Concurrent questions must never share a tenant.

    Each question wipes its tenant before ingesting, so two questions in flight
    on one tenant erase each other's memories mid-run: recall silently collapses
    and the score is meaningless rather than merely low.
    """
    if workers > 1 and shard_count == 1 and not allow_shared:
        return ("--workers > 1 on a single tenant is unsafe: every question wipes "
                "the tenant first, so concurrent questions delete each other's "
                "memories. Use --workers 1, add --instances url|tenant shards "
                "(one server process per tenant), or pass --allow-shared-tenant.")
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--key", required=True)
    parser.add_argument("--instances", default=None,
                        help="extra isolated shards as url|tenant,url|tenant. Each "
                             "shard must be its own process with its own tenant; "
                             "concurrent workers on one tenant wipe each other.")
    parser.add_argument("--dsn", default=None,
                        help="Postgres DSN used to wipe the evaluation tenant "
                             "between questions; without it questions are not isolated")
    parser.add_argument("--tenant", default="lme-eval")
    parser.add_argument("--llm-url", default=os.getenv(
        "LME_LLM_URL",
        "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"))
    parser.add_argument("--llm-model", default=os.getenv("LME_LLM_MODEL", "qwen-plus"))
    parser.add_argument("--llm-key", default=os.getenv("DASHSCOPE_API_KEY"))
    parser.add_argument("--judge-model", default=os.getenv("LME_JUDGE_MODEL"),
                        help="separate model for grading; defaults to --llm-model")
    parser.add_argument("--limit", type=int, default=40)
    parser.add_argument("--data", default=str(DATA),
                        help="question set to read; defaults to the oracle haystack")
    parser.add_argument("--ids-file", default=None,
                        help="pin the exact subset of a previous run: a JSON list of "
                             "question ids, or a previous report whose "
                             "results[].question_id are reused")
    parser.add_argument("--types", default=None,
                        help="comma separated question types to include")
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--settle", type=float, default=2.0,
                        help="seconds to wait after ingest before searching. The FTS "
                             "projection is delivered by the outbox worker, so an "
                             "immediate read can miss rows that are about to appear")
    parser.add_argument("--search-limit", type=int, default=30,
                        help="claims handed to the answering model; counting and "
                             "aggregation questions need the full set, not the top 30")
    parser.add_argument("--include-assistant", action="store_true",
                        help="index assistant turns too; the single-session-assistant "
                             "category is otherwise structurally unwinnable")
    parser.add_argument("--context-turns", type=int, default=0,
                        help="prepend this many recent turns to each extraction call "
                             "so coreference resolves; duplicates are absorbed by "
                             "exact-fact deduplication")
    parser.add_argument("--allow-shared-tenant", action="store_true",
                        help="permit workers > 1 against a single tenant. Off by "
                             "default because each question wipes its tenant first, "
                             "so concurrent questions delete each other's memories")
    parser.add_argument("--out", default="data/eval/longmemeval_report.json")
    parser.add_argument("--trace", action="store_true")
    args = parser.parse_args()

    items = json.loads(Path(args.data).read_text(encoding="utf-8"))
    if args.types:
        wanted = {t.strip() for t in args.types.split(",") if t.strip()}
        items = [i for i in items if i["question_type"] in wanted]
    if args.ids_file:
        payload = json.loads(Path(args.ids_file).read_text(encoding="utf-8"))
        ids = ([r["question_id"] for r in payload.get("results", [])]
               if isinstance(payload, dict) else list(payload))
        order = {qid: k for k, qid in enumerate(ids)}
        items = [i for i in items if i["question_id"] in order]
        items.sort(key=lambda i: order[i["question_id"]])
        missing = len(order) - len(items)
        if missing:
            print(f"warning: {missing} pinned ids are absent from {args.data}",
                  file=sys.stderr)
    elif args.limit:
        # stratified: keep the natural type mix instead of the first N records
        buckets: dict[str, list[dict]] = {}
        for item in items:
            buckets.setdefault(item["question_type"], []).append(item)
        picked, index = [], 0
        while len(picked) < min(args.limit, len(items)):
            for name in sorted(buckets):
                if index < len(buckets[name]) and len(picked) < args.limit:
                    picked.append(buckets[name][index])
            index += 1
            if index > 500:
                break
        items = picked

    shards: list[tuple[Api, str]] = [(Api(args.base_url, args.key), args.tenant)]
    for spec in (args.instances or "").split(","):
        spec = spec.strip()
        if not spec:
            continue
        url, tenant = spec.split("|", 1)
        shards.append((Api(url.strip(), args.key), tenant.strip()))
    if args.workers > 1 and len(shards) == 1 and not args.allow_shared_tenant:
        parser.error(isolation_error(args.workers, len(shards),
                                     args.allow_shared_tenant))
    llm = Llm(args.llm_url, args.llm_model, args.llm_key)
    judge_llm = (llm if not args.judge_model or args.judge_model == args.llm_model
                 else Llm(args.llm_url, args.judge_model, args.llm_key))
    results: list[dict] = []
    lock = threading.Lock()
    started = time.time()

    def work(pair) -> None:
        index, item = pair
        api, tenant = shards[index % len(shards)]
        try:
            row = run_question(api, llm, item, dsn=args.dsn,
                               tenant=tenant, trace=args.trace,
                               judge_llm=judge_llm, settle=args.settle,
                               search_limit=args.search_limit,
                               include_assistant=args.include_assistant,
                               context_turns=args.context_turns)
        except Exception as exc:
            row = {"question_id": item["question_id"],
                   "question_type": item["question_type"],
                   "question": item["question"], "gold": item["answer"],
                   "answer": "", "correct": False,
                   "reason": f"harness error: {exc}"[:160], "recalled": 0,
                   "ingest": {}, "seconds": 0}
        with lock:
            results.append(row)
            done = len(results)
            mark = "ok " if row["correct"] else "miss"
            print(f"[{done}/{len(items)}] {mark} {row['question_type'][:22]:22} "
                  f"recall={row['recalled']:2} {row['question'][:52]}", flush=True)

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        list(pool.map(work, list(enumerate(items))))

    by_type: dict[str, dict] = {}
    for row in results:
        bucket = by_type.setdefault(row["question_type"],
                                    {"n": 0, "correct": 0, "recall": 0})
        bucket["n"] += 1
        bucket["correct"] += int(bool(row["correct"]))
        bucket["recall"] += row["recalled"]
    for name, bucket in by_type.items():
        bucket["accuracy"] = bucket["correct"] / bucket["n"]
        bucket["avg_recalled"] = bucket["recall"] / bucket["n"]

    total = len(results)
    summary = {
        "benchmark": "LongMemEval (oracle haystack)",
        "questions": total,
        "correct": sum(1 for r in results if r["correct"]),
        "accuracy": (sum(1 for r in results if r["correct"]) / total) if total else 0.0,
        "by_type": by_type,
        "turns_ingested": sum((r.get("ingest") or {}).get("turns", 0) for r in results),
        "claims_created": sum((r.get("ingest") or {}).get("created", 0) for r in results),
        "ingest_errors": sum((r.get("ingest") or {}).get("errors", 0) for r in results),
        "seconds": round(time.time() - started, 1),
        "llm_model": args.llm_model,
        "judge_model": args.judge_model or args.llm_model,
        "question_set": Path(args.data).name,
        "pinned_subset": Path(args.ids_file).name if args.ids_file else None,
        "search_limit": args.search_limit,
        "include_assistant": args.include_assistant,
        "context_turns": args.context_turns,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    report = {"summary": summary, "results": results}
    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")

    print("\n=== LongMemEval ===")
    print(f"questions {summary['questions']}  accuracy {summary['accuracy']:.3f}  "
          f"({summary['correct']}/{summary['questions']})  "
          f"turns {summary['turns_ingested']}  claims {summary['claims_created']}  "
          f"errors {summary['ingest_errors']}  {summary['seconds']}s")
    for name in sorted(by_type):
        b = by_type[name]
        print(f"  {name:26} {b['accuracy']:.3f}  n={b['n']:3}  "
              f"avg_recalled={b['avg_recalled']:.1f}")
    print(f"report -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
