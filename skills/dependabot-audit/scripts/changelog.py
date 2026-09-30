#!/usr/bin/env python3
"""Phase 2's changelog ladder, plus the reconciliation the ladder cannot do.

Until 0.36.0 the ladder was three rungs of prose in `references/uv-lock.md`, tried
in order and stopped at the first that answered:

    1. release notes      -- runs out when the project publishes none
    2. the repo changelog -- runs out when a patch has no section
    3. the commit range   -- runs out when the tags are missing

**Every one of those exit conditions is an absence**, so a rung that returns real,
well-formed content ends the ladder. There was no rung whose exit condition was
*"this rung answered, and its answer is incomplete"* -- and that is the case the
ladder was built out of, not an edge of it.

Measured on `rumdl` v0.2.60...v0.2.62 (2026-08-26/27), the bump that produced #94:
rung 1 answers for both versions, rung 2 answers for both, and each says exactly
one `### Added` bullet. The range holds **18 commits, five of them `fix(...)`**,
two in the shape Phase 2 exists to find -- `stop rewriting Rust source when
formatting doc comments`, which wrote `# [derive(Debug)]` to disk, and `stop
reading a lazy continuation as a setext underline`. A run that honored the ladder
as written reported "two additive releases" and was wrong about the only
interesting thing in the bump.

That example moved on 2026-09-05. The project generates `CHANGELOG.md` from
conventional commits and the generator was dropping `fix` types; when it was
fixed, the release bodies for v0.2.61/64/65 were rewritten in place at 07:05 and
the v0.2.66 release regenerated the whole changelog at 14:09, retroactively
filling in every past version. So the `0.2.61` entry reads one `### Added` bullet
at refs v0.2.62..v0.2.65 and `### Added` + five `### Fixed` at v0.2.66 and later.

That is `rumdl`'s tooling, not a general fact about releases, and the two checks
below are written as measurements rather than as assumptions about any project.

**A release body can be rewritten in place.** True of the GitHub API everywhere,
not of any one project: `--jq .body` cannot see it, `updated_at` can, and
`edited_since_published` reads it.

**A changelog's entry for a version may depend on the ref you read it at** --
when the file is generated it is rebuilt each release, when it is hand-maintained
it is appended to and old sections are stable. Rather than deciding which kind a
project is, `main()` reads the changelog at the proposed tag *and* at the default
branch and reports a difference only where one exists. On a hand-maintained
changelog that is silent.

One caution the ladder cannot check for itself: on `rumdl`, rung 1 is *produced
from* rung 2 (`scripts/extract-changelog.sh`, an awk slice), so the two agreeing
is the same text twice. Elsewhere they may be genuinely independent. What holds
regardless is only that rung 3 cannot be rewritten without rewriting history.
Live, this range now reconciles at exit `0`.

**The obvious heuristic does not save it.** "Does this project document its fixes
at all?" returns a confident yes: 0.2.56, 0.2.57, 0.2.59 and 0.2.60 all carry a
`### Fixed` section. Only the versions under audit had none, because the project's
release automation lists `feat` and drops `fix`. Nothing in rung 1's output says so.

So the rungs are not a fallback chain. They are two different kinds of source:
**prose is what the project chose to say, and the range is what actually landed.**
This script reads all three, always, and reports the difference between them.

## Why the range is fetched unconditionally

#94 proposed making the range mandatory *where the bumped package is a gate the
repo runs in write mode*. This script goes one step further and always fetches it,
because gating the **call** on that judgement asks the auditor to be right about
write mode before it has the evidence, and a wrong guess restores exactly the
silence the script exists to remove. The judgement still matters -- it is what
`--write-mode` sets -- but it changes how a finding is *reported*, never whether
it is *looked for*. One `compare` call is what rung 3 already cost when it ran.

## What "unreconciled" means, and which way it errs

A commit counts as reconciled when its description turns up in the prose the
other two rungs produced -- as a substring after normalisation, or close enough
by `difflib` that a reader would call it the same entry.

**Which commits are reconciled depends on who did the labelling, and the output
says which.** Where the project writes conventional commits, fix types are read,
and so are dependency bumps of any type: a `docs:` commit absent from a changelog
is correct behaviour, and rows nobody acts on are how a report stops being read,
but `chore(deps): refresh Rust dependencies` is how rumdl v0.2.76 shipped the fix
for RUSTSEC-2026-0285 in rustls, and its notes never named it (#169). Where it does not, nothing is
filtered -- because a filter that keys on `fix(` reports **zero fixes** for a
range full of them, which is this plugin's own failure class rebuilt inside the
tool written to remove it. `python/mypy` v2.3.0...v2.3.1 is that case, and the
first version of this file called it clean.

**Both halves err toward reporting.** A changelog that rewords an entry past the
threshold produces a row the auditor reads and dismisses in a second. The other
error is the one that shipped: a fix silently counted as covered. Those costs are
not comparable, so the threshold sits where the cheap mistake happens.

The unlabelled mode is noisy at scale -- ruff 0.16.2...0.16.5 leaves 266 of 307
unnamed, most of them `ty`, a second product under the same tags. That is
answered by **ranking and a cap, never by filtering**: destructive shapes first,
fix-worded next, dependency bumps third, the top `SHOWN` on the terminal and every
one of them in the evidence file -- and the cut line counts what it cut, by tier.

Usage:
    changelog.py --scratch DIR --from VERSION --to VERSION \\
                 (--package NAME | --repo-slug OWNER/REPO) [--write-mode] [--gap]

Exit status: 0 = the prose names every fix in the range, and every dependency bump.
1 = it does not, and the unreconciled commits are listed (a Phase 2 finding, not a
script failure).
2 = could not run.
Requires Python 3.11+. Network: `gh api`, and PyPI for `--package`.
"""

from __future__ import annotations

import argparse
import datetime
import difflib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, NamedTuple, NoReturn

TIMEOUT = 60

# Conventional-commit types whose absence from a changelog is worth a row.
# `docs`, `chore`, `ci`, `test`, `refactor` and `style` are left out on purpose:
# a changelog that omits them is behaving correctly, and rows nobody acts on are
# how a report stops being read.
FIX_TYPES = ("fix", "perf", "revert", "security", "sec")

# Types that say, in the project's own labelling, that the commit ships nothing:
# a `ci(deps)` bump moves a workflow's tool, not the wheel. A dependency bump is
# a candidate under any *other* type, because `chore` and `build` are exactly the
# labels a runtime crate bump arrives under (#169). Measured on rumdl
# v0.2.61...v0.2.62: `ci(deps): move upd to v0.8.2 ...` is the case.
UNSHIPPED_TYPES = ("ci", "docs", "test", "style")

# SKILL.md Phase 2: *entries like "stop deleting..." or "no longer removes..."*.
# Exactly those two shapes, and not the wider family of English negations.
#
# The shape is the **negation**, not the verb after it -- rumdl's `stop reading a
# lazy continuation as a setext underline` names no destructive verb and still
# splits one blockquote into three blocks under MD022. But a first version that
# also matched `avoid`, `prevent` and `never` fired on `[ty] Avoid composite
# Salsa keys for unspecialized MROs` and `[ty] Avoid deadlock when scheduling
# watch checks` in ruff 0.16.2...0.16.5 -- ordinary English, no data loss. A
# marker that fires on a fifth of the rows marks nothing.
#
# Narrowing is cheap here because this **ranks, it does not filter**: every
# unreconciled commit is listed either way, and `FIX_WORDED` below carries the
# looser family into second place rather than out of the report.
DESTRUCTIVE = re.compile(r"\b(?:stops?|stopped|stopping|no longer)\b", re.IGNORECASE)

