# EVO-X2 EC operator toolkit

A Linux hardware-operations toolkit and reverse-engineering notebook for reading the GMKtec EVO-X2/Sixunited AXB35-02 embedded controller and selecting its APU power mode. It also records a deployment recipe for a separate upstream fan daemon. This is board-specific, privileged tooling—not a general fan-control app or a portable hardware guarantee.

## Existing experience and assets

- [scripts/nucbox-ec-readonly.py](scripts/nucbox-ec-readonly.py) prints known EC register values, power-mode name, temperature and three fan RPM channels as JSON. Fan3's 8000 sentinel maps to zero.
- [scripts/nucbox-pmode.py](scripts/nucbox-pmode.py) exposes `status` and root-only `set balanced|performance|quiet`, writing register `0x31` and checking immediate readback.
- [scripts/nucbox-pmode-click-scan.py](scripts/nucbox-pmode-click-scan.py) collects sensor/clock/power samples and phase summaries while an operator cycles P-MODE. Default is no synthetic load; optional OpenSSL workers add load.
- [deploy](deploy/) provides example module configuration and systemd unit/timer files. Published filenames and installed executable/unit names differ; they are deployment artifacts requiring reconciliation, not an install-and-go bundle.
- [README.md](README.md) records the register map, historical single-unit thermal measurements, firmware reversion/module-write hazards and upstream attribution. These are prior observations, not freshly verified readings or a training/reference dataset.

The fan daemon itself is **not shipped here**: the README credits [nathanmarlor/strix-halo-fan-control](https://github.com/nathanmarlor/strix-halo-fan-control). Its behavior and fail-safe claims need inspection/testing of the installed upstream version. This repository's scripts are original P-MODE/telemetry artifacts, not a new independent fan-daemon implementation.

## Limits

Do not infer current host temperature, deployment health or hardware safety from the notebook. The shipped readers take one EC image; despite README discussion of tach tearing, they do not implement double-read consistency filtering. Even `pmode status` can try to load a kernel module. Documentation review uses no EC access, privileged command, hardware load or service operation; real operator-side verification remains outstanding.
