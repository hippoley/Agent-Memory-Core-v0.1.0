"""Evidence-first handoff -> effect -> independent readback probe.

Two separate AMC clients are required. This is a deterministic protocol probe,
not a claim that a language model or physical device has been validated.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable
from uuid import uuid4


class HandoffEffectFailure(RuntimeError):
    """An effect was not independently verified."""


@dataclass(frozen=True)
class EffectReceipt:
    task_id: str
    handoff_id: str
    target_memory_id: str
    verified_memory_id: str
    device_id: str
    property: str
    before: object
    observed: object
    expected: object


def execute_verified_handoff(
    *, agent_a, agent_b, device_id: str, property_name: str,
    expected_value: object, actuator: Callable[[str, str, object], object],
    observer: Callable[[str, str], object], source_id: str | None = None,
) -> EffectReceipt:
    """Use AMC public SDK to transfer intent and prove an external state change.

    Agent B must be an independent SDK session. It sees the immutable accepted
    packet rather than original dialogue. The actuator's return value is never
    evidence. Observer must read actual state via a separate path. The caller
    must verify that the two paths are physically independent if that matters.
    Never claim durable task completion: task status requires a worker lease.
    """
    if (not isinstance(device_id, str) or not device_id.strip()
            or not isinstance(property_name, str) or not property_name.strip()
            or expected_value is None or not callable(actuator)
            or not callable(observer)):
        raise ValueError("Device, property, target, actuator and observer required")
    if agent_a is agent_b:
        raise ValueError("Agent A and B must use independent sessions")
    source = source_id or "handoff-probe:" + uuid4().hex
    task = agent_a.create_task(
        task_type="example.memory.batch.organize",
        input={"goal": "apply verified device setting", "device_id": device_id,
               "property": property_name})
    task_id = task["task_id"]
    intent = agent_a.remember(
        subject_id=device_id, predicate="desired_" + property_name,
        value={property_name: expected_value}, source_id=source,
        valid_from=datetime.now(timezone.utc).isoformat())
    target_id = intent["id"]
    packet = agent_a.create_handoff(
        task_id=task_id, source_agent="agent-A", target_agent="agent-B",
        subject_ids=[device_id], token_budget=4096)
    handoff_id = packet["handoff_id"]

    accepted = agent_b.accept_handoff(handoff_id, target_agent="agent-B")
    if accepted.get("status") != "accepted":
        raise HandoffEffectFailure("Agent B did not accept the handoff")
    payload = accepted.get("payload")
    claims = payload.get("claims") if isinstance(payload, dict) else None
    if not isinstance(claims, list):
        raise HandoffEffectFailure("Accepted handoff has no canonical claims")
    matches = [c for c in claims if isinstance(c, dict)
               and c.get("id") == target_id and c.get("subject_id") == device_id
               and c.get("predicate") == "desired_" + property_name
               and isinstance(c.get("value"), dict)
               and property_name in c["value"]]
    if len(matches) != 1:
        raise HandoffEffectFailure("Accepted handoff lacks exactly one target intent")
    target = matches[0]["value"][property_name]
    if target != expected_value:
        raise HandoffEffectFailure("Accepted intent differs from expected target")

    before = observer(device_id, property_name)
    if before == target:
        raise HandoffEffectFailure("Target was already satisfied before execution")
    actuator(device_id, property_name, target)
    # Never trust the command return value, only independent readback.
    after = observer(device_id, property_name)
    if after != target:
        raise HandoffEffectFailure("Independent readback did not match target")

    proof = agent_b.remember(
        subject_id=device_id, predicate="verified_effect",
        value={"handoff_id": handoff_id, "property": property_name,
               "expected": target, "observed": after, "before": before,
               "verification": "independent_readback"},
        source_id="effect-readback:" + handoff_id,
        valid_from=datetime.now(timezone.utc).isoformat())
    proof_id = proof["id"]
    rows = agent_b.recall(device_id)["items"]
    if not any(isinstance(c, dict) and c.get("id") == proof_id
               and isinstance(c.get("value"), dict)
               and c["value"].get("observed") == target for c in rows):
        raise HandoffEffectFailure("Verification receipt not found after canonical reread")
    return EffectReceipt(task_id, handoff_id, target_id, proof_id,
                         device_id, property_name, before, after, target)