# A line of the *prose* that changes what the tool writes when it fixes (#185).
# `DESTRUCTIVE` reads the negation, in commits; rumdl writes the same bug the other
# way round, as what the fix now keeps -- `keep each line's ending when fixing a
# file with mixed line endings`, `withhold blank lines that would change how the
# lists parse` -- and on fpga-board-sim #443's gap, v0.2.76...v0.2.78, every one
# was worded so. That prose is 9 KB, so none of it was printed, and the output said
# `security-shaped lines: none` and `RECONCILED`. So this reads three shapes: the
# negation, the preservation (`keep`, `preserve`, `withhold`, `leave ... alone`),
# and a write named outright (`when fixing`, `autofix`, `unsafe fix`, `the fix`).
#
# Measured 2026-09-30 in write mode, one listed line per entry: 34 of 46 were fixes
# to what a fix or a format writes -- rumdl v0.2.76...v0.2.78 20 of 23, ruff
# 0.16.0...0.16.9 6 of 10, ruff 0.15.20...0.16.0 3 of 7 (one is `ISC003` autofix
# stripping `+` from comments), black 25.9.0...26.3.1 5 of 6. The rest are CLI
# help, `Stop recommending ...`, `Document fix safety`. Not listed: a rule's change
# worded as detection, `MD077: move a fenced block as a whole` -- the file has it.
#
# Read over the prose only. Over ruff's commit range it adds ~80 `[ty] Preserve ...`
# rows from ty, the second product under ruff's tags. And listed only in write mode:
# uv's `Keep uv workspace metadata read-only` and pytest's `no longer` entries are
# about behaviour, and a repo that does not write with the tool has no fix to lose.
FIX_MODE = re.compile(
    r"\b(?:stops?|stopped|stopping|no longer)\b"
    r"|\b(?:keep|keeps|kept|preserv\w*|retain\w*|withh(?:o|e)ld\w*)\b"
    r"|\bleav(?:e|es|ing)\b.{0,60}?\b(?:alone|as written|intact|unchanged|in place|where it is)\b"
    r"|\b(?:when|while|during)\s+(?:fix(?:ing)?|formatting|reflow(?:ing)?|rewriting|trimming"
    r"|wrapping|sorting)\b"
    r"|--fix\b|\bautofix\w*|\bunsafe\s+fix"
    r"|\bfix(?:es)?\s+(?:mode|replacements?|safety|it|them)\b"
    r"|\b(?:list|rule|document|warning|its|their|the|a|an|this)\s+fix(?:es)?\b",
    re.IGNORECASE,
)
# A rule the line names: rumdl's `MD032`, ruff's `UP040`, `SIM109`, `PLR6104`. The
# config is what says whether this repo runs it.
RULE_ID = re.compile(r"\b(?:MD\d{3}|[A-Z]{1,4}\d{3,4})\b")
SHOWN_FIX_MODE = 30

# Second tier of the ranking: subjects that read like a correction without
# carrying the destructive shape. Only ordering depends on this, so a miss costs
# a place in a list and never a row.
FIX_WORDED = re.compile(
    r"\b(?:fix(?:e[sd])?|bug|crash|regress(?:ion|es)?|revert(?:s|ed)?|correct(?:s|ed)?"
    r"|restore[sd]?|repair(?:s|ed)?|broken|incorrect|wrong|corrupt(?:s|ed|ion)?"
    r"|data.loss|panic|deadlock|leak|overwrit(?:e|es|ing|ten))\b",
    re.IGNORECASE,
)

# Third tier, and in conventional mode a candidate whatever its type: a commit
# that moves a dependency. A crate compiled into a wheel is Phase 2's first scope
# row, and a bump is how its fix arrives -- `rumdl` v0.2.76 shipped the rustls fix
# for RUSTSEC-2026-0285 as `chore(deps): refresh Rust dependencies`, named in no
# notes. Two halves: a conventional scope that names dependencies, and the subject
# shapes bots and maintainers write, whatever the label. Measured against ruff
# 0.16.7...0.16.8, whose `[ty] Resolve dependencies within ...` and `[ty] Share
# strings in dependency metadata` name dependencies and bump none: neither matches.
DEPENDENCY_SCOPE = re.compile(
    r"^(?:[a-z]+\((?:deps|deps-dev|dependencies|dependency)\)|deps(?:\([^)]*\))?)!?:",
    re.IGNORECASE,
)
DEPENDENCY_BUMP = re.compile(
    r"\b(?:update|bump|upgrade)\s+(?:rust\s+crates?|crates?|dependency|dependencies|deps)\b"
    r"|\bbump\s+\S+\s+from\s+\S+\s+to\b"
    r"|\bbump\s+the\s+[\w-]+\s+group\b"
    r"|\block\s*file\s+maintenance\b"
    r"|\b(?:refresh|update|upgrade|bump)\s+(?:[\w-]+\s+){0,3}(?:dependencies|deps|crates)\b"
    r"|\b(?:update|refresh|bump)\s+(?:the\s+)?(?:cargo\.lock|lockfile|lock\s+file)\b"
    r"|\bcargo\s+update\b",
    re.IGNORECASE,
)

# A bump's body is kept to what it says about the bump. Renovate's squash commit
# carries its whole PR description, and everything from its `### Configuration`
# heading on is schedule, rebase and automerge settings: on the #438 replay, two
# bodies grew ruff 0.16.7...0.16.8's evidence file by 23%, most of it that tail.
BOT_SETTINGS = re.compile(r"^#{2,3} Configuration\b", re.MULTILINE)

# What the evidence file is scanned for, so a `Security` heading or an advisory id
# is a line number in the output rather than something a reader has to find. The
# read stays required: a security fix can be a plain bullet with no heading and no
# id, as actions/checkout@v7's was. Measured over ten ranges (ruff, uv, rumdl,
# pytest, mypy, requests, urllib3, jinja2, cryptography): every advisory the prose
# names is found -- uv's GHSA-2cv4-cqwr-gwf7, requests' CVE-2024-47081 under
# `**Security**`, urllib3's two CVEs, jinja2's GHSA -- and the words alone add at
# most eleven lines to uv's 1,314, mostly commit subjects such as "Sync the Ruff
# security mirror", which the section label beside each line makes plain.
ADVISORY_ID = re.compile(
    r"\b(?:CVE-\d{4}-\d{4,}|GHSA(?:-[0-9a-z]{4}){3}|RUSTSEC-\d{4}-\d{4}|PYSEC-\d{4}-\d+)\b",
    re.IGNORECASE,
)
SECURITY_HEADING = re.compile(r"^\s*(?:#{1,6}\s|\*\*)[^\n]*\bsecurity\b", re.IGNORECASE)
SECURITY_WORD = re.compile(r"\b(?:security|vulnerab\w*|exploit\w*|CVSS)\b", re.IGNORECASE)

# The section headings `write_evidence` writes, and nothing a release body carries.
SECTION = re.compile(
    r"^## (?:rung [12]\b|unreconciled --|destructive-fix shape --|dependency bumps --)"
)

# Security-word lines printed before the rest are counted rather than shown.
SHOWN_WORDS = 10
BUMP_BODY_LINES = 40

# The ranking's tiers, named for the line that says what the cap cut.
TIERS = ("destructive-shaped", "fix-worded", "dependency bump(s)", "other")

# How many unreconciled rows reach the terminal. The rest go to the evidence
# file. A wall of rows is the same failure as silence -- the reader's eye slides
# off it -- and ruff 0.16.2...0.16.5 produces exactly that at 266.
#
# Ranked first, capped second, so the cap can only ever cut the tail. Measured on
# rumdl v0.2.60...v0.2.62: the two destructive-shaped fixes come back from the API
# at positions 4 and 5 of 5, and the ranking puts them at 1 and 2.
#
# An earlier version of this comment cited ruff's own marked rows "at positions 8
# and 13". Those were `Avoid composite Salsa keys` and `Avoid deadlock`, which the
# narrowing described above deliberately stopped marking -- so the comment was
# describing a superseded regex, and contradicting the one twenty lines up. That
# range now has **zero** marked rows, which is why its wall needs the
# multi-product note rather than a marker.
SHOWN = 40

# How much of the prose `main()` prints on the terminal, in bytes, rather than
# leaving it only in the evidence file (#178). A pointer to a file is a read a run
# can skip, and one did: the #438 replay under 0.57.0 read three of its four
# evidence files and never opened ruff 0.16.9's, whose notes were two kilobytes.
# Measured over sixteen ranges on 2026-09-29, every single-release one fits:
# ruff 0.16.8's changelog section is 2,536 bytes and babel 2.17.0's 2,876. The
# multi-release gaps do not, and are not meant to: rumdl's four releases are
# 9,674 and uv's nine 15,664. The cap exists because a run batches its calls,
# and #438's four came back as one 13,658-character tool result. Claude Code
# spills a result to a file somewhere between 29,000 characters, which came back
# whole, and 56,900, which did not.
INLINE_BYTES = 4000

# `fix(scope): description` / `fix!: description` / `fix: description`.
CONVENTIONAL = re.compile(
    r"^(?P<type>[a-z]+)(?:\((?P<scope>[^)]*)\))?(?P<bang>!)?:\s*(?P<rest>.+)$"
)

# A changelog by any of the names projects actually use. Matched against the
# repository's own root listing rather than constructed, for the same reason the
# release tag is: a guessed name returns 404, and a 404 reads exactly like "this
# project keeps no changelog".
CHANGELOG_NAME = re.compile(
    r"^(?:CHANGE(?:LOG|S)|HISTORY|NEWS|RELEASES?)(?:\.(?:md|rst|txt))?$", re.IGNORECASE
)

