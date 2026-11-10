#!/usr/bin/env python3
"""Read or set the EVO-X2/Sixunited AXB35 EC P-MODE register.

The only write this script performs is a single byte to EC register 0x31:

  0x00 balanced
  0x01 performance
  0x02 quiet

For safety, write support for Linux ec_sys is enabled only around the write and
the module is then returned to exactly the state it was in before (see
EcSysState). If it was already write-capable -- e.g. the fan daemon or
modprobe.d enabled it -- it is not cycled at all.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager
from enum import Enum
from pathlib import Path


EC_IO = Path("/sys/kernel/debug/ec/ec0/io")
WRITE_SUPPORT_PARAM = Path("/sys/module/ec_sys/parameters/write_support")
POWER_REGISTER = 0x31
POWER_MODES = {
    "balanced": 0x00,
    "performance": 0x01,
    "quiet": 0x02,
}
POWER_MODE_NAMES = {value: key for key, value in POWER_MODES.items()}


class EcSysState(Enum):
    """Lifecycle of the kernel ``ec_sys`` module, as observed from sysfs.

    Legal transitions (every move goes through exactly one function below)::

                 ensure_ec_sys_loaded()
        ABSENT ─────────────────────────▶ READONLY or WRITABLE
                                          (whatever modprobe.d says)

                 enable_ec_sys_write()
        ABSENT / READONLY ──────────────▶ WRITABLE
        WRITABLE ───────────────────────▶ WRITABLE   (no-op: never cycle the
                                                      module under another writer)

                 restore_ec_sys(prior)    (only ever moves *down*)
        WRITABLE ───────────────────────▶ READONLY / ABSENT
        READONLY ───────────────────────▶ ABSENT
        X ──────────────────────────────▶ X          (no-op)

    Writing the EC is only legal in WRITABLE: in READONLY the kernel silently
    discards writes (see README, "the ec_sys write-support trap").
    """

    ABSENT = "absent"
    READONLY = "readonly"
    WRITABLE = "writable"


# Order used by restore_ec_sys(): it may only move to a lower rank.
_EC_SYS_RANK = {EcSysState.ABSENT: 0, EcSysState.READONLY: 1, EcSysState.WRITABLE: 2}


class EcSysError(RuntimeError):
    """An illegal or failed ec_sys transition."""


def require_root_for_set(action: str) -> None:
    if action == "set" and os.geteuid() != 0:
        raise SystemExit("setting P-MODE requires root; run with sudo")


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=check)


def ec_sys_state() -> EcSysState:
    """Derive the module state from sysfs/debugfs. Unknown values read as READONLY
    (the safe answer: nothing will be written)."""
    if not EC_IO.exists():
        return EcSysState.ABSENT
    try:
        flag = WRITE_SUPPORT_PARAM.read_text(encoding="utf-8").strip()
    except OSError:
        return EcSysState.READONLY
    return EcSysState.WRITABLE if flag in ("Y", "y", "1") else EcSysState.READONLY


def ensure_ec_sys_loaded() -> EcSysState:
    """ABSENT -> loaded (module defaults). Any loaded state is left untouched."""
    if ec_sys_state() is EcSysState.ABSENT:
        run(["modprobe", "ec_sys"])
    state = ec_sys_state()
    if state is EcSysState.ABSENT:
        raise EcSysError(f"missing {EC_IO} after loading ec_sys")
    return state


def enable_ec_sys_write() -> EcSysState:
    """Move to WRITABLE and return the state observed before, for restore_ec_sys().

    Already WRITABLE is a no-op: unloading would pull the debugfs file out from
    under any other EC writer (the fan daemon) for no reason.
    """
    prior = ec_sys_state()
    if prior is EcSysState.WRITABLE:
        return prior
    run(["modprobe", "-r", "ec_sys"], check=False)
    run(["modprobe", "ec_sys", "write_support=1"])
    if ec_sys_state() is not EcSysState.WRITABLE:
        # e.g. the module was busy, so the reload never happened.
        raise EcSysError("ec_sys did not come up with write_support=1; refusing to write")
    return prior


def restore_ec_sys(prior: EcSysState) -> None:
    """Return the module to ``prior``. Only downward moves are legal."""
    current = ec_sys_state()
    if current is prior:
        return
    if _EC_SYS_RANK[prior] > _EC_SYS_RANK[current]:
        raise EcSysError(f"illegal ec_sys restore {current.value} -> {prior.value}")
    run(["modprobe", "-r", "ec_sys"], check=False)
    if prior is EcSysState.READONLY:
        # Explicit, so a modprobe.d write_support=1 default cannot override it.
        run(["modprobe", "ec_sys", "write_support=0"], check=False)


@contextmanager
def ec_sys_write_window() -> Iterator[None]:
    prior = enable_ec_sys_write()
    try:
        yield
    finally:
        restore_ec_sys(prior)


def write_ec_byte(offset: int, value: int) -> None:
    """The single EC write path. Refused unless the module is WRITABLE."""
    state = ec_sys_state()
    if state is not EcSysState.WRITABLE:
        raise EcSysError(f"refusing EC write while ec_sys is {state.value}")
    with EC_IO.open("r+b", buffering=0) as fh:
        fh.seek(offset)
        fh.write(bytes([value]))


def power_mode_name(raw: int) -> str:
    return POWER_MODE_NAMES.get(raw, f"unknown_{raw}")


def read_sample() -> dict[str, object]:
    try:
        ensure_ec_sys_loaded()
    except EcSysError as exc:
        raise SystemExit(str(exc)) from exc
    data = EC_IO.read_bytes()
    raw = data[POWER_REGISTER]
    fan3_rpm = data[0x28] * 256 + data[0x29]
    if fan3_rpm == 8000:
        fan3_rpm = 0
    return {
        "ts": time.time(),
        "apu_power_mode": power_mode_name(raw),
        "apu_power_mode_raw": raw,
        "ec_temp_c": data[0x70],
        "fan1_rpm": data[0x35] * 256 + data[0x36],
        "fan2_rpm": data[0x37] * 256 + data[0x38],
        "fan3_rpm": fan3_rpm,
    }


def set_mode(mode: str) -> dict[str, object]:
    target = POWER_MODES[mode]
    current = read_sample()
    if current["apu_power_mode_raw"] == target:
        current["changed"] = False
        return current

    try:
        with ec_sys_write_window():
            write_ec_byte(POWER_REGISTER, target)
            time.sleep(0.2)
    except EcSysError as exc:
        raise SystemExit(f"failed to set {mode}: {exc}") from exc

    sample = read_sample()
    if sample["apu_power_mode_raw"] != target:
        raise SystemExit(f"failed to set {mode}; observed {sample}")
    sample["changed"] = True
    return sample


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("status")
    set_parser = sub.add_parser("set")
    set_parser.add_argument("mode", choices=sorted(POWER_MODES))
    args = parser.parse_args()

    require_root_for_set(args.action)
    if args.action == "status":
        sample = read_sample()
    elif args.action == "set":
        sample = set_mode(args.mode)
    else:
        raise AssertionError(args.action)

    print(json.dumps(sample, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
