"""Score the live service against data/eval/gold_home_zh.jsonl.

Three measurements, deliberately separated so a low score cannot hide behind a
strong one:

  extraction  P/R/F1 on A (in-vocabulary) + B (negation) — values compared
              deeply, not just predicates. C contributes false positives.
  coverage    share of D (out-of-vocabulary domain facts) that produced any
              claim at all. Not part of F1: the shipped pack has no vocabulary
              for them, so mixing them in would flatter or punish by accident.
  update      share of E pairs where the second write retires the first
              (CONTRADICTION / SUPERSEDES with a replaced id).
  retrieval   Recall@5 and MRR on seeded facts, query -> expected subject.

Every run is destructive to the target tenant: it soft-forgets subjects between
items. Point it at a throwaway tenant instance, never at demo-home.

Usage:
  python scripts/eval/run_eval.py --base-url http://127.0.0.1:8767 --key "$KEY"
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
GOLD = ROOT / "data" / "eval" / "gold_home_zh.jsonl"

SUBJECTS = ["主卧", "次卧", "卧室", "客厅", "书房", "厨房", "卫生间", "浴室",
            "阳台", "玄关", "儿童房"]


class Api:
    def __init__(self, base: str, key: str):
        self.c = httpx.Client(base_url=base.rstrip("/"), timeout=20,
                              trust_env=False,
                              headers={"Authorization": f"Bearer {key}"})

    def call(self, method: str, path: str, **kw):
        r = self.c.request(method, path, **kw)
        if r.status_code >= 400:
            raise RuntimeError(f"{method} {path} -> {r.status_code} {r.text[:200]}")
        return r.json() if r.text else None

    def process(self, text: str, source_id: str) -> dict:
        return self.call("POST", "/v1/memories/process",
                         json={"text": text, "source_id": source_id})

    def claims(self, subject: str, include_superseded: bool = False) -> list[dict]:
        q = {"subject_id": subject, "limit": 50}
        if include_superseded:
            q["include_superseded"] = "true"
        return self.call("GET", "/v1/memories", params=q)["items"]

    def forget_subject(self, subject: str) -> None:
        try:
            self.call("DELETE", "/v1/memories", params={"subject_id": subject})
        except RuntimeError:
            pass

    def search(self, query: str, limit: int = 5) -> list[dict]:
        return self.call("POST", "/v1/search",
                         json={"query": query, "limit": limit})["items"]


def load_gold() -> list[dict]:
    return [json.loads(l) for l in GOLD.read_text(encoding="utf-8").splitlines() if l.strip()]


def fact_key(pred: str, value) -> str:
    return pred + "=" + json.dumps(value, sort_keys=True, ensure_ascii=False)


def extracted_facts(api: Api, res: dict, source_id: str) -> list[tuple[str, dict]]:
    """Return (predicate, value) actually stored by this write."""
    out: list[tuple[str, dict]] = []
    for rel in res.get("relations", []):
        mid = rel.get("memory_id")
        if not mid:
            continue
        for item in api.claims(rel["subject_id"], include_superseded=True):
            if item["id"] == mid:
                out.append((item["predicate"], item["value"]))
                break
    return out


def run_extraction(api: Api, items: list[dict]) -> dict:
    tp = fp = fn = 0
    per_kind: dict[str, dict] = defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0, "n": 0})
    details = []
    for it in items:
        kind = it["kind"]
        if kind not in ("A", "B", "C"):
            continue
        if kind != "C" and not it["gold"]:
            continue
        src = "eval:" + it["id"]
        res = api.process(it["text"], src)
        got = extracted_facts(api, res, src)
        gold = {(g["predicate"], json.dumps(g["value"], sort_keys=True, ensure_ascii=False))
                for g in it["gold"]}
        gotk = {(p, json.dumps(v, sort_keys=True, ensure_ascii=False)) for p, v in got}
        hit = gold & gotk
        item_tp, item_fp, item_fn = len(hit), len(gotk - gold), len(gold - gotk)
        tp += item_tp; fp += item_fp; fn += item_fn
        k = per_kind[kind]
        k["tp"] += item_tp; k["fp"] += item_fp; k["fn"] += item_fn; k["n"] += 1
        if item_fp or item_fn:
            details.append({"id": it["id"], "kind": kind, "text": it["text"],
                            "gold": sorted(x[0] for x in gold),
                            "got": sorted(x[0] for x in gotk)})
        for subj in {r["subject_id"] for r in res.get("relations", [])}:
            api.forget_subject(subj)
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * p * r / (p + r) if p + r else 0.0
    return {"precision": p, "recall": r, "f1": f1, "tp": tp, "fp": fp, "fn": fn,
            "by_kind": {k: dict(v) for k, v in per_kind.items()},
            "errors": details[:40]}


def run_coverage(api: Api, items: list[dict]) -> dict:
    covered = 0
    total = 0
    for it in items:
        if it["kind"] != "D":
            continue
        total += 1
        res = api.process(it["text"], "eval:" + it["id"])
        if res.get("relations"):
            covered += 1
        for subj in {r["subject_id"] for r in res.get("relations", [])}:
            api.forget_subject(subj)
    return {"covered": covered, "total": total,
            "rate": covered / total if total else 0.0}


def run_updates(api: Api, items: list[dict]) -> dict:
    pairs: dict[str, list[dict]] = defaultdict(list)
    for it in items:
        if it["kind"] == "E":
            pairs[it["text"]].append(it)
    ok = 0
    total = 0
    misses = []
    e_items = [i for i in items if i["kind"] == "E"]
    for i in range(0, len(e_items), 2):
        before, after = e_items[i], e_items[i + 1]
        total += 1
        api.process(before["text"], "eval:" + before["id"])
        res = api.process(after["text"], "eval:" + after["id"])
        rels = res.get("relations", [])
        retired = bool(res.get("replaced")) or any(
            r.get("relation") in ("CONTRADICTION", "SUPERSEDES") for r in rels)
        if retired:
            ok += 1
        else:
            misses.append({"before": before["text"], "after": after["text"],
                           "relations": [r.get("relation") for r in rels],
                           "replaced": res.get("replaced")})
        for subj in {r["subject_id"] for r in rels}:
            api.forget_subject(subj)
    return {"ok": ok, "total": total, "rate": ok / total if total else 0.0,
            "misses": misses}


def run_retrieval(api: Api, items: list[dict], n: int = 20) -> dict:
    """Two query shapes, because the lexical channel is a literal substring match.

    subject   "主卧"          — what the demo uses and what the channel supports
    composite "主卧温度"      — natural phrasing; needs a tokenising or vector
                                channel, so measuring it exposes the gap
    """
    seeds = [it for it in items if it["kind"] == "A"][:n]
    cases = []
    for it in seeds:
        api.process(it["text"], "eval:" + it["id"])
        subj = next((s for s in SUBJECTS if s in it["text"]), None)
        word = {"temperature_preference": "温度", "noise_preference": "安静",
                "light_preference": "灯光"}[it["gold"][0]["predicate"]]
        if subj:
            cases.append((subj, word, subj))

    def score(kind: str) -> dict:
        hits = 0
        rr = 0.0
        misses = []
        for subj_q, word, subj in cases:
            q = subj_q if kind == "subject" else subj_q + word
            got = api.search(q, limit=5)
            rank = next((i + 1 for i, g in enumerate(got)
                         if g.get("subject_id") == subj), None)
            if rank:
                hits += 1
                rr += 1.0 / rank
            else:
                misses.append({"query": q, "expected": subj,
                               "got": [g.get("subject_id") for g in got]})
        n_cases = len(cases)
        return {"queries": n_cases,
                "recall_at_5": hits / n_cases if n_cases else 0.0,
                "mrr": rr / n_cases if n_cases else 0.0,
                "misses": misses[:10]}

    out = {"subject_query": score("subject"), "composite_query": score("composite")}
    for subj in {c[2] for c in cases}:
        api.forget_subject(subj)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--key", required=True)
    ap.add_argument("--json-out", default="")
    args = ap.parse_args()

    api = Api(args.base_url, args.key)
    caps = api.call("GET", "/v1/capabilities")
    if not caps.get("extraction", {}).get("rule_pack", True):
        print("warning: instance may not have a rule pack configured")

    items = load_gold()
    print(f"gold: {len(items)} items\n")

    print("1/4 extraction (A+B, C as false-positive control) ...")
    ext = run_extraction(api, items)
    print("2/4 coverage (D, out-of-vocabulary) ...")
    cov = run_coverage(api, items)
    print("3/4 updates (E pairs) ...")
    upd = run_updates(api, items)
    print("4/4 retrieval (Recall@5 / MRR) ...")
    ret = run_retrieval(api, items)

    print("\n=== scorecard ===")
    print(f"extraction  P={ext['precision']:.3f}  R={ext['recall']:.3f}  F1={ext['f1']:.3f}"
          f"  (tp={ext['tp']} fp={ext['fp']} fn={ext['fn']})")
    for k in sorted(ext["by_kind"]):
        v = ext["by_kind"][k]
        print(f"  kind {k}: n={v['n']:3d} tp={v['tp']:3d} fp={v['fp']:3d} fn={v['fn']:3d}")
    print(f"coverage    {cov['covered']}/{cov['total']} = {cov['rate']:.1%} (vocabulary gap)")
    print(f"updates     {upd['ok']}/{upd['total']} = {upd['rate']:.1%}")
    sq, cq = ret["subject_query"], ret["composite_query"]
    print(f"retrieval   subject queries   Recall@5={sq['recall_at_5']:.3f}  MRR={sq['mrr']:.3f}")
    print(f"            composite queries Recall@5={cq['recall_at_5']:.3f}  MRR={cq['mrr']:.3f}")

    report = {"extraction": ext, "coverage": cov, "updates": upd, "retrieval": ret}
    out = Path(args.json_out) if args.json_out else ROOT / "data" / "eval" / "report_home_zh.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nreport -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