# Any ATX heading, split into its level and its text. Which heading belongs to
# which version is `section_for`'s problem, and it is harder than it looks: the
# link target carries the *previous* version.
HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
# A setext heading is a line of text with a rule of `=` (level 1) or `-` (level 2)
# under it. `pre-commit/pre-commit` heads every version this way in Markdown.
SETEXT_RULE = re.compile(r"^ {0,3}(=+|-+)[ \t]*$")
# Lines that cannot be the text of a setext heading: list items, blockquotes,
# tables, HTML. A `-` rule under a list item is a thematic break, not a heading.
NOT_PARAGRAPH = re.compile(r"^\s*(?:[-*+]\s|\d+[.)]\s|>|\||<)")
FENCE_OPEN = re.compile(r"^ {0,3}(`{3,}|~{3,})")
# A reStructuredText title's rule: any one punctuation character, repeated. docutils
# takes every non-alphanumeric printable ASCII character, and projects use more than
# `=` and `-`: `pyca/cryptography` and `pypa/packaging` head versions over `~`.
RST_RULE = re.compile(r"^([!-/:-@\[-`{-~])\1*[ \t]*$")
# `.. _v46-0-1:` names the title below it, so it belongs to the next section.
RST_TARGET = re.compile(r"^\.\. _[^:]*:\s*$")
VERSION_TOKEN = re.compile(r"\d+\.\d+")
# A repo-relative path to something named like a changelog, bare or inside a
# `github.com/<owner>/<repo>/blob/<ref>/` URL. Only ever followed from a file
# that carries no version headings of its own -- see `changelog_at`.
POINTER = re.compile(
    r"(?:github\.com/(?P<slug>[\w.-]+/[\w.-]+)/blob/[^/\s]+/)?"
    r"(?P<path>(?:[\w.-]+/)+(?:changelog|changes|history|news|release[-_]?notes)[\w.-]*"
    r"\.(?:md|rst|txt))",
    re.IGNORECASE,
)

# How alike two entries must read before one counts as the other. Deliberately
# forgiving of rewording and deliberately not of omission -- see the module
# docstring on which error this file prefers.
MATCH_RATIO = 0.82

# One path segment of a GitHub `owner/repo`. Rejects `.` and `..` outright,
# because the slug is interpolated into a `gh api repos/<slug>/...` path and
# `..` walks out of `repos/` into a different endpoint.
SEGMENT = re.compile(r"^(?!\.{1,2}$)[A-Za-z0-9._-]{1,100}$")

# GitHub routes that sit where an owner would. GitHub reserves them as account
# names, so none of them owns a repository (#183). Measured 2026-09-30 over 44
# packages' PyPI metadata: six resolved to `sponsors/<user>`, because their
# `Funding` link comes before the repository, and five of those six are in
# `fpga-board-sim`'s lockfile. The other routes are the same shape, one link
# type over: an organisation's discussions, a user's projects, an advisory.
NOT_AN_OWNER = frozenset({"sponsors", "orgs", "users", "apps", "marketplace", "advisories"})


def fail(what: str) -> NoReturn:
    """Exit 2 -- could not run. Never 1, which means the prose came up short."""
    print(f"error: {what}", file=sys.stderr)
    raise SystemExit(2)


def _gh(args: list[str]) -> str | None:
    """Run `gh`; stdout on success, `None` on any failure. Never exits.

    The single network seam for GitHub, so the offline suite replaces one
    function and drives every line of parsing and reconciliation below.

    `gh` writes an API error body to **stdout** and still exits non-zero, so the
    exit code is the signal and the body is the explanation. Reading the body
    without the exit code is how a 404 becomes a well-formed "no releases here".
    That is why the failure is `None` and not `""`: a caller cannot mistake "the
    call failed" for "the answer was empty", which is this phase's own failure
    mode in miniature.

    A missing `gh` is the one thing that ends the run here rather than at the
    call site -- no rung of this ladder can proceed without it, so a soft return
    would only defer the same exit by three calls.
    """
    try:
        proc = subprocess.run(  # noqa: S603
            ["gh", *args],  # noqa: S607
            capture_output=True,
            text=True,
            check=False,
            timeout=TIMEOUT,
        )
    except FileNotFoundError:
        fail("`gh` is not on PATH; every rung of this ladder is a GitHub API call")
    except subprocess.TimeoutExpired:
        return None
    return proc.stdout if proc.returncode == 0 else None


def _gh_hard(args: list[str]) -> str:
    """`_gh`, for the calls the phase cannot proceed without. Exit 2 on failure.

    The release list and the commit range are both of that kind: without either,
    the reconciliation this script exists for cannot be attempted, and reporting
    "no fixes found" from a call that never ran is the defect, not the report.
    """
    out = _gh(args)
    if out is None:
        fail(f"`gh {' '.join(args[:2])}` failed -- run it by hand to see why")
    return out


def valid_slug(slug: str) -> bool:
    """Exactly two well-formed path segments.

    `--repo-slug` is interpolated into `gh api repos/<slug>/...`, so `../..`
    would walk out of `repos/` and query a different endpoint. Checked even
    though the caller is the auditor rather than the package: the same string
    reaches the same place, and a typo that silently answers about something
    else is the failure mode this whole phase is about.
    """
    parts = slug.split("/")
    return len(parts) == 2 and all(SEGMENT.match(p) for p in parts)


def github_slug(url: str) -> str | None:
    """`owner/repo` if `url` is a GitHub repository URL, else None.

    **The host is compared, never searched for.** `project_urls` is written by
    the package author -- the party this whole plugin exists to not trust -- and
    a substring test on `"github.com/"` hands the audit whatever repository that
    author names. Measured, against the version that shipped in the prose from
    0.33.0 and in the first cut of this script:

        https://evil.example.invalid/github.com/attacker/lookalike -> attacker/lookalike
        https://example.invalid/?q=github.com/attacker/repo        -> attacker/repo
        https://github.com/../../users/octocat                     -> ../..

    The first two point Phase 2's entire changelog read at a repository the
    package author controls: tidy release notes, no unreconciled fixes, a clean
    currency row. The third walks out of `repos/` into a different API endpoint,
    because the slug is interpolated into a `gh api` path.

    That is worse now than it was before this file existed. The reconciliation
    is a verdict input Phase 7 reads, so a package that can choose which
    repository answers for it can choose its own Phase 2 finding.

    Segments are validated too, which is what rejects `..` -- a name may not be
    `.` or `..`, and may hold only the characters GitHub actually allows. And an
    owner that is a GitHub route, `sponsors` above all, is not a repository at
    all (#183): see `NOT_AN_OWNER`.
    """
    if not url:
        return None
    try:
        parsed = urllib.parse.urlsplit(url.strip())
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https"):
        return None
    # `.hostname` is already lowercased and strips any `user:pass@` and `:port`.
    if parsed.hostname not in ("github.com", "www.github.com"):
        return None
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) < 2:
        return None
    owner, repo = parts[0], parts[1].removesuffix(".git")
    if not (SEGMENT.match(owner) and SEGMENT.match(repo)) or owner.lower() in NOT_AN_OWNER:
        return None
    return f"{owner}/{repo}"


def resolve_repo(package: str) -> str:
    """`owner/repo` from PyPI's JSON metadata, which is where it actually lives.

    The Simple API the Phase 1 script uses carries artifacts and nothing else, so
    this is the one place the JSON API is the right call. The repo is *in* the
    metadata and is not guessable from the package name -- `mirrors-mypy` and
    `python/mypy`, `rumdl` and `rvben/rumdl`.
    """
    url = f"https://pypi.org/pypi/{package}/json"
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT) as response:
            data = json.load(response)
    except urllib.error.HTTPError as exc:
        fail(f"PyPI has no metadata for {package!r} (HTTP {exc.code})")
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        fail(f"could not read PyPI metadata for {package!r}: {exc}")

    urls = list((data.get("info", {}).get("project_urls") or {}).values())
    urls.append(data.get("info", {}).get("home_page") or "")
    for url in urls:
        slug = github_slug(url)
        if slug:
            return slug
    fail(
        f"{package} names no GitHub repository in its PyPI metadata -- pass "
        f"--repo-slug, and say in the report that the link was not derived"
    )


def releases(slug: str) -> list[dict[str, Any]]:
    """Every published release, newest first: tag, body, date, both stamps.

    `--paginate` because a long-lived project's current release is not on page
    one of anything if the caller asks for a version a year back.

    `published` and `updated` are carried separately from `at`, and compared in
    Python rather than in the `--jq`. A release body is mutable -- `rvben/rumdl`
    backfilled a `### Fixed` section into v0.2.61 ten days after cutting the tag,
    which is the worked example in `references/uv-lock.md` -- and `.body` alone
    cannot say so. Doing the comparison here keeps it loud: jq would answer
    `false` for a response that carried no `updated_at` at all, since `null`
    sorts below every string, so an API that stopped returning the field would
    read as "nothing was ever edited". `at` keeps its `created_at` fallback for
    display and is not used for the comparison, because `updated_at` is normally
    later than `created_at` on a release nobody has touched.
    """
    rows = _gh_hard(
        [
            "api",
            f"repos/{slug}/releases",
            "--paginate",
            "--jq",
            ".[] | {tag: .tag_name, body: .body, at: (.published_at // .created_at), "
            "published: .published_at, updated: .updated_at, "
            "assets: (.assets | length), assets_updated: ([.assets[].updated_at] | max)}",
        ]
    ).strip()
    if not rows:
        return []
    try:
        return [json.loads(line) for line in rows.splitlines() if line.strip()]
    except json.JSONDecodeError as exc:
        fail(f"the release list for {slug} did not parse: {exc}")


# How close the release's `updated_at` must sit to its last asset upload for the
# upload to explain it. Measured 0-1s on every asset-explained release checked.
ASSET_SLACK = datetime.timedelta(seconds=60)


