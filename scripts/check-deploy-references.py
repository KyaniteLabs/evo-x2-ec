#!/usr/bin/env python3
"""Fail when a deploy unit or shipped script names a file this repo does not ship.

Scans ``deploy/*.service``, ``deploy/*.timer`` and every ``scripts/*.py`` for
absolute executable paths and unit names, then resolves each against the files
in this repository plus an explicit allowlist of externally installed
artifacts (``EXTERNAL_INSTALLS``).

Resolution rules:

* A reference under an install prefix (``/usr/local/`` or ``~/bin/``) must name
  a file this repo ships, unless it is named in ``EXTERNAL_INSTALLS``.
* OS binaries (``/bin``, ``/sbin``, ``/usr/bin``, ``/usr/sbin``) are supplied by
  the package manager, not this repo.
* Kernel interfaces and runtime data/output paths are out of scope by prefix.
* A timer's ``Unit=`` must name a unit file this repo ships in ``deploy/``.

Usage: check-deploy-references.py [REPO_ROOT]
Exit:  0 every reference resolves, 1 at least one does not.
"""

from __future__ import annotations

import ast
import shlex
import sys
from pathlib import Path

# Installed-at prefixes whose basenames must match a file this repo ships.
INSTALL_PREFIXES = ("/usr/local/", "~/bin/")

# OS-owned binaries: resolved by the package manager, not this repo.
SYSTEM_PREFIXES = ("/bin/", "/sbin/", "/usr/bin/", "/usr/sbin/")

# Kernel interfaces and runtime data/output paths, not shipped executables.
RUNTIME_PREFIXES = ("/sys/", "/proc/", "/dev/", "/etc/", "/srv/", "/var/", "/run/", "/tmp/")

# Operator-supplied allowlist: externally installed artifacts a unit may name.
EXTERNAL_INSTALLS = {
    "strix-halo-fand": (
        "installed from nathanmarlor/strix-halo-fan-control; "
        "deploy/strix-halo-fand.service is that project's unit as deployed (README)"
    ),
}

# Unit directives whose value starts with the executable to run.
EXEC_KEYS = (
    "ExecCondition",
    "ExecStartPre",
    "ExecStart",
    "ExecStartPost",
    "ExecStop",
    "ExecStopPost",
    "ExecReload",
)


def unit_directives(path: Path):
    """Yield (line number, section, key, value) for a systemd unit file."""
    section = ""
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped[0] in "#;":
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped[1:-1].strip()
            continue
        if "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        yield lineno, section, key.strip(), value.strip()


def executable_of(value: str) -> str | None:
    """First argv token of an Exec* value, with systemd's -@+! prefixes removed."""
    stripped = value.strip()
    while stripped[:1] in "-@+!":
        stripped = stripped[1:]
    try:
        argv = shlex.split(stripped)
    except ValueError:
        return None
    return argv[0] if argv else None


def resolve_basename(basename: str, shipped: dict[str, Path]) -> str | None:
    """None when the basename resolves, else the reason it does not."""
    if basename in shipped:
        return None
    if basename in EXTERNAL_INSTALLS:
        return None
    available = ", ".join(sorted(shipped)) or "(none)"
    return (
        f"{basename!r} is not shipped by this repo and is not in EXTERNAL_INSTALLS "
        f"(shipped scripts: {available})"
    )


def classify(token: str, shipped: dict[str, Path]) -> str | None:
    """None when the path is out of scope or resolves, else the reason it does not."""
    if token.startswith("~/"):
        return resolve_basename(token.rsplit("/", 1)[-1], shipped)
    if not token.startswith("/"):
        return None
    for prefix in SYSTEM_PREFIXES:
        if token.startswith(prefix):
            return None
    for prefix in RUNTIME_PREFIXES:
        if token.startswith(prefix):
            return None
    for prefix in INSTALL_PREFIXES:
        if token.startswith(prefix):
            return resolve_basename(Path(token).name, shipped)
    return f"{token!r} has no resolution rule (unknown absolute prefix)"


def script_string_paths(path: Path):
    """Yield (line number, absolute-path string) from string constants in a script."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        token = node.value.strip()
        if not token.startswith(("/", "~/")):
            continue
        try:
            token = shlex.split(token)[0]
        except ValueError:
            continue
        yield node.lineno, token


def main(argv: list[str]) -> int:
    repo_root = Path(argv[1]).resolve() if len(argv) > 1 else Path(__file__).resolve().parent.parent
    shipped = {p.name: p for p in (repo_root / "scripts").glob("*.py") if p.is_file()}
    problems: list[str] = []
    checked = 0
    deploy = repo_root / "deploy"

    for unit in sorted(deploy.glob("*.service")):
        rel = unit.relative_to(repo_root)
        for lineno, section, key, value in unit_directives(unit):
            if section == "Service" and key in EXEC_KEYS:
                token = executable_of(value)
                if token is None:
                    continue
                checked += 1
                reason = classify(token, shipped)
                if reason:
                    problems.append(f"{rel}:{lineno} {key} -> {token}: {reason}")

    for timer in sorted(deploy.glob("*.timer")):
        rel = timer.relative_to(repo_root)
        for lineno, section, key, value in unit_directives(timer):
            if section != "Timer" or key != "Unit":
                continue
            checked += 1
            target = deploy / value
            if "/" in value or not target.is_file():
                problems.append(
                    f"{rel}:{lineno} Unit -> {value}: no shipped unit file deploy/{value}"
                )

    for script in sorted((repo_root / "scripts").glob("*.py")):
        if script.resolve() == Path(__file__).resolve():
            continue
        rel = script.relative_to(repo_root)
        for lineno, token in script_string_paths(script):
            checked += 1
            reason = classify(token, shipped)
            if reason:
                problems.append(f"{rel}:{lineno} path -> {token}: {reason}")

    if problems:
        print(f"FAIL: {len(problems)} unresolved deployment reference(s) in {repo_root}")
        for problem in problems:
            print(f"  {problem}")
        return 1
    print(f"PASS: {checked} deployment reference(s) resolve against shipped files in {repo_root}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
