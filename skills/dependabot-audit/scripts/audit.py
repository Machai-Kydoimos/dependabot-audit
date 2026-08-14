#!/usr/bin/env python3
"""Mechanical half of a dependency-bump audit for PyPI / uv.lock.

Three checks, no judgment:

  provenance  every artifact the lockfile pins for the changed packages, compared
              byte-for-byte against what PyPI serves today (sha256, size, URL,
              yanked)
  currency    the registry's actual latest version and its publish time, so the
              caller can compare against when the PR was opened
  vulns       an OSV batch query across every PyPI-sourced package in the lock

Usage:
    audit.py <uv.lock> [--changed pkg[,pkg...] | --changed-vs <baseline.lock>] [--json]

`--changed-vs` derives the changed set by diffing against the base branch's
lockfile, which is the reliable way to handle a grouped bump — a grouped PR title
says "with 3 updates" and names none of them. It compares artifacts as well as
versions, so a lockfile that re-points a URL and hash while leaving the version
alone is selected and said out loud rather than passing as "no changes".

Exit status: 0 = nothing to report, 1 = at least one discrepancy, stale version,
or known vulnerability, 2 = usage/lookup error — including a requested package
that is not in the lockfile, an empty selection, an unreadable lockfile, and a
registry that could not be reached. Never audit fewer packages than asked in
silence, never print CLEAN on a run that verified nothing, and never let a failed
lookup exit as though it were a finding.
Requires Python 3.11+ (tomllib) and network access.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, NoReturn

PYPI = "https://pypi.org/pypi/{name}/json"
OSV_BATCH = "https://api.osv.dev/v1/querybatch"
# api.osv.dev rejects a larger batch with a 400. Measured at the boundary: 1000
# queries returns 1000 results, 1001 returns HTTP 400.
OSV_BATCH_LIMIT = 1000
# Pages to follow for one package before reporting the result as truncated. A
# package with more vulnerabilities than this has bigger problems than paging.
OSV_PAGE_LIMIT = 10
TIMEOUT = 60
# One retry. An audit makes a call per changed package plus the OSV batch, and
# losing a dozen good calls to one transient 502 is worse than waiting 2s.
ATTEMPTS = 2
BACKOFF = 2.0
UA = "dependabot-audit (+https://github.com/Machai-Kydoimos/dependabot-audit)"


def fail(what: str) -> NoReturn:
    """Exit 2 — the audit could not run.

    Distinct from exit 1, which means the audit ran and found something. An
    unhandled exception exits 1, so every foreseeable failure has to come through
    here: otherwise a registry outage is indistinguishable from a finding, and
    the caller reads "OSV unreachable" as "OSV reported a vulnerability".
    """
    print(f"error: {what}", file=sys.stderr)
    raise SystemExit(2)


def _get_json(url: str, payload: bytes | None = None) -> Any:
    # Callers build URLs from the module constants above, but asserting the scheme
    # is cheaper than trusting it: a `file:` or custom scheme is what S310 warns
    # about, and part of these URLs comes from a lockfile written by the PR under
    # audit, which is exactly the input not to trust.
    if not url.startswith("https://"):
        raise ValueError(f"refusing a non-https URL: {url}")
    req = urllib.request.Request(url)  # noqa: S310 — scheme checked above
    req.add_header("User-Agent", UA)
    if payload is not None:
        req.data = payload
        req.add_header("Content-Type", "application/json")
    for attempt in range(ATTEMPTS):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310
                return json.load(resp)
        except urllib.error.HTTPError as exc:
            # A 4xx is an answer, not a hiccup: "no such package" is information
            # the caller needs now, and retrying only delays it.
            if exc.code < 500 or attempt == ATTEMPTS - 1:
                raise
            time.sleep(BACKOFF)
        except (OSError, json.JSONDecodeError):
            # OSError covers URLError and TimeoutError both; a read that times
            # out mid-body raises TimeoutError, which is not a URLError.
            if attempt == ATTEMPTS - 1:
                raise
            time.sleep(BACKOFF)
    raise AssertionError("unreachable: the loop returns or raises")


def _normalize(name: str) -> str:
    """PEP 503 name normalization, so Pillow/pillow and foo_bar/foo-bar match."""
    return re.sub(r"[-_.]+", "-", name).lower()


def artifact_hashes(pkg: dict[str, Any]) -> tuple[str, ...]:
    """Every artifact hash this entry pins, sorted — the entry's real identity.

    A version is not the only thing a lockfile diff can move. A PR can rewrite an
    artifact's `url` and `hash` and leave the version alone, which is the single
    lockfile change most worth catching, and a changed-set keyed on the version
    selects nothing at all for it.
    """
    artifacts = ([pkg["sdist"]] if "sdist" in pkg else []) + list(pkg.get("wheels", []))
    return tuple(sorted(str(a.get("hash", "")) for a in artifacts))


def derive_changed(packages: list[dict[str, Any]], baseline_path: str) -> list[dict[str, str]]:
    """What moved relative to a baseline lockfile, and in what way.

    Compares (name, version) *pairs* rather than a name->version mapping: a
    lockfile may legitimately pin one package at several versions under different
    resolution-markers, and collapsing those by name reports a spurious change.
    Within a matching pair it then compares the artifact hashes, so an artifact
    substitution at an unchanged version is selected rather than missed.

    Returns one record per changed *name*, with `kind` in:

        added       the name is not in the baseline at all
        version     the name is, at some other version
        artifacts   same name and version, different artifacts — not a routine bump

    Removals are not reported: there is nothing left to verify for them.
    """
    base = pypi_sourced(load_lock(baseline_path))
    base_versions: dict[str, set[str]] = {}
    base_artifacts: dict[tuple[str, str], set[str]] = {}
    for p in base:
        key = _normalize(p["name"])
        base_versions.setdefault(key, set()).add(p["version"])
        base_artifacts.setdefault((key, p["version"]), set()).update(artifact_hashes(p))

    seen: set[str] = set()
    changed: list[dict[str, str]] = []
    for p in packages:
        key, version = _normalize(p["name"]), p["version"]
        if key not in base_versions:
            kind, was = "added", ""
        elif version not in base_versions[key]:
            kind, was = "version", ", ".join(sorted(base_versions[key]))
        elif set(artifact_hashes(p)) != base_artifacts[key, version]:
            kind, was = "artifacts", version
        else:
            continue
        if p["name"] in seen:
            continue
        seen.add(p["name"])
        changed.append({"name": p["name"], "kind": kind, "was": was, "now": version})
    return changed


def select_targets(
    packages: list[dict[str, Any]], changed: list[str]
) -> tuple[list[dict[str, Any]], list[str]]:
    """Lockfile entries matching the requested names, plus the names not found.

    A name can map to SEVERAL entries — one package pinned at different versions
    under different resolution-markers — so this returns every match. Selecting
    through a name->entry mapping keeps one and drops the rest, and the dropped
    artifacts go unverified while the output still looks complete.
    """
    wanted = {_normalize(n) for n in changed}
    targets = [p for p in packages if _normalize(p["name"]) in wanted]
    found = {_normalize(p["name"]) for p in targets}
    unmatched = [n for n in changed if _normalize(n) not in found]
    return targets, unmatched


def _sortable(version: str) -> tuple[int, ...]:
    """Best-effort numeric version key; non-numeric segments sort as -1."""
    parts = []
    for seg in version.split("."):
        parts.append(int(seg) if seg.isdigit() else -1)
    return tuple(parts)


def load_lock(path: str) -> list[dict[str, Any]]:
    with open(path, "rb") as fh:
        data = tomllib.load(fh)
    if "package" not in data:
        raise ValueError("no [[package]] entries — is this a lockfile?")
    return list(data["package"])


def _is_pypi(pkg: dict[str, Any]) -> bool:
    return str(pkg.get("source", {}).get("registry", "")).startswith("https://pypi.org")


def pypi_sourced(packages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [p for p in packages if _is_pypi(p)]


def non_pypi(packages: list[dict[str, Any]]) -> list[str]:
    """Names of packages this script cannot verify: git, path, or a private index.

    Reported rather than dropped. A bumped git dependency that never appears in
    the output is an under-audit indistinguishable from a clean one — the same
    failure `select_targets` guards against, one level up.
    """
    return [p["name"] for p in packages if not _is_pypi(p)]


def check_provenance(entry: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
    """Compare every locked artifact for one package against PyPI's record."""
    name, version = entry["name"], entry["version"]
    result: dict[str, Any] = {"name": name, "version": version, "artifacts": [], "ok": True}

    release = meta.get("releases", {}).get(version)
    if not release:
        result["ok"] = False
        result["error"] = f"version {version} not present on PyPI"
        return result

    live = {f["filename"]: f for f in release}
    artifacts = []
    if "sdist" in entry:
        artifacts.append(("sdist", entry["sdist"]))
    artifacts += [("wheel", w) for w in entry.get("wheels", [])]

    for kind, art in artifacts:
        filename = art["url"].rsplit("/", 1)[-1]
        record = live.get(filename)
        if record is None:
            result["ok"] = False
            result["artifacts"].append(
                {"kind": kind, "filename": filename, "ok": False, "error": "not on PyPI"}
            )
            continue

        # None means "not recorded, so not compared" — distinct from False, which
        # means compared and different. `size` is optional in a uv.lock artifact
        # table: uv omits it when the index does not report one, and comparing it
        # unconditionally reports `size MISMATCH` on an artifact whose hash and URL
        # match PyPI byte-for-byte. That is the row a reader is least able to
        # dismiss — "the hash matches but the size does not" reads like tampering.
        checks: dict[str, bool | None] = {
            "sha256": record["digests"]["sha256"] == art["hash"].removeprefix("sha256:"),
            "size": record["size"] == art["size"] if "size" in art else None,
            "url": record["url"] == art["url"],
            "not_yanked": not record.get("yanked", False),
        }
        ok = all(v for v in checks.values() if v is not None)
        result["ok"] &= ok
        result["artifacts"].append({"kind": kind, "filename": filename, "ok": ok, "checks": checks})

    return result


