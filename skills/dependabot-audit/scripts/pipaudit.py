#!/usr/bin/env python3
"""Phase 3's auditor half for `uv.lock`: `pip-audit` over the PR's lockfile, at its ref.

The OSV half of Phase 3 is `audit.py`'s batch from Phase 1, over the whole lockfile
at `pr-<N>`. This is the corroborating half, the ecosystem's own auditor, and until
0.56.0 it was a prose block that failed in three ways (#165). Each was measured on
uv 0.12.19 and pip-audit 2.10.1:

  - **it read the checkout.** The block ran `cd "$SCRATCH/pr-<N>"`, and that
    worktree exists only where Phase 4 or 5 will run -- not under `--no-execute`,
    not at `$MAY_EXECUTE=no`, not once Phase 1's gate fired. With no `|| exit`, the
    export ran in the user's checkout and exported *its* lockfile. Reproduced on
    `fpga-board-sim` #438 from a checkout at the base: `ruff==0.16.7`, where the PR
    proposes 0.16.8, and the name check after it passed.
  - **it never exported extras.** `--all-groups` covers `[dependency-groups]`, not
    `[project.optional-dependencies]`, so a package reached only through an extra
    was missing from the file.
  - **one local package made `pip-audit` refuse the whole file.** A workspace member
    exports as `-e ./packages/x`, a path dependency as `./vendor/x`, and a git source
    as `x @ git+https://...`. None of them can carry a hash, so `pip-audit
    --disable-pip` stops at *"requirement ... does not contain a hash"*, at exit 1 --
    the status it also uses for *vulnerabilities found* -- with empty stdout.

What this does instead:

  1. reads `uv.lock` at `--ref` and at `--base`, and derives the versions the PR
     introduces. That is the set the export has to carry, checked by version, not by
     name;
  2. writes the lockfile and every regular-file `pyproject.toml` at `--ref` into
     `<scratch>/<ref>-manifests`, with `git cat-file`. That is all `uv export
     --frozen` reads. On #438 the export from those files is byte-identical to the
     one from a full worktree, and on a workspace fixture it is identical to the
     fixture's full tree. With no worktree needed, this runs on every path Phase 3
     does, and since no source file from the PR is on disk, nothing the PR ships can
     run;
  3. exports every package, group and extra, and leaves out what cannot be audited
     from a requirements file: workspace members, local paths, and git or URL
     sources. Each one is named in the output;
  4. checks that the export carries every version the PR introduces;
  5. runs `pip-audit --no-deps --disable-pip` on it, which resolves nothing, never
     calls `pip` and installs nothing. It reads the JSON rather than the exit
     status, which means the same thing for a finding and for a refusal, and it
     says which packages the row covered;
  6. strips the markers first, and splits the pins into passes that hold each name
     once. `pip-audit` evaluates a marker against the interpreter `uvx` gives it
     and drops a pin that does not match -- not as skipped, absent from its JSON.
     On #438 that was 4 of 38: `colorama ; sys_platform == 'win32'` and three
     `python_full_version < '3.11'` forks, rpds-py's older one among them. A name
     pinned twice cannot share a file (*"duplicate requirements"*, exit 1, no
     JSON), hence the passes. Every exported pin must come back in some pass's
     JSON, or the output names it as unaudited.

`uv export --frozen` never builds. Measured with a workspace member whose metadata
is dynamic and whose build backend writes a file when imported: `uv lock` imported
it, the frozen export did not, and it exported the member's dependencies from the
lockfile. `--no-config` keeps the PR's `[tool.uv]` settings out of the export --
on every fixture above the output is identical with and without it, and a
`required-version = ">=99"` that stops the plain export at exit 2 is ignored.
`uvx` does not read a project's index settings (an unreachable `index-url` in the
directory's `pyproject.toml` did not stop it fetching pip-audit), and here it runs
from `--scratch` anyway.

`pip-audit --locked` would not help. It does not necessarily parse `uv.lock`, which
is why the export is the path, rather than pointing `pip-audit` at the lockfile.

Exit status:
  0 = `pip-audit` covered every package the export carries, including every version
      the PR introduces, and found nothing.
  1 = it found something: an advisory, or a version the PR introduces that the
      export does not carry or the auditor skipped. The output says which.
  2 = it could not run, and the row is underivable, never clean.

    pipaudit.py --scratch DIR --ref pr-<N> --base <merge base>

Requires Python 3.11+ (tomllib), `git`, `uv`, and network access for `uvx`.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path, PurePosixPath
from typing import Any, NoReturn

TIMEOUT = 300

# Sources a requirements file cannot give `pip-audit` with a hash. One such line
# makes it refuse every line, so each is left out and named instead. Local ones go
# through `--no-emit-workspace` and `--no-emit-local`; the rest by name.
LOCAL = ("editable", "virtual", "directory", "path")
UNHASHABLE = ("git", "url")

# Every package, group and extra; nothing that cannot carry a hash; none of the
# PR's own uv settings; the lockfile read, never updated. On uv 0.12.19
# `--no-emit-local` also leaves out workspace members, so `--no-emit-workspace` is
# redundant today (a live mutation removing it alone survives); it stays so the
# intent survives a change to that.
EXPORT = [
    "export",
    "-q",
    "--frozen",
    "--no-config",
    "--format",
    "requirements.txt",
    "--all-packages",
    "--no-emit-workspace",
    "--no-emit-local",
    "--all-groups",
    "--all-extras",
]

# `name==version` at the start of an export line; a marker or a `\` may follow.
PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;\\]+)")
HASH = re.compile(r"--hash=(\S+)")


def fail(what: str) -> NoReturn:
    """Exit 2. Reserved for "could not run", never for "ran and found something"."""
    print(f"error: {what}", file=sys.stderr)
    raise SystemExit(2)


def first_line(text: str) -> str:
    for line in text.splitlines():
        if line.strip():
            return line.strip()[:240]
    return ""


def run(argv: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[bytes]:
    """Run a command; bytes out, so a file is written back exactly as git holds it."""
    try:
        return subprocess.run(  # noqa: S603
            argv,
            cwd=cwd,
            capture_output=True,
            check=False,
            timeout=TIMEOUT,
        )
    except FileNotFoundError:
        fail(f"`{argv[0]}` is not on PATH")
    except subprocess.TimeoutExpired:
        fail(f"`{' '.join(argv[:2])}` exceeded {TIMEOUT}s")


def text_of(proc: subprocess.CompletedProcess[bytes], stream: str = "stdout") -> str:
    return (proc.stdout if stream == "stdout" else proc.stderr).decode("utf-8", "replace")


def normalise(name: str) -> str:
    """PEP 503, so `Jinja2` in the lockfile and `jinja2` in the export are one name."""
    return re.sub(r"[-_.]+", "-", name).lower()


def lock_at(ref: str) -> list[dict[str, Any]]:
    proc = run(["git", "show", f"{ref}:uv.lock"])
    if proc.returncode != 0:
        fail(f"cannot read uv.lock at {ref}: {first_line(text_of(proc, 'stderr'))}")
    try:
        data = tomllib.loads(text_of(proc))
    except tomllib.TOMLDecodeError as exc:
        fail(f"uv.lock at {ref} is not TOML: {exc}")
    packages = data.get("package")
    if not isinstance(packages, list):
        fail(f"uv.lock at {ref} has no [[package]] entries")
    return [p for p in packages if isinstance(p, dict) and isinstance(p.get("name"), str)]


def kind_of(package: dict[str, Any]) -> str:
    """Where a lockfile entry comes from: `registry`, a local kind, or git/url."""
    source = package.get("source")
    if isinstance(source, dict):
        for key in ("registry", *UNHASHABLE, *LOCAL):
            if key in source:
                return key
    return "unknown"


def registry_pins(packages: list[dict[str, Any]]) -> dict[str, set[str]]:
    pins: dict[str, set[str]] = {}
    for p in packages:
        if kind_of(p) == "registry" and "version" in p:
            pins.setdefault(normalise(p["name"]), set()).add(str(p["version"]))
    return pins


def introduced(pr: list[dict[str, Any]], base: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """(name, version) pairs the PR's lockfile pins from a registry and the base's does not.

    Pairs, not a name->version map: a lockfile pins one package at several versions
    under different resolution markers, and the export carries each fork.
    """
    now, was = registry_pins(pr), registry_pins(base)
    return sorted(
        (name, v) for name, versions in now.items() for v in versions - was.get(name, set())
    )


def unauditable(
    pr: list[dict[str, Any]], base: list[dict[str, Any]]
) -> tuple[dict[str, str], list[str]]:
    """({name: kind} this row cannot audit, [git or URL sources the PR changed]).

    A local package is the project's own code, so its change is Phase 1's scope
    question and not this row's. A git or URL dependency is a dependency, and
    `pip-audit` cannot see it here -- so a PR that moves one is a gap to name.
    """
    kinds = {p["name"]: kind_of(p) for p in pr if kind_of(p) != "registry"}
    before = {normalise(p["name"]): p.get("source") for p in base}
    moved = [
        p["name"]
        for p in pr
        if kind_of(p) in UNHASHABLE and before.get(normalise(p["name"])) != p.get("source")
    ]
    return kinds, sorted(set(moved))


def materialise(ref: str, dest: Path) -> tuple[list[str], list[str]]:
    """Write `uv.lock` and every `pyproject.toml` at `ref` under `dest`. (written, skipped)

    Regular files only. A symlink or a submodule is skipped and named: following one
    would read something other than what the ref holds, and a path that is not a
    plain relative one is refused rather than joined onto `dest`.
    """
    listing = run(["git", "ls-tree", "-r", "-z", "--full-tree", ref])
    if listing.returncode != 0:
        fail(f"cannot list the tree at {ref}: {first_line(text_of(listing, 'stderr'))}")
    wanted: list[tuple[str, str]] = []
    skipped: list[str] = []
    for entry in text_of(listing).split("\0"):
        meta, _, path = entry.partition("\t")
        if not path:
            continue
        pure = PurePosixPath(path)
        if path != "uv.lock" and pure.name != "pyproject.toml":
            continue
        mode, kind, sha = [*meta.split(), "", "", ""][:3]
        if kind != "blob" or mode not in ("100644", "100755"):
            skipped.append(f"{path} (mode {mode}, not a regular file)")
        elif pure.is_absolute() or any(part in ("", ".", "..") for part in pure.parts):
            skipped.append(f"{path} (not a plain relative path)")
        else:
            wanted.append((path, sha))
    names = {path for path, _ in wanted}
    if "uv.lock" not in names or "pyproject.toml" not in names:
        fail(f"{ref} has no regular-file uv.lock and pyproject.toml at its root")
    if dest.exists():
        shutil.rmtree(dest)
    for path, sha in wanted:
        blob = run(["git", "cat-file", "blob", sha])
        if blob.returncode != 0:
            fail(f"cannot read {path} at {ref}: {first_line(text_of(blob, 'stderr'))}")
        target = dest.joinpath(*PurePosixPath(path).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(blob.stdout)
    return sorted(names), skipped


def entries(requirements: Path) -> list[tuple[str, str, list[str]]]:
    """(name, version, hashes) for every pin the export carries, markers dropped.

    A pin line starts at the margin; its `--hash=` lines are indented under it.
    """
    found: list[tuple[str, str, list[str]]] = []
    for line in requirements.read_text(encoding="utf-8").splitlines():
        pin = PIN.match(line)
        if pin:
            found.append((pin.group(1), pin.group(2), []))
        elif found and line[:1].isspace():
            found[-1][2].extend(HASH.findall(line))
    return found


def passes(pins: list[tuple[str, str, list[str]]]) -> list[list[tuple[str, str, list[str]]]]:
    """Split the pins so no file names a package twice; the first pass holds most."""
    split: list[list[tuple[str, str, list[str]]]] = []
    for pin in pins:
        for group in split:
            if all(normalise(pin[0]) != normalise(other[0]) for other in group):
                group.append(pin)
                break
        else:
            split.append([pin])
    return split


def write_pass(pins: list[tuple[str, str, list[str]]], path: Path) -> None:
    lines: list[str] = []
    for name, version, hashes in pins:
        lines.append(f"{name}=={version}" + (" \\" if hashes else ""))
        lines += [
            f"    --hash={h}" + (" \\" if i < len(hashes) - 1 else "") for i, h in enumerate(hashes)
        ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def audit(requirements: Path, report: Path, cwd: Path) -> tuple[str, dict[str, Any] | None, str]:
    """('clean' | 'found' | 'refused', the JSON, the reason), for one pass file.

    Read the JSON, not the status.

    `pip-audit` exits 1 for vulnerabilities found and 1 for a file it refused, and
    only the refusal leaves no JSON behind. The report path is emptied first, so an
    earlier run's JSON can never stand in for this one's.
    """
    report.unlink(missing_ok=True)
    proc = run(
        [
            "uvx",
            "pip-audit",
            "-r",
            str(requirements),
            "--no-deps",
            "--disable-pip",
            "--format",
            "json",
            "--output",
            str(report),
            "--progress-spinner",
            "off",
        ],
        cwd=cwd,
    )
    stderr = text_of(proc, "stderr")
    reason = next(
        (ln.strip() for ln in stderr.splitlines() if ln.startswith("ERROR")),
        first_line(stderr) or f"exit {proc.returncode}",
    )
    if proc.returncode not in (0, 1) or not report.is_file():
        return "refused", None, reason
    try:
        data = json.loads(report.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return "refused", None, f"its JSON did not parse; {reason}"
    if not isinstance(data, dict) or not isinstance(data.get("dependencies"), list):
        return "refused", None, "its JSON carries no dependency list"
    found = any(dep.get("vulns") for dep in data["dependencies"] if isinstance(dep, dict))
    if proc.returncode == 1 and not found:
        return "refused", None, reason
    return ("found" if found else "clean"), data, ""


def version_of_auditor(cwd: Path) -> str:
    proc = run(["uvx", "pip-audit", "--version"], cwd=cwd)
    return first_line(text_of(proc)) or "pip-audit (version unknown)"


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 3's pip-audit, over the PR's lockfile")
    parser.add_argument("--scratch", required=True, help="$SCRATCH from the Phase 0 handoff")
    parser.add_argument("--ref", required=True, help="the PR's ref, pr-<N>")
    parser.add_argument("--base", required=True, help="$BASE_SHA, the merge base")
    args = parser.parse_args()

    scratch = Path(args.scratch)
    if not scratch.is_dir():
        fail(f"{scratch} does not exist -- re-derive $SCRATCH, or Phase 0 never ran")
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", args.ref)
    manifests = scratch / f"{stem}-manifests"
    requirements = scratch / f"{stem}-requirements.txt"

    pr, base = lock_at(args.ref), lock_at(args.base)
    new = introduced(pr, base)
    kinds, moved = unauditable(pr, base)
    written, skipped = materialise(args.ref, manifests)

    unhashable = sorted(name for name, kind in kinds.items() if kind in UNHASHABLE)
    argv = ["uv", *EXPORT, *(f"--no-emit-package={n}" for n in unhashable)]
    proc = run([*argv, "-o", str(requirements)], cwd=manifests)
    if proc.returncode != 0:
        fail(f"uv export failed ({proc.returncode}): {first_line(text_of(proc, 'stderr'))}")
    pinned = entries(requirements)
    pins: dict[str, set[str]] = {}
    for name, version, _ in pinned:
        pins.setdefault(normalise(name), set()).add(version)
    missing = [(n, v) for n, v in new if v not in pins.get(n, set())]

    head = run(["git", "rev-parse", "--short=12", args.ref])
    print(f"pipaudit: {args.ref} at {text_of(head).strip() or '?'}, against {args.base[:12]}")
    print(f"  manifests at {args.ref}: {', '.join(written)}")
    print(f"             -> {manifests}")
    for line in skipped:
        print(f"  not written: {line}")
    print(f"  lockfile: {len(pr)} package(s); {len(pr) - len(kinds)} from a registry")
    if kinds:
        named = ", ".join(f"{n} ({k})" for n, k in sorted(kinds.items()))
        print(f"  left out of the export, not auditable from it: {named}")
    print(f"  export: {len(pinned)} pin(s) -> {requirements}")
    print(f"          {' '.join(argv)}")

    if new:
        print(f"\nThe PR introduces {len(new)} version(s) from a registry:")
        for name, version in new:
            where = "MISSING from the export" if (name, version) in missing else "in the export"
            print(f"  {name}=={version}   {where}")
    else:
        print("\nThe PR introduces no new version from a registry.")
    for name in moved:
        print(f"  {name}: the PR moves its {kinds[name]} source, which pip-audit cannot see here")

    deps: list[dict[str, Any]] = []
    split = passes(pinned)
    for number, group in enumerate(split, start=1):
        file = scratch / f"{stem}-pass{number}.txt"
        write_pass(group, file)
        state, data, reason = audit(file, scratch / f"{stem}-pip-audit{number}.json", scratch)
        if state == "refused":
            print(f"\npip-audit did not audit pass {number} of {len(split)} ({file}): {reason}")
            print("RESULT: UNDERIVABLE -- the auditor half of this row did not run.")
            return 2
        deps += [d for d in (data or {}).get("dependencies", []) if isinstance(d, dict)]

    def key(d: dict[str, Any]) -> tuple[str, str]:
        return normalise(str(d.get("name", ""))), str(d.get("version", ""))

    skipped_pins = {key(d) for d in deps if d.get("skip_reason")}
    answered = {key(d) for d in deps}
    unaudited = [f"{n}=={v}" for n, v, _ in pinned if (normalise(n), v) not in answered]
    audited = len(answered - skipped_pins)
    print(f"\n{version_of_auditor(scratch)}, --no-deps --disable-pip, PyPI's advisory data,")
    print(f"markers stripped, in {len(split)} pass(es) so no file names a package twice:")
    print(f"  {audited} of {len(pinned)} pin(s) audited, {len(skipped_pins)} skipped")
    for d in deps:
        if d.get("skip_reason"):
            print(f"  skipped {d.get('name')}: {first_line(str(d['skip_reason']))}")
    for pin in unaudited:
        print(f"  NOT AUDITED, absent from pip-audit's answer: {pin}")

    changed = set(new)
    advisories = 0
    for d in deps:
        seen: set[str] = set()
        for vuln in d.get("vulns") or []:
            if not isinstance(vuln, dict) or vuln.get("id") in seen:
                continue
            seen.add(str(vuln.get("id")))
            advisories += 1
            tag = "  <- a version this PR introduces" if key(d) in changed else ""
            fixed = ", ".join(vuln.get("fix_versions") or []) or "no fix listed"
            aliases = " ".join(vuln.get("aliases") or [])
            pin = f"{d.get('name')}=={d.get('version')}"
            print(f"  {pin}  {vuln.get('id')}  {aliases}  fixed in {fixed}{tag}")

    uncovered = [
        *(f"{n}=={v} (not in the export)" for n, v in missing),
        *(f"{n}=={v} (skipped by pip-audit)" for n, v in sorted(changed & skipped_pins)),
        *(f"{n} (moved {kinds[n]} source)" for n in moved),
        *(f"{pin} (not audited)" for pin in unaudited),
    ]
    print()
    if advisories:
        noun = "advisory" if advisories == 1 else "advisories"
        print(f"RESULT: FOUND -- {advisories} {noun} in the export.")
    if uncovered:
        print(f"RESULT: INCOMPLETE -- this row does not cover: {', '.join(uncovered)}")
    if not advisories and not uncovered:
        print(f"RESULT: CLEAN -- no known vulnerabilities in the {audited} pin(s) audited,")
        print("which include every version this PR introduces.")
    print("This row covers what the export carries; Phase 1's OSV batch covers the whole lockfile.")
    return 1 if advisories or uncovered else 0


def cli() -> NoReturn:
    """Entry point. Anything unforeseen becomes exit 2, never exit 1."""
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as exc:
        fail(f"unexpected {type(exc).__name__}: {exc} -- a bug, not a finding")


if __name__ == "__main__":
    cli()
