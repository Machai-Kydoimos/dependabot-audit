#!/usr/bin/env python3
"""Phase 2's currency: each release above the proposal, dated the way the bot dates it.

Phase 7's table has two rows for a plain gap: *outside the cooldown, merge and
follow up*, and *inside the window, merge and offer no follow-up*. Until 0.59.0 a run
chose between them by hand, from a one-line boundary and `audit.py`'s release
dates, and two replays of `fpga-board-sim` #436 (setup-uv v10.0.1 -> v10.1.0) took
different rows on the same evidence (#187).

**The dates were not the bot's either.** `audit.py` dates a PyPI release by its
first file. Dependabot reads pypi.org's JSON API and keeps the *last* file it lists
for each version (`format_version_releases` in
`python/lib/dependabot/python/package/package_details_fetcher.rb`, dependabot-core
`d4120ca`, 2026-09-30). The list is in filename order, so that is usually the
sdist. rumdl 0.2.77's wheels landed 2026-09-23T13:25Z and its sdist
2026-09-26T14:54Z, and `fpga-board-sim` #443 opened 2026-09-28T13:10Z:
- by the first file, 0.2.77 was outside the window, so the bot was behind;
- by the bot's date it was inside, and the bot proposed 0.2.76.

Checked against all 19 of that repository's uv PRs: the last-listed rule explains
every version the bot chose, and the first-file rule is wrong once, on #443.

A GitHub release is dated by `published_at`, then by the tag, then by the commit
(`common/lib/dependabot/git_cooldown_date_resolver.rb`). This reads the release.
The window is 3 days unless the config sets one (`DEFAULT_COOLDOWN_DAYS` in
`updater/lib/dependabot/job.rb`), and it is measured when the bot runs, which is
when it opens the PR. So every release above the proposal gets a label:

  after the PR opened     the bot never weighed it
  inside the window       the bot was waiting: "merge as-is", no follow-up for it
  outside the window      the bot passed it over: "merge as-is, then follow up"

**The default is never assumed where it may not hold.** A `cooldown:` block of
`default-days` alone sets the window. A richer one, a Renovate PR, or a config
with no entry for the ecosystem makes the labels `underivable`, and the output
says which. A `Security` entry or a fix-mode fix
in the gap takes its own row, cooldown notwithstanding. `changelog.py` and
`vendored.py` report those.

It also prints what the default branch pins now, which says whether a merged PR's
follow-up has landed (#179), and the `changelog.py` ranges the loop reads.

    currency.py --owner O --name N --number PR --ref pr-<N> --base <merge base> \\
                --created-at <PR's createdAt> [--default origin/<branch>]

Exit status: 0 = everything the PR moves is the registry's latest. 1 = a gap, each
release labelled and the row it selects named, or a label that is underivable and
why. 2 = could not run.
Requires Python 3.11+ (tomllib), `git`, `gh`, and the network: PyPI and GitHub.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import tomllib
import urllib.error
import urllib.request
from typing import Any, NamedTuple, NoReturn

import audit
import runners
import vendored

TIMEOUT = 60
PYPI = "https://pypi.org/pypi"
PYPI_INDEX = "https://pypi.org/simple"
DEFAULT_DAYS = 3
# A release this close to the boundary was decided by the bot's clock, which ran a
# few seconds before the PR's `created_at`. Said, not guessed.
BORDERLINE = datetime.timedelta(hours=1)
# A release's files land within minutes of each other from one CI run. Wider than
# this, they arrived on different days, as rumdl 0.2.77's did, and the first file's
# date is not the one the bot reads, so the output says both.
SPREAD = datetime.timedelta(minutes=10)

# The `package-ecosystem` values that govern each ecosystem this plugin covers.
CONFIG_NAMES = {
    "uv.lock": ("uv", "pip"),
    "github-actions": ("github-actions",),
    "pre-commit": ("pre-commit",),
}

# `uses: owner/repo[/path]@ref  # comment`. The comment carries the version when the
# pin is a SHA, which is how Dependabot writes one.
USES = re.compile(
    r"""^\s*-?\s*uses:\s*["']?(?P<slug>[\w.-]+/[\w.-]+)(?:/[^@\s"']*)?@(?P<ref>[^\s"'#]+)"""
    r"""["']?\s*(?:#\s*(?P<comment>\S+))?"""
)
SHA = re.compile(r"^[0-9a-f]{40}$")
# A tag that names a version: `v10.2.0`, `10.2.0`, or a moving `v1`. A suffix makes
# it a pre-release, and the bot proposes none.
TAG_VERSION = re.compile(r"^[vV]?(\d+(?:\.\d+){0,3})$")

