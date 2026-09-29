"""Incident memory: recording is agent-output only, the summary is compact and capped, and none of it is
ever treated as evidence by the validator (memory.py has no notion of evidence IDs at all - the only way
that could matter is if the prompt text leaked into an evidence_ids list, which diagnosis.validate() would
reject as an unknown ID like anything else). isolated_incident_memory (conftest, autouse) keeps this off the
real logs/incidents.jsonl for every test automatically."""
from backend.agent import memory


class FakeInv:
    def __init__(self, phase="resolved", component="base_controller", root_cause="x", action="restart_component",
                repaired=True, verified=True, finished_at=100.0):
        self.id = "abc123"
        self.phase = type("P", (), {"value": phase})()
        self.diagnosis = {"faulty_component": component, "root_cause": root_cause} if component else None
        self.repair = {"action": action, "executed": repaired} if action else None
        self.verification = {"verified": verified}
        self.finished_at = finished_at
        self.created_at = finished_at


def test_record_then_read_round_trips():
    assert memory.all_incidents() == []
    memory.record(FakeInv())
    incidents = memory.all_incidents()
    assert len(incidents) == 1
    assert incidents[0]["component"] == "base_controller" and incidents[0]["verified"] is True


def test_record_skips_investigations_with_no_settled_component():
    memory.record(FakeInv(phase="inconclusive", component=None))
    memory.record(FakeInv(phase="healthy", component="none"))
    assert memory.all_incidents() == []


def test_record_keeps_diagnosed_but_unrepaired_and_rejected_investigations():
    """A DIAGNOSED (no action recommended) or REJECTED (human said no) investigation still named a component -
    that is still useful context, just with repaired=verified=False."""
    memory.record(FakeInv(phase="diagnosed", action=None, repaired=False, verified=False))
    memory.record(FakeInv(phase="rejected", action=None, repaired=False, verified=False))
    incidents = memory.all_incidents()
    assert len(incidents) == 2
    assert all(not e["repaired"] and not e["verified"] for e in incidents)


def test_summary_is_empty_with_no_history():
    assert memory.summary() == ""


def test_summary_aggregates_per_component_and_reports_the_most_recent_outcome():
    memory.record(FakeInv(component="base_controller", verified=True, finished_at=1.0))
    memory.record(FakeInv(component="base_controller", verified=False, repaired=True, finished_at=2.0))  # most recent
    s = memory.summary()
    assert "base_controller: 2 past incident(s)" in s
    assert "repaired, not verified" in s                      # reflects the LATEST outcome, not the first


def test_summary_caps_to_the_most_recently_seen_components():
    for i in range(8):
        memory.record(FakeInv(component=f"comp{i}", finished_at=float(i)))
    s = memory.summary(limit_components=3)
    assert s.count("past incident(s)") == 3
    for c in ("comp7", "comp6", "comp5"):                      # the 3 most recently seen
        assert c in s
    assert "comp0" not in s


def test_a_broken_log_file_never_crashes_the_agent(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "PATH", tmp_path / "incidents.jsonl")
    memory.PATH.write_text("not json\n{\"component\": \"x\", \"verified\": true, \"repaired\": true, \"ts\": 1}\n")
    assert memory.all_incidents() == [{"component": "x", "verified": True, "repaired": True, "ts": 1}]
    assert "x: 1 past incident(s)" in memory.summary()


def test_record_is_resilient_to_an_unwritable_log_path(monkeypatch):
    import pathlib

    class Unwritable(pathlib.Path):
        def open(self, *a, **k):
            raise OSError("read-only filesystem")

        def mkdir(self, *a, **k):
            pass
    monkeypatch.setattr(memory, "PATH", Unwritable("/nonexistent/incidents.jsonl"))
    memory.record(FakeInv())   # must not raise
