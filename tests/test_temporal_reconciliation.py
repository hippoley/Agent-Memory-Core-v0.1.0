"""Read-only Temporal/PostgreSQL lifecycle divergence classification."""
import pytest

from agent_memory_core.temporal_reconciliation import assess_temporal_task


def task(status="running"):
    return {"task_id":"task-1","workflow_id":"tenant:task-1","status":status}


def test_missing_temporal_execution_is_not_automatically_restarted():
    result=assess_temporal_task(task=task(),temporal_status=None,temporal_exists=False)
    assert result.classification=="TEMPORAL_EXECUTION_MISSING"
    assert result.safe_for_automatic_repair is False


def test_temporal_completed_does_not_complete_postgres_task():
    result=assess_temporal_task(task=task(),temporal_status="COMPLETED")
    assert result.classification=="TEMPORAL_COMPLETED_POSTGRES_NONTERMINAL"
    assert result.safe_for_automatic_repair is False


@pytest.mark.parametrize("status",["CANCELED","CANCELLED","TERMINATED","TIMED_OUT"])
def test_temporal_stop_does_not_cancel_postgres_task(status):
    result=assess_temporal_task(task=task(),temporal_status=status)
    assert result.classification=="TEMPORAL_STOPPED_POSTGRES_NONTERMINAL"
    assert result.safe_for_automatic_repair is False


def test_temporal_failure_does_not_mark_business_task_failed():
    result=assess_temporal_task(task=task(),temporal_status="FAILED")
    assert result.classification=="TEMPORAL_FAILED_POSTGRES_NONTERMINAL"


def test_unresolved_effect_blocks_completed_task_assessment():
    result=assess_temporal_task(
        task=task("completed"),temporal_status="COMPLETED",
        effects=[{"effect_key":"charge-card","status":"unknown"}])
    assert result.classification=="PG_COMPLETED_WITH_UNRESOLVED_EFFECTS"
    assert result.unresolved_effect_keys==("charge-card",)


def test_missing_postgres_task_is_reported_as_orphan_temporal():
    result=assess_temporal_task(task=None,temporal_status="RUNNING",temporal_exists=True)
    assert result.classification=="ORPHAN_TEMPORAL"


def test_aligned_nonterminal_states_remain_observational():
    result=assess_temporal_task(task=task(),temporal_status="RUNNING")
    assert result.classification=="ALIGNED_NONTERMINAL"
    assert result.safe_for_automatic_repair is False


def test_terminal_records_are_not_overwritten():
    result=assess_temporal_task(task=task("failed"),temporal_status="COMPLETED")
    assert result.classification=="TERMINAL_STATE_DIVERGENCE"
