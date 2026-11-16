# EVO-X2 EC architecture

## Parts and flow

```text
readonly script -> debugfs EC image -> register decode -> JSON stdout
pmode status -> load ec_sys if missing -> EC image -> JSON stdout
pmode set -> root check -> current mode -> module reload with writes
          -> write 0x31 -> module reload -> immediate readback -> JSON/error
click scan -> sysfs + sensors/rocm-smi/turbostat + EC helper -> JSONL
           -> phase aggregates -> summary JSON / exit 0 or thermal-guard exit 2
```

All three scripts are standalone standard-library Python. The EC path is `/sys/kernel/debug/ec/ec0/io`; duty registers `0x33/0x34` belong to the documented external fan-daemon recipe, not the P-MODE writer. Read values use big-endian tach pairs and fan3 sentinel decoding.

The scan defaults to 75 seconds, 25-second phases and zero OpenSSL workers. Positive `--threads` starts a child process; it terminates/kills that child on cleanup. It writes timestamped samples and summary files and checks observed Tctl against a default 82°C guard. Sampling tools execute serially; timeouts, missing executables or absent sensors are not a comprehensive thermal interlock.

[deploy/nucbox-pmode-performance.timer](deploy/nucbox-pmode-performance.timer) names `gpu-host-pmode-performance.service`, while the committed service has a `nucbox-` filename and executes `/usr/local/bin/gpu-host-pmode.py`. Likewise the scan fallback expects `/usr/local/bin/gpu-host-ec-readonly.py`. Deployment must deliberately map these sanitized names; copying files unchanged does not establish a working installation. The fan unit loads/reloads `ec_sys` and expects an externally installed `/usr/local/bin/strix-halo-fand`.

## Risky paths and failures

- **Hardware writes:** root-only mode switching affects shared thermal/power behavior. Unsupported registers/boards are not safe just because file I/O succeeds. Use the authorized operations lane; documentation is not permission to deploy or change mode.
- **Module concurrency:** `set_mode` reloads `ec_sys` around its write and restores it with a plain load; concurrent fan control can lose its I/O path/write support. README proposes a persistent write-support default but that is a privileged deployment change, not proof of single-writer safety.
- **False success/reversion:** `ec_sys` can discard writes without write support; immediate P-MODE readback is not persistence under firmware/other writers. Unknown mode values are rendered explicitly. No sustained verification occurs in the setter.
- **Telemetry integrity:** one-shot EC reads can tear; short images can index-fail; no plausibility/smoothing layer exists in these scripts. Scan fallback can return sparse data, and a missing Tctl bypasses its threshold check. Missing commands/timeouts can abort without a final summary.
- **Rollback:** do not assume stopping a timer restores the previous power mode. Fan-auto handback is an external-daemon behavior referenced by the unit, not implemented/tested here. Any hardware restoration must be separately authorized and observed.

## Documentation verification

Source parsing and pure synthetic register/summary helper checks can run without executing hardware paths. No `status`, `set`, scan, module, daemon or unit operation is used for these docs. No current hardware performance/safety or operator-side product acceptance is claimed. Docs-only rollback is reverting this commit.