def _when(stamp: str) -> datetime.datetime:
    return datetime.datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def edited_since_published(row: dict[str, Any]) -> str | None:
    """ "EDITED" where a release changed after publication and no asset upload
    explains it; None when untouched or when an upload does.

    **`updated_at` moves for asset uploads too, and 0.44.0 marked those as
    edits.** Round twenty-one of the replay gate, 2026-09-19: `rumdl` v0.2.73 was
    published 17:36:49 and its fourteen assets uploaded at 19:07:29, one second
    before `updated_at` -- and this printed *"EDITED ... This is the current
    text, not what went out with the tag"* about a body that is byte-identical
    to its changelog section at the tag. A false claim, from the check written to
    stop false claims. The fact that assets move `updated_at` was already
    measured and written into 0.44.0's changelog; the check was built without it.

    The discriminator is in the same response: the latest asset's `updated_at`.
    Measured on the real edits -- rumdl v0.2.61, .64, .65, rewritten hours to
    days later -- the release's stamp sits the full gap past the last asset;
    on the asset-only ones it sits within a second. Where a release has no
    assets at all, nothing can explain the change away, and it is marked.

    What this cannot see: a body edited in the same minute an asset was
    re-uploaded. And "changed" is all it can prove -- a retitle or a prerelease
    flip moves the stamp too -- which is why the marker says the notes *may* not
    be the announcement, not that they are not.

    Returns the marker, not a bool, so the caller cannot render a missing answer
    as a clean one: where a stamp or the asset list is absent this says
    `unknown`, which is a third state and reads as one.
    """
    published, updated = row.get("published"), row.get("updated")
    if (
        not isinstance(published, str)
        or not isinstance(updated, str)
        or not published
        or not updated
    ):
        return "edit status unknown -- the release carried no timestamps"
    if _when(updated) <= _when(published):
        return None
    assets, last_asset = row.get("assets"), row.get("assets_updated")
    if assets is None:
        return "edit status unknown -- the release carried no asset list"
    if (
        isinstance(last_asset, str)
        and last_asset
        and _when(updated) - _when(last_asset) <= ASSET_SLACK
    ):
        return None
    return f"EDITED {updated}, after publication at {published}, and not by an asset upload"


def match_tag(version: str, published: list[str], slug: str) -> str | None:
    """The tag this project actually used for `version`, or None.

    **Matched, never constructed.** Projects disagree about the `v` prefix and
    change their minds mid-life: `ruff` releases `0.16.4` while its older tags
    carry `v`, and `rumdl` releases `v0.2.58`. A guessed tag returns "not found",
    which reads exactly like "this version has no notes".

    Equality, not prefix: `0.2.6` must not match `v0.2.61`.

    The release list answers first because it is already fetched. A version with
    no release still usually has a tag -- `python/mypy` tags `v2.3.1` and
    publishes nothing -- so the fallback probes both spellings against the git
    ref. Probing both is still matching: neither spelling is assumed, and the
    one that answers is reported.
    """
    for candidate in (version, f"v{version}"):
        if candidate in published:
            return candidate
    for candidate in (version, f"v{version}"):
        if _gh(["api", f"repos/{slug}/git/ref/tags/{candidate}"]) is not None:
            return candidate
    return None


def gap(
    published: list[dict[str, str]], from_tag: str, to_tag: str
) -> tuple[list[dict[str, str]], str]:
    """(the releases from `from_tag` exclusive to `to_tag` inclusive, why it is short).

    Sliced out of the API's own newest-first ordering rather than by parsing
    versions, so no PEP 440 comparison has to be right for this to be. When
    `from_tag` has no release the slice cannot be taken and only `to_tag`'s notes
    are gathered -- which costs prose, never the range: `compare` is a call about
    two refs and does not consult this list at all. The fallible half of the
    ladder is the half the finding does not rest on.

    **The second return value is the fix for this function's own defect.** An
    empty window has three causes and they are not the same answer, but the first
    version returned a bare `[]` for all of them and `main` printed *"this project
    publishes no releases for these versions"* -- true for one cause and false for
    the other two. A backported patch line got told the project publishes nothing,
    by the tool written to stop absence of evidence reading as evidence of absence.
    The comment here even claimed it said so; nothing did.
    """
    tags = [row["tag"] for row in published]
    if to_tag not in tags:
        return [], f"no published release for {to_tag}"
    top = tags.index(to_tag)
    if from_tag not in tags:
        return [published[top]], f"no published release for {from_tag}; only {to_tag}'s notes"
    bottom = tags.index(from_tag)
    if bottom <= top:
        # Newest-first, so `from` at or above `to` means the versions are not in
        # the order the caller believes -- a downgrade, or a backported patch line.
        return [], (
            f"{from_tag} is not older than {to_tag} in the published order -- "
            "a downgrade or a backported line, so no window can be sliced. "
            "The commit range below is unaffected; it asks about two refs."
        )
    return published[top:bottom], ""


class Changelog(NamedTuple):
    """A changelog as read: what the output calls it, its text, and its syntax."""

    name: str
    text: str
    # Read by reStructuredText's rules rather than Markdown's -- see `is_rst`.
    rst: bool


def is_rst(path: str) -> bool:
    """Is the file at `path` reStructuredText, going by its name?

    **Everything but `.md`.** The names matched here end in `.md`, `.rst`, `.txt`
    or nothing, and measured over the changelogs of 53 packages on 2026-09-29, the
    `.txt` and extensionless ones are reStructuredText too: `lxml`'s
    `CHANGES.txt` heads its versions over `=`, and `six`'s and `pyparsing`'s
    `CHANGES` over `-`. The syntax has to be known before a line is read, because
    one line means two things: `~~~~` opens a code fence in Markdown and underlines
    a title in reStructuredText.
    """
    return not path.lower().endswith(".md")


def changelog_at(slug: str, tag: str | None) -> Changelog | None:
    """The repo's changelog at `tag`, or None if it keeps none.

    Two calls and no guessing: list the root at that ref, match a name, fetch it
    raw. A constructed `CHANGELOG.md` 404s on a project that spells it
    `CHANGES.rst`, and the 404 is indistinguishable from having no changelog.

    `tag=None` reads the default branch, which is not the same document. A
    generated changelog is rewritten in full at every release, so the section for
    one version is a function of the ref you read it at -- see `main()`.
    """
    ref = f"?ref={tag}" if tag else ""
    listing = _gh(
        ["api", f"repos/{slug}/contents{ref}", "--jq", '.[] | select(.type=="file") | .name']
    )
    if listing is None:
        return None
    names = [line.strip() for line in listing.splitlines() if CHANGELOG_NAME.match(line.strip())]
    if not names:
        return None
    name = sorted(names)[0]
    text = _raw(slug, name, ref)
    if text and not version_headings(text, is_rst(name)):
        followed = _follow_pointer(slug, name, text, ref)
        if followed:
            return followed
    return Changelog(name, text, is_rst(name)) if text else None


def _raw(slug: str, path: str, ref: str) -> str | None:
    return _gh(
        ["api", f"repos/{slug}/contents/{path}{ref}", "-H", "Accept: application/vnd.github.raw"]
    )


def version_headings(text: str, rst: bool = False) -> int:
    """How many headings in `text` carry a version-shaped token.

    Zero is the signature of a changelog that is not one -- a signpost. A real
    changelog heads its sections with versions; a pointer heads itself with the
    word *Changelog* and says where to look.
    """
    return sum(
        1 for _, title in headings(text.splitlines(), rst).values() if VERSION_TOKEN.search(title)
    )


def _follow_pointer(slug: str, name: str, text: str, ref: str) -> Changelog | None:
    """One hop out of a stub, to a path in the same repository, at the same ref.

    **A matched name is not a changelog.** `pytest-dev/pytest` keeps a root
    `CHANGELOG.rst` of **230 bytes** that says the changelog is elsewhere; the
    real one is **500,693 bytes** at `doc/en/changelog.rst`. Matching the name
    was the fix for guessing it (a guessed name 404s, and a 404 reads as "keeps
    no changelog") -- and the measured hazard is one step past that: the name
    matched, the file fetched, and it was a signpost, reported exactly as a
    project with no changelog at all would be (#133).

    Only same-repository paths, only one hop, only from a file with no version
    headings of its own, and only a target that *has* them. The ref is the one
    being read, not whatever branch the pointer's URL names -- pytest's says
    `blob/main/`, and following that from a tag read would answer a question
    about a different commit.
    """
    for match in POINTER.finditer(text):
        owner = match.group("slug")
        if owner and owner.lower() != slug.lower():
            continue
        path = match.group("path")
        target = _raw(slug, path, ref)
        if target and version_headings(target, is_rst(path)):
            return Changelog(f"{path} (via the pointer in {name})", target, is_rst(path))
    return None


