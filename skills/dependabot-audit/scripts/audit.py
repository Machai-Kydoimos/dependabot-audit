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
that is not in the lockfile, an empty selection, an unreadable lockfile, a
lockfile belonging to an ecosystem this plugin does not cover, and a registry
that could not be reached. Never audit fewer packages than asked in silence,
never print CLEAN on a run that verified nothing, and never let a failed lookup
exit as though it were a finding.
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
from collections.abc import Sequence
from typing import Any, NoReturn

# The Simple API's JSON form (PEP 691/700/714), not the legacy
# /pypi/<name>/json. The legacy endpoint's `releases` key is its undocumented,
# long-discouraged half, and it is the only one that does not expose PEP 740
# provenance. One request either way.
SIMPLE = "https://pypi.org/simple/{name}/"
SIMPLE_ACCEPT = "application/vnd.pypi.simple.v1+json"
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
# Ceiling on an honoured Retry-After. A registry is entitled to ask for ten
# minutes; an audit is not entitled to stall that long without saying anything.
RETRY_AFTER_CAP = 30.0
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


def _retry_delay(exc: urllib.error.HTTPError) -> float:
    """Honour `Retry-After`, capped.

    Capped because an audit that stalls for ten minutes is worse than one that
    fails fast at exit 2 and lets the caller decide. `Retry-After` may also be an
    HTTP-date, which is not worth parsing for a single retry — fall back rather
    than crash on it.
    """
    header = exc.headers.get("Retry-After") if exc.headers else None
    if not header:
        return BACKOFF
    try:
        return min(float(header), RETRY_AFTER_CAP)
    except ValueError:
        return BACKOFF


def _get_json(url: str, payload: bytes | None = None, accept: str | None = None) -> Any:
    # Callers build URLs from the module constants above, but asserting the scheme
    # is cheaper than trusting it: a `file:` or custom scheme is what S310 warns
    # about, and part of these URLs comes from a lockfile written by the PR under
    # audit, which is exactly the input not to trust.
    if not url.startswith("https://"):
        raise ValueError(f"refusing a non-https URL: {url}")
    req = urllib.request.Request(url)  # noqa: S310 — scheme checked above
    req.add_header("User-Agent", UA)
    if accept is not None:
        req.add_header("Accept", accept)
    if payload is not None:
        req.data = payload
        req.add_header("Content-Type", "application/json")
    for attempt in range(ATTEMPTS):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310
                return json.load(resp)
        except urllib.error.HTTPError as exc:
            # A 4xx is an answer, not a hiccup: "no such package" is information
            # the caller needs now, and retrying only delays it. 429 is the one
            # exception — it explicitly means "try again", often with a
            # Retry-After — and an audit issues a call per changed package plus
            # the OSV batch, which is exactly the burst shape that trips a
            # limiter. Both registries this talks to rate-limit.
            if (exc.code < 500 and exc.code != 429) or attempt == ATTEMPTS - 1:
                raise
            time.sleep(_retry_delay(exc))
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
    return tuple(sorted(str(a.get("hash", "")) for _, a in iter_artifacts(pkg)))


