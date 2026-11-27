#!/usr/bin/env python3
"""Read known EVO-X2/Sixunited AXB35 EC registers through Linux ec_sys.

This is read-only: no register is ever written. It expects root and
`sudo modprobe ec_sys` to have exposed `/sys/kernel/debug/ec/ec0/io`.

The three ways an operator can fail to reach the EC need three different
actions, so each gets its own message on stderr and a non-zero exit:

* module not loaded  -> `missing ...; run: sudo modprobe ec_sys`
* not root           -> `cannot read ...: permission denied ... rerun with sudo
  or as root`
* `--help`           -> usage on stdout, exit 0, the EC path is never touched.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path


EC_IO = Path("/sys/kernel/debug/ec/ec0/io")

POWER_MODES = {
    0x00: "balanced",
    0x01: "performance",
    0x02: "quiet",
}

REGS = {
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


class EcUnavailable(RuntimeError):
    """The EC debug interface cannot be used; the message is the operator's fix."""


def rpm(data: bytes, hi: int, lo: int, fan_name: str) -> int:
    value = data[hi] * 256 + data[lo]
    if fan_name == "fan3" and value == 8000:
        return 0
    return value


def read_ec_io() -> bytes:
    """Read the EC register file, mapping each failure to its own remedy.

    Reading first (instead of probing with ``Path.exists()``) keeps the
    diagnosis honest on every supported Python: 3.12 lets ``exists()`` raise
    ``PermissionError`` for an unreadable debugfs, while 3.14 turns that same
    denial into ``False`` and would misreport it as "module not loaded".
    """
    try:
        return EC_IO.read_bytes()
    except FileNotFoundError:
        raise EcUnavailable(f"missing {EC_IO}; run: sudo modprobe ec_sys") from None
    except PermissionError:
        raise EcUnavailable(
            f"cannot read {EC_IO}: permission denied; debugfs is root-only, "
            "rerun with sudo or as root"
        ) from None
    except OSError as exc:
        raise EcUnavailable(f"cannot read {EC_IO}: {exc.strerror or exc}") from None


def build_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        prog="nucbox-ec-readonly.py",
        description="Dump known read-side EVO-X2/Sixunited AXB35 EC registers as JSON.",
        epilog=(
            "Read-only: no register is written. Requires root and "
            "/sys/kernel/debug/ec/ec0/io (sudo modprobe ec_sys)."
        ),
    )


def main(argv: list[str] | None = None) -> int:
    # Parsed first, so --help prints usage and exits 0 without touching the EC.
    build_parser().parse_args(argv)
    try:
        data = read_ec_io()
    except EcUnavailable as exc:
        print(exc, file=sys.stderr)
        return 1
    raw = {name: data[offset] for name, offset in REGS.items()}
    sample = {
        "ts": time.time(),
        **raw,
        "apu_power_mode": POWER_MODES.get(raw["apu_power_mode_raw"], f"unknown_{raw['apu_power_mode_raw']}"),
        "fan1_rpm": rpm(data, 0x35, 0x36, "fan1"),
        "fan2_rpm": rpm(data, 0x37, 0x38, "fan2"),
        "fan3_rpm": rpm(data, 0x28, 0x29, "fan3"),
    }
    print(json.dumps(sample, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
