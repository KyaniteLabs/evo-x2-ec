"""Load the deployable scripts (dash-named, not a package) as modules.

Nothing here touches real hardware: every test that exercises EC access swaps
the module's sysfs paths and ``run`` for an in-memory FakeKernel.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def load_script(name: str):
    path = SCRIPTS / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FakeKernel:
    """Just enough of modprobe + ec_sys to model the module lifecycle.

    * ``modprobe ec_sys [write_support=N]`` on an unloaded module loads it with
      that option, else with the modprobe.d default; on a loaded module it is a
      no-op (exactly like real modprobe).
    * ``modprobe -r ec_sys`` fails while ``busy`` (another process holds it).
    """

    def __init__(self, root: Path, *, loaded: bool, write_support: bool,
                 modprobe_d_default: bool = False, busy: bool = False) -> None:
        self.io = root / "io"
        self.param = root / "write_support"
        self.modprobe_d_default = modprobe_d_default
        self.busy = busy
        self.ec = bytearray(256)
        self.commands: list[list[str]] = []
        self.unloads = 0
        if loaded:
            self._load(write_support)

    @property
    def loaded(self) -> bool:
        return self.io.exists()

    @property
    def write_support(self) -> bool:
        return self.loaded and self.param.read_text().strip() == "Y"

    def _load(self, write_support: bool) -> None:
        self.io.write_bytes(bytes(self.ec))
        self.param.write_text("Y\n" if write_support else "N\n")

    def sync(self) -> None:
        """Apply what was written to the debugfs file -- or discard it (the trap)."""
        if not self.loaded:
            return
        data = self.io.read_bytes()
        if self.write_support:
            self.ec[:] = data
        else:
            self.io.write_bytes(bytes(self.ec))

    def run(self, cmd: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
        self.sync()
        self.commands.append(list(cmd))
        rc = 0
        if cmd[:3] == ["modprobe", "-r", "ec_sys"]:
            if self.loaded and self.busy:
                rc = 1
            elif self.loaded:
                self.io.unlink()
                self.param.unlink()
                self.unloads += 1
        elif cmd[:2] == ["modprobe", "ec_sys"]:
            if not self.loaded:
                ws = self.modprobe_d_default
                for opt in cmd[2:]:
                    if opt.startswith("write_support="):
                        ws = opt.split("=", 1)[1] in ("1", "Y", "y")
                self._load(ws)
        else:
            raise AssertionError(f"unexpected command {cmd}")
        if check and rc:
            raise subprocess.CalledProcessError(rc, cmd)
        return subprocess.CompletedProcess(cmd, rc, "", "")


@pytest.fixture(scope="session")
def pmode():
    return load_script("nucbox-pmode")


@pytest.fixture(scope="session")
def click_scan():
    return load_script("nucbox-pmode-click-scan")


@pytest.fixture(scope="session")
def ec_readonly():
    return load_script("nucbox-ec-readonly")


@pytest.fixture
def make_kernel(tmp_path, monkeypatch, pmode):
    def factory(**kwargs) -> FakeKernel:
        kernel = FakeKernel(tmp_path, **kwargs)
        monkeypatch.setattr(pmode, "EC_IO", kernel.io)
        monkeypatch.setattr(pmode, "WRITE_SUPPORT_PARAM", kernel.param, raising=False)
        monkeypatch.setattr(pmode, "run", kernel.run)
        monkeypatch.setattr(pmode.time, "sleep", lambda _s: kernel.sync())
        return kernel

    return factory
