"""Stable public memory contract, independent of adapters."""
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Protocol

CONTRACT_VERSION="1.0.0"

@dataclass(frozen=True)
class Capability:
    name: str
    available: bool
    reason: str = ""

def capabilities(*,graph=False,semantic=False,temporal=False,objects=False,
                 observations=True,events=True,processing=True,lexical=True,
                 local_extraction=False):
    return [asdict(x) for x in (
        Capability("memory.claims",True),
        Capability("memory.bitemporal",True),
        Capability("memory.processing",processing,"structured extractor candidates required" if processing else "memory processing is disabled"),
        Capability("memory.lexical",lexical,"FTS projection is disabled" if not lexical else ""),
        Capability("memory.local_extraction",local_extraction,"No loopback local model extractor is configured" if not local_extraction else ""),
        Capability("memory.vector",semantic,"caller-generated embeddings; vector index disabled" if not semantic else ""),
        Capability("memory.graph",graph,"AGE adapter not enabled" if not graph else ""),
        Capability("memory.objects",objects,"object adapter not enabled" if not objects else ""),
        Capability("memory.observations",observations,"observation API is disabled" if not observations else ""),
        Capability("events.replay",events,"durable event replay is disabled" if not events else ""),
        Capability("events.sse",events,"SSE event stream is disabled" if not events else ""),
        Capability("workflow.temporal",temporal,"Temporal worker is not enabled" if not temporal else "")
    )]

class MemoryPort(Protocol):
    def remember(self, **kwargs) -> str: ...
    def recall(self, **kwargs) -> list[dict]: ...
    def close(self) -> None: ...

def aware_iso(ts: str | None):
    if ts is None: return None
    value=datetime.fromisoformat(ts.replace("Z","+00:00"))
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("A timezone is required")
    return value.astimezone(timezone.utc)
