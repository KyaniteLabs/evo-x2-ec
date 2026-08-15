# GMKtec EVO-X2 (AMD Strix Halo) — Embedded-Controller Fan & Power-Mode Control

Reverse-engineered register map, kernel traps, and a working fan-control setup
for the GMKtec EVO-X2 (AMD Ryzen AI Max+ 395, "Strix Halo", Sixunited
AXB35-02 mainboard), observed on Linux via the kernel `ec_sys` interface.

> **Honest framing — read this first.**
> This document is a **validation and extension of prior work, not a
> from-scratch discovery.** The original reverse-engineering of the EC P-MODE
> register on this machine was done by **Simon Gonzalez de Cruz in May 2026**
> (the `scripts/` in this repo are those artifacts, lightly sanitized). The
> fan-control daemon deployed and validated here is
> [nathanmarlor/strix-halo-fan-control](https://github.com/nathanmarlor/strix-halo-fan-control)
> (MIT), reverse-engineered from the ACPI DSDT of the **Bosgame M5** — a
> rebrand of the *same* Sixunited AXB35-02 board used in the EVO-X2. The
> August 2026 session summarized here confirmed that upstream register map on
> a second chassis (EVO-X2), extended it, characterized two failure modes of
> the Linux EC access path, and deployed the daemon with a tuned curve.
> See [Credits](#credits) for the full attribution chain.

---

## Table of contents

1. [Hardware context](#hardware-context)
2. [EC access path and the `ec_sys` write-support trap](#ec-access-path-and-the-ec_sys-write-support-trap)
3. [EC register map](#ec-register-map)
4. [The one-shot revert law](#the-one-shot-revert-law)
5. [The module-cycling hazard (multiple EC writers)](#the-module-cycling-hazard-multiple-ec-writers)
6. [P-MODE mechanism and enforcement](#p-mode-mechanism-and-enforcement)
7. [Measurements: stock curve and fan maxima](#measurements-stock-curve-and-fan-maxima)
8. [Fan daemon deploy recipe and tuned curve](#fan-daemon-deploy-recipe-and-tuned-curve)
9. [The WMI/FCMI alternative (prior art, untested here)](#the-wmifcmi-alternative-prior-art-untested-here)
10. [Safety notes](#safety-notes)
11. [Repository layout](#repository-layout)
12. [Credits](#credits)

---

## Hardware context

- **Machine:** GMKtec EVO-X2 mini-PC
- **APU:** AMD Ryzen AI Max+ 395 (Strix Halo)
- **Mainboard:** Sixunited AXB35-02 — shared with the **Bosgame M5** (on which
  [nathanmarlor/strix-halo-fan-control](https://github.com/nathanmarlor/strix-halo-fan-control)
  was developed) and the FEVM FA-EX9
- **EC:** the M5 carries an ITE IT5570; the EVO-X2 exposes the same register
  layout, which is what matters here (chip model on the EVO-X2 unit was not
  independently verified)
- **The problem:** the firmware exposes **no standard Linux fan control** — no
  hwmon PWM, no fan tach hwmon, no ACPI fan object — and its auto curve is
  tuned for quiet, letting the package ride the thermal edge under sustained
  load. The only lever is direct Embedded-Controller access.

## EC access path and the `ec_sys` write-support trap

All reads/writes here go through the kernel `ec_sys` module's debugfs file:

```
/sys/kernel/debug/ec/ec0/io
```

**The trap.** By default `ec_sys` loads **read-only**. In that state the
debugfs file *looks* writable, and writes to it are **silently discarded** —
`dd of=/sys/kernel/debug/ec/ec0/io` exits **0** with no error. You will
believe your write succeeded; the EC never saw it. This cost real debugging
time twice (once against the P-MODE register, once against the fan duty
registers).

**The fix** — make write support the module default and load it at boot:

`/etc/modprobe.d/ec_sys-write.conf`
```
options ec_sys write_support=1
```

`/etc/modules-load.d/ec_sys.conf`
```
ec_sys
```

Then `modprobe ec_sys` (by anything, at any time) comes up write-capable.
Copies of both files are in [`deploy/`](deploy/). Verify with:

```
cat /sys/module/ec_sys/parameters/write_support   # want: Y
```

> A write that succeeds *and sticks* (see the revert law below) is the only
> real proof the trap is not biting you.

## EC register map

Registers below were observed on an EVO-X2 running Linux. The "known since"
column credits when each register first entered the map on this machine —
May 2026 (the original P-MODE reverse-engineering session, whose scripts read
the read-side registers) or August 2026 (the validation/extension session).

| Register | Meaning | Encoding / notes | Known since |
|---|---|---|---|
| `0x21` | fan1 mode byte | semantics not fully characterized (firmware-internal fan state) | May 2026 |
| `0x22` | fan1 level byte | semantics not fully characterized | May 2026 |
| `0x23` | fan2 mode byte | semantics not fully characterized | May 2026 |
| `0x24` | fan2 level byte | semantics not fully characterized | May 2026 |
| `0x25` | fan3 mode byte | semantics not fully characterized | May 2026 |
| `0x26` | fan3 level byte | semantics not fully characterized | May 2026 |
| `0x28`/`0x29` | **fan3 RPM** | big-endian pair: `(b[0x28]<<8)\|b[0x29]`; value **8000 (0x1F40) is a "no fan" sentinel** → treat as 0 | May 2026 |
| `0x31` | **APU power mode ("P-MODE")** | `0x00` balanced, `0x01` performance, `0x02` quiet | May 2026 |
| `0x33` | **fan1 duty** | write `0x80 \| duty` (duty 0–100) for manual; write `0x00` to return to firmware auto | Aug 2026 (predicted by M5 DSDT work) |
| `0x34` | **fan2 duty** | same encoding as `0x33` | Aug 2026 (predicted by M5 DSDT work) |
| `0x35`/`0x36` | fan1 tachometer (RPM) | big-endian pair; the **lower offset holds the high byte** — matches the M5's DSDT packing | May 2026 |
| `0x37`/`0x38` | fan2 tachometer (RPM) | big-endian pair, same packing | May 2026 |
| `0x70` | package temperature (°C) | | May 2026 |

### Tach tearing

The EC updates each tachometer's two bytes **non-atomically**, so a single
read can tear into garbage (e.g. one stale byte + one fresh byte). Read the
full EC image **twice ~20 ms apart** and accept a fan's RPM only if both
reads agree and are plausible; otherwise hold the last-known-good value.
Both the May scripts and the fan daemon use this double-read pattern.

### Fan3: a third tachometer channel

`0x28`/`0x29` is a **third fan tachometer**, distinct from the two duty/tach
pairs at `0x33`–`0x38` — evidence the AXB35 platform supports a third fan
(or at least reserves its telemetry), with `8000` used as a "no fan
installed" sentinel. This register is **not** documented in the upstream
fan-control repo as of this writing and is offered back to that project (see
[`PR-TO-NATHANMARLOR.md`](PR-TO-NATHANMARLOR.md)).

## The one-shot revert law

**A single write to a fan duty register (`0x33`/`0x34`) does not stick.**
The firmware observes manual overrides and **reverts them within seconds**.
Measured behavior: one-shot writes are reliably undone in single-digit
seconds.

The only way to hold a manual duty is **continuous re-assertion** — a
userspace loop that rewrites the register faster than the firmware reverts
it. This is *why* every working EC fan daemon is a polling daemon: the
deployed daemon rewrites duty every **2 s** and holds indefinitely.

The same class of policing motivates the P-MODE timer (below): a one-shot
`0x31` write is not guaranteed to survive other agents (the vendor's own
tooling, the front-panel button, firmware policy), so the May deployment
re-asserts it every 30 s and performance mode has held continuously since.

> Corollary for testing: "I wrote the register and it changed" proves
> nothing. Write it, sleep 5–10 s, read it back. If it reverted, either the
> firmware police it (this law) or your write was silently discarded (the
> `ec_sys` trap). The two failure modes look identical from userspace —
> check `write_support` first.

## The module-cycling hazard (multiple EC writers)

Two independent EC writers run on this machine:

1. the **P-MODE timer** (every 30 s), and
2. the **fan-control daemon** (every 2 s).

The original May P-MODE script was written as a careful single-user citizen:
it enabled `ec_sys` write support only around its own write, then **reloaded
the module read-only** afterwards:

```python
def restore_readonly_ec_sys() -> None:
    run(["modprobe", "-r", "ec_sys"], check=False)
    run(["modprobe", "ec_sys"], check=False)   # without write_support!
```

Once the fan daemon arrived, that safety measure turned into a hazard:

- every 30 s, `modprobe -r` transiently removes `/sys/kernel/debug/ec/ec0/io`
  out from under the daemon;
- the subsequent plain `modprobe ec_sys` reloads the module **without
  `write_support`** — and from that moment the daemon's writes are
  **silently discarded** (`dd`-style success, exit 0, no effect) while the
  daemon looks perfectly healthy.

Net effect: fan control silently degrades to the firmware's auto curve, with
no error anywhere. This exact accident (a module-refcount tug-of-war between
the P-MODE timer and the fan daemon) was diagnosed during the August session.

**Mitigation (deployed):** once `/etc/modprobe.d/ec_sys-write.conf` sets
`write_support=1` as the module default, *any* reload — including the P-MODE
script's "restore" — comes up write-capable, so the downgrade leg of the
accident can no longer happen. The transient unload window still exists;
architecturally, prefer a **single EC writer** (or have one daemon own both
P-MODE and fan duty) over multiple writers that cycle the module.

## P-MODE mechanism and enforcement

Register `0x31` holds the firmware's APU power mode:

| Value | Mode |
|---|---|
| `0x00` | balanced |
| `0x01` | performance |
| `0x02` | quiet |

This was the **original May 2026 discovery** on this machine: `scripts/nucbox-pmode.py`
reads/sets it; `scripts/nucbox-pmode-click-scan.py` is the validation harness
that ran a fixed bounded CPU workload and logged package watts, GPU/APU
watts, clocks, and temperatures while the mode was cycled, to prove the
register's effect on real power/thermal behavior without needing to see the
physical button event.

Because a one-shot write can be overwritten by other agents, the deployed
setup is a systemd oneshot + timer (`deploy/nucbox-pmode-performance.*`) that
re-asserts `0x31 = 0x01` (performance) every 30 s, since May 2026.

Example status output:

```
$ sudo nucbox-pmode.py status
{
  "apu_power_mode": "performance",
  "apu_power_mode_raw": 1,
  "ec_temp_c": 62,
  "fan1_rpm": 2400,
  "fan2_rpm": 2380,
  "fan3_rpm": 0,
  "ts": 1755283000.0
}
```

(values illustrative; fan3 reports the `8000 → 0` sentinel on this unit)

## Measurements: stock curve and fan maxima

All numbers measured on one EVO-X2 unit, August 2026.

**Stock firmware auto curve** (both fans, before any daemon):

- Saturates at **~60% duty / ~3260 RPM at 90 °C and above** — the stock
  curve simply stops ramping as the package climbs past 90 °C. Combined with
  the fan maxima below, this is why these boxes ride the thermal edge under
  sustained GPU load: the firmware leaves ~40% of fan2's headroom on the
  table at exactly the temperatures where it's needed.

**Fan maxima at 100% duty** (manual, via `0x80|100`):

| Fan | RPM at 100% duty |
|---|---|
| fan1 | ~3480 |
| fan2 | **4539** |

The two fans are **asymmetric**: fan2 has substantially more headroom
(4539 vs ~3480 RPM). Any tuned curve that commands both fans with one duty
value is effectively fan1-limited at the top end; the firmware's own 60%
cap is far below either maximum.

## Fan daemon deploy recipe and tuned curve

The deployed daemon is
[nathanmarlor/strix-halo-fan-control](https://github.com/nathanmarlor/strix-halo-fan-control)
(MIT) — a single-file Python daemon (`strix-halo-fand`) that reads the
hottest of `amdgpu`/`k10temp`, maps it through a curve, and writes
`0x80|duty` to `0x33`/`0x34` every 2 s. Its fail-safe design is excellent
and is *why* it was chosen: on any error or SIGTERM it writes `0x00` to hand
the fans back to the firmware's auto curve, and a minimum-duty floor keeps
airflow up even if something wedges.

**Recipe** (as deployed on this machine):

1. Enable `ec_sys` write support at boot (see
   [the trap](#ec-access-path-and-the-ec_sys-write-support-trap)):
   install `deploy/ec_sys-write.conf` → `/etc/modprobe.d/` and
   `deploy/ec_sys.conf` → `/etc/modules-load.d/`.
2. Install the daemon from the upstream repo (it ships an install script and
   a systemd unit; the deployed unit is copied at
   `deploy/strix-halo-fand.service` — its `ExecStartPre` loads `ec_sys` with
   `write_support=1` as a belt-and-braces measure).
3. Replace the daemon's default `CURVE` line with the tuned curve below.
4. `systemctl enable --now strix-halo-fand`.

**Tuned curve** ("quiet-idle tune", 2026-08-15) — in the daemon's config
section:

```python
CURVE = [(45, 30), (58, 38), (68, 60), (78, 85), (82, 95), (999, 100)]
FLOOR = 30        # minimum duty %, never starve airflow
INTERVAL = 2.0    # seconds between re-assertions (must beat the revert law)
```

| Package temp | Duty |
|---|---|
| below 45 °C | 30% (quiet idle) |
| 45–58 °C | 38% |
| 58–68 °C | 60% |
| 68–78 °C | 85% |
| 78–82 °C | 95% |
| 82 °C and above | 100% |

**Measured result** (105 s GPU load, peak package temperature):

| Setup | Peak temp |
|---|---|
| Stock firmware auto curve | 97.5–97.8 °C |
| Daemon + tuned curve | **96.5 °C** |

Honest read: the peak-temperature delta is modest (~1–1.3 °C). The practical
wins are the *shape* of the response — defined quiet idle (30% instead of the
stock idle behavior), earlier ramping as load builds, and full 100% duty
engagement above 82 °C that the stock curve never reaches (it caps at
~60%/~3260 RPM even at 90 °C+). Longer sustained loads are where the stock
curve's saturation should hurt most; a single 105 s load bounds the claim.

## The WMI/FCMI alternative (prior art, untested here)

Raw `ec_sys` writes work but are crude. Two sibling-platform projects
documented the cleaner, kernel-sanctioned route — ACPI/WMI methods that talk
to the same EC:

- [MintyMods/ip3-power-switch](https://github.com/MintyMods/ip3-power-switch)
  — makes the front-panel power-profile button work on Linux for AI mini-PCs
  built around the IP3 Tech AMD Strix Halo mainboard (Corsair AI, and
  Beelink/GMK variants), driving the EC's power-profile state via WMI.
- [pettijohn/corsair-ai-workstation-performance-level-linux](https://github.com/pettijohn/corsair-ai-workstation-performance-level-linux)
  — Linux driver (Rust) + UI reading the performance level from the EC
  through the WMI **FCMI** method on the Corsair AI Workstation 300 series.

The WMI/FCMI path was **not** attempted on this EVO-X2; it is cited as prior
art and the likely better long-term direction (a proper kernel WMI driver
instead of debugfs EC poking).

## Safety notes

- You are poking an Embedded Controller that also runs the thermal
  protection. Registers were characterized empirically; there is no vendor
  documentation. Use at your own risk.
- The deployed daemon is fail-safe by design: errors/stop hand fans back to
  firmware auto; worst realistic outcome is a fan stuck at 100% (loud, but
  safe) until reboot.
- Keep exactly **one** module-cycling citizen on the box (see the
  [module-cycling hazard](#the-module-cycling-hazard-multiple-ec-writers)),
  or set `write_support=1` as the module default so no reload can downgrade
  it.
- Register semantics beyond the table above were **not** characterized;
  writing unlisted registers is unwritten territory.

## Repository layout

```
scripts/
  nucbox-pmode.py            # read/set EC 0x31 P-MODE (May 2026, original RE artifact)
  nucbox-ec-readonly.py      # dump known read-side registers as JSON (May 2026)
  nucbox-pmode-click-scan.py # power/thermal validation harness while cycling modes (May 2026)
deploy/
  ec_sys-write.conf          # -> /etc/modprobe.d/   (write_support=1 default)
  ec_sys.conf                # -> /etc/modules-load.d/ (load ec_sys at boot)
  nucbox-pmode-performance.service  # oneshot: force 0x31 = performance
  nucbox-pmode-performance.timer    # re-assert every 30 s
  strix-halo-fand.service    # upstream daemon's systemd unit as deployed
PR-TO-NATHANMARLOR.md        # DRAFT PR description for the upstream repo (not sent)
```

Paths were sanitized for publication: the scripts originally live at
`~/bin/` on the author's machine; the units here reference
`/usr/local/bin/` — adjust to taste.

## Credits

- **Simon Gonzalez de Cruz (May 2026)** — original reverse-engineering of the
  EVO-X2 EC P-MODE register (`0x31`), the read-side register map
  (`0x21`–`0x29`, `0x35`–`0x38`, `0x70`), the power/thermal validation
  harness, and the 30 s P-MODE enforcement timer that has held performance
  mode since. The `scripts/` directory is his work.
- **[nathanmarlor/strix-halo-fan-control](https://github.com/nathanmarlor/strix-halo-fan-control)**
  (MIT) — the fan-control daemon deployed and validated here. Its EC register
  offsets (`0x33`/`0x34` duty, `0x35`–`0x38` tach, including the
  big-endian packing and non-atomic tearing) were reverse-engineered from the
  Bosgame M5's ACPI DSDT — the same Sixunited AXB35-02 board as the EVO-X2 —
  and that map is exactly what the August 2026 EVO-X2 validation confirmed.
- **[MintyMods/ip3-power-switch](https://github.com/MintyMods/ip3-power-switch)**
  — prior art for the WMI path to EC power-profile control on the IP3 Tech
  Strix Halo mainboard family (Corsair AI / Beelink / GMK variants).
- **[pettijohn/corsair-ai-workstation-performance-level-linux](https://github.com/pettijohn/corsair-ai-workstation-performance-level-linux)**
  (GPL-3.0) — prior art for reading the performance level from the EC via
  the WMI **FCMI** method on the sibling Corsair AI Workstation platform.

August 2026 validation/extension session: confirmed the register map on the
EVO-X2, characterized the one-shot revert law, the `ec_sys` write-support
trap and the module-cycling hazard, measured the stock curve/fan maxima, and
deployed + tuned the daemon (all documented above).

## License

[MIT](LICENSE) — applies to this repository's scripts and documentation. The
upstream daemon retains its own MIT license; `ip3-power-switch` and
pettijohn's driver retain theirs.