# The row is the plain gap's. A Security entry or a fix-mode fix outranks it, and
# changelog.py names those, so the condition is printed with the row, not after it.
UNLESS = "  row, unless the gap carries a Security entry or a fix-mode fix: "

AFTER, INSIDE, OUTSIDE, UNDATED = "after", "inside", "outside", "undated"
LABELS = {
    AFTER: "after the PR opened: the bot never weighed it",
    INSIDE: "inside the window when the PR opened: the bot was waiting",
    OUTSIDE: "outside the window when the PR opened: the bot passed it over",
    UNDATED: "undated: the registry gives no time for it",
}


class Unreadable(Exception):
    """A registry read failed. The package's row is underivable, not current."""


class Release(NamedTuple):
    version: str
    dated: datetime.datetime | None  # the time the bot reads
    first: datetime.datetime | None  # the first file, where that differs
    yanked: bool


def fail(what: str) -> NoReturn:
    print(f"error: {what}", file=sys.stderr)
    sys.exit(2)


def _git(args: list[str]) -> tuple[int, str]:
    try:
        done = subprocess.run(  # noqa: S603
            ["git", *args],  # noqa: S607
            capture_output=True, text=True, check=False, timeout=TIMEOUT,
        )  # fmt: skip
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        fail(f"cannot run git: {exc}")
    return done.returncode, done.stdout


def _gh(args: list[str]) -> str | None:
    """`gh` output, or None when it failed. Never "" for a failure."""
    try:
        done = subprocess.run(  # noqa: S603
            ["gh", *args],  # noqa: S607
            capture_output=True, text=True, check=False, timeout=TIMEOUT,
        )  # fmt: skip
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        fail(f"cannot run gh: {exc}")
    return done.stdout if done.returncode == 0 else None


def _json_url(url: str) -> Any:
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT) as response:  # noqa: S310
            return json.load(response)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise Unreadable(f"{url}: {exc}") from exc


def show(ref: str, path: str) -> str | None:
    code, out = _git(["show", f"{ref}:{path}"])
    return out if code == 0 else None


def when(stamp: object) -> datetime.datetime | None:
    if not isinstance(stamp, str) or not stamp:
        return None
    try:
        return datetime.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None


def at(moment: datetime.datetime | None) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ") if moment else "(no time)"


# --- the bot's window ---------------------------------------------------------------


def window(base: str, ecosystem: str, author: str) -> tuple[datetime.timedelta | None, str]:
    """The window the bot applied to this ecosystem, and where it came from.

    None means underivable. The config is read at the merge base, which is the one
    the bot had when it opened the PR, and which the PR cannot choose.
    """
    if author == "renovate[bot]":
        return None, "a Renovate PR, and its `minimumReleaseAge` is not computed here"
    if author != "dependabot[bot]":
        return datetime.timedelta(0), f"proposed by {author}, not a bot, so no cooldown applies"
    text = show(base, ".github/dependabot.yml") or show(base, ".github/dependabot.yaml")
    if text is None:
        return None, "no .github/dependabot.yml at the merge base"
    try:
        config = runners.load(text)
    except (runners.Underivable, runners.Unreadable, ValueError, IndexError) as exc:
        return None, f".github/dependabot.yml could not be read: {exc}"
    names = CONFIG_NAMES[ecosystem]
    updates = config.get("updates") if isinstance(config, dict) else None
    entries = [
        u for u in updates or [] if isinstance(u, dict) and u.get("package-ecosystem") in names
    ]
    if not entries:
        return None, (
            f"no `updates` entry for {' or '.join(names)}: a security update, which no "
            "cooldown holds, or a config this script cannot match"
        )
    blocks = [entry["cooldown"] for entry in entries if "cooldown" in entry]
    if blocks:
        # `default-days` alone applies to every dependency; semver-specific days and
        # `include`/`exclude` need the update type and the name, which are not read here.
        # cli/cli sets `cooldown: default-days: 3` alone (measured on #13996).
        days = [b.get("default-days") if isinstance(b, dict) else None for b in blocks]
        plain = all(isinstance(b, dict) and set(b) == {"default-days"} for b in blocks)
        if plain and len(set(days)) == 1 and isinstance(days[0], str) and days[0].isdigit():
            return datetime.timedelta(days=int(str(days[0]))), (
                f"`cooldown: default-days: {days[0]}` in .github/dependabot.yml"
            )
        return None, (
            "its `cooldown:` block sets more than `default-days`, and semver-specific "
            "days and include/exclude lists are not computed here"
        )
    return datetime.timedelta(days=DEFAULT_DAYS), (
        f"Dependabot's default: the `{entries[0]['package-ecosystem']}` entry in "
        ".github/dependabot.yml sets no `cooldown:`"
    )


