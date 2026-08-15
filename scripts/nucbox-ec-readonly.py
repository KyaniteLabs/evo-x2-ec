#!/usr/bin/env python3
"""Read known EVO-X2/Sixunited AXB35 EC registers through Linux ec_sys.

This is read-only. It expects `sudo modprobe ec_sys` to have exposed
`/sys/kernel/debug/ec/ec0/io`.
"""

from __future__ import annotations

import json
import time
from pathlib import Path


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


def rpm(data: bytes, hi: int, lo: int, fan_name: str) -> int:
    value = data[hi] * 256 + data[lo]
    if fan_name == "fan3" and value == 8000:
        return 0
    return value


def main() -> int:
    io = Path("/sys/kernel/debug/ec/ec0/io")
    if not io.exists():
        raise SystemExit("missing /sys/kernel/debug/ec/ec0/io; run: sudo modprobe ec_sys")
    data = io.read_bytes()
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
