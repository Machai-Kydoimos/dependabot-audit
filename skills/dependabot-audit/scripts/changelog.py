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
says which.** Where the project writes conventional commits, only fix types are
read: a `docs:` commit absent from a changelog is correct behaviour, and rows
nobody acts on are how a report stops being read. Where it does not, nothing is
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
fix-worded next, the top `SHOWN` on the terminal and every one of them in the
evidence file.

Usage:
    changelog.py --scratch DIR --from VERSION --to VERSION \\
                 (--package NAME | --repo-slug OWNER/REPO) [--write-mode]

Exit status: 0 = the prose names every fix in the range. 1 = it does not, and the
unreconciled commits are listed (a Phase 2 finding, not a script failure).
2 = could not run.
Requires Python 3.11+. Network: `gh api`, and PyPI for `--package`.
"""

from __future__ import annotations

import argparse
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
from typing import NoReturn

TIMEOUT = 60

# Conventional-commit types whose absence from a changelog is worth a row.
# `docs`, `chore`, `ci`, `test`, `refactor` and `style` are left out on purpose:
# a changelog that omits them is behaving correctly, and rows nobody acts on are
# how a report stops being read.
FIX_TYPES = ("fix", "perf", "revert", "security", "sec")

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

# Second tier of the ranking: subjects that read like a correction without
# carrying the destructive shape. Only ordering depends on this, so a miss costs
# a place in a list and never a row.
FIX_WORDED = re.compile(
    r"\b(?:fix(?:e[sd])?|bug|crash|regress(?:ion|es)?|revert(?:s|ed)?|correct(?:s|ed)?"
    r"|restore[sd]?|repair(?:s|ed)?|broken|incorrect|wrong|corrupt(?:s|ed|ion)?"
    r"|data.loss|panic|deadlock|leak|overwrit(?:e|es|ing|ten))\b",
    re.IGNORECASE,
)

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

# How alike two entries must read before one counts as the other. Deliberately
# forgiving of rewording and deliberately not of omission -- see the module
# docstring on which error this file prefers.
MATCH_RATIO = 0.82

# One path segment of a GitHub `owner/repo`. Rejects `.` and `..` outright,
# because the slug is interpolated into a `gh api repos/<slug>/...` path and
# `..` walks out of `repos/` into a different endpoint.
SEGMENT = re.compile(r"^(?!\.{1,2}$)[A-Za-z0-9._-]{1,100}$")


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
    `.` or `..`, and may hold only the characters GitHub actually allows.
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
    if not (SEGMENT.match(owner) and SEGMENT.match(repo)):
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


def releases(slug: str) -> list[dict[str, str]]:
    """Every published release, newest first: tag, body, date.

    `--paginate` because a long-lived project's current release is not on page
    one of anything if the caller asks for a version a year back.
    """
    rows = _gh_hard(
        [
            "api",
            f"repos/{slug}/releases",
            "--paginate",
            "--jq",
            ".[] | {tag: .tag_name, body: .body, at: (.published_at // .created_at)}",
        ]
    ).strip()
    if not rows:
        return []
    try:
        return [json.loads(line) for line in rows.splitlines() if line.strip()]
    except json.JSONDecodeError as exc:
        fail(f"the release list for {slug} did not parse: {exc}")


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


def changelog_at(slug: str, tag: str) -> tuple[str, str] | None:
    """(filename, text) for the repo's changelog at `tag`, or None if it keeps none.

    Two calls and no guessing: list the root at that ref, match a name, fetch it
    raw. A constructed `CHANGELOG.md` 404s on a project that spells it
    `CHANGES.rst`, and the 404 is indistinguishable from having no changelog.
    """
    listing = _gh(
        ["api", f"repos/{slug}/contents?ref={tag}", "--jq", '.[] | select(.type=="file") | .name']
    )
    if listing is None:
        return None
    names = [line.strip() for line in listing.splitlines() if CHANGELOG_NAME.match(line.strip())]
    if not names:
        return None
    name = sorted(names)[0]
    text = _gh(
        [
            "api",
            f"repos/{slug}/contents/{name}?ref={tag}",
            "-H",
            "Accept: application/vnd.github.raw",
        ]
    )
    return (name, text) if text else None


def section_for(text: str, version: str) -> str:
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
    """
    lines = text.splitlines()
    wanted = {version, f"v{version}"}
    for index, line in enumerate(lines):
        head = HEADING.match(line)
        if not head:
            continue
        level, title = len(head.group(1)), head.group(2)
        label = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", title)
        tokens = {token.strip("[](){}<>,:;.") for token in label.split()}
        if not (tokens & wanted):
            continue
        body = [line]
        for following in lines[index + 1 :]:
            nxt = HEADING.match(following)
            if nxt and len(nxt.group(1)) <= level:
                break
            body.append(following)
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

    - `conventional`: the project labels its commits, so filter to `FIX_TYPES`.
      A `docs:` commit absent from a changelog is correct behaviour.
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
        if parsed and parsed[0] in FIX_TYPES:
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


def rank(subject: str) -> int:
    """Sort key: destructive shape first, fix-worded next, everything else last.

    So the cap below can only ever cut the tail. The two rows Phase 2 came for
    sat at positions 8 and 13 of 266 in ruff 0.16.2...0.16.5, in the order the
    API returned them.
    """
    if DESTRUCTIVE.search(subject):
        return 0
    return 1 if FIX_WORDED.search(subject) else 2


def write_evidence(
    scratch: Path,
    slug: str,
    from_tag: str,
    to_tag: str,
    blocks: list[str],
    missing: list[str],
    bodies: dict[str, str],
) -> Path:
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
    from; a body for all 266 of ruff's would be the wall this file exists to
    replace.
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
    out.write_text("\n".join(header + blocks + tail), encoding="utf-8")
    return out


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
    for row in window:
        blocks.append(f"## rung 1 -- release notes, {row['tag']} ({row['at']})\n\n{row['body']}")
    print(f"rung 1 -- release notes: {len(window)} release(s) in the gap")
    if why:
        print(f"         ({why})")

    found = changelog_at(slug, to_tag)
    sections = 0
    if found:
        name, text = found
        for row in window or [{"tag": to_tag}]:
            body = section_for(text, row["tag"].removeprefix("v"))
            if body:
                sections += 1
                blocks.append(f"## rung 2 -- {name}, {row['tag']}\n\n{body}")
        print(f"rung 2 -- {name}: {sections} section(s) for the versions in the gap")
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
    what = "of fix type" if mode == "conventional" else "after release chores"
    print(f"rung 3 -- commit range: {len(messages)} commit(s), {len(subjects)} {what}")
    if mode == "conventional":
        print(
            f"         classifier: conventional commits, so only {'/'.join(FIX_TYPES[:3])} are read"
        )
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
    saved = write_evidence(scratch, slug, from_tag, to_tag, blocks, missing, bodies)
    print(f"evidence saved to {saved}")
    print("Read it for `Security` sections -- no count here substitutes for that.")
    print()

    noun = "fix commit(s)" if mode == "conventional" else "commit(s)"
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
        mark = "   <- destructive-fix shape" if DESTRUCTIVE.search(subject) else ""
        print(f"  {subject}{mark}")
    if len(missing) > SHOWN:
        print(
            f"  ... and {len(missing) - SHOWN} more, all of them in the file above."
            "\n  Ranked, so nothing marked was cut."
        )
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
            "None carries the destructive-fix shape, but the prose still does not name\n"
            "them. Report the gap and what the commits say -- that is the row."
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