def label(
    dated: datetime.datetime | None,
    opened: datetime.datetime,
    span: datetime.timedelta | None,
) -> str | None:
    """Where one release stood when the bot decided. None: the window is underivable."""
    if dated is None:
        return UNDATED
    if dated > opened:
        return AFTER
    if span is None:
        return None
    return INSIDE if dated > opened - span else OUTSIDE


def row(labelled: list[tuple[Release, str | None]]) -> str:
    """The plain-gap row of Phase 7's table these labels select."""
    passed = [r for r, lab in labelled if lab == OUTSIDE]
    if passed:
        return (
            '"A gap exists, outside the cooldown" -> merge as-is, then follow up, '
            f"to {passed[-1].version}"
        )
    if any(lab is None or lab == UNDATED for _, lab in labelled):
        return "underivable: a release above could not be placed, so neither gap row applies"
    return '"A gap exists inside the cooldown window" -> merge as-is, and no follow-up for it'


# --- what moved, and what the registries say ---------------------------------------


def lock(ref: str) -> list[dict[str, Any]] | None:
    text = show(ref, "uv.lock")
    if text is None:
        return None
    try:
        packages = tomllib.loads(text).get("package")
    except tomllib.TOMLDecodeError as exc:
        fail(f"uv.lock at {ref} is not TOML: {exc}")
    return [p for p in packages or [] if isinstance(p, dict) and p.get("name")]


def pinned(packages: list[dict[str, Any]], name: str) -> list[str]:
    wanted = vendored._norm(name)
    return sorted(
        (str(p.get("version")) for p in packages if vendored._norm(p["name"]) == wanted),
        key=audit._version_key,
    )


def pypi_releases(name: str) -> list[Release]:
    """Every final release, dated by the file the bot reads: the last one listed."""
    data = _json_url(f"{PYPI}/{name}/json")
    found = []
    for version, files in (data.get("releases") or {}).items():
        if not files:
            continue
        try:
            if audit._is_prerelease(version):
                continue
        except ValueError:
            continue
        stamps = [when(f.get("upload_time_iso_8601")) for f in files]
        first = min((s for s in stamps if s), default=None)
        found.append(Release(version, stamps[-1], first, bool(files[-1].get("yanked"))))
    return sorted(found, key=lambda r: audit._version_key(r.version))


def github_releases(slug: str) -> list[tuple[Release, tuple[int, ...]]]:
    """Every published, final release, dated by `published_at`, and its numbers."""
    out = _gh(
        [
            "api", "--paginate", f"repos/{slug}/releases?per_page=100", "--jq",
            ".[] | select((.draft or .prerelease) | not) | [.tag_name, .published_at] | @tsv",
        ]
    )  # fmt: skip
    if out is None:
        raise Unreadable(f"gh api repos/{slug}/releases failed")
    found = []
    for line in out.splitlines():
        tag, _, stamp = line.partition("\t")
        numbers = TAG_VERSION.match(tag.strip())
        if numbers:
            parts = tuple(int(n) for n in numbers.group(1).split("."))
            found.append((Release(tag.strip(), when(stamp.strip()), None, False), parts))
    return sorted(found, key=lambda pair: pair[1])


def workflow_pins(ref: str) -> dict[str, set[tuple[str, str]]]:
    """{owner/repo: {(ref, comment)}} across every workflow at `ref`."""
    code, listing = _git(["ls-tree", "--name-only", f"{ref}:.github/workflows"])
    pins: dict[str, set[tuple[str, str]]] = {}
    if code != 0:
        return pins
    for name in listing.splitlines():
        if not re.search(r"\.ya?ml$", name):
            continue
        for line in (show(ref, f".github/workflows/{name}") or "").splitlines():
            found = USES.match(line)
            if found:
                pin = (found.group("ref"), found.group("comment") or "")
                pins.setdefault(found.group("slug"), set()).add(pin)
    return pins


