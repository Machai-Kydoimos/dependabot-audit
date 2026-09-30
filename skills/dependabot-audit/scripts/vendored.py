#!/usr/bin/env python3
"""Phase 3 for what a compiled wheel carries inside it: advisories on the crates it ships.

`ruff`, `uv` and `rumdl` are Rust, and each wheel carries a whole crate graph that no
Python-side scanner reads. An advisory filed against one of those crates reaches
nothing Phase 3 queried, and a release that fixes one need not say so:

  - **ruff 0.16.9** (2026-09-24) ships `salsa` 0.28.5, which fixes RUSTSEC-2026-0308
    (GHSA-xc3w-55vh-cw3w), a use-after-free OSV marks `unsound`. ruff 0.16.7 and
    0.16.8 ship `salsa` 0.28.2 in all 17 wheels. The advisory was published eight
    and a half hours before the release, and its notes name none of it. The `fpga-board-sim`
    #438 replay reported them as containing *"nothing security-shaped"* -- true of
    the notes, and wrong about the release (#171).
  - **rumdl 0.2.76** moved `rustls` 0.23.38 -> 0.23.45 in `Cargo.lock`, the fix for
    RUSTSEC-2026-0285, in a commit titled `refresh Rust dependencies`. No rumdl
    wheel ships `rustls`: it is a dev-dependency, and `Cargo.lock` records those
    too. Read from the lockfile, it is exposure; read from the wheel, it is not.

So this reads the **wheels**, never `Cargo.lock`. A PEP 770 wheel carries its own
CycloneDX SBOM under `.dist-info/sboms/`, and that is the shipped set. Each wheel is
read, not the first: rumdl 0.2.76's seven wheels list 214 to 219 components --
`inotify` on Linux, `fsevent-sys` on macOS, `mimalloc` and `windows-sys` on
Windows -- so one wheel does not stand for the others. A component whose `scope`
is `excluded` is a build-time crate (`cc`, `autocfg`, `jobserver`) and is left out.
The reads are HTTP ranges: the zip's directory and the SBOM member, never the
whole wheel. ruff 0.16.8's 17 wheels are 175 MB, and their SBOMs read in 4 s.

Every shipped component is queried in OSV by its purl, at three versions: the
current pin, the proposed one, and the registry's latest. An advisory is then:

  fixed by this PR      shipped at the current pin, not at the proposed one
  fixed above this PR   shipped at the proposed pin, not at the latest
  introduced            shipped at the proposed pin, not at the current one
  standing              shipped at all of them

A wheel with no SBOM is `underivable`, never clean: PEP 770 is recent, and
pydantic-core 2.41.5 carries none in any of its 120 wheels. A pure-Python wheel
(`none-any`) vendors nothing and is skipped. **A release with no wheel at all is
not that**, and is `underivable` too (#188): an sdist ships whatever its build
produces, and actionlint-py's build downloads the compiled actionlint Go binary.
An `unmaintained` notice is listed, and alone is not a finding: ruff ships two,
both compile-time macro crates.

    vendored.py --ref pr-<N> --base <merge base>            # every package uv.lock moves
    vendored.py --package NAME --current V --proposed V     # one, as a hook installs it

Exit status: 0 = no advisory on anything these wheels ship, and every compiled
wheel read carried an SBOM. 1 = an advisory -- fixed, introduced or standing -- or
a shipped set that could not be read: a compiled wheel's, a release with no wheel,
or a release the registry would not serve. The output says which, per package and
per wheel. 2 = could not run.

Requires Python 3.11+ (tomllib), `git` for `--ref`, and the network: PyPI and OSV.
"""

from __future__ import annotations

import argparse
import io
import json
import re
import subprocess
import sys
import tomllib
import urllib.error
import urllib.request
import zipfile
from typing import Any, NoReturn

TIMEOUT = 60
PYPI = "https://pypi.org/pypi"
OSV = "https://api.osv.dev/v1"

# Bytes per range read. A wheel's directory and a CycloneDX member each fit in one
# or two, so a wheel costs a handful of requests whatever its size.
BLOCK = 1 << 16

# OSV's querybatch limit.
BATCH = 1000

# A release whose first few compiled wheels carry no SBOM is underivable whatever
# the rest hold, so reading on only costs time: pydantic-core ships over a hundred
# wheels a release, and at 2.41.x none has one.
BARE_PROBE = 3