def headings(lines: list[str], rst: bool = False) -> dict[int, tuple[int, str]]:
    """Line index -> (level, title) for every heading, ATX and setext alike.

    `rst` reads the lines as reStructuredText instead -- see `rst_headings`.

    **Setext, because the `pre-commit` ecosystem's own repository uses it.**
    `pre-commit/pre-commit` writes `4.6.2 - 2026-08-10` over a rule of `=`, and
    an ATX-only reader walks all 72,898 bytes of that file and finds no version
    at all -- a successful read that parses to nothing, reported as "no section"
    (#133).

    **Code fences are skipped**, and not as a nicety. `python/mypy` heads its
    versions at `##` and puts Python in its examples, so a `# comment` inside a
    fence read as a level-1 heading and ended the section early: measured
    2026-09-19, `## Mypy 2.0` came back as 34 of its 246 lines, stopped at
    `# mypy: allow-redefinition`. Three of mypy's six latest sections were cut.

    Stricter than CommonMark in one place, deliberately: the text line must
    follow a blank line (or an RST overline). A changelog heads a version with
    one line, and requiring the gap stops the last line of an ordinary paragraph
    from becoming a heading because a `---` thematic break happens to follow it.
    YAML front matter is skipped for the same reason -- its closing `---` sits
    under a `key: value` line.
    """
    if rst:
        return rst_headings(lines)
    found: dict[int, tuple[int, str]] = {}
    start = 0
    if lines and lines[0].strip() == "---":
        for index in range(1, len(lines)):
            if lines[index].strip() in ("---", "..."):
                start = index + 1
                break
    fence = ""
    for index in range(start, len(lines)):
        line = lines[index]
        opened = FENCE_OPEN.match(line)
        if opened:
            marker = opened.group(1)
            if not fence:
                fence = marker
            elif marker[0] == fence[0] and len(marker) >= len(fence):
                fence = ""
            continue
        if fence:
            continue
        atx = HEADING.match(line)
        if atx:
            found[index] = (len(atx.group(1)), atx.group(2))
            continue
        if index + 1 >= len(lines) or not line.strip():
            continue
        rule = SETEXT_RULE.match(lines[index + 1])
        if not rule or NOT_PARAGRAPH.match(line) or SETEXT_RULE.match(line):
            continue
        before = lines[index - 1] if index > start else ""
        if before.strip() and before.strip() != lines[index + 1].strip():
            continue
        found[index] = (1 if rule.group(1)[0] == "=" else 2, line.strip())
    return found


def rst_headings(lines: list[str]) -> dict[int, tuple[int, str]]:
    """Line index -> (level, title) for every reStructuredText section title.

    A title is a line of text over a rule of one punctuation character, repeated,
    with the same rule above it or not. Until 0.58.0 the Markdown reader read these
    files too, and a `~` rule defeated it both ways (#177). Measured over 53
    packages' changelogs on 2026-09-29:

    - `pyca/cryptography` (161 versions) and `pypa/packaging` (54) head their
      versions over `~`, and the Markdown reader found none: rung 2 called each
      file "a pointer or a stub".
    - `python-babel/babel` and `twisted/twisted` put `~` under their subsections,
      and the Markdown reader took each `~~~~` for a code fence. Every heading
      between two of them was skipped, so a version's entries were read as the
      section above it: babel's 2.16.0 came back inside 2.17.0's section, and 20
      of its 47 versions had none.

    So there are no fences here, because reStructuredText has none, and no ATX
    headings. A title starts in the first column, since an indented block is a
    quote or a code sample, which is where a `#` or a rule inside code sits. Its
    rule is at least as long as the title or at least four characters, which is
    where docutils stops reading a short rule as text.

    **Levels come from the order the styles first appear**, which is docutils'
    own rule, and not from the character. `pyca/pyopenssl` heads versions over
    `-` and their subsections over `^`. A fixed ranking that put every rule other
    than `=` at level 2 would end each version at its first subsection.
    """
    found: dict[int, tuple[int, str]] = {}
    styles: list[tuple[str, bool]] = []
    for index in range(len(lines) - 1):
        line, under = lines[index], lines[index + 1]
        rule = RST_RULE.match(under)
        title = line.strip()
        if not rule or not title or RST_RULE.match(line) or NOT_PARAGRAPH.match(line):
            continue
        before = lines[index - 1] if index else ""
        overlined = before.rstrip() == under.rstrip()
        if line[0].isspace() and not overlined:
            continue
        # A blank line above it, an overline, or the hyperlink target that names it.
        if before.strip() and not overlined and not before.startswith(".. "):
            continue
        width = len(under.rstrip())
        if width < len(title) and width < 4:
            continue
        style = (rule.group(1), overlined)
        if style not in styles:
            styles.append(style)
        found[index] = (styles.index(style) + 1, title)
    return found


def section_for(text: str, version: str, rst: bool = False) -> str:
    """The changelog section for exactly `version`, or "".

    **The link target is removed before the heading is read.** A generated
    changelog heads each section with a compare link carrying the *previous*
    version -- `## [0.2.61](.../compare/v0.2.60...v0.2.61) - 2026-08-26` -- so a
    substring test over the raw line matches 0.2.60 and hands back the wrong
    section while looking like it worked. Measured: the first version of this
    kept the link and found **no** section in a file that has one for every
    release.

    Then any token, not the first, because projects head their sections
    differently: `## [0.2.62](...) - 2026-08-27` and `## Mypy 2.3` both have to
    answer, and only one of them leads with the number.

    In reStructuredText the lines just above the next title belong to it, or to
    no section: its overline, the `.. _v46-0-1:` target that names it, and a
    `----` transition. They are left off. A rule is four characters or more, as a
    transition is, because `keyring` writes a bullet of a lone `-`.
    """
    lines = text.splitlines()
    found = headings(lines, rst)
    wanted = {version, f"v{version}"}
    for index, (level, title) in sorted(found.items()):
        label = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", title)
        tokens = {token.strip("[](){}<>,:;.") for token in label.split()}
        if not (tokens & wanted):
            continue
        body = [lines[index]]
        for position in range(index + 1, len(lines)):
            nxt = found.get(position)
            if nxt and nxt[0] <= level:
                break
            body.append(lines[position])
        while (
            rst
            and len(body) > 2
            and (
                not body[-1].strip()
                or RST_TARGET.match(body[-1])
                or (RST_RULE.match(body[-1]) and len(body[-1].strip()) >= 4)
            )
        ):
            body.pop()
        return "\n".join(body).rstrip()
    return ""


def commits(slug: str, from_tag: str, to_tag: str) -> list[str]:
    """Every commit message in `from_tag...to_tag`. The rung that cannot omit one.

    Whole messages, not subjects: the body is where a fix says what it corrupted,
    and rumdl's Rust-source fix names `# [derive(Debug)]` only there.
    """
    rows = _gh_hard(
        [
            "api",
            f"repos/{slug}/compare/{from_tag}...{to_tag}",
            "--jq",
            ".commits[].commit.message | @json",
            "--paginate",
        ]
    ).strip()
    if not rows:
        return []
    # `@json` is load-bearing. A commit message is multi-line, so the plain
    # filter runs eighteen messages together with no record boundary -- and the
    # subject of one lands inside the body of the last. `@json` escapes the
    # newlines, so one line is one message however long its body.
    try:
        return [json.loads(line) for line in rows.splitlines() if line.strip()]
    except json.JSONDecodeError as exc:
        fail(f"the commit range for {slug} did not parse: {exc}")


