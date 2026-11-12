"""The LongMemEval harness must feed the store the right turns at the right time."""
import importlib.util
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "run_longmemeval", ROOT / "scripts" / "eval" / "run_longmemeval.py")
harness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(harness)


class RecordingApi:
    def __init__(self):
        self.calls = []

    def process(self, *, text, source_id, observed_at):
        self.calls.append({"text": text, "source_id": source_id,
                           "observed_at": observed_at})
        return {"created": ["m1"], "duplicates": 0, "rejected": 0, "replaced": []}


def test_dates_parse_to_utc_instants():
    assert harness.parse_date("2023/03/15 (Wed) 10:00") == datetime(
        2023, 3, 15, 10, 0, tzinfo=timezone.utc)
    assert harness.parse_date("2024/01/02 (Tue)") == datetime(
        2024, 1, 2, tzinfo=timezone.utc)


def test_only_user_turns_are_ingested_in_session_order():
    item = {
        "question_id": "q1",
        "haystack_dates": ["2023/03/20 (Mon) 09:00", "2023/03/15 (Wed) 10:00"],
        "haystack_sessions": [
            [{"role": "user", "content": "second session"},
             {"role": "assistant", "content": "assistant chatter"}],
            [{"role": "user", "content": "first session"}],
        ],
    }
    api = RecordingApi()
    stats = harness.ingest(api, item)
    assert [c["text"] for c in api.calls] == ["first session", "second session"]
    assert stats["turns"] == 2 and stats["created"] == 2
    assert api.calls[0]["observed_at"].day == 15
    assert api.calls[1]["observed_at"].day == 20
    assert api.calls[0]["source_id"].startswith("lme:q1:")


def test_claims_render_as_facts_not_raw_json_blobs():
    text = harness.render_claims([
        {"subject_id": "user", "predicate": "car_issue",
         "value": {"issue": "GPS"}, "valid_from": "2023-03-15T10:00:00+00:00"}])
    assert "user | car_issue = " in text and "GPS" in text
    assert harness.render_claims([]) == "(no records)"
