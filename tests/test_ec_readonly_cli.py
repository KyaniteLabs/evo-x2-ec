"""Operator-facing failure modes of nucbox-ec-readonly.py.

The first thing an operator meets is not a register dump: it is a dumper that
cannot reach the EC because the module is not loaded, because they are not
root, or because they asked for ``--help``. Each of those needs a different
action, so each gets its own message and its own exit code -- never a raw
traceback.

No test here needs EC hardware. The real ``/sys/kernel/debug/ec/ec0/io`` is
only ever touched by ``--help``, which must not touch it at all.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "nucbox-ec-readonly.py"


class UnreadablePath:
    """Stands in for debugfs: the path exists but reading it is denied."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def __fspath__(self) -> str:
        return str(self.path)

    def __str__(self) -> str:
        return str(self.path)

    def read_bytes(self) -> bytes:
        raise PermissionError(13, "Permission denied", str(self.path))


class Bomb:
    """Any attribute access means --help touched the EC path."""

    def __getattr__(self, name: str):
        raise AssertionError(f"--help touched the EC path (attribute {name!r})")


# --- --help ----------------------------------------------------------------


def test_help_exits_zero_without_touching_the_ec_path(ec_readonly, monkeypatch, capsys):
    monkeypatch.setattr(ec_readonly, "EC_IO", Bomb())

    with pytest.raises(SystemExit) as excinfo:
        ec_readonly.main(["--help"])

    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "usage:" in out
    assert "nucbox-ec-readonly.py" in out


def test_help_in_a_real_interpreter_on_a_host_without_ec_or_privileges():
    proc = subprocess.run([sys.executable, str(SCRIPT), "--help"], capture_output=True, text=True)

    assert proc.returncode == 0
    assert "usage:" in proc.stdout
    assert proc.stderr == ""


# --- path absent -----------------------------------------------------------


def test_absent_path_prints_the_existing_remediation(ec_readonly, monkeypatch, tmp_path, capsys):
    io = tmp_path / "ec" / "ec0" / "io"
    monkeypatch.setattr(ec_readonly, "EC_IO", io)

    rc = ec_readonly.main([])

    assert rc != 0
    assert capsys.readouterr().err.strip() == f"missing {io}; run: sudo modprobe ec_sys"


# --- path unreadable -------------------------------------------------------


def test_read_denied_names_the_cause_and_the_fix(ec_readonly, monkeypatch, tmp_path, capsys):
    io = tmp_path / "io"
    monkeypatch.setattr(ec_readonly, "EC_IO", UnreadablePath(io))

    rc = ec_readonly.main([])

    err = capsys.readouterr().err
    assert rc != 0
    assert "permission denied" in err.lower()
    assert "sudo" in err and "root" in err
    assert str(io) in err
    # the two causes must not share one message: "missing" means modprobe, not sudo
    assert "modprobe" not in err


@pytest.mark.skipif(
    os.geteuid() == 0, reason="root reads a chmod 000 file, so the denial never happens"
)
def test_present_but_unreadable_file_on_a_real_filesystem(ec_readonly, monkeypatch, tmp_path, capsys):
    io = tmp_path / "io"
    io.write_bytes(bytes(256))
    io.chmod(0o000)
    monkeypatch.setattr(ec_readonly, "EC_IO", io)

    rc = ec_readonly.main([])

    assert rc != 0
    assert "permission denied" in capsys.readouterr().err.lower()


# --- the real script, in a real interpreter, with no traceback -------------

DRIVER = """\
import importlib.util, sys
from pathlib import Path

spec = importlib.util.spec_from_file_location("ec_readonly", {script!r})
module = importlib.util.module_from_spec(spec)
sys.modules["ec_readonly"] = module
spec.loader.exec_module(module)
module.EC_IO = Path({io!r})
raise SystemExit(module.main(sys.argv[1:]))
"""


def run_script_with_io(tmp_path: Path, io: Path, *args: str) -> subprocess.CompletedProcess[str]:
    driver = tmp_path / "driver.py"
    driver.write_text(DRIVER.format(script=str(SCRIPT), io=str(io)))
    return subprocess.run([sys.executable, str(driver), *args], capture_output=True, text=True)


def test_absent_path_has_no_traceback(tmp_path):
    proc = run_script_with_io(tmp_path, tmp_path / "no-such-ec" / "io")

    assert proc.returncode != 0
    assert "missing" in proc.stderr and "sudo modprobe ec_sys" in proc.stderr
    assert "Traceback" not in proc.stderr


@pytest.mark.skipif(
    os.geteuid() == 0, reason="root reads a chmod 000 file, so the denial never happens"
)
def test_unreadable_path_has_no_traceback(tmp_path):
    io = tmp_path / "io"
    io.write_bytes(bytes(256))
    io.chmod(0o000)

    proc = run_script_with_io(tmp_path, io)

    assert proc.returncode != 0
    assert "permission denied" in proc.stderr.lower()
    assert "sudo" in proc.stderr and "root" in proc.stderr
    assert "Traceback" not in proc.stderr


# --- output contract -------------------------------------------------------

FIXTURE = bytes(range(256))  # deterministic: register N reads N


def test_register_map_is_unchanged(ec_readonly):
    assert ec_readonly.REGS == {
        "fan1_mode_raw": 0x21,
        "fan1_level_raw": 0x22,
        "fan2_mode_raw": 0x23,
        "fan2_level_raw": 0x24,
        "fan3_mode_raw": 0x25,
        "fan3_level_raw": 0x26,
        "fan3_rpm_hi": 0x28,
        "fan3_rpm_lo": 0x29,
        "apu_power_mode_raw": 0x31,
        "fan1_rpm_hi": 0x35,
        "fan1_rpm_lo": 0x36,
        "fan2_rpm_hi": 0x37,
        "fan2_rpm_lo": 0x38,
        "ec_temp_c": 0x70,
    }


def test_sample_keys_and_values_are_unchanged(ec_readonly, monkeypatch, tmp_path, capsys):
    io = tmp_path / "io"
    io.write_bytes(FIXTURE)
    monkeypatch.setattr(ec_readonly, "EC_IO", io)

    assert ec_readonly.main([]) == 0
    sample = json.loads(capsys.readouterr().out)

    assert set(sample) == {"ts", *ec_readonly.REGS, "apu_power_mode", "fan1_rpm", "fan2_rpm", "fan3_rpm"}
    assert sample["apu_power_mode_raw"] == 0x31
    assert sample["apu_power_mode"] == "unknown_49"
    assert sample["fan1_rpm"] == 0x35 * 256 + 0x36
    assert sample["fan2_rpm"] == 0x37 * 256 + 0x38
    assert sample["fan3_rpm"] == 0x28 * 256 + 0x29
    assert sample["ec_temp_c"] == 0x70
