"""Provider contract for safe durable external-effect execution."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any,Protocol,runtime_checkable


@dataclass(frozen=True)
class ProviderCapabilities:
    idempotency: bool
    reconciliation: bool
    lookup_consistency: str
    idempotency_retention_seconds: int | None = None


@runtime_checkable
class DurableEffectProvider(Protocol):
    capabilities: ProviderCapabilities
    def execute(self,request:dict,*,idempotency_key:str)->dict: ...
    def reconcile(self,request:dict,*,idempotency_key:str,
                  provider_reference:str|None)->dict: ...


class ProviderContractError(ValueError):
    pass


def validate_provider(provider:Any)->ProviderCapabilities:
    caps=getattr(provider,"capabilities",None)
    if not isinstance(caps,ProviderCapabilities):
        raise ProviderContractError("Provider must expose ProviderCapabilities")
    if not caps.idempotency:
        raise ProviderContractError("Stable provider idempotency is required")
    if not caps.reconciliation:
        raise ProviderContractError("Provider reconciliation is required")
    if caps.lookup_consistency not in {"strong","linearizable"}:
        raise ProviderContractError("Authoritative strong lookup is required before retry")
    if caps.idempotency_retention_seconds is not None and caps.idempotency_retention_seconds<=0:
        raise ProviderContractError("Idempotency retention must be positive")
    if not callable(getattr(provider,"execute",None)) or not callable(getattr(provider,"reconcile",None)):
        raise ProviderContractError("Provider must implement execute() and reconcile()")
    return caps


def validate_reconciliation_outcome(outcome:Any,caps:ProviderCapabilities)->dict:
    if not isinstance(outcome,dict):
        raise ProviderContractError("Reconciliation outcome must be an object")
    state=outcome.get("outcome")
    if state not in {"committed","not_found","unknown","failed"}:
        raise ProviderContractError("Unsupported reconciliation outcome")
    if state=="not_found" and caps.lookup_consistency not in {"strong","linearizable"}:
        raise ProviderContractError("Non-authoritative NOT_FOUND cannot authorize retry")
    if state=="committed" and not outcome.get("provider_reference"):
        raise ProviderContractError("COMMITTED outcome requires provider_reference")
    return outcome
