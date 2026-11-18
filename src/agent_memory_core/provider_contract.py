"""Provider contract for safely executing and reconciling durable external effects.

A provider without stable idempotency and authoritative lookup may still be
called by application code, but it is not eligible for automatic recovery or
NOT_FOUND-based retry in this runtime.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class ProviderCapabilities:
    idempotency: bool
    reconciliation: bool
    lookup_consistency: str
    idempotency_retention_seconds: int | None = None


@runtime_checkable
class DurableEffectProvider(Protocol):
    capabilities: ProviderCapabilities

    def execute(self, request: dict, *, idempotency_key: str) -> dict:
        ...

    def reconcile(self, request: dict, *, idempotency_key: str,
                  provider_reference: str | None) -> dict:
        ...


class ProviderContractError(ValueError):
    """Provider cannot meet the durable effect safety contract."""


def validate_provider(provider: Any) -> ProviderCapabilities:
    caps=getattr(provider,"capabilities",None)
    if not isinstance(caps,ProviderCapabilities):
        raise ProviderContractError(
            "Provider must expose ProviderCapabilities; refusing unqualified execution")
    if not caps.idempotency:
        raise ProviderContractError(
            "Provider lacks stable idempotency; durable automatic execution is unsafe")
    if not caps.reconciliation:
        raise ProviderContractError(
            "Provider lacks reconciliation; UNKNOWN outcomes cannot be resolved safely")
    if caps.lookup_consistency not in {"strong","linearizable"}:
        raise ProviderContractError(
            "Provider lookup must be strongly consistent before NOT_FOUND can authorize retry")
    if caps.idempotency_retention_seconds is not None and caps.idempotency_retention_seconds <= 0:
        raise ProviderContractError("Provider idempotency retention must be positive")
    if not callable(getattr(provider,"execute",None)) or not callable(
        getattr(provider,"reconcile",None)
    ):
        raise ProviderContractError("Provider must implement execute() and reconcile()")
    return caps


def validate_reconciliation_outcome(outcome: Any, capabilities: ProviderCapabilities) -> dict:
    if not isinstance(outcome,dict):
        raise ProviderContractError("Provider reconciliation must return an object")
    state=outcome.get("outcome")
    if state not in {"committed","not_found","unknown","failed"}:
        raise ProviderContractError("Unsupported provider reconciliation outcome")
    if state=="not_found" and capabilities.lookup_consistency not in {"strong","linearizable"}:
        raise ProviderContractError(
            "NOT_FOUND from a non-authoritative lookup cannot authorize retry")
    if state=="committed" and not outcome.get("provider_reference"):
        raise ProviderContractError(
            "COMMITTED reconciliation must include a durable provider_reference")
    return outcome