_PRERELEASE = re.compile(r"(a|b|c|rc|alpha|beta|pre|preview|dev)\d*$", re.IGNORECASE)


def _is_prerelease(version: str) -> bool:
    """Crude PEP 440 pre-release / dev test; `packaging` is not available here.

    Tail-anchored on purpose, so `1.0.post1` — a real release a bot can propose —
    survives while `1.3.0rc1`, which one never will, is dropped.
    """
    return bool(_PRERELEASE.search(version))


def fork_context(entry: dict[str, Any], pins: dict[str, list[str]]) -> tuple[int, bool]:
    """How often this package is pinned in the lock, and whether this is the highest.

    uv forks a package into several `[[package]]` blocks when different parts of
    the resolution need different versions.
    """
    versions = pins.get(_normalize(entry["name"])) or [entry["version"]]
    highest = max(_sortable(v) for v in versions)
    return len(versions), _sortable(entry["version"]) >= highest


def check_currency(
    entry: dict[str, Any],
    meta: dict[str, Any],
    *,
    pins: int = 1,
    newest: bool = True,
) -> dict[str, Any]:
    """Report the registry's true latest version alongside the locked one.

    `pins` and `newest` place the entry among the lockfile's pins of the same
    package, which is what decides whether `resolution-markers` excuse a gap.
    """
    name, locked = entry["name"], entry["version"]
    latest = meta["info"]["version"]
    releases = meta.get("releases", {})

    def published(version: str) -> str | None:
        # The *earliest* artifact, not an arbitrary one. The currency question is
        # whether this version existed before the PR was opened, and wheels built
        # by CI can land hours after the sdist they accompany.
        stamps = [
            f["upload_time_iso_8601"]
            for f in releases.get(version) or []
            if f.get("upload_time_iso_8601")
        ]
        return min(stamps, default=None)

    gap = [
        v
        for v in releases
        if releases[v]
        and not _is_prerelease(v)
        and _sortable(locked) < _sortable(v) <= _sortable(latest)
    ]
    # Ordered by publish time, because that is the comparison the report asks the
    # reader to make. _sortable breaks ties and covers an absent timestamp.
    gap.sort(key=lambda v: (published(v) or "", _sortable(v)))

    constrained = bool(entry.get("resolution-markers"))
    return {
        "name": name,
        "locked": locked,
        "latest": latest,
        "current": locked == latest,
        "constrained": constrained,
        "pins": pins,
        # uv stamps resolution-markers on *every* block of a forked package, so
        # the markers alone cannot say which pin is expected to trail. A lower
        # fork is held back by design; the highest is the live pin, and its
        # markers excuse nothing. Exempting both hides staleness on the one that
        # matters — the failure this key exists to avoid.
        "held_back": constrained and not newest,
        "locked_published": published(locked),
        "latest_published": published(latest),
        "gap": [
            {
                "version": v,
                "published": published(v),
                "yanked": all(f.get("yanked", False) for f in releases[v]),
            }
            for v in gap
        ],
    }


