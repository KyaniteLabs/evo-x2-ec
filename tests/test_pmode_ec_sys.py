"""ec_sys module lifecycle in nucbox-pmode.py (EcSysState)."""

from __future__ import annotations

import pytest
from hypothesis import settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, precondition, rule

from conftest import FakeKernel

KERNEL_FOR = {  # one fake-kernel setup per state; must cover the whole enum
    "ABSENT": dict(loaded=False, write_support=False),
    "READONLY": dict(loaded=True, write_support=False),
    "WRITABLE": dict(loaded=True, write_support=True),
}


def test_kernel_table_is_exhaustive(pmode):
    assert set(KERNEL_FOR) == {s.name for s in pmode.EcSysState}
    assert set(pmode._EC_SYS_RANK) == set(pmode.EcSysState)


@pytest.mark.parametrize("name", sorted(KERNEL_FOR))
def test_derived_state(pmode, make_kernel, name):
    make_kernel(**KERNEL_FOR[name])
    assert pmode.ec_sys_state() is pmode.EcSysState[name]


@pytest.mark.parametrize(("flag", "expected"), [
    ("Y\n", "WRITABLE"), ("1", "WRITABLE"), ("y", "WRITABLE"),
    ("N\n", "READONLY"), ("0", "READONLY"), ("garbage", "READONLY"),
])
def test_write_support_values_tolerated(pmode, make_kernel, flag, expected):
    kernel = make_kernel(loaded=True, write_support=False)
    kernel.param.write_text(flag)
    assert pmode.ec_sys_state() is pmode.EcSysState[expected]


def test_missing_param_file_reads_readonly(pmode, make_kernel):
    kernel = make_kernel(loaded=True, write_support=True)
    kernel.param.unlink()
    assert pmode.ec_sys_state() is pmode.EcSysState.READONLY


# --- ensure_ec_sys_loaded -------------------------------------------------

ENSURE_LOADED = {  # start -> (resulting state, modprobe.d default)
    "ABSENT": "READONLY",
    "READONLY": "READONLY",
    "WRITABLE": "WRITABLE",
}


@pytest.mark.parametrize("name", sorted(ENSURE_LOADED))
def test_ensure_loaded(pmode, make_kernel, name):
    assert set(ENSURE_LOADED) == {s.name for s in pmode.EcSysState}
    kernel = make_kernel(**KERNEL_FOR[name])
    assert pmode.ensure_ec_sys_loaded() is pmode.EcSysState[ENSURE_LOADED[name]]
    assert kernel.unloads == 0  # never cycles a loaded module


def test_ensure_loaded_honours_modprobe_d(pmode, make_kernel):
    make_kernel(loaded=False, write_support=False, modprobe_d_default=True)
    assert pmode.ensure_ec_sys_loaded() is pmode.EcSysState.WRITABLE


# --- enable_ec_sys_write ---------------------------------------------------

@pytest.mark.parametrize("name", sorted(KERNEL_FOR))
def test_enable_write_returns_prior(pmode, make_kernel, name):
    kernel = make_kernel(**KERNEL_FOR[name])
    assert pmode.enable_ec_sys_write() is pmode.EcSysState[name]
    assert pmode.ec_sys_state() is pmode.EcSysState.WRITABLE
    assert kernel.unloads == (1 if name == "READONLY" else 0)


def test_enable_write_refuses_when_module_busy(pmode, make_kernel):
    make_kernel(loaded=True, write_support=False, busy=True)
    with pytest.raises(pmode.EcSysError):
        pmode.enable_ec_sys_write()


# --- restore_ec_sys --------------------------------------------------------

def _all_pairs(pmode):
    return [(a, b) for a in pmode.EcSysState for b in pmode.EcSysState]


def test_restore_table(pmode, tmp_path, monkeypatch):
    rank = pmode._EC_SYS_RANK
    for current, prior in _all_pairs(pmode):
        sub = tmp_path / f"{current.name}-{prior.name}"
        sub.mkdir()
        kernel = FakeKernel(sub, **KERNEL_FOR[current.name], modprobe_d_default=True)
        monkeypatch.setattr(pmode, "EC_IO", kernel.io)
        monkeypatch.setattr(pmode, "WRITE_SUPPORT_PARAM", kernel.param)
        monkeypatch.setattr(pmode, "run", kernel.run)
        if rank[prior] > rank[current]:
            with pytest.raises(pmode.EcSysError):
                pmode.restore_ec_sys(prior)
            assert pmode.ec_sys_state() is current
        else:
            pmode.restore_ec_sys(prior)
            assert pmode.ec_sys_state() is prior, (current, prior)
            if current is prior:
                assert kernel.commands == []


# --- writes ----------------------------------------------------------------

@pytest.mark.parametrize("name", ["ABSENT", "READONLY"])
def test_write_refused_unless_writable(pmode, make_kernel, name):
    kernel = make_kernel(**KERNEL_FOR[name])
    with pytest.raises(pmode.EcSysError):
        pmode.write_ec_byte(pmode.POWER_REGISTER, 1)
    kernel.sync()
    assert kernel.ec[pmode.POWER_REGISTER] == 0


def test_write_window_restores_on_error(pmode, make_kernel):
    make_kernel(loaded=True, write_support=False)
    with pytest.raises(RuntimeError):
        with pmode.ec_sys_write_window():
            assert pmode.ec_sys_state() is pmode.EcSysState.WRITABLE
            raise RuntimeError("boom")
    assert pmode.ec_sys_state() is pmode.EcSysState.READONLY


