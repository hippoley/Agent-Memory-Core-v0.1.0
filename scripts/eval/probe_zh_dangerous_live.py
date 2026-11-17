"""Opt-in REAL-Qwen P0.5 Chinese hazardous-memory semantic evaluation.

Runs in an isolated API tenant with real PostgreSQL and the configured provider.
Every case has canonical seed ID(s), model attestation, final state, audit trail,
and explicit fail-closed verdict. No fixture or mocked model output.
This is a *frozen small adversarial probe*, not a statistical quality benchmark.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4
import httpx
from probe_grounded_atomic_live import require_loopback, assess_model_attestation

def canon(rows):
    return sorted(
        [(r["id"], r["subject_id"], r["predicate"], r["value"],
          bool(r.get("superseded_at"))) for r in rows],
        key=lambda x: x[0])

def run(args):
    if not args.ack_disposable or not args.key or len(args.key) < 24:
        raise ValueError("Requires explicit disposable-tenant acknowledgement and token")
    base = require_loopback(args.base_url)
    cases = []
    prefix = uuid4().hex[:12]
    with httpx.Client(base_url=base, timeout=120, trust_env=False,
                      headers={"Authorization": "Bearer " + args.key}) as client:
        def call(method, path, **kw):
            response = client.request(method, path, **kw)
            response.raise_for_status()
            return response.json()
        if call("GET","/v1/memories",
                params={"limit": 1,"include_superseded":"true"})["items"]:
            raise RuntimeError("Tenant is not unused; refusing to mutate")
        def read(subject, history=False):
            return call("GET","/v1/memories",params={"subject_id":subject,"limit":100,
                              "include_superseded":str(history).lower()})["items"]
        def seed(subject, pred, value, source):
            return call("POST","/v1/memories",json={
                "subject_id":subject,"predicate":pred,"value":value,"source_id":source
            })["id"]
        def execute(case_id, prompt, context, subjects, check):
            previous={sub:read(sub,True) for sub in subjects}
            before={sub:read(sub) for sub in subjects}
            # An actual search preflight must find canonical evidence; do not
            # misreport model hallucination if search cannot find existing facts.
            search=call("POST","/v1/search",json={"query":prompt,"limit":20})
            evidence_ids={x["memory_id"] for x in search.get("items",[])}
            required={r["id"] for sub in subjects for r in before[sub]}
            preflight=required.issubset(evidence_ids)
            entry={"id":case_id,"prompt":prompt,"prior_turns":context,
                   "subjects":subjects,"seed_ids":sorted(required),
                   "grounding_preflight":preflight}
            if not preflight:
                entry.update(passed=False,failure_stage="grounding_retrieval")
                cases.append(entry)
                return
            try:
                proc=call("POST","/v1/memories/process",json={
                    "text":prompt,"source_id":"p0-zh:"+case_id+":"+prefix,
                    "context_turns":context,"ground_with_memory":True,
                    "atomic_batch":True})
                current={sub:read(sub) for sub in subjects}
                history={sub:read(sub,True) for sub in subjects}
                identity=assess_model_attestation(
                    proc.get("model_call_attestation") or {},
                    model=args.model,provider="openai-compatible")
                checks=check(before,current,previous,history,proc)
                checks.update(identity)
                checks["atomic_response"]=proc.get("atomic_batch") is True
                entry.update(checks=checks,passed=all(x is True for x in checks.values()),
                             model_call_attestation=proc.get("model_call_attestation"),
                             process={"created":proc.get("created"),"replaced":proc.get("replaced"),
                                      "rejected":proc.get("rejected")},
                             current=current,history=history)
            except httpx.HTTPStatusError as ex:
                entry.update(passed=False,failure_stage="http",
                             status=ex.response.status_code,
                             endpoint_path=ex.request.url.path)
            cases.append(entry)

        neg="room-" + prefix + "-neg"
        id_neg=seed(neg,"temperature_preference",{"celsius":28},"p0:neg:old")
        def neg_check(b,c,p,h,proc):
            old=next((x for x in h[neg] if x["id"]==id_neg),None)
            return {"new_26_current":len(c[neg])==1 and c[neg][0]["value"]=={"celsius":26},
                    "old_28_retired":bool(old and old.get("superseded_at")),
                    "no_28_current":all(x["value"]!={"celsius":28} for x in c[neg])}
        execute("negation",f"房间 {neg} 不是要28度，现在明确改成26度，不要记成28度。",
                [f"房间 {neg} 原来设置28度。"],[neg],neg_check)

        ret="supplier-" + prefix + "-ret"
        id_ret=seed(ret,"settlement_period",{"months":1},"p0:ret:old")
        def ret_check(b,c,p,h,proc):
            old=next((x for x in h[ret] if x["id"]==id_ret),None)
            return {"explicit_old_retired":bool(old and old.get("superseded_at")),
                    "no_active_replacement":len(c[ret])==0,
                    "old_id_in_replaced":id_ret in (proc.get("replaced") or [])}
        execute("explicit_retract",f"明确撤回供应商 {ret} 每月结算的旧记忆。不知道新的周期，不要添加新的周期。",
                [f"供应商 {ret} 以前每月结算一次。"],[ret],ret_check)

        ana="room-" + prefix + "-ana"
        id_ana=seed(ana,"noise_preference",{"level":"quiet"},"p0:ana:old")
        def ana_check(b,c,p,h,proc):
            old=next((x for x in h[ana] if x["id"]==id_ana),None)
            return {"anaphora_target_retired":bool(old and old.get("superseded_at")),
                    "no_current_quiet":len(c[ana])==0,
                    "old_id_in_replaced":id_ana in (proc.get("replaced") or [])}
        execute("clear_anaphora",f"房间 {ana} 的静音偏好不再需要，把刚才说的那条取消。",
                [f"房间 {ana} 应保持安静。"],[ana],ana_check)

        amb_a="room-" + prefix + "-amb-a"
        amb_b="room-" + prefix + "-amb-b"
        seed(amb_a,"noise_preference",{"level":"quiet"},"p0:amb:a")
        seed(amb_b,"light_preference",{"level":"dim"},"p0:amb:b")
        def amb_check(b,c,p,h,proc):
            return {"ambiguous_no_active_mutation":all(c[k]==b[k] for k in (amb_a,amb_b)),
                    "ambiguous_no_history_mutation":all(canon(h[k])==canon(p[k])
                                      for k in (amb_a,amb_b)),
                    "ambiguous_no_replaced":not proc.get("replaced")}
        execute("ambiguous_anaphora",f"关于 {amb_a} 和 {amb_b}，取消刚才那个。",
                [f"{amb_a} 要静音。",f"{amb_b} 灯光需要调暗。"],
                [amb_a,amb_b],amb_check)

        con_a="supplier-" + prefix + "-con-a"
        con_b="supplier-" + prefix + "-con-b"
        old_a=seed(con_a,"settlement_period",{"months":1},"p0:con:a")
        old_b=seed(con_b,"settlement_period",{"months":6},"p0:con:b")
        def con_check(b,c,p,h,proc):
            return {"target_updated":len(c[con_a])==1 and c[con_a][0]["value"]=={"months":3},
                    "protected_neighbor_untouched":c[con_b]==b[con_b],
                    "protected_neighbor_history_untouched":canon(h[con_b])==canon(p[con_b]),
                    "protected_neighbor_not_replaced":old_b not in (proc.get("replaced") or [])}
        execute("predicate_subject_collision",
                f"供应商 {con_a} 现在改为每三个月结算；供应商 {con_b} 仍然每六个月结算，不要改它。",
                [f"{con_a} 原先月结。",f"{con_b} 每六个月结算一次。"],
                [con_a,con_b],con_check)

    verdict={"scope":"real_qwen_chinese_hazardous_semantics_v1",
             "model":args.model,"cases":cases,"total":len(cases),
             "passed_count":sum(c.get("passed") is True for c in cases),
             "false_retraction_count":sum(
                 c["id"] in ("ambiguous_anaphora","predicate_subject_collision")
                 and c.get("checks",{}).get("ambiguous_no_history_mutation",
                       c.get("checks",{}).get("protected_neighbor_history_untouched")) is False
                 for c in cases),
             "passed":len(cases)==5 and all(c.get("passed") is True for c in cases)}
    return verdict

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--base-url",required=True)
    p.add_argument("--key",default=os.getenv("MEMORY_API_KEY"))
    p.add_argument("--model",required=True)
    p.add_argument("--ack-disposable",action="store_true")
    p.add_argument("--report",required=True,type=Path)
    a=p.parse_args()
    try:
        report=run(a)
    except httpx.HTTPStatusError as e:
        report={"scope":"real_qwen_chinese_hazardous_semantics_v1",
                "passed":False,"failure_stage":"http",
                "http_status":e.response.status_code,
                "endpoint_path":e.request.url.path}
    except Exception as e:
        report={"scope":"real_qwen_chinese_hazardous_semantics_v1",
                "passed":False,"failure_stage":"exception","failure_type":type(e).__name__}
    a.report.parent.mkdir(parents=True,exist_ok=True)
    a.report.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return 0 if report.get("passed") is True else 1

if __name__=="__main__":
    raise SystemExit(main())