def version_of(pin: tuple[str, str]) -> str:
    """The version a pin claims: the comment beside a SHA, else the ref itself."""
    ref, comment = pin
    return comment if SHA.match(ref) and comment else ref


# --- the report -----------------------------------------------------------------------


def print_releases(
    gap: list[Release], opened: datetime.datetime, span: datetime.timedelta | None
) -> list[tuple[Release, str | None]]:
    labelled = []
    for release in gap:
        where = label(release.dated, opened, span)
        labelled.append((release, where))
        text = LABELS[where] if where else "window underivable, so not placed"
        print(f"  {release.version:<10} {at(release.dated)}  {text}")
        if release.first and release.dated and abs(release.first - release.dated) > SPREAD:
            print(
                f"  {'':<10} its first file landed {at(release.first)}; "
                "the bot dates it by the last file listed"
            )
        if span is not None and where in (AFTER, INSIDE) and release.dated:
            print(f"  {'':<10} the bot can propose it from {at(release.dated + span)}")
        if span and release.dated and abs(release.dated - (opened - span)) < BORDERLINE:
            print(f"  {'':<10} within an hour of the boundary: the bot's own clock decided")
    return labelled


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 2's currency, dated as the bot dates it")
    parser.add_argument("--owner", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--number", required=True, type=int)
    parser.add_argument("--ref", required=True, help="pr-<N>, the PR's head")
    parser.add_argument("--base", required=True, help="$BASE_SHA, the merge base")
    parser.add_argument("--created-at", required=True, help="$CREATED_AT, checked against the PR")
    parser.add_argument("--default", help="origin/$DEFAULT: what the default branch pins now")
    args = parser.parse_args()

    facts = _gh(
        [
            "api", f"repos/{args.owner}/{args.name}/pulls/{args.number}",
            "--jq", "[.created_at, .user.login] | @tsv",
        ]
    )  # fmt: skip
    if facts is None or "\t" not in facts:
        fail(f"cannot read PR #{args.number} of {args.owner}/{args.name}")
    stamp, _, author = facts.strip().partition("\t")
    opened = when(stamp)
    if opened is None:
        fail(f"PR #{args.number} has no readable created_at: {stamp!r}")
    # The handoff's value, checked the way exercised.py checks --head-sha: a stale or
    # another PR's phase0.env labels every release against the wrong moment.
    if when(args.created_at) != opened:
        fail(
            f"--created-at {args.created_at} is not when PR #{args.number} opened "
            f"({at(opened)}) -- re-run Phase 0"
        )
    print(f"PR #{args.number} opened {at(opened)} by {author}")

    trailing = 0
    moved = 0
    ranges: list[str] = []

    before, after = lock(args.base), lock(args.ref)
    if before is not None and after is not None and before != after:
        span, why = window(args.base, "uv.lock", author)
        print(f"\nuv.lock -- window: {why}")
        if span:
            print(f"  boundary {at(opened - span)}: a release dated after it was inside the window")
        print("  dates: a PyPI release by the last file its JSON API lists, as Dependabot reads it")
        now = lock(args.default) if args.default else None
        names = sorted({vendored._norm(t["name"]) for t in vendored.moved(after, before)})
        for name in names:
            moved += 1
            proposed = pinned(after, name)[-1]
            current = (pinned(before, name) or ["(new)"])[-1]
            source = next(
                (p.get("source") or {} for p in after if vendored._norm(p["name"]) == name),
                {},
            )
            registry = str(source.get("registry", "")).rstrip("/")
            try:
                if registry != PYPI_INDEX:
                    raise Unreadable(
                        f"its registry is {registry or 'not a registry'}, and this reads "
                        "pypi.org's JSON API only"
                    )
                everything = pypi_releases(name)
            except Unreadable as exc:
                trailing += 1
                print(f"\n{name} {current} -> {proposed}: UNDERIVABLE -- {exc}")
                continue
            key = audit._version_key(proposed)
            gap = [r for r in everything if audit._version_key(r.version) > key and not r.yanked]
            yanked = [
                r.version for r in everything if audit._version_key(r.version) > key and r.yanked
            ]
            if current != "(new)":
                ranges.append(f'"{name} {current} {proposed}"')
            if not gap:
                print(f"\n{name} {current} -> {proposed}: the latest")
            else:
                trailing += 1
                ranges.append(f'"{name} {proposed} {gap[-1].version} --gap"')
                print(f"\n{name} {current} -> {proposed}: {len(gap)} newer release(s)")
                labelled = print_releases(gap, opened, span)
                print(f"{UNLESS}{row(labelled)}")
            if yanked:
                print(f"  yanked, so not a candidate: {', '.join(yanked)}")
            if now is not None:
                there = pinned(now, name)
                print(f"  {args.default} pins {', '.join(there) or 'nothing of it'} now")

    old, new = workflow_pins(args.base), workflow_pins(args.ref)
    actions = sorted(slug for slug in new if new[slug] != old.get(slug, set()))
    if actions:
        span, why = window(args.base, "github-actions", author)
        print(f"\ngithub-actions -- window: {why}")
        if span:
            print(f"  boundary {at(opened - span)}: a release dated after it was inside the window")
        print("  dates: a GitHub release by its published_at, as Dependabot reads it")
        now_pins = workflow_pins(args.default) if args.default else None
        for slug in actions:
            moved += 1
            added = new[slug] - old.get(slug, set())
            claims = sorted({version_of(p) for p in added})
            current = ", ".join(sorted({version_of(p) for p in old.get(slug, set())})) or "(new)"
            proposed = claims[-1]
            numbers = TAG_VERSION.match(proposed)
            try:
                if not numbers:
                    raise Unreadable(f"the pin claims {proposed!r}, which names no version")
                published = github_releases(slug)
                if not published:
                    raise Unreadable(
                        "the action publishes no releases, and its tags are not dated here"
                    )
            except Unreadable as exc:
                trailing += 1
                print(f"\n{slug} {current} -> {proposed}: UNDERIVABLE -- {exc}")
                continue
            # A pin to a moving `v1` picks up v1.x by itself: only a higher major is a gap.
            mine = tuple(int(n) for n in numbers.group(1).split("."))
            gap = [r for r, parts in published if parts[: len(mine)] > mine]
            if len(claims) > 1:
                print(f"\n{slug}: the PR pins {', '.join(claims)}; read above the highest")
            if not gap:
                print(f"\n{slug} {current} -> {proposed}: the latest")
            else:
                trailing += 1
                print(f"\n{slug} {current} -> {proposed}: {len(gap)} newer release(s)")
                labelled = print_releases(gap, opened, span)
                print(f"{UNLESS}{row(labelled)}")
            if now_pins is not None:
                there = sorted({version_of(p) for p in now_pins.get(slug, set())})
                print(f"  {args.default} pins {', '.join(there) or 'nothing of it'} now")

    code, _ = _git(["diff", "--quiet", args.base, args.ref, "--", ".pre-commit-config.yaml"])
    if code == 1:
        span, why = window(args.base, "pre-commit", author)
        print(f"\npre-commit -- window: {why}")
        if span:
            print(f"  boundary {at(opened - span)}: a release dated after it was inside the window")
        print(
            "  its hooks are not dated here: compare each hook repository's releases against\n"
            "  the boundary by hand (references/pre-commit.md § Phase 2)"
        )

    if ranges:
        # A gap range carries `--gap`, which the loop passes on: changelog.py then names
        # the follow-up's target where the gap has a Security entry or a fix-mode fix.
        print(f"\nchangelog.py ranges, adopted and gap: {' '.join(ranges)}")
    noun = "dependency" if moved == 1 else "dependencies"
    if trailing:
        print(
            "\nA `Security` entry or a fix-mode fix in a gap takes its own row, cooldown "
            "notwithstanding:\nchangelog.py and vendored.py report those, and Phase 7's "
            "table ranks them above these rows."
        )
        verb = "trails" if trailing == 1 else "trail"
        print(f"\nRESULT: GAP -- {trailing} of {moved} moved {noun} {verb} the latest or could")
        print("not be placed; each names the row it selects, or why not.")
        return 1
    if not moved:
        print("\nRESULT: NOTHING MOVED -- no uv.lock package and no workflow pin differs.")
        return 0
    print(
        f"\nRESULT: CURRENT -- the {moved} moved {noun} {'is' if moved == 1 else 'are'} the latest."
    )
    return 0


def cli() -> NoReturn:
    """Entry point. Anything unforeseen becomes exit 2, never exit 1."""
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as exc:
        if os.environ.get("DEPENDABOT_AUDIT_DEBUG"):
            raise
        fail(f"unexpected {type(exc).__name__}: {exc} -- a bug, not a finding")


if __name__ == "__main__":
    cli()
