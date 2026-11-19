"""Provider conformance rejects adapters that cannot make recovery safe."""
import pytest

from agent_memory_core.provider_contract import (
    ProviderCapabilities,ProviderContractError,validate_provider,
    validate_reconciliation_outcome,
)


class ConformingProvider:
    capabilities=ProviderCapabilities(
        idempotency=True,reconciliation=True,lookup_consistency="strong",
        idempotency_retention_seconds=86400)
    def execute(self,request,*,idempotency_key):
        return {"provider_reference":"ref-1","result":{"accepted":True}}
    def reconcile(self,request,*,idempotency_key,provider_reference):
        return {"outcome":"committed","provider_reference":"ref-1","result":{"accepted":True}}


def test_conforming_provider_is_accepted():
    caps=validate_provider(ConformingProvider())
    assert caps.lookup_consistency=="strong"


@pytest.mark.parametrize("capabilities",[
    ProviderCapabilities(False,True,"strong",86400),
    ProviderCapabilities(True,False,"strong",86400),
    ProviderCapabilities(True,True,"eventual",86400),
    ProviderCapabilities(True,True,"strong",0),
])
def test_unsafe_provider_capabilities_are_rejected(capabilities):
    provider=ConformingProvider()
    provider.capabilities=capabilities
    with pytest.raises(ProviderContractError):
        validate_provider(provider)


def test_provider_without_explicit_capabilities_is_rejected():
    class LegacyProvider:
        def execute(self,*args,**kwargs): return {}
        def reconcile(self,*args,**kwargs): return {"outcome":"not_found"}
    with pytest.raises(ProviderContractError,match="ProviderCapabilities"):
        validate_provider(LegacyProvider())


def test_not_found_is_only_accepted_with_strong_lookup():
    weak=ProviderCapabilities(True,True,"eventual",86400)
    with pytest.raises(ProviderContractError):
        validate_reconciliation_outcome({"outcome":"not_found"},weak)


def test_committed_reconciliation_requires_durable_provider_reference():
    caps=ConformingProvider.capabilities
    with pytest.raises(ProviderContractError,match="provider_reference"):
        validate_reconciliation_outcome({"outcome":"committed"},caps)


def test_unknown_is_a_valid_nonterminal_reconciliation_result():
    assert validate_reconciliation_outcome(
        {"outcome":"unknown","error":{"code":"LOOKUP_TIMEOUT"}},
        ConformingProvider.capabilities)["outcome"]=="unknown"