def _osv_ids(query: dict[str, Any], result: dict[str, Any]) -> tuple[list[str], bool]:
    """Every vulnerability id for one package, following OSV's pagination.

    A `querybatch` result carries at most one page and hands back a
    `next_page_token` when there are more. Left unread those ids are simply
    dropped from the report, which is under-reporting — the direction this tool
    exists not to fail in. Rare enough that the extra call almost never happens.

    Returns the ids and whether the page cap was hit with more still outstanding,
    so a truncation can be said out loud rather than inferred.
    """
    ids = [v["id"] for v in result.get("vulns", [])]
    token = result.get("next_page_token")
    for _ in range(OSV_PAGE_LIMIT):
        if not token:
            return ids, False
        payload = json.dumps({"queries": [{**query, "page_token": token}]}).encode()
        page = _get_json(OSV_BATCH, payload)["results"][0]
        ids += [v["id"] for v in page.get("vulns", [])]
        token = page.get("next_page_token")
    return ids, bool(token)


def check_vulns(packages: list[dict[str, Any]]) -> dict[str, Any]:
    pkgs = [(p["name"], p["version"]) for p in packages]
    queries = [{"package": {"name": n, "ecosystem": "PyPI"}, "version": v} for n, v in pkgs]

    # OSV rejects a batch over the limit with a 400 (measured: 1000 ok, 1001 not).
    # Unchunked, a lockfile large enough to trip it loses the vulnerability phase
    # outright, after every provenance and currency call has already been paid for.
    results: list[Any] = []
    for start in range(0, len(queries), OSV_BATCH_LIMIT):
        chunk = queries[start : start + OSV_BATCH_LIMIT]
        results += _get_json(OSV_BATCH, json.dumps({"queries": chunk}).encode())["results"]

    hits = []
    # strict=True catches a chunk that came back short, which is the failure this
    # pairing has to be protected from: the ids would silently shift packages.
    for (name, version), query, result in zip(pkgs, queries, results, strict=True):
        if not result.get("vulns"):
            continue
        ids, truncated = _osv_ids(query, result)
        hits.append({"name": name, "version": version, "ids": ids, "truncated": truncated})
    return {"queried": len(pkgs), "hits": hits}


