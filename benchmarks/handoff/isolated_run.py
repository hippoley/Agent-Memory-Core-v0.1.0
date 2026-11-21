"""Isolated Agent B + HTTP provider probe; requires an actual running AMC API.

This is a controlled process/side-effect experiment, not a benchmark result.
Provider journals externally observed effects in a separate SQLite process.
"""
from __future__ import annotations

import argparse
import json
import os
import hmac
import sqlite3
import time
from pathlib import Path
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from urllib.parse import quote, unquote, urlsplit
try:
    from benchmarks.handoff.provider_fencing import provider_fence
except ModuleNotFoundError:
    # Running the probe directly by script path without PYTHONPATH=repo root.
    from provider_fencing import provider_fence


def decide(pack: dict) -> dict:
    """Derive a narrow supplier decision exclusively from accepted claims."""
    if pack.get("omitted_claim_ids") or pack.get("unresolved_effects"):
        raise ValueError("incomplete or unresolved context")
    values = {}
    for claim in pack.get("claims", []):
        key = claim.get("predicate")
        if key not in ("payment_period", "requires_approval"):
            continue
        if key in values:
            raise ValueError("ambiguous policy facts")
        val = claim.get("value")
        if not isinstance(val, dict) or key not in val:
            raise ValueError("invalid policy claim")
        values[key] = val[key]
    if set(values) != {"payment_period", "requires_approval"}:
        raise ValueError("missing necessary claims")
    if values["payment_period"] not in ("monthly", "quarterly") or values["requires_approval"] is not True:
        raise ValueError("unsafe policy; no action permitted")
    return values