def normalise(entry: str) -> str:
    """An entry reduced to the words a reader would compare.

    Markdown emphasis, backticks, link syntax, the leading bullet and the
    trailing commit link all differ between a changelog entry and the commit it
    was generated from, and none of them is content.
    """
    text = entry.strip()
    text = re.sub(r"\(\[[0-9a-f]{6,}\]\([^)]*\)\)", " ", text)  # trailing ([abc123](url))
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)  # [label](url) -> label
    text = re.sub(r"[*_`~]+", " ", text)
    text = re.sub(r"^[-*+\s]+", "", text)
    text = re.sub(r"[^a-z0-9]+", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def described(message: str) -> tuple[str, str] | None:
    """(type, description) for a conventional-commit subject, or None.

    The scope is dropped from the description because the changelog carries it
    separately -- `fix(cli): resolve X` becomes `- **cli**: resolve X` -- so
    keeping it on one side and not the other would make every entry a mismatch.
    """
    subject = message.strip().splitlines()[0] if message.strip() else ""
    found = CONVENTIONAL.match(subject.strip())
    if not found:
        return None
    return found.group("type").lower(), found.group("rest").strip()


def labelled(messages: list[str]) -> bool:
    """Does this project label its commits, so that filtering to fixes is safe?

    **The filter is only honest when the project did the labelling.** Dropping
    every non-`fix(...)` commit from the report is justified where the project
    itself said which were fixes; where it did not, the same filter reports
    "0 fix commits" for a range full of them -- which is this plugin's own
    failure class, an absence of evidence read as evidence of absence, rebuilt
    inside the tool written to remove it.

    Measured on `python/mypy` v2.3.0...v2.3.1, the range the reference already
    cites: six commits, **none** conventional, four of them fixes --
    `Fix crash when unpacking return value from overload (#21830)` and three
    `[mypyc]` ones. The first version of this file called that range clean.

    A simple majority, because a project either adopted the convention or did
    not; the mixed case is rare and falls to the safe side, which is reporting
    more.
    """
    if not messages:
        return False
    parsed = sum(1 for message in messages if described(message))
    return parsed * 2 > len(messages)


def subject_of(message: str) -> str:
    return message.strip().splitlines()[0].strip() if message.strip() else ""


# `Bump version to 2.3.1`, `chore: bump version to v0.2.62`, `Release 1.4.0`.
# The one exclusion applied in **both** modes, because a release chore is never
# a finding and every project has two of them per version. Narrow on purpose,
# and counted in the output so the accounting stays complete.
RELEASE_CHORE = re.compile(
    r"^(?:chore(?:\([^)]*\))?:\s*)?(?:bump|prepare|release|version)\b.*?\bv?\d+\.\d+",
    re.IGNORECASE,
)


def candidates(messages: list[str]) -> tuple[list[str], str, int]:
    """(subjects worth reconciling, which classifier ran, release chores dropped).

    Two modes, and **the output says which one ran** -- the same discipline the
    ladder applies to its rungs, one level down. A count of fixes means something
    different depending on who did the classifying, so a reader must not have to
    guess.

    - `conventional`: the project labels its commits, so filter to `FIX_TYPES`,
      plus any commit that bumps a dependency, whatever its type -- the label on
      a `chore(deps)` says nothing about the fix it may carry (#169). A `docs:`
      commit absent from a changelog is correct behaviour.
    - `unlabelled`: it does not, so **nothing is filtered**. Every commit the
      prose fails to name is listed. That is noisier and it is the honest answer:
      the script cannot tell a fix from a refactor here, and saying so beats
      guessing quietly.
    """
    subjects = [subject_of(m) for m in messages if subject_of(m)]
    kept = [s for s in subjects if not RELEASE_CHORE.match(s)]
    chores = len(subjects) - len(kept)
    if not labelled(messages):
        return kept, "unlabelled", chores
    fixes = []
    for subject in kept:
        parsed = described(subject)
        kind = parsed[0] if parsed else ""
        if kind in FIX_TYPES or (is_dependency_bump(subject) and kind not in UNSHIPPED_TYPES):
            fixes.append(subject)
    return fixes, "conventional", chores


def description_of(subject: str) -> str:
    """The part of a subject worth comparing against a changelog entry.

    Conventional subjects give up their type and scope; an unlabelled one is
    compared whole, minus a trailing `(#1234)` PR reference, which is in every
    GitHub squash subject and in no changelog entry.
    """
    parsed = described(subject)
    text = parsed[1] if parsed else subject
    return re.sub(r"\s*\(#\d+\)\s*$", "", text).strip()


def reconciled(description: str, prose_lines: list[str]) -> bool:
    """Does the prose say this? Substring first, then `difflib` for a reword.

    Errs toward `False` -- see the module docstring. The threshold is compared
    against the single best-matching prose line rather than the whole document,
    so a long changelog cannot dilute a real match into a miss or accumulate
    stray words into a false one.
    """
    want = normalise(description)
    if not want:
        return False
    for line in prose_lines:
        if want in line:
            return True
    best = difflib.get_close_matches(want, prose_lines, n=1, cutoff=MATCH_RATIO)
    return bool(best)


def bump_body(message: str) -> str:
    """What a bump's message says about the bump: what moved, and its notes."""
    text = message.strip()
    settings = BOT_SETTINGS.search(text)
    if settings:
        text = text[: settings.start()].rstrip().removesuffix("---").rstrip()
    lines = text.splitlines()
    if len(lines) > BUMP_BODY_LINES:
        rest = len(lines) - BUMP_BODY_LINES
        lines = [*lines[:BUMP_BODY_LINES], f"[... {rest} more line(s) in the commit]"]
    return "\n".join(lines)


def is_dependency_bump(subject: str) -> bool:
    """Does this subject move a dependency? A label or a shape says so."""
    return bool(DEPENDENCY_SCOPE.search(subject) or DEPENDENCY_BUMP.search(subject))


def rank(subject: str) -> int:
    """Sort key: destructive shape, fix-worded, dependency bump, everything else.

    So the cap below cuts the tail first. The two rows Phase 2 came for sat at
    positions 8 and 13 of 266 in ruff 0.16.2...0.16.5, in the order the API
    returned them; and in 0.16.7...0.16.8 `Update Rust crate bstr` and `uuid`
    sat among the 32 rows cut, because a bump shared the tail with everything
    else and the tail is in API order (#169).
    """
    if DESTRUCTIVE.search(subject):
        return 0
    if FIX_WORDED.search(subject):
        return 1
    return 2 if is_dependency_bump(subject) else 3


def write_evidence(
    scratch: Path,
    slug: str,
    from_tag: str,
    to_tag: str,
    blocks: list[str],
    missing: list[str],
    bodies: dict[str, str],
) -> tuple[Path, list[tuple[int, int, str]], list[str]]:
    """Save both halves of the comparison, so reading them is not a second fetch.

    Phase 2 reads the prose for `Security` sections, which no count in this
    script's output can stand in for. Release bodies carry download tables and
    install instructions; those are left in, because trimming what looks like
    boilerplate is how a `Security` heading below one gets trimmed with it.

    The unreconciled list goes in the same file and **is never capped here**.
    The terminal shows the ranked head; this is the whole of it, so the cap
    shortens the reading and not the evidence.

    **The destructive-shaped rows carry their full commit message.** `commits()`
    fetches whole messages precisely because *"the body is where a fix says what
    it corrupted"* -- rumdl's Rust-source fix names `# [derive(Debug)]` only
    there -- and the first version of this file then dropped every body on the
    floor and told the terminal reader to go and fetch the range again. A
    docstring claiming what the code discards, in a file whose whole subject is
    prose that does not match what landed.

    Only the marked rows, because these are the ones Phase 7 takes the verdict
    from, and the dependency bumps that carry a body; a body for all 266 of
    ruff's would be the wall this file exists to replace.

    **It returns the file's index as well**: each section's first and last line
    and its heading (#173). ruff's 0.16.7...0.16.8 file is 300 lines, and a run
    that read two such files in one command spilled past its output and cut its
    own slices with `awk`. With the index printed beside the path, the slice is
    supplied. The file's lines come back too, for the security scan.
    """
    out = scratch / f"changelog-{slug.replace('/', '-')}-{from_tag}-{to_tag}.md"
    header = [
        f"# Phase 2 evidence for {slug} {from_tag}...{to_tag}",
        "",
        "Written by `changelog.py`. Read the prose for `Security` sections: a",
        "privately disclosed fix ships with no CVE and every scanner reports clean.",
        "",
    ]
    tail: list[str] = []
    if missing:
        tail = [
            "",
            f"## unreconciled -- in the range, in none of the prose above ({len(missing)})",
            "",
            *(f"- {subject}" for subject in missing),
        ]
    marked = [s for s in missing if DESTRUCTIVE.search(s) and bodies.get(s, "").strip()]
    # A bump's body is where a bot lists what moved, and a maintainer's refresh
    # may name nothing -- so only where there is a body past the subject line.
    bumps = [
        s
        for s in missing
        if is_dependency_bump(s)
        and not DESTRUCTIVE.search(s)
        and len(bodies.get(s, "").strip().splitlines()) > 1
    ]
    if marked:
        tail += [
            "",
            f"## destructive-fix shape -- full commit message ({len(marked)})",
            "",
            "The shape SKILL.md Phase 2 sends you to find. The body is where the",
            "data loss is described; the subject rarely names it.",
        ]
        for subject in marked:
            tail += ["", f"### {subject}", "", "```", bodies[subject].strip(), "```"]
    if bumps:
        tail += [
            "",
            f"## dependency bumps -- full commit message ({len(bumps)})",
            "",
            "A dependency compiled into the wheel can carry a fix no Python-side",
            "scanner sees. The body is where a bump lists what moved; a bot's own",
            "settings are cut, and a long body is capped.",
        ]
        for subject in bumps:
            tail += ["", f"### {subject}", "", "```", bump_body(bodies[subject]), "```"]
    lines = "\n".join(header + blocks + tail).split("\n")
    out.write_text("\n".join(lines), encoding="utf-8")
    # Only the headings this function writes: a release body carries `##` lines
    # of its own -- ruff's has `## Install ruff 0.16.8` -- and they are not sections.
    starts = [n for n, line in enumerate(lines) if SECTION.match(line)]
    index = [
        (start + 1, (starts[k + 1] if k + 1 < len(starts) else len(lines)), lines[start][3:])
        for k, start in enumerate(starts)
    ]
    return out, index, lines


def inline_prose(
    lines: list[str], index: list[tuple[int, int, str]], verbatim: list[str]
) -> tuple[list[str], list[tuple[int, int]]]:
    """(the prose to print, the spans of it left in the file), or nothing to print.

    **The scan cannot stand in for the read, so the read is made unskippable
    where it is short.** cryptography 46.0.2's whole entry is one bullet,
    `Updated Windows, macOS, and Linux wheels to be compiled with OpenSSL 3.5.4.`,
    and OpenSSL 3.5.4 is the release that fixed CVE-2025-9230. It carries no id,
    no heading and no word, so the scan says *none*, and the only thing between
    that and the report was a line asking for a read (#177).

    Rung 2 is the changelog and goes first. Rung 1 is left out where it carries
    rung 2 verbatim, because it adds nothing then but the download table (#173).
    A rung 1 that says something else goes in as well if both fit, and otherwise
    stays in the file with its lines named. Where there is no rung 2, rung 1 is the
    prose. Over `INLINE_BYTES` nothing is printed, and the pointer stays.
    """

    def text_of(start: int, end: int) -> list[str]:
        chunk = lines[start - 1 : end]
        while chunk and not chunk[-1].strip():
            chunk.pop()
        return chunk

    def size(spans: list[tuple[int, int]]) -> int:
        return sum(len(line) + 1 for start, end in spans for line in text_of(start, end))

    rung2 = [(start, end) for start, end, label in index if label.startswith("rung 2")]
    rung1 = [
        (start, end)
        for start, end, label in index
        if label.startswith("rung 1")
        and label.removeprefix("rung 1 -- release notes, ").split(" (")[0] not in verbatim
    ]
    for chosen, left in (
        (sorted(rung2 + rung1), []),
        (rung2, rung1),
    ):
        if chosen and size(chosen) <= INLINE_BYTES:
            return [line for start, end in chosen for line in [*text_of(start, end), ""]], left
    return [], []


def one_source(section: str, body: str) -> bool:
    """Does a release body carry a changelog section verbatim?

    Line by line, whitespace collapsed, with the section's own version heading left
    out -- rumdl's release workflow drops `## [0.2.75](...) - 2026-09-20` and keeps
    the rest, then appends its download table. Measured over the ranges the scan
    above names: ruff's, uv's, rumdl's and requests 2.32.5's release notes all carry
    their changelog section, so the two agreeing is one text read twice. A body that
    does not carry it says nothing either way: pytest's notes differ from its
    changelog in form, and a reformatted copy is not independence.
    """

    def lines_of(text: str) -> list[str]:
        return [" ".join(line.split()) for line in text.splitlines() if line.strip()]

    wanted = lines_of(section)
    if wanted and wanted[0].startswith("#"):
        wanted = wanted[1:]
    return bool(wanted) and "\n".join(wanted) in "\n".join(lines_of(body))


def entry_text(line: str) -> str:
    """A prose line as a reader would quote it: no bullet, bold, link syntax or
    trailing commit and PR links, which is where most of a rumdl line's bytes are."""
    text = re.sub(r"\s*\(\[(?:[0-9a-f]{6,}|#\d+)\]\([^)]*\)\)", "", line.strip())
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"^(?:[-*+]|\d+[.)])\s+", "", text).replace("**", "")
    return re.sub(r"\s+", " ", text.replace("\\[", "[").replace("\\]", "]")).strip()