class Unreadable(Exception):
    """One wheel's shipped set could not be read -- that wheel, not the run."""


def fail(what: str) -> NoReturn:
    """Exit 2. Reserved for "could not run", never for "ran and found something"."""
    print(f"error: {what}", file=sys.stderr)
    raise SystemExit(2)


# --- the network ---------------------------------------------------------------


def _http(
    url: str, *, data: bytes | None = None, headers: dict[str, str] | None = None
) -> tuple[int, dict[str, str], bytes]:
    """(status, headers, body). The one seam every request goes through."""
    request = urllib.request.Request(url, data=data, headers=headers or {})  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:  # noqa: S310
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers or {}), b""


def _json(url: str, *, body: Any = None) -> Any:
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if body is not None else {}
    status, _, raw = _http(url, data=data, headers=headers)
    if status != 200:
        raise Unreadable(f"{url} answered HTTP {status}")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise Unreadable(f"{url} returned unparseable JSON: {exc}") from exc


class RangeFile(io.RawIOBase):
    """A remote file that `zipfile` can seek in, read a block at a time by HTTP range.

    A server that ignores `Range` and answers 200 hands over the whole file, which is
    kept and read from: slower, never wrong.
    """

    def __init__(self, url: str, size: int) -> None:
        super().__init__()
        self.url, self.size, self.pos = url, size, 0
        self.blocks: dict[int, bytes] = {}
        self.whole: bytes | None = None

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self.pos

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        base = {io.SEEK_SET: 0, io.SEEK_CUR: self.pos, io.SEEK_END: self.size}[whence]
        self.pos = max(0, base + offset)
        return self.pos

    def _block(self, index: int) -> bytes:
        if self.whole is not None:
            return self.whole[index * BLOCK : (index + 1) * BLOCK]
        if index not in self.blocks:
            start = index * BLOCK
            end = min(self.size, start + BLOCK) - 1
            status, _, body = _http(self.url, headers={"Range": f"bytes={start}-{end}"})
            if status == 200:
                self.whole = body
                return self._block(index)
            if status != 206:
                raise Unreadable(f"range read answered HTTP {status}")
            self.blocks[index] = body
        return self.blocks[index]

    def readinto(self, buffer: Any) -> int:
        view = memoryview(buffer).cast("B")
        wanted = min(len(view), max(0, self.size - self.pos))
        done = 0
        while done < wanted:
            index, offset = divmod(self.pos, BLOCK)
            chunk = self._block(index)[offset : offset + wanted - done]
            if not chunk:
                break
            view[done : done + len(chunk)] = chunk
            done += len(chunk)
            self.pos += len(chunk)
        return done


def shipped_by(url: str, size: int) -> list[dict[str, Any]] | None:
    """The components a wheel's SBOM lists as shipped, or None when it has no SBOM."""
    try:
        with zipfile.ZipFile(io.BufferedReader(RangeFile(url, size), BLOCK)) as wheel:
            members = [n for n in wheel.namelist() if "/sboms/" in n and n.endswith(".json")]
            if not members:
                return None
            found: list[dict[str, Any]] = []
            for member in members:
                document = json.loads(wheel.read(member))
                found += [
                    c
                    for c in document.get("components") or []
                    if isinstance(c, dict) and c.get("purl") and c.get("scope") != "excluded"
                ]
            return found
    except (zipfile.BadZipFile, json.JSONDecodeError, KeyError, OSError) as exc:
        raise Unreadable(f"{type(exc).__name__}: {exc}") from exc


# --- which wheels ------------------------------------------------------------------


def compiled(filename: str) -> bool:
    """A wheel built for a platform. `none-any` is pure Python and vendors nothing."""
    return filename.endswith(".whl") and not filename.endswith("-none-any.whl")


def wheels_on_pypi(name: str, version: str) -> list[tuple[str, str, int]]:
    """(filename, url, size) of every wheel PyPI serves for this release."""
    release = _json(f"{PYPI}/{name}/{version}/json")
    return [
        (f["filename"], f["url"], int(f["size"]))
        for f in release.get("urls") or []
        if f.get("packagetype") == "bdist_wheel"
    ]


def latest_on_pypi(name: str) -> str:
    return str(_json(f"{PYPI}/{name}/json")["info"]["version"])


