"""EC evidence reporting in nucbox-pmode-click-scan.py.

Drives the real ``ec_snapshot()`` and the real ``main()`` against a stubbed
helper: no sudo, no EC register, no hardware. The contract under test: the
reported ``ec_sys_readable`` boolean must always match the data beside it, and
a missing sample must say why (exit status + stderr tail) in the artifact.
"""

from __future__ import annotations

import itertools
import json
import subprocess
import sys
from pathlib import Path

import pytest

EC_FIELDS = ("ec_power_mode", "ec_power_mode_raw", "ec_temp_c", "fan1_rpm", "fan2_rpm", "fan3_rpm")
VALID_SAMPLE = {
    "apu_power_mode": "quiet",
    "apu_power_mode_raw": 2,
    "ec_temp_c": 41,
    "fan1_rpm": 1234,
    "fan2_rpm": 0,
    "fan3_rpm": 0,
}


@pytest.fixture
def no_driver(click_scan, monkeypatch, tmp_path):
    """The ec_su_axb35 sysfs driver is absent, so the fallback path runs."""
    real_path = click_scan.Path

    def fake_path(p, *a, **kw):
        return real_path(tmp_path / "missing") if str(p) == "/sys/class/ec_su_axb35" else real_path(p, *a, **kw)

    monkeypatch.setattr(click_scan, "Path", fake_path)


def stub_helper(click_scan, monkeypatch, *, stdout="", stderr="", rc=0, exc=None):
    calls: list[list[str]] = []

    def fake_run(cmd, **kw):
        calls.append(list(cmd))
        if exc is not None:
            raise exc
        return subprocess.CompletedProcess(cmd, rc, stdout, stderr)

    monkeypatch.setattr(click_scan.subprocess, "run", fake_run)
    return calls


# ---- ec_snapshot(): the boolean must match the data --------------------------


def test_empty_output_is_not_readable_and_says_why(click_scan, monkeypatch, no_driver):
    stub_helper(click_scan, monkeypatch, stdout="", stderr="sudo: a terminal is required\n", rc=127)
    snap = click_scan.ec_snapshot()
    assert snap["ec_driver_loaded"] is False
    assert snap["ec_sys_readable"] is False
    assert all(snap.get(key) is None for key in EC_FIELDS)
    assert snap["ec_helper_rc"] == 127
    assert snap["ec_helper_stderr_tail"] == "sudo: a terminal is required"
    assert "no output" in snap["ec_read_error"]


@pytest.mark.parametrize("stdout", ["{}\n", '{"ts": 1.0}\n', '{"unrelated": true}\n'])
def test_object_without_ec_fields_is_not_evidence(click_scan, monkeypatch, no_driver, stdout):
    """LIVE BUG before this change: an empty JSON object counted as a readable
    EC, so ec_sys_readable=true sat beside six null EC fields."""
    stub_helper(click_scan, monkeypatch, stdout=stdout, rc=0)
    snap = click_scan.ec_snapshot()
    assert snap["ec_sys_readable"] is False
    assert all(snap.get(key) is None for key in EC_FIELDS)
    assert "no EC fields" in snap["ec_read_error"]
    assert snap["ec_helper_rc"] == 0


@pytest.mark.parametrize("stdout", ["", "   \n", "junk", "[]", "null", "{}\n", '{"ts": 1}\n'])
def test_readable_flag_never_outruns_the_data(click_scan, monkeypatch, no_driver, stdout):
    stub_helper(click_scan, monkeypatch, stdout=stdout, rc=0)
    snap = click_scan.ec_snapshot()
    has_data = any(snap.get(key) is not None for key in EC_FIELDS)
    assert snap["ec_sys_readable"] is has_data


def test_failed_helper_records_exit_status_and_stderr_tail(click_scan, monkeypatch, no_driver):
    stderr = "sudo: /usr/local/bin/nucbox-ec-readonly.py: command not found\n"
    calls = stub_helper(click_scan, monkeypatch, stdout="", stderr=stderr, rc=127)
    snap = click_scan.ec_snapshot()
    assert calls == [["sudo", click_scan.EC_FALLBACK_HELPER]]
    assert snap["ec_helper_rc"] == 127
    assert snap["ec_helper_stderr_tail"] == stderr.strip()
    assert "127" in snap["ec_read_error"]


def test_stderr_tail_is_bounded(click_scan, monkeypatch, no_driver):
    stub_helper(click_scan, monkeypatch, stdout="", stderr="x" * 400 + "TAIL", rc=2)
    tail = click_scan.ec_snapshot()["ec_helper_stderr_tail"]
    assert len(tail) <= click_scan.EC_STDERR_TAIL_CHARS
    assert tail.endswith("TAIL")


def test_hung_helper_records_the_timeout(click_scan, monkeypatch, no_driver):
    stub_helper(click_scan, monkeypatch, exc=subprocess.TimeoutExpired(["sudo"], 5))
    snap = click_scan.ec_snapshot()
    assert snap["ec_sys_readable"] is False
    assert snap["ec_helper_rc"] is None
    assert "timed out" in snap["ec_read_error"]


def test_missing_helper_binary_records_the_oserror(click_scan, monkeypatch, no_driver):
    stub_helper(click_scan, monkeypatch, exc=FileNotFoundError("sudo"))
    snap = click_scan.ec_snapshot()
    assert snap["ec_sys_readable"] is False
    assert snap["ec_helper_rc"] is None
    assert "FileNotFoundError" in snap["ec_read_error"]