def fix_mode_lines(lines: list[str], index: list[tuple[int, int, str]]) -> list[tuple[int, str]]:
    """(line, text) for each prose line that reads like a change to what a fix writes,
    at its first line only: rung 1 so often carries rung 2 verbatim (#185)."""
    seen: set[str] = set()
    found = []
    for start, end, label in index:
        if not label.startswith("rung "):
            continue
        for number in range(start, end + 1):
            text = entry_text(lines[number - 1])
            key = normalise(text)
            if not key or key in seen or not FIX_MODE.search(text):
                continue
            seen.add(key)
            found.append((number, text))
    return found


def security_lines(
    lines: list[str], index: list[tuple[int, int, str]]
) -> list[tuple[int, str, str]]:
    """(line, why, text) for every line of the evidence file that looks like security:
    an advisory id, a heading naming security, or the words. The file's own header,
    which tells the reader to look for exactly this, is left out."""
    first = index[0][0] if index else len(lines) + 1
    found = []
    for number, line in enumerate(lines, start=1):
        if number < first:
            continue
        ident = ADVISORY_ID.search(line)
        if ident:
            found.append((number, ident.group(0), line))
        elif SECURITY_HEADING.search(line):
            found.append((number, "heading", line))
        elif SECURITY_WORD.search(line):
            found.append((number, "word", line))
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 2's changelog ladder, reconciled.")
    parser.add_argument("--scratch", required=True, help="$SCRATCH from the Phase 0 handoff")
    parser.add_argument("--from", dest="old", required=True, help="the version the lockfile has")
    parser.add_argument("--to", dest="new", required=True, help="the version the bump proposes")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--package", help="PyPI name; the repo comes from its metadata")
    source.add_argument("--repo-slug", dest="slug", help="owner/repo, when it is already known")
    parser.add_argument(
        "--write-mode",
        action="store_true",
        help="this repo runs the tool with --fix/--write/-i, so a destructive fix is data loss",
    )
    parser.add_argument(
        "--gap",
        action="store_true",
        help="the range is the gap above the proposal: a fix in it sets the follow-up's target",
    )
    args = parser.parse_args()

    scratch = Path(args.scratch)
    if not scratch.is_dir():
        fail(f"{scratch} does not exist -- re-derive $SCRATCH, or Phase 0 never ran")

    if args.slug and not valid_slug(args.slug):
        fail(f"--repo-slug {args.slug!r} is not an owner/repo pair")
    slug = args.slug or resolve_repo(args.package)
    published = releases(slug)
    tags = [row["tag"] for row in published]

    to_tag = match_tag(args.new, tags, slug)
    from_tag = match_tag(args.old, tags, slug)
    if to_tag is None:
        fail(f"{slug} has no release and no tag for {args.new} -- rung 3 has nothing to compare to")
    if from_tag is None:
        fail(
            f"{slug} has no release and no tag for {args.old} -- rung 3 has nothing to compare from"
        )

    print(f"repo:  {slug}" + ("" if args.slug else f"  (from {args.package}'s PyPI metadata)"))
    print(f"range: {from_tag}...{to_tag}")
    print()

    # --- rungs 1 and 2: what the project chose to say ------------------------
    window, why = gap(published, from_tag, to_tag)
    blocks: list[str] = []
    edits: list[str] = []
    released = {row["tag"]: row["body"] for row in window}
    verbatim: list[str] = []
    for row in window:
        mark = edited_since_published(row)
        header = f"## rung 1 -- release notes, {row['tag']} ({row['at']})"
        if mark:
            edits.append(f"{row['tag']}: {mark}")
            header += f"\n\n**{mark}.** This may not be the text that went out with the tag."
        blocks.append(f"{header}\n\n{row['body']}")
    print(f"rung 1 -- release notes: {len(window)} release(s) in the gap")
    for line in edits:
        print(f"         {line}")
    if why:
        print(f"         ({why})")

    found = changelog_at(slug, to_tag)
    later = changelog_at(slug, None)
    sections = 0
    regenerated: list[str] = []
    if found:
        name, text, rst = found
        head_text, head_rst = (later.text, later.rst) if later else ("", rst)
        for row in window or [{"tag": to_tag}]:
            version = row["tag"].removeprefix("v")
            body = section_for(text, version, rst)
            if body:
                sections += 1
                blocks.append(f"## rung 2 -- {name}, {row['tag']}\n\n{body}")
            head_body = section_for(head_text, version, head_rst) if head_text else ""
            notes = released.get(row["tag"], "")
            if notes and (one_source(body, notes) or one_source(head_body, notes)):
                verbatim.append(row["tag"])
            if head_body and head_body.strip() != body.strip():
                regenerated.append(row["tag"])
                blocks.append(
                    f"## rung 2 at the default branch -- {name}, {row['tag']}\n\n"
                    f"**This differs from the same section read at {to_tag}.** A generated\n"
                    f"changelog is rewritten in full at every release, so the entry for one\n"
                    f"version is a function of the ref you read it at.\n\n{head_body}"
                )
        if version_headings(text, rst):
            print(f"rung 2 -- {name}: {sections} section(s) for the versions in the gap")
        else:
            print(f"rung 2 -- {name} carries no version headings at all ({len(text)} bytes)")
            print("         -- a pointer or a stub, not a changelog with nothing to say;")
            print("            no same-repo path in it led to one. Report it; it is not 'none'.")
        for tag in regenerated:
            print(f"         {tag}: the section at the default branch DIFFERS")
            print(f"                 from the one at {to_tag} -- both are in the evidence file")
        if verbatim:
            # uv-lock.md asked the reader to check this by hand, from the project's
            # release procedure. Where the text says it outright, it is said here.
            print(f"         rung 1 carries rung 2's section verbatim for {', '.join(verbatim)}:")
            print("         one text read twice, so the two agreeing is not corroboration")
    else:
        print("rung 2 -- no changelog file at this tag")

    prose_lines = [
        normalise(line) for block in blocks for line in block.splitlines() if normalise(line)
    ]

    # --- rung 3: what actually landed ----------------------------------------
    messages = commits(slug, from_tag, to_tag)
    subjects, mode, chores = candidates(messages)
    # Subject -> whole message, so the evidence file can carry the body of a
    # marked row. First-wins: two commits can share a subject, and the earlier
    # is the one the range lists first.
    bodies: dict[str, str] = {}
    for message in messages:
        bodies.setdefault(subject_of(message), message)
    what = (
        "of fix type or bumping a dependency" if mode == "conventional" else "after release chores"
    )
    print(f"rung 3 -- commit range: {len(messages)} commit(s), {len(subjects)} {what}")
    if mode == "conventional":
        print(f"         classifier: conventional commits, so {'/'.join(FIX_TYPES)} are read,")
        print("         and dependency bumps of any type -- a chore(deps) can carry a crate's fix")
    else:
        print("         classifier: this project does not label its commits, so")
        print("         nothing is filtered -- every unnamed commit is listed below")
    if chores:
        print(f"         ({chores} release chore(s) excluded)")
    print()

    # --- the reconciliation --------------------------------------------------
    missing = sorted(
        (s for s in subjects if not reconciled(description_of(s), prose_lines)), key=rank
    )
    saved, index, lines = write_evidence(scratch, slug, from_tag, to_tag, blocks, missing, bodies)
    print(f"evidence saved to {saved}, {len(lines)} lines:")
    for start, end, label in index:
        print(f"  lines {start:>4}-{end:<4} {label}")
    hits = security_lines(lines, index)
    where = {n: label for start, end, label in index for n in range(start, end + 1)}
    marked = [h for h in hits if h[1] != "word"]
    words = [h for h in hits if h[1] == "word"]
    if hits:
        print("security-shaped lines, with the section each sits in:")
        for number, why, line in marked + words[:SHOWN_WORDS]:
            print(f"  line {number:>4}  {why}  [{where.get(number, '')}]  {line.strip()[:90]}")
        if len(words) > SHOWN_WORDS:
            print(f"  ... and {len(words) - SHOWN_WORDS} more with the words alone, in the file")
    else:
        print("security-shaped lines: none -- no advisory id, heading or word in the file.")
    fixes = fix_mode_lines(lines, index)
    if fixes and not args.write_mode:
        print(
            f"fix-mode lines in the prose: {len(fixes)}, not listed -- this repo does not "
            "run the tool\nin write mode (no --write-mode), so no fix of it writes here."
        )
    elif fixes:
        print(
            "fix-mode lines in the prose -- this repo runs the tool in write mode, so each\n"
            f"can be a bug in what a fix wrote here ({len(fixes)}, first line of each):"
        )
        for number, text in fixes[:SHOWN_FIX_MODE]:
            print(f"  line {number:>4}  {text[:110]}")
        if len(fixes) > SHOWN_FIX_MODE:
            print(f"  ... and {len(fixes) - SHOWN_FIX_MODE} more, in the file")
        rules = sorted({rule for _, text in fixes for rule in RULE_ID.findall(text)})
        print(f"rules they name: {', '.join(rules) or 'none'}.")
        print("A rule this repo's config turns off is inert here; a line that names no rule")
        print("is the fix engine's own, and runs wherever the tool does.")
    # A fix above the proposal is Hold if the bump moved into the bug and a follow-up
    # if it did not. Both rows end at the fixed version, and the #438 replays under
    # 0.59.0 split on naming it: one took Phase 7's "neither row by default" as no
    # target at all. So the target is printed here, where the gap's fixes are.
    shapes = []
    if args.gap and args.write_mode and fixes:
        shapes.append(f"{len(fixes)} fix-mode line(s) in a tool this repo runs in write mode")
    if args.gap and marked:
        shapes.append(f"{len(marked)} security-shaped line(s), an advisory id or a heading")
    if shapes:
        print(
            f"IN THE GAP: {' and '.join(shapes)}.\n"
            f"The follow-up's target is {args.new}, cooldown notwithstanding: Phase 7's rows\n"
            "for a fix above the proposal all end there, and differ only in whether this PR\n"
            "merges first. That is Phase 4's reproducer question, underivable without it."
        )
    prose = [(s, e) for s, e, label in index if label.startswith("rung ")]
    span = f"lines {prose[0][0]}-{prose[-1][1]}" if prose else "the prose rungs"
    shown, left = inline_prose(lines, index, verbatim)
    if shown:
        print("The scan finds ids, headings and words, and a fix written as a plain bullet")
        print("has none of them. The prose is short, so here it is. Read it for `Security`")
        print("entries and for what changed:")
        print()
        for line in shown:
            print(f"  | {line}".rstrip())
        for start, end in left:
            print(f"Rung 1 says something else in lines {start}-{end}, over what this prints.")
            print("Read it there.")
    else:
        print(f"Read rungs 1 and 2 ({span}) for `Security` entries all the same: the scan finds")
        print("ids, headings and words, and a fix written as a plain bullet has none of them.")
        if prose:
            print(f"(Not printed here: the prose is over the {INLINE_BYTES} bytes this prints.)")
    print()

    noun = "commit(s)"
    if mode == "conventional":
        # "fix commit(s)" stays true of a `fix(deps)` bump; only a bump the
        # project labelled something else changes what was counted.
        noun = (
            "fix or dependency-bump commit(s)"
            if any((described(s) or ("",))[0] not in FIX_TYPES for s in subjects)
            else "fix commit(s)"
        )
    if not missing:
        print(
            f"RECONCILED: the prose names all {len(subjects)} {noun} in the range."
            if subjects
            else f"RECONCILED: the range carries no {noun} to reconcile."
        )
        return 0

    print(f"UNRECONCILED: {len(missing)} of {len(subjects)} {noun} are in the range")
    print("and in none of the prose above.")
    print()
    destructive = [s for s in missing if DESTRUCTIVE.search(s)]
    for subject in missing[:SHOWN]:
        mark = ""
        if DESTRUCTIVE.search(subject):
            mark = "   <- destructive-fix shape"
        elif is_dependency_bump(subject):
            mark = "   <- dependency bump"
        print(f"  {subject}{mark}")
    if len(missing) > SHOWN:
        # Counted from the rows actually cut, so it is true of every tier -- the
        # line this replaces said "nothing marked was cut", which was true of its
        # own marker and read as true of everything (#169).
        cut = [rank(s) for s in missing[SHOWN:]]
        tiers = ", ".join(f"{cut.count(t)} {TIERS[t]}" for t in range(len(TIERS)) if t in cut)
        print(f"  ... and {len(missing) - SHOWN} more, all of them in the file above.")
        print(f"  Cut from this list: {tiers}.")
    print()
    if mode == "unlabelled" and len(missing) > SHOWN:
        # Gated on having actually elided rows, not on the ratio. Measured on
        # ruff 0.16.2...0.16.5 (266 of 307), where the repository ships `ty`
        # under the same tags and those subjects are most of the range -- saying
        # so stops a true count being read as an accusation. An earlier version
        # gated on the ratio alone and fired on mypy's 4 of 4, where the project
        # ships one product and the changelog really has no section. When the
        # whole list is on screen the reader can see that for themselves.
        print(
            f"Most of this range is unnamed ({len(missing)} of {len(subjects)}), which is\n"
            "usual for a repository shipping more than one product under one tag.\n"
            "The ranked head above is the part to read first; this is not a claim\n"
            "that the project omitted that many fixes."
        )
        print()
    bumps = [s for s in missing if is_dependency_bump(s)]
    if bumps:
        print(
            f"{len(bumps)} of them bump{'s' if len(bumps) == 1 else ''} a dependency. "
            "One compiled into the wheel can carry\n"
            "a fix no Python-side scanner sees, and the notes need not name it:\n"
            "vendored.py in Phase 3 reads whether a wheel ships it."
        )
        print()
    if destructive:
        mode_note = (
            "this repo runs the tool in write mode, so"
            if args.write_mode
            else "if this repo runs the tool in write mode (--fix/--write/-i) then"
        )
        print(
            f"{len(destructive)} carr{'ies' if len(destructive) == 1 else 'y'} the shape "
            "Phase 2 sends you to find. Read the full\nmessage in the range: "
            f"{mode_note}\nthese are data-loss bugs in a mode that runs automatically, "
            "and they reach\nPhase 7's verdict rather than its footnotes."
        )
    else:
        print(
            "None of these unreconciled commits carries the destructive-fix shape, but the\n"
            "prose does not name them. Report the gap and what the commits say -- that is\n"
            "the row. The prose's own fix-mode lines are the list above, if any."
        )
    return 1


def cli() -> NoReturn:
    """Entry point. Anything unforeseen becomes exit 2, never exit 1.

    Exit 1 here means the prose came up short -- a finding for the report. An
    unhandled exception exits 1 too, so without this a crash would be read as a
    project having quietly dropped its fixes. Set `DEPENDABOT_AUDIT_DEBUG` to
    re-raise.
    """
    try:
        sys.exit(main())
    except SystemExit:
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