def initialize(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS effect_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        effect_key TEXT NOT NULL, request_id TEXT NOT NULL,
        executed INTEGER NOT NULL CHECK (executed IN (0,1)),
        payload_json TEXT NOT NULL)""")
    conn.commit()


def serve_provider(db_path: str, host: str, port: int, after_commit_gate: str | None = None):
    effect_token = os.environ.get("PROVIDER_EFFECT_TOKEN")
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = urlsplit(self.path).path
            if not path.startswith("/effects/"):
                self.send_error(404)
                return
            request_id = unquote(path[len("/effects/"):])
            if not request_id or "/" in request_id or len(request_id) > 512:
                self.send_error(400)
                return
            with sqlite3.connect(db_path, timeout=5) as conn:
                row = conn.execute(
                    "SELECT effect_key,payload_json FROM effect_events "
                    "WHERE request_id=? AND executed=1", (request_id,)
                ).fetchone()
            if row is None:
                self.send_error(404, "No committed receipt")
                return
            body = json.dumps({
                "status": "committed", "request_id": request_id,
                "effect_key": row[0], "payload": json.loads(row[1]),
            }, sort_keys=True).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            if self.path != "/effects":
                self.send_error(404)
                return
            # Test Provider: the Agent B runtime has no dispatch credential.
            # A checkpoint or handoff ID is not itself execution authority.
            supplied = self.headers.get("Authorization", "")
            if (not effect_token or not hmac.compare_digest(
                supplied, "Bearer " + effect_token
            )):
                self.send_error(403, "Dedicated Provider executor credential required")
                return
            try:
                n = int(self.headers.get("Content-Length", "0"))
                if not 0 < n <= 16384:
                    self.send_error(413)
                    return
                event = json.loads(self.rfile.read(n))
                if not isinstance(event, dict) or set(event) != {"effect_key", "request_id", "payload"}:
                    raise ValueError("malformed event")
                if not all(isinstance(event[k], str) and event[k] for k in ("effect_key", "request_id")):
                    raise ValueError("missing identifiers")
            except (ValueError, TypeError):
                self.send_error(400)
                return
            # Provider rechecks the current fenced owner at the actual effect
            # commit boundary, not merely when the executor prepared its POST.
            with provider_fence(event, self.headers) as authorized:
                if not authorized:
                    self.send_error(403, "AMC fenced execution admission rejected")
                    return
                with sqlite3.connect(db_path, timeout=5) as conn:
                    conn.execute("BEGIN IMMEDIATE")
                    prior = conn.execute(
                        "SELECT effect_key,payload_json FROM effect_events WHERE request_id=? AND executed=1",
                        (event["request_id"],)).fetchone()
                    canonical_payload = json.dumps(event["payload"], sort_keys=True)
                    if prior and prior != (event["effect_key"], canonical_payload):
                        conn.rollback()
                        self.send_error(409, "idempotency key reused with different effect")
                        return
                    executed = 0 if prior else 1
                    conn.execute(
                        "INSERT INTO effect_events(effect_key,request_id,executed,payload_json) VALUES(?,?,?,?)",
                        (event["effect_key"], event["request_id"], executed,
                         json.dumps(event["payload"], sort_keys=True)))
                    conn.commit()
            # Fault-injection only: signal AFTER the SQLite commit and BEFORE
            # emitting an HTTP response. A supervising test can SIGKILL B here.
            # A replay is not blocked because the first attempt already committed.
            if after_commit_gate is not None and executed:
                marker = Path(after_commit_gate)
                marker.write_text(event["request_id"], encoding="utf-8")
                deadline = time.monotonic() + 20
                while not Path(after_commit_gate + ".release").exists():
                    if time.monotonic() > deadline:
                        return  # no success response: unsafe ambiguity is preserved
                    time.sleep(0.01)
            body = json.dumps({"executed": bool(executed)}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    with sqlite3.connect(db_path) as conn:
        initialize(conn)
    ThreadingHTTPServer((host, port), Handler).serve_forever()


def amc_client():
    from agent_memory_core.sdk import MemoryClient
    return MemoryClient(
        os.environ["MEMORY_API_URL"], os.environ["MEMORY_API_KEY"],
        tenant_id=os.environ["MEMORY_TENANT_ID"])


def agent_a(subject_id: str):
    """Seed one task and source-linked policy facts, then issue a durable handoff."""
    with amc_client() as api:
        task = api.create_task(
            task_type="example.memory.batch.organize",
            input={"goal": "review supplier and request approval, never pay"})
        now = datetime.now(timezone.utc).isoformat()
        # Real revision rather than a preconstructed current claim:
        # Agent A first stores outdated v2 monthly policy, then corrects to
        # the independently verifiable v3 quarterly policy.
        old = api.remember(
            subject_id=subject_id, predicate="payment_period",
            value={"payment_period": "monthly"},
            source_id="supplier-contract:v2", valid_from=now)
        api.remember(
            subject_id=subject_id, predicate="payment_period",
            value={"payment_period": "quarterly"},
            source_id="supplier-contract:v3", valid_from=now,
            replaces_id=old["id"])
        api.remember(
            subject_id=subject_id, predicate="requires_approval",
            value={"requires_approval": True},
            source_id="supplier-contract:v3", valid_from=now)
        handoff = api.create_handoff(
            task_id=task["task_id"], source_agent="agent-A",
            target_agent="agent-B", subject_ids=[subject_id], token_budget=4096)
    # The manifest is the ONLY input Agent B needs apart from credentials.
    print(json.dumps({
        "task_id": task["task_id"], "subject_id": subject_id,
        "handoff_id": handoff["handoff_id"],
        "context_tokens_estimate": handoff["payload"]["estimated_tokens"],
    }))


def reconcile_provider(provider_url: str, request_id: str, effect_key: str,
                       expected_payload: dict) -> str:
    """Only a matching externally committed receipt authorizes continuation.

    404 is not proof an earlier in-flight request cannot commit later; it is
    merely permission to attempt the same idempotency-keyed request against
    THIS provider. Network errors, malformed receipts and conflicts fail closed.
    """
    receipt_url = (provider_url.rstrip("/") + "/effects/" +
                   quote(request_id, safe=""))
    try:
        with urlopen(receipt_url, timeout=8) as response:
            if response.status != 200:
                raise RuntimeError("UNKNOWN provider receipt status")
            receipt = json.load(response)
    except HTTPError as exc:
        if exc.code == 404:
            return "absent"
        raise RuntimeError("UNKNOWN provider reconciliation") from exc
    except (OSError, ValueError, TypeError) as exc:
        raise RuntimeError("UNKNOWN provider reconciliation") from exc
    if not isinstance(receipt, dict) or (
        receipt.get("status") != "committed" or
        receipt.get("request_id") != request_id or
        receipt.get("effect_key") != effect_key or
        receipt.get("payload") != expected_payload
    ):
        raise RuntimeError("CONFLICT provider receipt mismatch; no action permitted")
    return "committed"


def agent_b(handoff_id: str, subject_id: str, provider_url: str, *,
            accept_only: bool = False):
    """No direct DB or Agent-A session access: only public HTTP clients."""
    with amc_client() as api:
        accepted = api.accept_handoff(handoff_id, target_agent="agent-B")
        if accepted.get("status") != "accepted":
            raise RuntimeError("handoff not accepted")
        pack = accepted["payload"]
        if subject_id not in pack.get("subjects", []):
            raise ValueError("subject is not in accepted context")
        decision = decide(pack)
        if accept_only:
            print("AGENT_B_ACCEPTED")
            return
        effect_key = "supplier-approval-request"
        request_id = handoff_id + ":" + effect_key
        business_payload = {"subject": subject_id, "period": decision["payment_period"]}
        expected_request = {
            "effect_key": effect_key, "request_id": request_id,
            "payload": business_payload,
        }
        # Agent B cannot create intent or impersonate the fenced Worker.
        # It only checks that a separate Worker committed the exact request
        # before any contact with the external Provider.
        task = api.get_task(pack["task_id"])
        latest = task.get("checkpoint") or {}
        intent = latest.get("checkpoint") or {}
        if (latest.get("version") != task.get("checkpoint_version")
                or intent.get("phase") != "effect-intent-registered"
                or intent.get("handoff_id") != handoff_id
                or intent.get("effect_request") != expected_request):
            raise RuntimeError("AMC_EFFECT_INTENT_MISSING_OR_CONFLICT; dispatch denied")
        # Provider execution is exclusively owned by a fenced Worker.
        # B can only verify the resulting receipt; B never sends POST.
        state = reconcile_provider(provider_url, request_id, effect_key, business_payload)
        if state != "committed":
            raise RuntimeError("AMC_EFFECT_NOT_COMMITTED; Agent B cannot dispatch")
        # A remote receipt does not authorize a canonical success claim until
        # the fenced AMC reconciler has also committed the durable task.
        completed = api.get_task(pack["task_id"])
        if completed.get("status") != "completed":
            raise RuntimeError("AMC_TASK_UNRESOLVED; Provider receipt alone is insufficient")
        api.remember(
            subject_id=subject_id, predicate="handoff_decision", value=decision,
            source_id="handoff:" + handoff_id,
            # Stable payload across process restarts; using wall-clock now()
            # would change the request body under the same idempotency key.
            valid_from=min(c["valid_from"] for c in pack["claims"]),
            idempotency_key="decision:" + handoff_id)
    print("AGENT_B_FINISHED")


def observe(case_id: str, subject_id: str, handoff_id: str, db_path: str, elapsed_ms: float,
            context_tokens: int):
    """Read canonical output and independently persisted provider effects."""
    with amc_client() as api:
        claims = api.recall(subject_id)["items"]
    decisions = [c["value"] for c in claims if c["predicate"] == "handoff_decision"
                 and c.get("source_id") == "handoff:" + handoff_id]
    with sqlite3.connect(db_path) as conn:
        actions = conn.execute(
            "SELECT effect_key FROM effect_events WHERE executed=1 AND request_id LIKE ? ORDER BY id",
            (handoff_id + ":%",)).fetchall()
    return {
        "case_id": case_id, "arm": "amc", "observer": "independent",
        # The current SDK does not give this observer an independently verified
        # complete task-effect reconciliation; never assert safety by default.
        "observation_complete": False, "unresolved_unknown": True,
        "observed_facts": decisions[0] if len(decisions) == 1 else {},
        "observed_effects": [{"effect_key": k} for (k,) in actions],
        "elapsed_ms": elapsed_ms, "context_tokens": context_tokens}


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("provider")
    p.add_argument("--db", required=True)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8799)
    p.add_argument("--after-commit-gate", help="Test-only pause after persistent provider commit")
    a = commands.add_parser("agent-a")
    a.add_argument("--subject-id", required=True)
    b = commands.add_parser("agent-b")
    b.add_argument("--handoff-id", required=True)
    b.add_argument("--subject-id", required=True)
    b.add_argument("--provider-url", default="http://127.0.0.1:8799")
    b.add_argument("--accept-only", action="store_true", help="Accept handoff without dispatching")
    o = commands.add_parser("observe")
    o.add_argument("--case-id", required=True)
    o.add_argument("--subject-id", required=True)
    o.add_argument("--handoff-id", required=True)
    o.add_argument("--db", required=True)
    o.add_argument("--elapsed-ms", type=float, required=True)
    o.add_argument("--context-tokens", type=int, required=True)
    args = parser.parse_args()
    if args.command == "provider":
        serve_provider(args.db, args.host, args.port, args.after_commit_gate)
    elif args.command == "agent-a":
        agent_a(args.subject_id)
    elif args.command == "agent-b":
        agent_b(args.handoff_id, args.subject_id, args.provider_url,
                accept_only=args.accept_only)
    else:
        print(json.dumps(observe(args.case_id, args.subject_id, args.handoff_id, args.db,
                                 args.elapsed_ms, args.context_tokens), ensure_ascii=False))


if __name__ == "__main__":
    main()