def iter_artifacts(pkg: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """(kind, artifact table) for the sdist and every wheel this entry pins."""
    sdist = [("sdist", pkg["sdist"])] if "sdist" in pkg else []
    return sdist + [("wheel", w) for w in pkg.get("wheels", [])]


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


# PEP 440, as much of it as PyPI can actually serve. `packaging` is not available
# here — audit.py runs under whatever bare python3 the audited repo has — and the
# previous best-effort key (split on ".", non-numeric segments sort as -1) put an
# epoch *below* unversioned releases, dropping it out of the currency gap
# entirely. That mattered once the Simple API made "latest" this script's own
# computation rather than something PyPI hands over.
_VERSION = re.compile(
    r"""^\s*v?
    (?:(?P<epoch>\d+)!)?
    (?P<release>\d+(?:\.\d+)*)
    (?:[-_.]?(?P<pre_l>a|b|c|rc|alpha|beta|pre|preview)[-_.]?(?P<pre_n>\d+)?)?
    (?:-(?P<post_n1>\d+)|[-_.]?(?P<post_l>post|rev|r)[-_.]?(?P<post_n2>\d+)?)?
    (?P<dev>[-_.]?dev[-_.]?(?P<dev_n>\d+)?)?
    (?:\+(?P<local>[a-z0-9]+(?:[-_.][a-z0-9]+)*))?
    \s*$""",
    re.VERBOSE | re.IGNORECASE,
)
_PRE_ALIAS = {"alpha": "a", "beta": "b", "c": "rc", "pre": "rc", "preview": "rc"}


def publisher_of(record: dict[str, Any], cache: dict[str, Any]) -> dict[str, Any] | None:
    """Who PyPI says built this artifact, per its PEP 740 attestation.

    A hash comparison catches a lockfile edited after it was written honestly. It
    cannot catch a bad artifact the registry itself is serving, because then the
    record and the lockfile agree and agreement is the whole test. An attestation
    names the repository and workflow that built the file, which is a materially
    stronger claim: *this wheel was built by the project's own CI*, not merely
    *this wheel is what PyPI is currently serving*.

    Returns None when the artifact carries no attestation, which is **normal** —
    Trusted Publishing postdates most of PyPI, and packages published by other
    means never will have one. Treating absence as a warning would make the row
    noise on most lockfiles and train the reader to skip it.

    Scope: this reads PyPI's *summary* of the bundle. It does not verify the
    Sigstore signature, which would mean a dependency, and stdlib-only is
    load-bearing here. The report has to say so — it is stronger than a hash
    echo, not independent of PyPI.
    """
    url = record.get("provenance")
    if not url:
        return None
    if url not in cache:
        try:
            bundles = _get_json(url)["attestation_bundles"]
        except (OSError, json.JSONDecodeError, KeyError):
            # An integrity endpoint that is down or shaped unexpectedly must not
            # take the audit with it: the hash checks stand on their own.
            cache[url] = None
        else:
            cache[url] = bundles[0]["publisher"] if bundles else None
    published: dict[str, Any] | None = cache[url]
    return published


def check_attestations(
    entry: dict[str, Any],
    project: dict[str, Any],
    *,
    previous: str = "",
    cache: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Publisher identity for the locked artifacts, and whether it moved.

    `previous` is the version this package held in the base lockfile, when there
    was one. Both versions' files are in the same Simple API response, so the
    comparison costs one extra request and needs no external source of truth —
    and "the previous release was built by the project's CI, this one by someone
    else" is the signal worth having.
    """
    cache = {} if cache is None else cache
    live = {f["filename"]: f for f in project.get("files", [])}
    locked_files = [
        live[art["url"].rsplit("/", 1)[-1]]
        for _, art in iter_artifacts(entry)
        if art.get("url", "").rsplit("/", 1)[-1] in live
    ]

    artifacts = [
        {"filename": f["filename"], "publisher": publisher_of(f, cache)} for f in locked_files
    ]
    attested = [a["publisher"] for a in artifacts if a["publisher"]]

    result: dict[str, Any] = {
        "name": entry["name"],
        "version": entry["version"],
        "artifacts": artifacts,
        "attested": len(attested),
        "unattested": len(artifacts) - len(attested),
        "previous": None,
        "changed": False,
    }
    if not previous or not attested:
        return result

    releases = files_by_version(project)
    for record in releases.get(previous, []):
        before = publisher_of(record, cache)
        if before:
            result["previous"] = {"version": previous, "publisher": before}
            result["changed"] = any(_publisher_id(before) != _publisher_id(p) for p in attested)
            break
    return result


def _publisher_id(publisher: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(publisher.get("kind", "")),
        str(publisher.get("repository", "")),
        str(publisher.get("workflow", "")),
    )


def format_publisher(publisher: dict[str, Any]) -> str:
    kind, repository, workflow = _publisher_id(publisher)
    return f"{kind} {repository} ({workflow})" if workflow else f"{kind} {repository}"


def _parse_version(version: str) -> dict[str, Any]:
    match = _VERSION.match(version)
    if not match:
        raise ValueError(f"not a PEP 440 version: {version!r}")
    part = match.groupdict()
    pre = None
    if part["pre_l"]:
        letter = part["pre_l"].lower()
        pre = (_PRE_ALIAS.get(letter, letter), int(part["pre_n"] or 0))
    post = None
    if part["post_n1"] is not None:
        post = int(part["post_n1"])
    elif part["post_l"]:
        post = int(part["post_n2"] or 0)
    # The whole group, not just its number: `1.0.dev` is a dev release with an
    # implicit 0, and reading only `dev_n` cannot tell it from no dev segment.
    dev = int(part["dev_n"] or 0) if part["dev"] else None
    return {
        "epoch": int(part["epoch"] or 0),
        "release": tuple(int(n) for n in part["release"].split(".")),
        "pre": pre,
        "post": post,
        "dev": dev,
        "local": part["local"],
    }


def _version_key(version: str) -> tuple[Any, ...]:
    """A total order over PEP 440 versions, as tuples of tuples.

    Each component is rank-prefixed rather than using sentinel objects, so the
    whole key is comparable with plain tuple comparison and no helper classes.

        pre    (0,) a dev release with no pre-segment  <  (1, letter, n) a
               pre-release  <  (2,) a final release
        post   (0,) absent  <  (1, n)
        dev    (0, n) a dev release  <  (1,) not a dev release
        local  (0,) absent  <  (1, text); PyPI rejects local versions on upload,
               so this only has to be deterministic, not PEP 440-exact

    Trailing zeros are trimmed from the release tuple, because PEP 440 says
    `1.0` and `1.0.0` are the same version.

    Raises ValueError on anything it cannot parse. A version this script cannot
    order is one whose currency it cannot judge, and refusing is the contract:
    silently sorting it to the bottom is how an epoch release fell out of the gap.
    """
    v = _parse_version(version)
    release = list(v["release"])
    while len(release) > 1 and release[-1] == 0:
        release.pop()

    if v["pre"] is not None:
        pre: tuple[Any, ...] = (1, *v["pre"])
    elif v["dev"] is not None and v["post"] is None:
        pre = (0,)
    else:
        pre = (2,)

    return (
        v["epoch"],
        tuple(release),
        pre,
        (0,) if v["post"] is None else (1, v["post"]),
        (1,) if v["dev"] is None else (0, v["dev"]),
        (0,) if v["local"] is None else (1, v["local"]),
    )


class UnsupportedLockfile(ValueError):
    """A file this script recognised, and does not audit.

    A ValueError, so every handler that already catches a bad lockfile catches
    this too — but raised separately, because exit 2 is the right answer for
    both and the *reason* is not. "Cannot read this file" sends the reader
    looking for corruption; "this is a Cargo.lock" sends them to the ecosystem
    boundary, which is where they actually are.
    """


# Signatures for the lockfiles this plugin does **not** audit. Since 0.8.0 the
# supported surface is `uv.lock` and GitHub Actions, so this message is the edge
# of the tool and the first thing anyone arriving with a different lockfile
# sees. Pointed at a real `Cargo.lock` it used to say `unexpected AttributeError
# ... This is a bug, not a finding` — every part of that right except the
# diagnosis, and it sent the reader hunting for a defect that does not exist.
#
# Listed: the three ecosystems whose recipes were removed in 0.8.0, Python's
# other lockfiles, and the manifests that sit beside a lockfile — `pyproject.toml`
# beside `uv.lock`, `go.mod` beside `go.sum`. Each signature was checked against a
# real file from a public repository. Anything not on the list keeps the generic
# message — a wrong name is worse than no name.
GO_SUM = re.compile(r"^\S+ v\S+ h1:[A-Za-z0-9+/=]+$", re.MULTILINE)
# Both lines are required. This runs before the parse, so a signature that a
# uv.lock could carry would refuse a lockfile the plugin does support — and one
# line of prose inside a TOML string is a great deal likelier than two.
GO_MOD_MODULE = re.compile(r"^module\s+\S+\s*$", re.MULTILINE)
GO_MOD_GO = re.compile(r"^go\s+\d+\.\d+", re.MULTILINE)
# Yarn v1 writes a banner; Berry writes a `__metadata:` block instead.
YARN_LOCK = re.compile(r"^# yarn lockfile v1$|^__metadata:$", re.MULTILINE)
PNPM_LOCK = re.compile(r"^lockfileVersion: ", re.MULTILINE)


def _boundary(path: str, what: str) -> str:
    return (
        f"{path} is {what}.\n"
        "       This plugin audits uv.lock and GitHub Actions, and nothing else.\n"
        "       npm, Cargo and Go recipes were removed rather than left as sketches:\n"
        "       an unverified verifier reports green instead of erroring. Report what\n"
        "       the ecosystem-independent phases established and say what was not\n"
        "       checked — see references/uv-lock.md."
    )


def _sniff_text(raw: str) -> str | None:
    """Formats identified from the bytes, before anything tries to parse them.

    Most of these reach `tomllib` and die on a syntax error, which names a
    column rather than a format — true and useless. Yarn v1 is the one that does
    not: its two-line header is valid TOML, so a sniff that only ran after a
    parse failure never saw it.
    """
    if GO_SUM.search(raw):
        return "a go.sum (Go)"
    if GO_MOD_MODULE.search(raw) and GO_MOD_GO.search(raw):
        return "a go.mod — a manifest, not a lockfile (Go)"
    if YARN_LOCK.search(raw):
        return "a yarn.lock (JavaScript, Yarn)"
    if PNPM_LOCK.search(raw):
        return "a pnpm-lock.yaml (JavaScript, pnpm)"
    return None


def _sniff_json(raw: str) -> str | None:
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    if "lockfileVersion" in data:
        return "a package-lock.json (JavaScript, npm)"
    meta = data.get("_meta")
    if isinstance(meta, dict) and "pipfile-spec" in meta:
        return "a Pipfile.lock (Python, Pipenv)"
    return None


def _sniff_toml(data: dict[str, Any], packages: list[Any]) -> str | None:
    """TOML that parses cleanly and is not a uv.lock.

    `Cargo.lock` is the dangerous one: it is TOML, it has `[[package]]` blocks,
    and its `source` is a **string** where uv writes a table — so it got all the
    way to `_is_pypi` before failing with an `AttributeError`.

    **Every signature here must be one a uv.lock cannot produce**, because this
    runs before the positive identification below and a misfire refuses a
    lockfile the plugin does support. Checked against a real uv.lock: top-level
    keys are `package`, `requires-python`, `resolution-markers`, `revision`,
    `version` — no `metadata` — and no `[[package]]` block carries `checksum`,
    `python-versions`, `files`, or a `source` that is not a table.

    The first draft ordered these the other way, on the theory that identifying
    uv first was the safe direction. It was not: poetry writes a `[package.source]`
    table too, so a real `poetry.lock` read as uv-shaped and fell through to the
    message claiming it was being compared against itself.
    """
    entries = [p for p in packages if isinstance(p, dict)]
    if any("checksum" in p or isinstance(p.get("source"), str) for p in entries):
        return "a Cargo.lock (Rust)"
    metadata = data.get("metadata")
    if (isinstance(metadata, dict) and "lock-version" in metadata) or any(
        "python-versions" in p and "files" in p for p in entries
    ):
        return "a poetry.lock (Python, Poetry)"
    if not entries and ("build-system" in data or "project" in data):
        return "a pyproject.toml — a manifest, not a lockfile (the lockfile is uv.lock)"
    return None


def _looks_like_uv(packages: list[Any]) -> bool:
    """Whether these `[[package]]` blocks are uv's.

    Deliberately broad — it runs after every foreign signature has already been
    ruled out, so its job is to admit anything uv might write rather than to
    discriminate. uv puts a `source` table on every block; `sdist` and `wheels`
    are its artifact keys.
    """
    return any(
        isinstance(p, dict) and (isinstance(p.get("source"), dict) or "sdist" in p or "wheels" in p)
        for p in packages
    )


def load_lock(path: str) -> list[dict[str, Any]]:
    """The `[[package]]` blocks of a uv.lock — or a refusal that names the file.

    Raises `UnsupportedLockfile` for a format this script recognises and does not
    audit, and plain `ValueError`/`TOMLDecodeError` for one it cannot read at
    all. Both exit 2 through `main`; only the message differs.
    """
    with open(path, "rb") as fh:
        # Decoded here rather than handing the handle to `tomllib` so the same
        # bytes can be sniffed. `errors="replace"` turns invalid UTF-8 into a
        # TOML syntax error, which is caught, instead of a UnicodeDecodeError,
        # which is not — and would print "this is a bug".
        raw = fh.read().decode("utf-8", errors="replace")

    # Sniff before parsing, not only after a parse failure. A yarn v1 lockfile
    # opens with two comment lines, which is *valid TOML*, so an
    # only-on-TOMLDecodeError sniff never looked at it. Every signature here is
    # one a uv.lock cannot produce, which is what makes running them first safe.
    found = _sniff_text(raw) or _sniff_json(raw)
    if found:
        raise UnsupportedLockfile(_boundary(path, found))

    # A TOMLDecodeError from here is a real parse error on a file nothing
    # recognised, and its line and column are the useful part. Let it out.
    data = tomllib.loads(raw)

    packages = data["package"] if isinstance(data.get("package"), list) else None
    found = _sniff_toml(data, packages or [])
    if found:
        raise UnsupportedLockfile(_boundary(path, found))
    if packages and _looks_like_uv(packages):
        return [p for p in packages if isinstance(p, dict)]
    if packages is None:
        raise ValueError("no [[package]] entries — is this a lockfile?")
    raise UnsupportedLockfile(
        _boundary(
            path,
            "not a uv.lock: it has [[package]] entries, but none of them carries a\n"
            "       `source` table, an `sdist` or a `wheels` list",
        )
    )


def _is_pypi(pkg: dict[str, Any]) -> bool:
    # The host is matched, not the prefix. `startswith("https://pypi.org")` also
    # accepted `https://pypi.org.evil.com/simple`, and the damage was silence
    # rather than a bad hash: artifacts are fetched from the `SIMPLE` constant
    # regardless, so a look-alike index still got checked against the real PyPI —
    # but it dropped out of `non_pypi` on the way, so nothing in the report ever
    # said the lockfile points somewhere other than PyPI.
    parts = urllib.parse.urlsplit(str(pkg.get("source", {}).get("registry", "")))
    return parts.scheme == "https" and parts.hostname == "pypi.org"


def pypi_sourced(packages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [p for p in packages if _is_pypi(p)]


def non_pypi(packages: list[dict[str, Any]]) -> list[str]:
    """Names of packages this script cannot verify: git, path, or a private index.

    Reported rather than dropped. A bumped git dependency that never appears in
    the output is an under-audit indistinguishable from a clean one — the same
    failure `select_targets` guards against, one level up.
    """
    return [p["name"] for p in packages if not _is_pypi(p)]


def fetch_project(name: str) -> dict[str, Any]:
    """The Simple API's record for one project.

    The name comes out of the lockfile under audit, so it does not get to shape
    the URL path.
    """
    safe = urllib.parse.quote(_normalize(name), safe="")
    project: dict[str, Any] = _get_json(SIMPLE.format(name=safe), accept=SIMPLE_ACCEPT)
    return project


def files_by_version(project: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Group the Simple API's flat file list by version.

    The Simple API carries no per-file version, so each file is attributed by
    matching its filename against the project's *own* `versions` list rather than
    by parsing the filename — longest match wins, so `1.0.1` is not mistaken for
    `1.0`. Measured across 24,512 real files from 12 projects: 2 unattributed,
    both old setuptools sdists named `setuptools-69.3.tar.gz` where PyPI lists the
    version as `69.3.0`.

    An unattributed file costs a *timestamp*, never a gap entry: which versions
    exist comes from `versions`, which is authoritative and complete. That is the
    important property — a version that drops out of the gap is one whose
    changelog never gets read.
    """
    grouped: dict[str, list[dict[str, Any]]] = {v: [] for v in project.get("versions", [])}
    ordered = sorted(grouped, key=len, reverse=True)
    for record in project.get("files", []):
        filename = record.get("filename", "")
        for version in ordered:
            if f"-{version}-" in filename or f"-{version}." in filename:
                grouped[version].append(record)
                break
    return grouped


def check_provenance(entry: dict[str, Any], project: dict[str, Any]) -> dict[str, Any]:
    """Compare every locked artifact for one package against PyPI's record."""
    name, version = entry["name"], entry["version"]
    result: dict[str, Any] = {"name": name, "version": version, "artifacts": [], "ok": True}

    known = {_version_key(v): v for v in project.get("versions", [])}
    if _version_key(version) not in known:
        result["ok"] = False
        result["error"] = f"version {version} not present on PyPI"
        return result

    # Keyed on filename, which is what the Simple API's flat list is already keyed
    # on — no release bucket to index through.
    live = {f["filename"]: f for f in project.get("files", [])}

    for kind, art in iter_artifacts(entry):
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
            "sha256": record["hashes"].get("sha256") == art["hash"].removeprefix("sha256:"),
            # PEP 700 made `size` mandatory on the Simple API, but a private index
            # need not be current with it, so absence stays a third state here too.
            "size": record["size"] == art["size"] if "size" in art and "size" in record else None,
            "url": record["url"] == art["url"],
            # PEP 592: `yanked` is false, true, or a *string* giving the reason.
            "not_yanked": not record.get("yanked", False),
        }
        ok = all(v for v in checks.values() if v is not None)
        result["ok"] &= ok
        result["artifacts"].append({"kind": kind, "filename": filename, "ok": ok, "checks": checks})

    return result


def _is_prerelease(version: str) -> bool:
    """A pre-release or a dev release — neither of which a bot ever proposes.

    Parsed rather than pattern-matched, so `1.0.post1` — a real release a bot
    *can* propose — survives while `1.3.0rc1` and `1.1.dev3` are dropped. Listing
    one in the gap sends the reader to a changelog that cannot be the answer.
    """
    parsed = _parse_version(version)
    return parsed["pre"] is not None or parsed["dev"] is not None


def _ordered_pins(versions: Sequence[str]) -> list[str]:
    """Every pin of one package, newest last, falling back to lockfile order.

    `_version_key` refuses a version it cannot parse, and for an *audited*
    package refusing is the contract — its currency is what the audit is for.
    This list is wider than the audited set, so the same refusal here would let
    one unparseable version in a package nobody asked about abort the whole run.
    Disclosure is not judgment: the names and versions are still worth printing
    unsorted, and lockfile order is a defined order rather than an arbitrary one.
    """
    try:
        return sorted(versions, key=_version_key)
    except ValueError:
        return list(versions)


def fork_context(entry: dict[str, Any], pins: dict[str, list[str]]) -> tuple[list[str], bool]:
    """Every version this package is pinned at, and whether this entry is the highest.

    uv forks a package into several `[[package]]` blocks when different parts of
    the resolution need different versions.

    Returns the versions rather than a count, because the count alone cannot be
    reported: a frozen install materialises one fork, and naming which requires
    naming the others.
    """
    versions = sorted(pins.get(_normalize(entry["name"])) or [entry["version"]], key=_version_key)
    return versions, _version_key(entry["version"]) >= _version_key(versions[-1])


def latest_version(project: dict[str, Any], releases: dict[str, list[dict[str, Any]]]) -> str:
    """The newest release a bot could propose.

    The Simple API has no `info.version`, so "latest" is this script's own
    computation rather than something the registry hands over. That is the one
    real cost of using the specified interface, and it is why `_version_key` is
    a parser rather than a heuristic.

    Pre-releases are excluded because a bot never proposes one, and a version
    whose files are *known* to be entirely yanked is excluded because
    recommending it would be wrong — but it stays visible in the gap, marked,
    rather than disappearing.

    A version with no attributed files is deliberately still eligible.
    `all_yanked` is false for an empty list, so unknown yank status does not
    exclude. Excluding it would drop an epoch release from consideration for
    exactly the reason this function was rewritten — through a different door.
    Naming an empty release as latest is a visible, recoverable wrong answer;
    silently omitting a real one is not.
    """
    usable = [
        v
        for v in project.get("versions", [])
        if not _is_prerelease(v) and not all_yanked(releases.get(v, []))
    ]
    if not usable:
        raise ValueError("no usable release on PyPI (every version is a pre-release or yanked)")
    return str(max(usable, key=_version_key))


def all_yanked(files: list[dict[str, Any]]) -> bool:
    """True only when there is something to judge and all of it is yanked."""
    return bool(files) and all(f.get("yanked", False) for f in files)


def check_currency(
    entry: dict[str, Any],
    project: dict[str, Any],
    *,
    pinned: Sequence[str] = (),
    newest: bool = True,
) -> dict[str, Any]:
    """Report the registry's true latest version alongside the locked one.

    `pinned` is every version of this package the lockfile carries and `newest`
    whether this entry is the highest of them. Together they place the entry
    among its siblings, which is what decides whether `resolution-markers`
    excuse a gap — and what lets the report say which fork an install exercised.
    """
    name, locked = entry["name"], entry["version"]
    siblings = list(pinned) or [locked]
    releases = files_by_version(project)
    latest = latest_version(project, releases)

    def published(version: str) -> str | None:
        # The *earliest* artifact, not an arbitrary one. The currency question is
        # whether this version existed before the PR was opened, and wheels built
        # by CI can land hours after the sdist they accompany.
        stamps = [f["upload-time"] for f in releases.get(version) or [] if f.get("upload-time")]
        return min(stamps, default=None)

    # Membership comes from `versions`, which is authoritative and complete, not
    # from the files — a version whose files could not be attributed still shows
    # up here, without a timestamp, rather than silently leaving the gap.
    gap = [
        v
        for v in project.get("versions", [])
        if not _is_prerelease(v) and _version_key(locked) < _version_key(v) <= _version_key(latest)
    ]
    # Ordered by publish time, because that is the comparison the report asks the
    # reader to make. _version_key breaks ties and covers an absent timestamp.
    gap.sort(key=lambda v: (published(v) or "", _version_key(v)))

    constrained = bool(entry.get("resolution-markers"))
    return {
        "name": name,
        "locked": locked,
        "latest": latest,
        "current": _version_key(locked) == _version_key(latest),
        "constrained": constrained,
        "pins": len(siblings),
        # The sibling versions themselves, not just how many. Phase 5 installs
        # one fork and Phase 1 verified all of them; a report that cannot name
        # the others cannot state the difference.
        "pinned": siblings,
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
                "yanked": all_yanked(releases.get(v, [])),
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
        # How old the pin is, alongside how old the gap is. Phase 2 compares
        # release times against the PR's `$CREATED_AT`, and a report that names
        # only when the *newer* release landed gives the reader one end of that.
        since = f" (published {cur['locked_published']})" if cur["locked_published"] else ""
        if cur["current"]:
            print(f"=== {cur['name']}: locked {cur['locked']}{since} IS the latest\n")
            continue
        if cur["held_back"]:
            print(
                f"=== {cur['name']}: locked {cur['locked']}{since}, registry latest "
                f"{cur['latest']} — HELD BACK by resolution-markers"
            )
            print(
                f"      behind a higher pin of the same package ({cur['pins']} in "
                "the lock); expected to"
            )
            print("      trail the registry, so not a staleness finding\n")
            continue
        print(
            f"=== {cur['name']}: locked {cur['locked']}{since}, "
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
        print("      earliest first; compare these against $CREATED_AT, the PR's own\n")

    # A forked package is checked in full and installed in part, and nothing in
    # the output said so. Phase 1 verifies the artifacts of every fork it audits;
    # a frozen install materialises one resolution. A green Phase 5 then reads as
    # though it covered all of them, which is a claim the run never made.
    # Mechanised here rather than left to the prose, because a disclosure the
    # report is merely asked to remember is one it can omit.
    #
    # Printed in two groups rather than one annotated list. The failure this
    # replaces was a report calling an unaudited fork "verified by Phase 1", and
    # a heading that says which check ran is harder to misread past than a
    # column the eye can skip.
    forks = report.get("forks", [])
    if forks:
        print("=== forked packages: uv pins these at more than one version")
        verified = [f for f in forks if f["audited"]]
        unverified = [f for f in forks if not f["audited"]]
        if verified:
            print("      artifacts verified against the registry above, one pin installed:")
            for f in verified:
                print(f"        {f['name']:24s} {f['pins']} pins: {', '.join(f['pinned'])}")
        if unverified:
            print("      NOT audited by this run — lockfile structure only, no artifact")
            print("      of these was checked, because they are outside the changed set:")
            for f in unverified:
                print(f"        {f['name']:24s} {f['pins']} pins: {', '.join(f['pinned'])}")
        print("      uv splits a package across blocks under different resolution-markers.")
        print("      A frozen install materialises only the resolution matching the")
        print("      interpreter and platform present — which need not be the highest")
        print("      pin. Name the one Phase 5 exercised (`uv run python -V` inside the")
        print("      synced environment) rather than reporting the install as though it")
        print("      covered every fork, and do not report an unaudited pin as verified.\n")

    for att in report.get("attestations", []):
        if not att["artifacts"]:
            continue
        head = f"=== {att['name']} {att['version']}: build provenance"
        if att["changed"]:
            before = format_publisher(att["previous"]["publisher"])
            now = format_publisher(next(a["publisher"] for a in att["artifacts"] if a["publisher"]))
            print(f"{head}  <-- PUBLISHER CHANGED")
            print(f"      {att['previous']['version']} was built by {before}")
            print(f"      {att['version']} is built by  {now}")
            print("      A release built somewhere new is worth explaining before merging.\n")
            continue
        if att["attested"]:
            for art in att["artifacts"]:
                if art["publisher"]:
                    print(f"{head}\n      {format_publisher(art['publisher'])}")
                    break
            if att["previous"]:
                print(f"      same publisher as {att['previous']['version']}")
            if att["unattested"]:
                print(f"      {att['unattested']} of {len(att['artifacts'])} artifacts unattested")
            print("      PyPI's summary of a PEP 740 attestation, not an independent")
            print("      signature check — stronger than a hash echo, not proof.\n")
        else:
            print(f"{head}\n      none — normal for a release predating Trusted Publishing\n")

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
    except UnsupportedLockfile as exc:
        # Already names the file and what it is. Prefixing "cannot read" here
        # would say the file is unreadable when it reads fine and is simply not
        # this plugin's to audit.
        fail(str(exc))
    except (OSError, tomllib.TOMLDecodeError, ValueError) as exc:
        fail(f"cannot read {args.lock}: {exc}")

    packages = pypi_sourced(locked)
    skipped = non_pypi(locked)

    selection: list[dict[str, str]] = []
    if args.changed_vs:
        try:
            selection = derive_changed(packages, args.changed_vs)
        except UnsupportedLockfile as exc:
            fail(str(exc))
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
        "attestations": [],
        "skipped": skipped,
    }
    # Fork structure comes from the whole lockfile, not just the selected set: a
    # bump can move one fork of a package while its sibling stays where it is.
    pins: dict[str, list[str]] = {}
    spelling: dict[str, str] = {}
    for pkg in packages:
        key = _normalize(pkg["name"])
        pins.setdefault(key, []).append(pkg["version"])
        spelling.setdefault(key, pkg["name"])

    # Every fork in the lockfile, not only the audited ones. Phase 5's disclosure
    # duty covers the whole install, so a fork outside the changed set is exactly
    # as capable of making a green install read as though it covered every pin —
    # and it used to be invisible here, which left the auditor deriving the list
    # by hand and reporting an unaudited package as "verified by Phase 1".
    #
    # `audited` carries the half that is easy to conflate and expensive to get
    # wrong: this run compared artifacts against the registry only for packages
    # in the changed set. For the rest this is lockfile structure and nothing
    # more, and the two must not be printed as one list.
    audited = {_normalize(p["name"]) for p in targets}
    report["forks"] = [
        {
            "name": spelling[key],
            "pinned": _ordered_pins(versions),
            "pins": len(versions),
            "audited": key in audited,
        }
        for key, versions in pins.items()
        if len(versions) > 1
    ]

    # What each selected package held in the base lockfile, so an attestation can
    # be compared against the release it replaces.
    was = {_normalize(c["name"]): c["was"] for c in selection if c["kind"] == "version"}

    project_cache: dict[str, Any] = {}
    publisher_cache: dict[str, Any] = {}
    for entry in targets:
        key = _normalize(entry["name"])
        if key not in project_cache:
            try:
                project_cache[key] = fetch_project(entry["name"])
            except (OSError, json.JSONDecodeError) as exc:
                fail(f"PyPI lookup failed for {entry['name']}: {exc}")
        project = project_cache[key]
        try:
            pinned, newest = fork_context(entry, pins)
            report["provenance"].append(check_provenance(entry, project))
            report["currency"].append(check_currency(entry, project, pinned=pinned, newest=newest))
            report["attestations"].append(
                check_attestations(
                    entry,
                    project,
                    # A forked package's `was` names every base version at once;
                    # only compare when it is unambiguous.
                    previous=was.get(key, "") if "," not in was.get(key, "") else "",
                    cache=publisher_cache,
                )
            )
        except ValueError as exc:
            # A version this script cannot order is one whose currency it cannot
            # judge. Refusing is the contract; sorting it to the bottom quietly is
            # how an epoch release fell out of the gap in the first place.
            fail(f"cannot audit {entry['name']}: {exc}")

    try:
        report["vulns"] = check_vulns(packages)
    except (OSError, json.JSONDecodeError) as exc:
        # Left unhandled this exits 1, which the contract reserves for findings —
        # an outage would read as "OSV reported a vulnerability".
        fail(f"OSV query failed: {exc}")
    report["clean"] = (
        all(p["ok"] for p in report["provenance"])
        and all(c["current"] or c["held_back"] for c in report["currency"])
        # A publisher that moved between two attested releases is a finding. A
        # *missing* attestation is not — it is normal for anything predating
        # Trusted Publishing, and flagging it would make the row noise.
        and not any(a["changed"] for a in report["attestations"])
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