def lock_at(ref: str) -> list[dict[str, Any]]:
    try:
        shown = subprocess.run(  # noqa: S603
            ["git", "show", f"{ref}:uv.lock"],  # noqa: S607
            capture_output=True, text=True, check=False, timeout=TIMEOUT,
        )  # fmt: skip
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        fail(f"cannot run git: {exc}")
    if shown.returncode != 0:
        fail(f"cannot read uv.lock at {ref}: {shown.stderr.strip()[:200]}")
    try:
        packages = tomllib.loads(shown.stdout).get("package")
    except tomllib.TOMLDecodeError as exc:
        fail(f"uv.lock at {ref} is not TOML: {exc}")
    return [p for p in packages or [] if isinstance(p, dict) and p.get("name")]


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _key(version: str) -> tuple[Any, ...]:
    """Enough of PEP 440 to order the releases a lockfile moves between."""
    return tuple((0, int(p)) if p.isdigit() else (-1, p) for p in re.split(r"[.+-]", version))


def moved(pr: list[dict[str, Any]], base: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Each registry package the PR's lockfile pins at a version the base's does not:
    name, current (highest base version, or None), proposed, and the lock's wheels."""
    before: dict[str, list[str]] = {}
    for p in base:
        if "registry" in (p.get("source") or {}):
            before.setdefault(_norm(p["name"]), []).append(str(p.get("version")))
    found = []
    for p in pr:
        name, version = _norm(p["name"]), str(p.get("version"))
        if "registry" not in (p.get("source") or {}) or version in before.get(name, []):
            continue
        current = max(before[name], key=_key) if before.get(name) else None
        wheels = [
            (str(w["url"]).rsplit("/", 1)[-1], str(w["url"]), int(w.get("size") or 0))
            for w in p.get("wheels") or []
            if isinstance(w, dict) and w.get("url")
        ]
        found.append({"name": p["name"], "current": current, "proposed": version, "wheels": wheels})
    return found


# --- what the wheels ship, and what OSV says about it --------------------------------


def read_release(wheels: list[tuple[str, str, int]]) -> dict[str, Any]:
    """Every compiled wheel's shipped set: {purl: {wheel, ...}}, and what was unread."""
    by_purl: dict[str, set[str]] = {}
    names: dict[str, tuple[str, str]] = {}
    unread: list[str] = []
    bare: list[str] = []
    read: list[str] = []
    platform = [w for w in wheels if compiled(w[0])]
    for index, (filename, url, size) in enumerate(platform):
        if index == BARE_PROBE and len(bare) == BARE_PROBE:
            break
        try:
            components = shipped_by(url, size or _size_of(url))
        except Unreadable as exc:
            unread.append(f"{filename}: {exc}")
            continue
        if components is None:
            bare.append(filename)
            continue
        read.append(filename)
        for c in components:
            by_purl.setdefault(c["purl"], set()).add(filename)
            names[c["purl"]] = (str(c.get("name")), str(c.get("version")))
    return {
        "wheels": [w[0] for w in platform],
        "read": read,
        "by_purl": by_purl,
        "names": names,
        "unread": unread,
        "bare": bare,
    }


def presence(release: dict[str, Any], purls: list[str]) -> bool | None:
    """Shipped, not shipped, or unknown: a wheel left unread could ship it."""
    if any(p in release["by_purl"] for p in purls):
        return True
    return False if len(release["read"]) == len(release["wheels"]) else None


def _size_of(url: str) -> int:
    status, headers, _ = _http(url, headers={"Range": "bytes=0-0"})
    got = re.search(r"/(\d+)$", headers.get("Content-Range", ""))
    if status != 206 or not got:
        raise Unreadable(f"no size for {url.rsplit('/', 1)[-1]} (HTTP {status})")
    return int(got.group(1))


def osv_ids(purls: list[str]) -> dict[str, list[str]]:
    """{purl: [advisory id, ...]} for every purl OSV knows an advisory against."""
    found: dict[str, list[str]] = {}
    for start in range(0, len(purls), BATCH):
        chunk = purls[start : start + BATCH]
        answer = _json(
            f"{OSV}/querybatch", body={"queries": [{"package": {"purl": p}} for p in chunk]}
        )
        for purl, result in zip(chunk, answer.get("results") or [], strict=False):
            ids = [v["id"] for v in result.get("vulns") or []]
            token = result.get("next_page_token")
            while token:
                page = _json(f"{OSV}/query", body={"package": {"purl": purl}, "page_token": token})
                ids += [v["id"] for v in page.get("vulns") or []]
                token = page.get("next_page_token")
            if ids:
                found[purl] = sorted(set(ids))
    return found


def describe(advisory_id: str) -> dict[str, str]:
    """The advisory's summary, aliases, and kind: `unsound`, `unmaintained`, or ``."""
    record = _json(f"{OSV}/vulns/{advisory_id}")
    kind = ""
    for affected in record.get("affected") or []:
        kind = kind or str((affected.get("database_specific") or {}).get("informational") or "")
    return {
        "summary": str(record.get("summary") or record.get("details") or "")[:90],
        "aliases": ", ".join(record.get("aliases") or []),
        "kind": kind,
    }


# --- the answer ----------------------------------------------------------------------


def classify(present: dict[str, bool | None]) -> str:
    """Where an advisory stands across the versions read. `None` is a release whose
    shipped set was not read in full, so it neither ships nor clears anything."""
    cur, pro, lat = present.get("current"), present.get("proposed"), present.get("latest")
    if pro is None:
        return "underivable at the proposed pin" + (", and the latest ships it" if lat else "")
    states = []
    if pro and cur is False:
        states.append("introduced by this PR")
    if not pro and cur:
        states.append("fixed by this PR")
    if pro and lat is False:
        states.append("fixed above this PR")
    if not pro and lat:
        states.append("introduced above this PR")
    if pro and not states:
        states.append("standing" if cur else "shipped at the proposed pin, current pin unread")
    return ", ".join(states) or "not shipped at the proposed pin"


def audit(
    name: str, versions: dict[str, str], wheels: dict[str, list[tuple[str, str, int]]]
) -> int:
    """Print one package's rows. Returns the number of findings (advisories that are
    not `unmaintained`, plus unread or SBOM-less wheels)."""
    shown = " -> ".join(f"{versions[k]}" for k in ("current", "proposed") if versions.get(k))
    latest = versions.get("latest")
    print(f"\n{name} {shown}" + (f" (latest {latest})" if latest else ""))
    releases = {label: read_release(wheels[label]) for label in versions}
    if not any(r["wheels"] for r in releases.values()):
        print("  pure Python at every version read: no wheel vendors anything")
        return 0
    findings = 0
    for label, release in releases.items():
        print(
            f"  {label} {versions[label]}: {len(release['wheels'])} compiled wheel(s), "
            f"{len(release['read'])} with an SBOM read, "
            f"{len(release['by_purl'])} component(s) shipped"
        )
        total = len(release["wheels"])
        if release["bare"] and len(release["bare"]) == BARE_PROBE and not release["by_purl"]:
            print(
                f"    UNDERIVABLE  no SBOM in the first {BARE_PROBE} of {total} compiled wheel(s), "
                "and the rest were not read -- not clean"
            )
            findings += 1
        elif release["bare"]:
            for filename in release["bare"][:5]:
                print(f"    UNDERIVABLE  {filename} carries no SBOM -- not clean, not read")
            if len(release["bare"]) > 5:
                print(f"    UNDERIVABLE  ... and {len(release['bare']) - 5} more without one")
            findings += 1
        for line in release["unread"]:
            print(f"    UNDERIVABLE  {line}")
            findings += 1
    purls = sorted({p for r in releases.values() for p in r["by_purl"]})
    by_purl = osv_ids(purls)
    advisories: dict[str, list[str]] = {}
    for purl, ids in by_purl.items():
        for advisory_id in ids:
            advisories.setdefault(advisory_id, []).append(purl)
    noted = []
    for advisory_id in sorted(advisories):
        info = describe(advisory_id)
        hit = advisories[advisory_id]
        present = {label: presence(r, hit) for label, r in releases.items()}
        state = classify(present)
        crates = sorted({"{} {}".format(*releases_name(releases, p)) for p in hit})
        if info["kind"] == "unmaintained":
            noted.append(f"{', '.join(crates)} ({advisory_id})")
            continue
        findings += 1
        kind = f"{info['kind']}: " if info["kind"] else ""
        alias = f" ({info['aliases']})" if info["aliases"] else ""
        print(f"  {state.upper()}  {advisory_id}{alias}  {', '.join(crates)}")
        print(f"    {kind}{info['summary']}")
        for label, release in releases.items():
            ships = sorted({w for p in hit for w in release["by_purl"].get(p, set())})
            at = f"    {label} {versions[label]}:"
            if ships:
                print(f"{at} in {len(ships)} of {len(release['wheels'])} wheel(s)")
            elif present[label] is None:
                print(f"{at} unknown -- its shipped set was not read in full")
            else:
                crate = next(iter(crates)).split()[0]
                now = sorted({v for p, (n, v) in release["names"].items() if n == crate}, key=_key)
                where = f", ships {crate} {', '.join(now)}" if now else f", no {crate}"
                print(f"{at} not shipped{where}")
    if noted:
        print(f"  unmaintained, noted and not a finding: {'; '.join(noted)}")
    if not advisories:
        print("  no OSV advisory on any component shipped at any version read")
    return findings


def releases_name(releases: dict[str, Any], purl: str) -> tuple[str, str]:
    """A shipped component's name and version, from whichever release lists it."""
    for release in releases.values():
        if purl in release["names"]:
            name, version = release["names"][purl]
            return str(name), str(version)
    return purl, ""


def main() -> int:
    parser = argparse.ArgumentParser(description="Advisories on the crates a wheel ships")
    parser.add_argument("--ref", help="the PR's tree, pr-<N>, whose uv.lock is read")
    parser.add_argument("--base", help="$BASE_SHA, whose uv.lock is the current pin")
    parser.add_argument("--package", help="one package, by its PyPI name")
    parser.add_argument("--current", help="with --package: the version pinned now")
    parser.add_argument("--proposed", help="with --package: the version proposed")
    args = parser.parse_args()

    if args.package:
        if not (args.current and args.proposed) or args.ref or args.base:
            fail("--package takes --current and --proposed, and not --ref or --base")
        targets = [
            {"name": args.package, "current": args.current, "proposed": args.proposed, "wheels": []}
        ]
    elif args.ref and args.base:
        targets = moved(lock_at(args.ref), lock_at(args.base))
    else:
        fail("give --ref and --base, or --package with --current and --proposed")

    findings = 0
    read = 0
    for target in targets:
        name = target["name"]
        try:
            latest = latest_on_pypi(name)
        except Unreadable as exc:
            latest = ""
            print(f"\n{name}: the registry's latest is unreadable ({exc}); read without it")
        versions = {"current": target["current"], "proposed": target["proposed"]}
        if not versions["current"]:
            del versions["current"]
        if latest and _key(latest) > _key(target["proposed"]):
            versions["latest"] = latest
        try:
            wheels = {
                label: (
                    target["wheels"]
                    if label == "proposed" and target["wheels"]
                    else wheels_on_pypi(name, version)
                )
                for label, version in versions.items()
            }
            if not wheels["proposed"]:
                # No wheel at all is not a pure-Python wheel (#188). An sdist ships
                # whatever its build produces: actionlint-py's downloads a Go binary.
                print(
                    f"\n{name} {target['proposed']}: UNDERIVABLE -- no wheel on PyPI, so it "
                    "is built from source at install, and its build decides what it ships"
                )
                findings += 1
                continue
            if not any(compiled(w[0]) for w in wheels["proposed"]):
                continue
            read += 1
            findings += audit(name, versions, wheels)
        except Unreadable as exc:
            print(f"\n{name}: UNDERIVABLE -- {exc}")
            findings += 1

    print()
    # A finding outranks "nothing was read": an UNDERIVABLE row is one of them, and
    # until 0.59.0 an unservable release printed that row and then this line, exit 0.
    if not read and not findings:
        print(
            f"RESULT: NOTHING VENDORED -- none of {len(targets)} moved package(s) is "
            "compiled: each ships pure-Python wheels only."
        )
        return 0
    if not findings:
        print(f"RESULT: CLEAN -- no advisory on anything {read} compiled package(s) ship.")
        return 0
    print(
        f"RESULT: FOUND -- {findings} row(s) above across {len(targets)} moved package(s): "
        "each advisory says whether this PR fixes it, introduces it, or leaves it, and "
        "each UNDERIVABLE row says what could not be read."
    )
    return 1


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