def _state(value: bool | None) -> str:
    """Three states, because collapsing the third into either of the others loses
    exactly the information the check is there to carry."""
    if value is None:
        return "not recorded"
    return "match" if value else "MISMATCH"


def render(report: dict[str, Any]) -> None:
    print(f"lockfile: {report['lock']}\n")

    # The stderr diagnostic is for whoever ran the command; this is for whoever
    # reads the report. A re-pointed artifact at an unchanged version has to
    # survive into both.
    swapped = [c["name"] for c in report.get("selection", []) if c["kind"] == "artifacts"]
    if swapped:
        print(f"!! ARTIFACTS CHANGED at an unchanged version: {', '.join(swapped)}")
        print("   A lockfile that re-points an artifact without moving the version is")
        print("   not a routine bump. The provenance rows below are the ones to read.\n")

    for prov in report["provenance"]:
        head = f"=== {prov['name']} {prov['version']}"
        if "error" in prov:
            print(f"{head}\n  !! {prov['error']}\n")
            continue
        print(head)
        for art in prov["artifacts"]:
            flag = "OK " if art["ok"] else "BAD"
            detail = art.get("error") or " | ".join(
                f"{k} {_state(v)}" for k, v in art["checks"].items()
            )
            print(f"  {flag} {art['kind']:5s} {art['filename']}\n      {detail}")
        print(
            f"  -> {len(prov['artifacts'])} artifact(s), "
            f"{'all match' if prov['ok'] else 'DISCREPANCIES'}\n"
        )

    for cur in report["currency"]:
        if cur["current"]:
            print(f"=== {cur['name']}: locked {cur['locked']} IS the latest\n")
            continue
        if cur["held_back"]:
            print(
                f"=== {cur['name']}: locked {cur['locked']}, registry latest "
                f"{cur['latest']} — HELD BACK by resolution-markers"
            )
            print(
                f"      behind a higher pin of the same package ({cur['pins']} in "
                "the lock); expected to"
            )
            print("      trail the registry, so not a staleness finding\n")
            continue
        print(
            f"=== {cur['name']}: locked {cur['locked']}, "
            f"registry latest {cur['latest']}  <-- NOT CURRENT"
        )
        if cur["constrained"]:
            print(
                f"      pinned under resolution-markers, but this is the newest of "
                f"{cur['pins']} pin(s):"
            )
            print(
                "      the markers excuse the gap only if they exclude the environment you target"
            )
        for rel in cur["gap"]:
            mark = " (yanked)" if rel["yanked"] else ""
            print(f"      {rel['version']:12s} published {rel['published']}{mark}")
        print("      earliest first; compare it against the PR's createdAt\n")

    vulns = report["vulns"]
    if vulns["hits"]:
        for hit in vulns["hits"]:
            more = "  (MORE NOT LISTED — OSV paging cap)" if hit.get("truncated") else ""
            print(f"  VULN {hit['name']}=={hit['version']}: {', '.join(hit['ids'])}{more}")
        print(f"\nOSV: {len(vulns['hits'])} of {vulns['queried']} packages affected")
    else:
        print(f"OSV: no known vulnerabilities across {vulns['queried']} packages")

    # The counts ride in the verdict line so it cannot overstate itself: a
    # reader who skims to RESULT must see the size of the evidence behind it.
    checked = sum(len(p["artifacts"]) for p in report["provenance"])
    print(
        f"\nRESULT: {'CLEAN' if report['clean'] else 'NEEDS REVIEW'}"
        f" — {len(report['provenance'])} package(s), {checked} artifact(s) checked"
    )
    if report["skipped"]:
        print(
            f"        {len(report['skipped'])} package(s) NOT checked "
            f"(not PyPI-sourced): {', '.join(report['skipped'])}"
        )
    print("This is the mechanical half only — changelog, behavior-change, and")
    print("reproduction phases are not covered here.")


