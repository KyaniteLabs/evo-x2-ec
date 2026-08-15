#!/usr/bin/env python3
"""Read or set the EVO-X2/Sixunited AXB35 EC P-MODE register.

The only write this script performs is a single byte to EC register 0x31:

  0x00 balanced
  0x01 performance
  0x02 quiet

For safety, write support for Linux ec_sys is enabled only around the write and
then the module is reloaded read-only again.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from pathlib import Path


EC_IO = Path("/sys/kernel/debug/ec/ec0/io")
POWER_REGISTER = 0x31
POWER_MODES = {
    "balanced": 0x00,
    "performance": 0x01,
    "quiet": 0x02,
}
POWER_MODE_NAMES = {value: key for key, value in POWER_MODES.items()}


def require_root_for_set(action: str) -> None:
    if action == "set" and os.geteuid() != 0:
        raise SystemExit("setting P-MODE requires root; run with sudo")


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=check)


def load_ec_sys(write: bool) -> None:
    if write:
        run(["modprobe", "-r", "ec_sys"], check=False)
        run(["modprobe", "ec_sys", "write_support=1"])
    else:
        if not EC_IO.exists():
            run(["modprobe", "ec_sys"])


def restore_readonly_ec_sys() -> None:
    run(["modprobe", "-r", "ec_sys"], check=False)
    run(["modprobe", "ec_sys"], check=False)


def read_sample() -> dict[str, object]:
    load_ec_sys(write=False)
    if not EC_IO.exists():
        raise SystemExit("missing /sys/kernel/debug/ec/ec0/io after loading ec_sys")
    data = EC_IO.read_bytes()
    raw = data[POWER_REGISTER]
    fan3_rpm = data[0x28] * 256 + data[0x29]
    if fan3_rpm == 8000:
        fan3_rpm = 0
    return {
        "ts": time.time(),
        "apu_power_mode": POWER_MODE_NAMES.get(raw, f"unknown_{raw}"),
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

    load_ec_sys(write=True)
    try:
        with EC_IO.open("r+b", buffering=0) as fh:
            fh.seek(POWER_REGISTER)
            fh.write(bytes([target]))
        time.sleep(0.2)
    finally:
        restore_readonly_ec_sys()

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