# --- set_mode end to end (regressions) -------------------------------------

def test_set_mode_from_readonly_restores_readonly(pmode, make_kernel):
    kernel = make_kernel(loaded=True, write_support=False)
    sample = pmode.set_mode("performance")
    assert sample["changed"] is True and sample["apu_power_mode"] == "performance"
    assert kernel.ec[pmode.POWER_REGISTER] == 1
    assert pmode.ec_sys_state() is pmode.EcSysState.READONLY


def test_set_mode_keeps_fan_daemon_write_support(pmode, make_kernel):
    """BUG (old code): restore_readonly_ec_sys() always reloaded ec_sys without
    write_support, silently breaking the fan daemon's writes every 30 s
    (README, "module-cycling hazard"). It must leave a WRITABLE module alone,
    without even cycling it."""
    kernel = make_kernel(loaded=True, write_support=True)
    pmode.set_mode("performance")
    assert kernel.write_support, "set_mode downgraded ec_sys to read-only"
    assert kernel.unloads == 0, "set_mode cycled a module another writer depends on"


def test_set_mode_refuses_silent_discard(pmode, make_kernel):
    """Hardening: when the module is busy it cannot be reloaded with write
    support. The old code wrote anyway (silently discarded by the kernel) and
    only the read-back noticed, with a misleading "observed balanced". Now the
    write is refused up front and the message names the cause."""
    make_kernel(loaded=True, write_support=False, busy=True)
    with pytest.raises(SystemExit, match="write_support"):
        pmode.set_mode("performance")


def test_set_mode_noop_when_already_in_mode(pmode, make_kernel):
    kernel = make_kernel(loaded=True, write_support=False)
    kernel.ec[pmode.POWER_REGISTER] = 2
    kernel.io.write_bytes(bytes(kernel.ec))
    assert pmode.set_mode("quiet")["changed"] is False
    assert kernel.commands == []


# --- power mode table --------------------------------------------------------

def test_power_mode_names(pmode, ec_readonly):
    assert pmode.POWER_MODE_NAMES == ec_readonly.POWER_MODES  # the two copies agree
    for raw, name in pmode.POWER_MODE_NAMES.items():
        assert pmode.power_mode_name(raw) == name
    assert pmode.power_mode_name(7) == "unknown_7"


# --- stateful property test -----------------------------------------------

class EcSysMachine(RuleBasedStateMachine):
    """Random interleavings of our transitions with outside actors (the fan
    daemon loading the module writable, someone unloading it)."""

    def __init__(self) -> None:
        super().__init__()
        import tempfile
        from pathlib import Path

        from conftest import load_script

        self._tmp = tempfile.TemporaryDirectory()
        self.pmode = load_script("nucbox-pmode")
        self.kernel = FakeKernel(Path(self._tmp.name), loaded=False, write_support=False)
        self.pmode.EC_IO = self.kernel.io
        self.pmode.WRITE_SUPPORT_PARAM = self.kernel.param
        self.pmode.run = self.kernel.run
        self.prior = None

    def teardown(self) -> None:
        self._tmp.cleanup()

    def state(self):
        return self.pmode.ec_sys_state()

    @rule(default=st.booleans())
    def set_modprobe_d(self, default):
        self.kernel.modprobe_d_default = default

    @rule()
    def ensure_loaded(self):
        before = self.state()
        after = self.pmode.ensure_ec_sys_loaded()
        assert after is not self.pmode.EcSysState.ABSENT
        if before is not self.pmode.EcSysState.ABSENT:
            assert after is before

    @precondition(lambda self: self.prior is None)
    @rule()
    def enable(self):
        self.prior = self.pmode.enable_ec_sys_write()
        assert self.state() is self.pmode.EcSysState.WRITABLE

    @precondition(lambda self: self.prior is not None)
    @rule(value=st.integers(0, 2))
    def write(self, value):
        self.pmode.write_ec_byte(self.pmode.POWER_REGISTER, value)
        self.kernel.sync()
        assert self.kernel.ec[self.pmode.POWER_REGISTER] == value

    @precondition(lambda self: self.prior is not None)
    @rule()
    def restore(self):
        prior, self.prior = self.prior, None
        self.pmode.restore_ec_sys(prior)
        assert self.state() is prior

    @precondition(lambda self: self.prior is None)
    @rule(writable=st.booleans())
    def outside_reload(self, writable):
        self.kernel.run(["modprobe", "-r", "ec_sys"], check=False)
        self.kernel.run(["modprobe", "ec_sys", f"write_support={int(writable)}"])

    @rule(value=st.integers(0, 255))
    def write_outside_window_is_refused_or_lands(self, value):
        if self.state() is self.pmode.EcSysState.WRITABLE:
            self.pmode.write_ec_byte(0x10, value)
        else:
            with pytest.raises(self.pmode.EcSysError):
                self.pmode.write_ec_byte(0x10, value)

    @invariant()
    def state_matches_kernel(self):
        kernel_state = (
            "ABSENT" if not self.kernel.loaded
            else "WRITABLE" if self.kernel.write_support else "READONLY"
        )
        assert self.state() is self.pmode.EcSysState[kernel_state]


TestEcSysMachine = EcSysMachine.TestCase
TestEcSysMachine.settings = settings(max_examples=150, stateful_step_count=30, deadline=None)