def test_valid_sample_is_readable_with_its_values(click_scan, monkeypatch, no_driver):
    stub_helper(click_scan, monkeypatch, stdout=json.dumps(VALID_SAMPLE), rc=0)
    snap = click_scan.ec_snapshot()
    assert snap["ec_sys_readable"] is True
    assert click_scan.ec_source_of(snap) is click_scan.EcSource.EC_SYS
    assert snap["ec_power_mode"] == "quiet"
    assert snap["ec_power_mode_raw"] == 2
    assert snap["ec_temp_c"] == 41
    assert snap["fan1_rpm"] == 1234
    assert snap["fan2_rpm"] == 0
    assert snap["fan3_rpm"] == 0


def test_single_ec_field_is_enough_evidence(click_scan, monkeypatch, no_driver):
    stub_helper(click_scan, monkeypatch, stdout='{"fan1_rpm": 0}', rc=0)
    snap = click_scan.ec_snapshot()
    assert snap["ec_sys_readable"] is True
    assert snap["fan1_rpm"] == 0


def test_helper_path_is_the_shipped_basename(click_scan):
    """Row 67: the fallback invoked gpu-host-ec-readonly.py, which the repo
    does not ship; the committed script is nucbox-ec-readonly.py."""
    assert click_scan.EC_FALLBACK_HELPER == "/usr/local/bin/nucbox-ec-readonly.py"
    shipped = Path(click_scan.__file__).resolve().parent / "nucbox-ec-readonly.py"
    assert shipped.is_file(), shipped


# ---- primary branch is untouched --------------------------------------------


def test_driver_branch_output_is_unchanged(click_scan, monkeypatch, tmp_path):
    root = tmp_path / "ec_su_axb35"
    (root / "apu").mkdir(parents=True)
    (root / "apu" / "power_mode").write_text("performance\n")
    fan = root / "fan1"
    fan.mkdir()
    (fan / "rpm").write_text("1500\n")
    (fan / "mode").write_text("auto\n")
    (fan / "level").write_text("3\n")
    real_path = click_scan.Path

    def fake_path(p, *a, **kw):
        return root if str(p) == "/sys/class/ec_su_axb35" else real_path(p, *a, **kw)

    def no_helper(*a, **kw):
        raise AssertionError("the fallback helper must not run when the driver is present")

    monkeypatch.setattr(click_scan, "Path", fake_path)
    monkeypatch.setattr(click_scan.subprocess, "run", no_helper)
    assert click_scan.ec_snapshot() == {
        "ec_driver_loaded": True,
        "ec_power_mode": "performance",
        "fan1_rpm": "1500",
        "fan1_mode": "auto",
        "fan1_level": "3",
    }


# ---- main(): the artifact must carry the evidence state ----------------------


def run_scan(click_scan, monkeypatch, tmp_path, *, stdout="", stderr="", rc=0):
    """One real main() iteration with a fake clock and a stubbed helper."""
    stub_helper(click_scan, monkeypatch, stdout=stdout, stderr=stderr, rc=rc)
    for name in ("cpufreq_snapshot", "sensor_snapshot", "rocm_snapshot", "turbostat_snapshot"):
        monkeypatch.setattr(click_scan, name, lambda: {})
    clock = itertools.count(0.0, 0.3)
    monkeypatch.setattr(click_scan.time, "time", lambda: next(clock))
    out = tmp_path / "out"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "nucbox-pmode-click-scan.py",
            "--duration",
            "1",
            "--phase-seconds",
            "1",
            "--threads",
            "0",
            "--out-dir",
            str(out),
        ],
    )
    rc_main = click_scan.main()
    rows = [json.loads(line) for line in sorted(out.glob("*.jsonl"))[0].read_text().splitlines()]
    summary = json.loads(sorted(out.glob("*.summary.json"))[0].read_text())
    return rc_main, rows, summary


def test_summary_flags_missing_ec_evidence(click_scan, monkeypatch, no_driver, tmp_path, capsys):
    rc_main, rows, summary = run_scan(
        click_scan, monkeypatch, tmp_path, stdout="", stderr="sudo: command not found", rc=127
    )
    assert rc_main == 0
    assert rows and all(row["ec_sys_readable"] is False for row in rows)
    assert all(row["ec_helper_rc"] == 127 for row in rows)
    assert all("command not found" in row["ec_helper_stderr_tail"] for row in rows)
    assert summary["ec_evidence_missing"] is True
    assert summary["ec_evidence"]["samples_missing_ec"] == len(rows)
    assert summary["ec_evidence"]["first_error"]
    # Deliberate choice: ok keeps its run/Tctl meaning; EC proof is its own
    # visible field (README) instead of silently changing ok for every run.
    assert summary["ok"] is True
    assert "EC evidence missing" in capsys.readouterr().out


def test_summary_reports_present_ec_evidence(click_scan, monkeypatch, no_driver, tmp_path, capsys):
    rc_main, rows, summary = run_scan(click_scan, monkeypatch, tmp_path, stdout=json.dumps(VALID_SAMPLE), rc=0)
    assert rc_main == 0
    assert rows and all(row["ec_sys_readable"] is True for row in rows)
    assert all(row["ec_temp_c"] == 41 for row in rows)
    assert summary["ec_evidence_missing"] is False
    assert summary["ec_evidence"]["samples_with_ec"] == len(rows)
    assert summary["ec_evidence"]["samples_missing_ec"] == 0
    assert "EC evidence missing" not in capsys.readouterr().out
