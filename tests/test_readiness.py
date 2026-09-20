"""Demo readiness / preflight logic (pure functions; the live checks are exercised by demo_preflight.sh)."""
import os

from backend import readiness as r


def c(id_, status, detail="x"):
    return r.check(id_, id_, status, detail)


def test_ready_only_when_nothing_failed():
    ok = r.summarize([c("ollama", r.PASS), c("model", r.PASS), c("load", r.WARN)])
    assert ok["ready"] and ok["infra_ready"] and ok["warnings"] and ok["reason"] is None
    bad = r.summarize([c("ollama", r.PASS), c("model", r.FAIL, "COLD")])
    assert not bad["ready"] and not bad["infra_ready"] and "COLD" in bad["reason"]


def test_an_active_fault_does_not_make_the_tooling_not_ready():
    s = r.summarize([c("ollama", r.PASS), c("model", r.PASS), c("robot_health", r.FAIL, "controller FAILED"),
                     c("discovery", r.FAIL, "missing: /base_controller")])
    assert not s["ready"] and s["infra_ready"]           # demo controls stay enabled during the incident
    assert s["infra_reason"] is None


def test_chips_reflect_checks():
    chips = r.chips([c("ros_env", r.PASS), c("ros_client", r.PASS), c("dds_config", r.PASS), c("ollama", r.PASS),
                     c("model", r.PASS), c("injector", r.PASS), c("repair", r.PASS)])
    assert chips == {"ros": "READY", "agent": "READY", "ollama": "READY", "model": "WARM", "dds": "READY"}
    chips = r.chips([c("ollama", r.FAIL), c("model", r.FAIL)])
    assert chips["ollama"] == "FAIL" and chips["model"] == "COLD"


def test_environment_check_needs_fragmenting_dds_config(monkeypatch, tmp_path):
    good = tmp_path / "c.xml"
    good.write_text("<CycloneDDS><MaxMessageSize>1400B</MaxMessageSize></CycloneDDS>")
    monkeypatch.setenv("CYCLONEDDS_URI", f"file://{good}")
    monkeypatch.setenv("ROS_DISTRO", "lyrical")
    monkeypatch.setenv("RMW_IMPLEMENTATION", "rmw_cyclonedds_cpp")
    monkeypatch.setenv("ROS_DOMAIN_ID", "73")
    assert all(x["status"] == r.PASS for x in r.check_environment())
    bad = tmp_path / "b.xml"
    bad.write_text("<CycloneDDS/>")
    monkeypatch.setenv("CYCLONEDDS_URI", f"file://{bad}")
    assert {x["id"]: x["status"] for x in r.check_environment()}["dds_config"] == r.FAIL
    monkeypatch.delenv("CYCLONEDDS_URI")
    assert {x["id"]: x["status"] for x in r.check_environment()}["dds_config"] == r.FAIL


def test_only_our_own_heavy_processes_are_ever_stopped(monkeypatch):
    killed = []
    ps = ("  PID COMMAND\n 111 python scripts/profile_diagnosis.py --runs 3\n 222 python -m some_other_job --policy x\n"
          " 333 python benchmarks/run_benchmark.py\n 444 /usr/bin/mysqld\n")
    monkeypatch.setattr(r, "_run", lambda cmd, timeout=4.0: ps)
    monkeypatch.setattr(r.os, "kill", lambda pid, sig: killed.append(pid))
    assert r.stop_own_heavy_processes() == [111, 333]
    assert killed == [111, 333]                          # the unrelated evaluation job and mysqld are left alone


def test_busy_processes_flags_foreign_load_but_marks_ours(monkeypatch):
    ps = ("  PID %CPU   RSS ARGS\n 10 140.0 2000000 python -m some_other_job\n 11 90.0 100000 python run_benchmark.py\n"
          " 12 95.0 900000 ollama runner\n 13 5.0 1000 bash\n")
    monkeypatch.setattr(r, "_run", lambda cmd, timeout=4.0: ps)
    procs = r.busy_processes()
    assert [(p["pid"], p["ours"]) for p in procs] == [(10, False), (11, True)]      # ollama itself is ignored


def test_warmup_reports_failure_without_raising(monkeypatch):
    import asyncio
    monkeypatch.setattr(r.llm, "OLLAMA_URL", "http://127.0.0.1:9")
    res = asyncio.run(r.llm.warmup())
    assert res["ok"] is False and res["error"]