def _why_selected(change: dict[str, str]) -> str:
    """One line saying what moved, because "changed" alone hides the shape."""
    if change["kind"] == "added":
        return f"{change['name']:<24} added at {change['now']}"
    if change["kind"] == "version":
        return f"{change['name']:<24} version {change['was']} -> {change['now']}"
    return f"{change['name']:<24} ARTIFACTS CHANGED at unchanged version {change['now']}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("lock", help="path to uv.lock")
    parser.add_argument("--changed", default="", help="comma-separated bumped packages")
    parser.add_argument(
        "--changed-vs",
        metavar="BASELINE_LOCK",
        help="derive the changed set by diffing against the base branch's lockfile "
        "(more reliable than reading a grouped PR title, which names no packages)",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON instead of text")
    args = parser.parse_args()

    try:
        locked = load_lock(args.lock)
    except (OSError, tomllib.TOMLDecodeError, ValueError) as exc:
        fail(f"cannot read {args.lock}: {exc}")

    packages = pypi_sourced(locked)
    skipped = non_pypi(locked)

    selection: list[dict[str, str]] = []
    if args.changed_vs:
        try:
            selection = derive_changed(packages, args.changed_vs)
        except (OSError, tomllib.TOMLDecodeError, ValueError) as exc:
            fail(f"cannot read baseline {args.changed_vs}: {exc}")
        changed = [c["name"] for c in selection]
        # Diagnostic, so stderr: on stdout it would sit in front of --json output
        # and make the documented machine-readable mode unparseable.
        print(
            f"derived {len(changed)} changed package(s) vs {args.changed_vs}:"
            f"{'' if changed else ' (none)'}",
            file=sys.stderr,
        )
        for change in selection:
            print(f"  {_why_selected(change)}", file=sys.stderr)
        if any(c["kind"] == "artifacts" for c in selection):
            print(
                "  ^ an artifact moved without the version moving. That is not a"
                " routine bump —\n"
                "    read the provenance rows below before anything else.",
                file=sys.stderr,
            )
        print(file=sys.stderr)
    else:
        changed = [n.strip() for n in args.changed.split(",") if n.strip()]

    targets, unmatched = select_targets(packages, changed)

    if unmatched:
        print(
            f"error: not found in {args.lock}: {', '.join(unmatched)}\n"
            "       An audit that silently checks fewer packages than it was asked to "
            "is worse than no audit.\n"
            "       Fix the names (or use --changed-vs) and re-run.",
            file=sys.stderr,
        )
        return 2

    # Auditing nothing is the same failure as auditing too little, and it is the
    # likelier one: point --changed-vs at the wrong lockfile and every version
    # matches, which reads as "no changes" rather than "wrong input".
    if not targets:
        # With artifacts in the comparison key, this message is now true: nothing
        # moved at all, rather than nothing moved *that was being looked at*.
        reason = (
            f"{args.lock} and {args.changed_vs} pin identical packages, versions,"
            " and artifact hashes — either this lockfile did not change, or it is"
            " being compared against itself"
            if args.changed_vs
            else "no package names were given"
        )
        print(
            f"error: nothing selected to audit: {reason}.\n"
            f"       {args.lock} pins {len(packages)} PyPI package(s), none selected.\n"
            "       A run that verifies nothing must not report CLEAN.",
            file=sys.stderr,
        )
        return 2

    report: dict[str, Any] = {
        "lock": args.lock,
        "selection": selection,
        "provenance": [],
        "currency": [],
        "skipped": skipped,
    }
    # Fork structure comes from the whole lockfile, not just the selected set: a
    # bump can move one fork of a package while its sibling stays where it is.
    pins: dict[str, list[str]] = {}
    for pkg in packages:
        pins.setdefault(_normalize(pkg["name"]), []).append(pkg["version"])

    meta_cache: dict[str, Any] = {}
    for entry in targets:
        key = _normalize(entry["name"])
        if key not in meta_cache:
            try:
                # The name comes out of the lockfile under audit, so it does not
                # get to shape the URL path.
                safe = urllib.parse.quote(entry["name"], safe="")
                meta_cache[key] = _get_json(PYPI.format(name=safe))
            except (OSError, json.JSONDecodeError) as exc:
                fail(f"PyPI lookup failed for {entry['name']}: {exc}")
        meta = meta_cache[key]
        n_pins, newest = fork_context(entry, pins)
        report["provenance"].append(check_provenance(entry, meta))
        report["currency"].append(check_currency(entry, meta, pins=n_pins, newest=newest))

    try:
        report["vulns"] = check_vulns(packages)
    except (OSError, json.JSONDecodeError) as exc:
        # Left unhandled this exits 1, which the contract reserves for findings —
        # an outage would read as "OSV reported a vulnerability".
        fail(f"OSV query failed: {exc}")
    report["clean"] = (
        all(p["ok"] for p in report["provenance"])
        and all(c["current"] or c["held_back"] for c in report["currency"])
        and not report["vulns"]["hits"]
    )

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        render(report)
    return 0 if report["clean"] else 1


def cli() -> NoReturn:
    """Entry point. Anything unforeseen becomes exit 2, never exit 1.

    Every *foreseeable* failure already routes through `fail()`. This is the
    backstop for the rest: an unhandled exception exits 1, which the contract
    reserves for "ran and found something", so a `KeyError` on a lockfile key
    reads as a discrepancy. The lockfile is written by the PR under audit, which
    is the input least entitled to be well-formed.

    Set `DEPENDABOT_AUDIT_DEBUG` to re-raise and keep the traceback.
    """
    try:
        sys.exit(main())
    except SystemExit:
        # `fail()`'s exit 2 and `main()`'s legitimate 0 and 1 all arrive here.
        # Re-raise before the broad handler, or all three get rewritten to 2.
        raise
    except Exception as exc:
        if os.environ.get("DEPENDABOT_AUDIT_DEBUG"):
            raise
        fail(
            f"unexpected {type(exc).__name__}: {exc}\n"
            "       This is a bug, not a finding. Set DEPENDABOT_AUDIT_DEBUG=1 "
            "for the traceback."
        )


if __name__ == "__main__":
    cli()
