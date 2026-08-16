# Third-Party Notices

This repository redistributes one adapted third-party file and references
two prior-art projects. Licenses verified against the upstream
repositories on 2026-08-15. Our own scripts and documentation are MIT
(see `LICENSE`).

## Redistributed (adapted) in this repository

### nathanmarlor/strix-halo-fan-control — MIT

- Upstream: https://github.com/nathanmarlor/strix-halo-fan-control
- File: `deploy/strix-halo-fand.service` — upstream's systemd unit,
  redistributed verbatim with an added provenance header (and, on the
  deployed machine, the daemon's tuned `CURVE` line documented in the
  README).
- The daemon itself (`strix-halo-fand`) is NOT redistributed here. Install
  it from the upstream repository, which ships the daemon, an install
  script, and this unit (see README "Fan daemon deploy recipe").

Copyright (c) 2026 Nathan Marlor

Permission is hereby granted, free of charge, to any person obtaining a
copy of this software and associated documentation files (the
"Software"), to deal in the Software without restriction, including
without limitation the rights to use, copy, modify, merge, publish,
distribute, sublicense, and/or sell copies of the Software, and to permit
persons to whom the Software is furnished to do so, subject to the
following conditions:

The above copyright notice and this permission notice shall be included
in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL
THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR
OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE,
ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR
OTHER DEALINGS IN THE SOFTWARE.

## Referenced prior art (no code included)

### MintyMods/ip3-power-switch — MIT

- Upstream: https://github.com/MintyMods/ip3-power-switch
- Copyright (c) 2026 MintyMods
- Relationship: prior art for the WMI path to EC power-profile control on
  the IP3 Tech Strix Halo mainboard family. Cited in the README; the WMI
  path was not attempted here and no upstream code is included.

### pettijohn/corsair-ai-workstation-performance-level-linux — GPL-3.0

- Upstream:
  https://github.com/pettijohn/corsair-ai-workstation-performance-level-linux
- Relationship: prior art for reading the EC performance level via the
  WMI FCMI method on the sibling Corsair AI Workstation platform. Cited
  in the README only — no upstream code is included in this repository
  (this repo stays MIT; nothing GPL-3.0 is copied in).

## Provenance of this repository's own artifacts

The May 2026 P-MODE reverse-engineering artifacts in `scripts/`
(`nucbox-pmode.py`, `nucbox-ec-readonly.py`, `nucbox-pmode-click-scan.py`)
and the `deploy/` modprobe/modules-load configs and P-MODE timer units are
the original work of Simon Gonzalez de Cruz (see README Credits).
