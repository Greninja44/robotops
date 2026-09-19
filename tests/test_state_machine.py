"""Investigation state machine transitions."""
import pytest

from backend.agent.state import TERMINAL, TRANSITIONS, InvalidTransition, Investigation, Phase


def test_happy_path_transitions():
    inv = Investigation("q")
    for p in (Phase.INVESTIGATING, Phase.DIAGNOSING, Phase.AWAITING_APPROVAL, Phase.REPAIRING, Phase.VERIFYING,
              Phase.RESOLVED):
        inv.transition(p)
    assert inv.done and inv.finished_at


def test_cannot_repair_without_approval_phase():
    inv = Investigation("q")
    inv.transition(Phase.INVESTIGATING)
    inv.transition(Phase.DIAGNOSING)
    with pytest.raises(InvalidTransition):
        inv.transition(Phase.REPAIRING)          # diagnosing -> repairing skips approval


def test_cannot_resolve_without_verifying():
    inv = Investigation("q")
    for p in (Phase.INVESTIGATING, Phase.DIAGNOSING, Phase.AWAITING_APPROVAL, Phase.REPAIRING):
        inv.transition(p)
    with pytest.raises(InvalidTransition):
        inv.transition(Phase.RESOLVED)


def test_terminal_states_have_no_exits():
    assert all(t not in TRANSITIONS for t in TERMINAL)
    inv = Investigation("q")
    inv.transition(Phase.INVESTIGATING)
    inv.transition(Phase.INCONCLUSIVE)
    with pytest.raises(InvalidTransition):
        inv.transition(Phase.INVESTIGATING)


def test_rejected_diagnosis_can_return_to_investigating():
    inv = Investigation("q")
    inv.transition(Phase.INVESTIGATING)
    inv.transition(Phase.DIAGNOSING)
    inv.transition(Phase.INVESTIGATING)


def test_failed_verification_can_reinvestigate():
    inv = Investigation("q")
    for p in (Phase.INVESTIGATING, Phase.DIAGNOSING, Phase.AWAITING_APPROVAL, Phase.REPAIRING, Phase.VERIFYING):
        inv.transition(p)
    inv.transition(Phase.INVESTIGATING)


def test_events_are_numbered_and_streamed():
    got = []
    inv = Investigation("q", listener=got.append)
    inv.emit("note", text="a")
    inv.transition(Phase.INVESTIGATING)
    assert [e["event"]["seq"] for e in got] == [1, 2] and got[1]["event"]["previous"] == "observing"
