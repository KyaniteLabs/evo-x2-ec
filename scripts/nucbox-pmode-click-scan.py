#!/usr/bin/env python3
"""Detect EVO-X2 P-MODE effects by sampling watts/clocks while the button is cycled.

This does not require Linux to see the physical button event. It measures the
effect of the firmware mode by running a fixed, bounded CPU workload and logging
CPU package watts, GPU/APU watts, clocks, and temperatures.
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


def run(cmd: list[str], timeout: int = 10) -> str:
    result = subprocess.run(
        cmd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        timeout=timeout,
        check=False,
    )
    return result.stdout


def number(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.strip().replace("W", "").replace("C", "").replace("Mhz", "").strip("() ")
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def sensor_snapshot() -> dict[str, Any]:
    raw = run(["sensors", "-j"], timeout=5)
    try:
        data = json.loads(raw) if raw.strip() else {}
    except Exception as exc:  # noqa: BLE001
        return {"sensors_error": repr(exc)}

    def find(prefix: str) -> dict[str, Any]:
        return next((value for key, value in data.items() if key.startswith(prefix)), {})

    k10 = find("k10temp-")
    amd = find("amdgpu-")
    nvme = find("nvme-")
    acpi = find("acpitz-")
    return {
        "tctl_c": k10.get("Tctl", {}).get("temp1_input"),
        "acpi_c": acpi.get("temp1", {}).get("temp1_input"),
        "gpu_edge_c": amd.get("edge", {}).get("temp1_input"),
        "gpu_ppt_w": (
            amd.get("PPT", {}).get("power1_average")
            or amd.get("PPT", {}).get("power1_input")
        ),
        "nvme_c": nvme.get("Composite", {}).get("temp1_input"),
    }


def rocm_snapshot() -> dict[str, Any]:
    raw = run(["rocm-smi", "--showtemp", "--showpower", "--showclocks", "--json"], timeout=8)
    try:
        data = json.loads(raw) if raw.strip() else {}
    except Exception as exc:  # noqa: BLE001
        return {"rocm_smi_error": repr(exc)}
    card = data.get("card0", {})
    return {
        "rocm_edge_c": number(card.get("Temperature (Sensor edge) (C)")),
        "rocm_power_w": number(card.get("Current Socket Graphics Package Power (W)")),
        "rocm_sclk_mhz": number(card.get("sclk clock speed:")),
        "rocm_sclk_level": card.get("sclk clock level:"),
    }


def turbostat_snapshot() -> dict[str, Any]:
    raw = run(["sudo", "turbostat", "--Summary", "--quiet", "--interval", "1", "--num_iterations", "1"], timeout=5)
    lines = [line for line in raw.splitlines() if line.strip()]
    if len(lines) < 2:
        return {"turbostat_error": raw[-500:]}
    header = lines[0].split()
    values = lines[-1].split()
    row = dict(zip(header, values, strict=False))
    return {
        "avg_mhz": number(row.get("Avg_MHz")),
        "busy_pct": number(row.get("Busy%")),
        "busy_mhz": number(row.get("Bzy_MHz")),
        "core_w": number(row.get("CorWatt")),
        "pkg_w": number(row.get("PkgWatt")),
        "ipc": number(row.get("IPC")),
    }


def cpufreq_snapshot() -> dict[str, Any]:
    root = Path("/sys/devices/system/cpu/cpu0/cpufreq")

    def read(name: str) -> str | None:
        path = root / name
        return path.read_text(encoding="utf-8").strip() if path.exists() else None

    return {
        "scaling_governor": read("scaling_governor"),
        "scaling_min_freq": read("scaling_min_freq"),
        "scaling_max_freq": read("scaling_max_freq"),
        "scaling_cur_freq": read("scaling_cur_freq"),
        "epp": read("energy_performance_preference"),
        "boost": Path("/sys/devices/system/cpu/cpufreq/boost").read_text(encoding="utf-8").strip()
        if Path("/sys/devices/system/cpu/cpufreq/boost").exists()
        else None,
    }


def ec_snapshot() -> dict[str, Any]:
    root = Path("/sys/class/ec_su_axb35")
    if not root.exists():
        raw = run(["sudo", "/usr/local/bin/nucbox-ec-readonly.py"], timeout=5)
        try:
            sample = json.loads(raw) if raw.strip() else {}
        except Exception:
            return {"ec_driver_loaded": False, "ec_sys_readable": False}
        return {
            "ec_driver_loaded": False,
            "ec_sys_readable": True,
            "ec_power_mode": sample.get("apu_power_mode"),
            "ec_power_mode_raw": sample.get("apu_power_mode_raw"),
            "ec_temp_c": sample.get("ec_temp_c"),
            "fan1_rpm": sample.get("fan1_rpm"),
            "fan2_rpm": sample.get("fan2_rpm"),
            "fan3_rpm": sample.get("fan3_rpm"),
        }

    def read(path: Path) -> str | None:
        return path.read_text(encoding="utf-8").strip() if path.exists() else None

    sample: dict[str, Any] = {
        "ec_driver_loaded": True,
        "ec_power_mode": read(root / "apu" / "power_mode"),
    }
    for fan in sorted(root.glob("fan*")):
        if not fan.is_dir():
            continue
        sample[f"{fan.name}_rpm"] = read(fan / "rpm")
        sample[f"{fan.name}_mode"] = read(fan / "mode")
        sample[f"{fan.name}_level"] = read(fan / "level")
    return sample


def start_load(duration: int, threads: int) -> subprocess.Popen[bytes] | None:
    if threads <= 0:
        return None
    return subprocess.Popen(
        [
            "timeout",
            str(duration + 5),
            "openssl",
            "speed",
            "-elapsed",
            "-seconds",
            str(duration + 5),
            "-multi",
            str(threads),
            "sha256",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def summarize(samples: list[dict[str, Any]], phase_seconds: int) -> list[dict[str, Any]]:
    if not samples:
        return []
    started = samples[0]["elapsed_s"]
    phases: dict[int, list[dict[str, Any]]] = {}
    for sample in samples:
        phase = int((sample["elapsed_s"] - started) // phase_seconds)
        phases.setdefault(phase, []).append(sample)

    def values(items: list[dict[str, Any]], key: str) -> list[float]:
        return [float(item[key]) for item in items if isinstance(item.get(key), (int, float))]

    summary = []
    for phase, items in sorted(phases.items()):
        row: dict[str, Any] = {
            "phase": phase,
            "start_s": phase * phase_seconds,
            "end_s": (phase + 1) * phase_seconds,
            "samples": len(items),
        }
        for key in ("pkg_w", "gpu_ppt_w", "rocm_power_w", "avg_mhz", "busy_mhz", "tctl_c", "gpu_edge_c"):
            vals = values(items, key)
            if vals:
                row[f"{key}_avg"] = round(statistics.fmean(vals), 3)
                row[f"{key}_max"] = round(max(vals), 3)
        summary.append(row)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", type=int, default=75)
    parser.add_argument("--phase-seconds", type=int, default=25)
    parser.add_argument(
        "--threads",
        type=int,
        default=0,
        help="Optional openssl CPU workers. Default 0 is passive/no synthetic load.",
    )
    parser.add_argument("--max-tctl", type=float, default=82.0)
    parser.add_argument("--out-dir", default="/srv/benchmarks/pmode-click-scan")
    parser.add_argument("--label", default="pmode-click-scan")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    jsonl_path = out_dir / f"{args.label}-{stamp}.jsonl"
    summary_path = out_dir / f"{args.label}-{stamp}.summary.json"

    print(f"Writing {jsonl_path}", flush=True)
    print(
        f"Run plan: {args.duration}s, {args.phase_seconds}s phases, {args.threads} openssl workers. "
        "Click P-MODE once at each phase boundary.",
        flush=True,
    )
    if args.threads == 0:
        print("Passive mode: no synthetic load. Best used while a real workload is already running.", flush=True)

    load = start_load(args.duration, args.threads)
    samples: list[dict[str, Any]] = []
    started = time.time()
    ok = True
    stop_reason = "completed"
    next_phase = 1

    try:
        with jsonl_path.open("a", encoding="utf-8") as fh:
            while time.time() - started < args.duration:
                elapsed = time.time() - started
                while elapsed >= next_phase * args.phase_seconds:
                    print(f"PHASE {next_phase}: click P-MODE once now if cycling manually.", flush=True)
                    next_phase += 1

                sample = {
                    "type": "sample",
                    "ts": time.time(),
                    "elapsed_s": round(elapsed, 3),
                    "phase": int(elapsed // args.phase_seconds),
                    **cpufreq_snapshot(),
                    **sensor_snapshot(),
                    **rocm_snapshot(),
                    **turbostat_snapshot(),
                    **ec_snapshot(),
                }
                samples.append(sample)
                fh.write(json.dumps(sample, sort_keys=True) + "\n")
                fh.flush()

                print(
                    "t={elapsed:5.1f}s phase={phase} Tctl={tctl}C pkg={pkg}W gpu={gpu}W "
                    "avg={avg}MHz busy={busy}%".format(
                        elapsed=elapsed,
                        phase=sample["phase"],
                        tctl=sample.get("tctl_c"),
                        pkg=sample.get("pkg_w"),
                        gpu=sample.get("gpu_ppt_w") or sample.get("rocm_power_w"),
                        avg=sample.get("avg_mhz"),
                        busy=sample.get("busy_pct"),
                    ),
                    flush=True,
                )

                tctl = sample.get("tctl_c")
                if isinstance(tctl, (int, float)) and tctl >= args.max_tctl:
                    ok = False
                    stop_reason = f"tctl_guard_{tctl}C"
                    break
    finally:
        if load is not None and load.poll() is None:
            load.terminate()
            try:
                load.wait(timeout=5)
            except subprocess.TimeoutExpired:
                load.kill()

    summary = {
        "ok": ok,
        "stop_reason": stop_reason,
        "duration_s": args.duration,
        "phase_seconds": args.phase_seconds,
        "threads": args.threads,
        "max_tctl": args.max_tctl,
        "jsonl_path": str(jsonl_path),
        "summary_path": str(summary_path),
        "phase_summary": summarize(samples, args.phase_seconds),
    }
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True), flush=True)
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
