"""The deploy units and scripts must only name files this repo actually ships."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CHECKER = REPO_ROOT / "scripts" / "check-deploy-references.py"


def run_checker(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CHECKER), str(root)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_shipped_references_resolve() -> None:
    result = run_checker(REPO_ROOT)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS" in result.stdout


def test_unshipped_executable_basename_fails(tmp_path: Path) -> None:
    write(tmp_path / "scripts" / "nucbox-pmode.py", "")
    write(
        tmp_path / "deploy" / "bad.service",
        "[Service]\nExecStart=/usr/local/bin/pre-rename-pmode.py set performance\n",
    )
    write(tmp_path / "deploy" / "bad.timer", "[Timer]\nUnit=bad.service\n")

    result = run_checker(tmp_path)

    assert result.returncode == 1, result.stdout
    assert "pre-rename-pmode.py" in result.stdout


def test_unknown_timer_unit_fails(tmp_path: Path) -> None:
    write(tmp_path / "scripts" / "nucbox-pmode.py", "")
    write(tmp_path / "deploy" / "bad.timer", "[Timer]\nUnit=missing.service\n")

    result = run_checker(tmp_path)

    assert result.returncode == 1, result.stdout
    assert "missing.service" in result.stdout
