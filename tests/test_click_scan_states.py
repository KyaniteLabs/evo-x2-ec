"""Run outcome and EC-source state in nucbox-pmode-click-scan.py."""

from __future__ import annotations

import json
import subprocess

import pytest
from hypothesis import given
from hypothesis import strategies as st

# ---- ScanRun lifecycle --------------------------------------------------------

TERMINAL = {  # terminal state -> (move, persisted ok, persisted stop_reason, exit code)
    "COMPLETED": (lambda run: run.complete(), True, "completed", 0),
    "TCTL_GUARD": (lambda run: run.trip_tctl_guard(83.5), False, "tctl_guard_83.5C", 2),
}


def test_terminal_table_exhaustive(click_scan):
    assert set(TERMINAL) | {"RUNNING"} == {s.name for s in click_scan.ScanState}


@pytest.mark.parametrize("name", sorted(TERMINAL))
def test_legal_transition_and_persisted_values(click_scan, name):
    move, ok, reason, code = TERMINAL[name]
    run = click_scan.ScanRun()
    assert run.state is click_scan.ScanState.RUNNING
    move(run)
    assert run.state is click_scan.ScanState[name]
    assert (run.ok, run.stop_reason, run.exit_code) == (ok, reason, code)
    # and the persisted pair reads back to the same state
    assert click_scan.scan_state_from_summary({"ok": ok, "stop_reason": reason}) is run.state


@pytest.mark.parametrize("first", sorted(TERMINAL))
@pytest.mark.parametrize("second", sorted(TERMINAL))
def test_terminal_states_refuse_moves(click_scan, first, second):
    run = click_scan.ScanRun()
    TERMINAL[first][0](run)
    with pytest.raises(ValueError):
        TERMINAL[second][0](run)
    assert run.state is click_scan.ScanState[first]


def test_running_has_no_outcome(click_scan):
    run = click_scan.ScanRun()
    for attr in ("ok", "stop_reason", "exit_code"):
        with pytest.raises(ValueError):
            getattr(run, attr)


def test_legacy_summaries_read(click_scan):
    S = click_scan.ScanState
    assert click_scan.scan_state_from_summary({"ok": True, "stop_reason": "completed"}) is S.COMPLETED
    assert click_scan.scan_state_from_summary({"ok": False, "stop_reason": "tctl_guard_82C"}) is S.TCTL_GUARD
    for bad in ({"ok": False, "stop_reason": "completed"}, {"ok": True, "stop_reason": "tctl_guard_90C"}, {}):
        with pytest.raises(ValueError):
            click_scan.scan_state_from_summary(bad)


@given(st.lists(st.sampled_from(["complete", "guard"]), min_size=1, max_size=6),
       st.floats(min_value=0, max_value=150, allow_nan=False))
def test_random_sequences(click_scan, moves, tctl):
    """Only the first move takes effect; ok/stop_reason/exit code always agree."""
    run = click_scan.ScanRun()
    for i, move in enumerate(moves):
        do = run.complete if move == "complete" else (lambda: run.trip_tctl_guard(tctl))
        if i == 0:
            do()
        else:
            with pytest.raises(ValueError):
                do()
        assert run.ok == (run.stop_reason == "completed") == (run.exit_code == 0)
        assert click_scan.scan_state_from_summary({"ok": run.ok, "stop_reason": run.stop_reason}) is run.state


# ---- EcSource ---------------------------------------------------------------

def test_ec_source_flags_exhaustive_and_round_trip(click_scan):
    assert set(click_scan.EC_SOURCE_FLAGS) == set(click_scan.EcSource)
    for source, flags in click_scan.EC_SOURCE_FLAGS.items():
        assert click_scan.ec_source_of(dict(flags)) is source


def test_ec_source_persisted_keys_unchanged(click_scan):
    E = click_scan.EcSource
    assert click_scan.EC_SOURCE_FLAGS[E.DRIVER] == {"ec_driver_loaded": True}
    assert click_scan.EC_SOURCE_FLAGS[E.EC_SYS] == {"ec_driver_loaded": False, "ec_sys_readable": True}
    assert click_scan.EC_SOURCE_FLAGS[E.UNAVAILABLE] == {"ec_driver_loaded": False, "ec_sys_readable": False}


@pytest.fixture
def no_driver(click_scan, monkeypatch, tmp_path):
    real_path = click_scan.Path

    def fake_path(p, *a):
        return real_path(tmp_path / "missing") if p == "/sys/class/ec_su_axb35" else real_path(p, *a)

    monkeypatch.setattr(click_scan, "Path", fake_path)


def _fake_run(monkeypatch, click_scan, stdout=None, exc=None):
    def fake(cmd, **kw):
        if exc is not None:
            raise exc
        return subprocess.CompletedProcess(cmd, 0, stdout, None)

    monkeypatch.setattr(click_scan.subprocess, "run", fake)


@pytest.mark.parametrize("stdout", ["", "   \n", "not json", "[1, 2]", "null"])
def test_fallback_without_data_is_unavailable(click_scan, monkeypatch, no_driver, stdout):
    """BUG (old code): empty script output was reported as ec_sys_readable=True
    with every EC field None; non-object JSON crashed with AttributeError."""
    _fake_run(monkeypatch, click_scan, stdout=stdout)
    assert click_scan.ec_snapshot() == {"ec_driver_loaded": False, "ec_sys_readable": False}


def test_fallback_with_data_is_ec_sys(click_scan, monkeypatch, no_driver):
    _fake_run(monkeypatch, click_scan, stdout=json.dumps({"apu_power_mode": "quiet", "apu_power_mode_raw": 2}))
    snap = click_scan.ec_snapshot()
    assert click_scan.ec_source_of(snap) is click_scan.EcSource.EC_SYS
    assert snap["ec_power_mode"] == "quiet" and snap["ec_power_mode_raw"] == 2


@pytest.mark.parametrize("exc", [FileNotFoundError("rocm-smi"), subprocess.TimeoutExpired("sudo", 5)])
def test_missing_or_hung_tool_does_not_abort_scan(click_scan, monkeypatch, no_driver, exc):
    """BUG (old code): a missing probe tool (FileNotFoundError) or a hung one
    (TimeoutExpired) escaped run() and aborted the scan without a summary."""
    _fake_run(monkeypatch, click_scan, exc=exc)
    assert click_scan.ec_snapshot()["ec_sys_readable"] is False
    assert click_scan.rocm_snapshot()["rocm_power_w"] is None
    assert "turbostat_error" in click_scan.turbostat_snapshot()
