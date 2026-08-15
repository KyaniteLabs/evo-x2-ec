# DRAFT — PR description for nathanmarlor/strix-halo-fan-control

> **Status: NOT SENT.** This is a ready-to-send draft prepared on 2026-08-15.
> Nothing has been forked, branched, or opened. Simon must approve before any
> of this text leaves the machine. When approved: fork the repo, create a
> docs branch, paste the "PR body" below, and submit.

---

**Suggested PR title:**

```
docs: EVO-X2 (AXB35-02) validation data, fan3 tach register (0x28/0x29), and an ec_sys multi-writer warning
```

**Suggested branch name:** `docs/evo-x2-validation`

---

## PR body

Hi — thanks for this project. I run a **GMKtec EVO-X2** (Ryzen AI Max+ 395,
Sixunited AXB35-02, same board family as your Bosgame M5), and your daemon
has been running great on it. This PR is **documentation only** — no code
changes. Three additions:

### 1. EVO-X2 validation data for the "validated boards" section

Everything your README reverse-engineered from the M5's DSDT checks out
byte-for-byte on the EVO-X2, which is a useful second data point since it's
a different chassis/vendor on the same AXB35-02 board:

- `0x33`/`0x34` fan duty registers: write `0x80 | duty`, `0x00` = firmware
  auto — confirmed.
- Tach packing at `0x35`–`0x38` — lower offset holds the **high** byte —
  confirmed, including the non-atomic tearing (your double-read-20ms-apart
  handling is necessary; single reads tear on the EVO-X2 too).
- `0x70` package temp — confirmed.
- **One-shot reversion**: manual duty writes are reverted by the firmware
  within seconds; only continuous re-assertion holds (your 2 s interval
  beats it comfortably). Worth calling out explicitly in the README since
  it's why one-shot `echo ... > ec0/io` "tutorials" don't work on these
  boxes.

Measured numbers you're welcome to fold into the README:

| Measurement (EVO-X2, stock firmware) | Value |
|---|---|
| Stock auto curve at 90 °C+ | saturates ~60% duty / ~3260 RPM |
| fan2 RPM at 100% duty | 4539 |
| fan1 RPM at 100% duty | ~3480 |
| Peak package temp, 105 s GPU load, stock curve | 97.5–97.8 °C |
| Peak package temp, 105 s GPU load, daemon + tuned curve | 96.5 °C |

Note the fan asymmetry (fan2 has ~1000 RPM more headroom than fan1) — a
single shared duty value is effectively fan1-limited at the top end. Might
be worth a README note for anyone tuning `CURVE`.

### 2. New register: fan3 tachometer at `0x28`/`0x29`

The EVO-X2 exposes a **third fan tachometer** at `0x28` (high byte) /
`0x29` (low byte), with **8000 (0x1F40) as a "no fan" sentinel** (report
0). On my unit it reads the sentinel, so I can't confirm a physical third
fan in the EVO-X2 chassis, but the register is live and distinct from
`0x35`–`0x38`. Suggest adding to the register table, possibly with a
`fan3_rpm` metric if you want it in the Prometheus export.

### 3. Warning: `ec_sys` module cycling silently kills concurrent EC writers

This one bit me and I'd like to save others the debugging. If a user runs
your daemon **plus** anything else that touches `ec_sys` and does the
"polite" load-write-unload dance — e.g. a P-MODE enforcement script that
runs `modprobe -r ec_sys; modprobe ec_sys` (no args) after its write — then
every cycle of that script:

1. transiently removes `/sys/kernel/debug/ec/ec0/io` from under the daemon
   (daemon errors, fail-safes to firmware auto), and
2. reloads `ec_sys` **without** `write_support`, after which the daemon's
   writes are **silently discarded** — the daemon looks healthy, logs
   nothing wrong, and the fans are back on the firmware curve.

Suggested README warning (feel free to reword):

> **Running other EC tools alongside the daemon.** Any tool that reloads
> `ec_sys` without `write_support=1` will silently break the daemon's
> writes: subsequent duty writes are discarded with no error. If you run
> other EC writers (power-mode scripts, vendor tools), create
> `/etc/modprobe.d/ec_sys-write.conf` containing `options ec_sys
> write_support=1` so *every* reload — by anyone — stays write-capable.

(For what it's worth, my setup runs your daemon alongside a systemd timer
that re-asserts the EC P-MODE register `0x31` every 30 s — the EVO-X2/Bosgame
M5 also expose the firmware power mode at `0x31`: `0`=balanced, `1`
=performance, `2`=quiet. Happy to contribute that as docs too if useful.)

---

Thanks again — the fail-safe design (auto-handback on any error, duty floor)
is exactly right and is why I trusted it on this box.

---

*Prepared 2026-08-15. Numbers measured on one EVO-X2 unit; single-sample
thermal peaks, so ranges are given where observed. Author of the underlying
EVO-X2 P-MODE reverse-engineering: Simon Gonzalez de Cruz (May 2026).*
