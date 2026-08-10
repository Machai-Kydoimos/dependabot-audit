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
    audit.py <uv.lock> [--changed pkg[,pkg...]] [--json]

Exit status: 0 = nothing to report, 1 = at least one discrepancy, stale version,
or known vulnerability. Requires Python 3.11+ (tomllib) and network access.
"""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
import urllib.error
import urllib.request
from typing import Any

PYPI = "https://pypi.org/pypi/{name}/json"
OSV_BATCH = "https://api.osv.dev/v1/querybatch"
TIMEOUT = 60


def _get_json(url: str, payload: bytes | None = None) -> Any:
    req = urllib.request.Request(url)
    if payload is not None:
        req.data = payload
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310
        return json.load(resp)


def _sortable(version: str) -> tuple[int, ...]:
    """Best-effort numeric version key; non-numeric segments sort as -1."""
    parts = []
    for seg in version.split("."):
        parts.append(int(seg) if seg.isdigit() else -1)
    return tuple(parts)


def load_lock(path: str) -> list[dict[str, Any]]:
    with open(path, "rb") as fh:
        return tomllib.load(fh)["package"]


def pypi_sourced(packages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        p
        for p in packages
        if str(p.get("source", {}).get("registry", "")).startswith("https://pypi.org")
    ]


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

        checks = {
            "sha256": record["digests"]["sha256"] == art["hash"].removeprefix("sha256:"),
            "size": record["size"] == art.get("size"),
            "url": record["url"] == art["url"],
            "not_yanked": not record.get("yanked", False),
        }
        ok = all(checks.values())
        result["ok"] &= ok
        result["artifacts"].append(
            {"kind": kind, "filename": filename, "ok": ok, "checks": checks}
        )

    return result


def check_currency(entry: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
    """Report the registry's true latest version alongside the locked one."""
    name, locked = entry["name"], entry["version"]
    latest = meta["info"]["version"]

    releases = meta.get("releases", {})
    gap = [
        v
        for v in releases
        if releases[v] and _sortable(locked) < _sortable(v) <= _sortable(latest)
    ]
    gap.sort(key=_sortable)

    def published(version: str) -> str | None:
        files = releases.get(version) or []
        return files[0].get("upload_time_iso_8601") if files else None

    return {
        "name": name,
        "locked": locked,
        "latest": latest,
        "current": locked == latest,
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


def check_vulns(packages: list[dict[str, Any]]) -> dict[str, Any]:
    pkgs = [(p["name"], p["version"]) for p in packages]
    queries = [
        {"package": {"name": n, "ecosystem": "PyPI"}, "version": v} for n, v in pkgs
    ]
    results = _get_json(OSV_BATCH, json.dumps({"queries": queries}).encode())["results"]

    hits = [
        {"name": n, "version": v, "ids": [x["id"] for x in r.get("vulns", [])]}
        for (n, v), r in zip(pkgs, results, strict=True)
        if r.get("vulns")
    ]
    return {"queried": len(pkgs), "hits": hits}


def render(report: dict[str, Any]) -> None:
    print(f"lockfile: {report['lock']}\n")

    for prov in report["provenance"]:
        head = f"=== {prov['name']} {prov['version']}"
        if "error" in prov:
            print(f"{head}\n  !! {prov['error']}\n")
            continue
        print(head)
        for art in prov["artifacts"]:
            flag = "OK " if art["ok"] else "BAD"
            detail = art.get("error") or " | ".join(
                f"{k} {'match' if v else 'MISMATCH'}" for k, v in art["checks"].items()
            )
            print(f"  {flag} {art['kind']:5s} {art['filename']}\n      {detail}")
        print(f"  -> {len(prov['artifacts'])} artifact(s), "
              f"{'all match' if prov['ok'] else 'DISCREPANCIES'}\n")

    for cur in report["currency"]:
        if cur["current"]:
            print(f"=== {cur['name']}: locked {cur['locked']} IS the latest\n")
            continue
        print(f"=== {cur['name']}: locked {cur['locked']}, "
              f"registry latest {cur['latest']}  <-- NOT CURRENT")
        for rel in cur["gap"]:
            mark = " (yanked)" if rel["yanked"] else ""
            print(f"      {rel['version']:12s} published {rel['published']}{mark}")
        print("      compare the earliest of these against the PR's createdAt\n")

    vulns = report["vulns"]
    if vulns["hits"]:
        for hit in vulns["hits"]:
            print(f"  VULN {hit['name']}=={hit['version']}: {', '.join(hit['ids'])}")
        print(f"\nOSV: {len(vulns['hits'])} of {vulns['queried']} packages affected")
    else:
        print(f"OSV: no known vulnerabilities across {vulns['queried']} packages")

    print("\nRESULT:", "CLEAN" if report["clean"] else "NEEDS REVIEW")
    print("This is the mechanical half only — changelog, behavior-change, and")
    print("reproduction phases are not covered here.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("lock", help="path to uv.lock")
    parser.add_argument("--changed", default="", help="comma-separated bumped packages")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of text")
    args = parser.parse_args()

    packages = pypi_sourced(load_lock(args.lock))
    changed = [n.strip() for n in args.changed.split(",") if n.strip()]
    targets = [p for p in packages if p["name"] in changed]

    if changed and not targets:
        print(f"error: none of {changed} found in {args.lock}", file=sys.stderr)
        return 2

    report: dict[str, Any] = {"lock": args.lock, "provenance": [], "currency": []}
    for entry in targets:
        try:
            meta = _get_json(PYPI.format(name=entry["name"]))
        except urllib.error.URLError as exc:
            print(f"error: PyPI lookup failed for {entry['name']}: {exc}", file=sys.stderr)
            return 2
        report["provenance"].append(check_provenance(entry, meta))
        report["currency"].append(check_currency(entry, meta))

    report["vulns"] = check_vulns(packages)
    report["clean"] = (
        all(p["ok"] for p in report["provenance"])
        and all(c["current"] for c in report["currency"])
        and not report["vulns"]["hits"]
    )

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        render(report)
    return 0 if report["clean"] else 1


if __name__ == "__main__":
    sys.exit(main())
