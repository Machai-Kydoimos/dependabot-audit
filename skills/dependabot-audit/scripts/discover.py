#!/usr/bin/env python3
"""Phase 0's derivation half: every output, each tagged with which state it is in.

Phase 0 has three states for every output and the prose asks a reader to hold all
of them apart by hand: **derived**, **absent** (often a finding in its own right),
and **underivable** — the call failed, or its precondition did not hold. Only the
third is dangerous, because the two known ways it happens both fail into a
*plausible* value rather than an error, and travel downstream as fact.

Both remaining Phase 0 defects were in one output. `$BASE_SHA`:

  - a base branch rewritten under an open PR sends `git merge-base` back to a much
    older shared ancestor, so a two-file bump presented as fourteen files and
    3,682 deletions and Phase 1's gate stopped the audit for a reason that was not
    true;
  - a PR that has **landed** has a head that is an ancestor of the default branch,
    so a merge base taken against it *is* the head — Phase 1's diff comes back
    empty, Phase 4 measures the PR against itself, and Phase 6 cross-checks the
    head against itself. All three report the reassuring answer.

This script takes the merge base from GitHub's own `compare` endpoint, which is
right in both states, and then **proves whether it is the branch point** rather
than assuming it. Measured: on `cli/cli` #14049, merged, `compare` returns the
real branch point where `git merge-base trunk pr-N` returns the head.

**Read-only.** No fetch, no worktree, no local git at all — every answer comes
from the API. The mutations Phase 0 performs stay visible in `SKILL.md`, where a
plugin whose contract is "reports, never merges" should keep them.

Exit codes follow the other scripts here:

    0   ran; nothing about this PR changes the shape of the audit
    1   ran, and found something — a rewritten base, a PR the bots did not open,
        an account that cannot merge, or an output that could not be derived
    2   could not run

    python3 discover.py --repo OWNER/NAME --number N [--json]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from typing import Any, NoReturn

TIMEOUT = 60

BOTS = frozenset({"dependabot[bot]", "renovate[bot]", "dependabot", "renovate"})

# Three states, and the third is the whole reason this script exists.
DERIVED = "derived"
ABSENT = "absent"
UNDERIVABLE = "underivable"

# Phase 1's gate, as data. Both rules were the reader's, both are mechanical, and
# both fail quietly: a scope that reads clean is what hands Phases 4 and 5 a
# shell.
#
# Filenames only. What a file *is* stays `audit.py`'s question — it sniffs
# content because a `Cargo.lock` is TOML with `[[package]]` blocks and a name is
# not evidence. This is the cheaper, earlier signal that decides which reference
# and which Phase 1 method apply, and it is deliberately not a second opinion on
# the sniff.
MANIFESTS = frozenset({"uv.lock", "pyproject.toml"})
WORKFLOW_DIRS = (".github/workflows/", ".github/actions/")

# Matched on the basename: `pre-commit` does not require the file at the repo
# root, and one per package is ordinary. A full-path match misses those and the
# bump falls back to `unknown`, which reports as a scope finding.
PRE_COMMIT_CONFIG = ".pre-commit-config.yaml"

# Named rather than lumped into "unknown": a Cargo bump is a **boundary**, and
# reporting it as a scope finding says the bump reached into source when what
# happened is that this plugin has no rule for it. An improvised recipe returned
# matching checksums and a clean OSV batch on a real one.
UNSUPPORTED_LOCKFILES = {
    "Cargo.lock": "Rust / Cargo",
    "package-lock.json": "JavaScript / npm",
    "yarn.lock": "JavaScript / Yarn",
    "pnpm-lock.yaml": "JavaScript / pnpm",
    "poetry.lock": "Python / Poetry",
    "Pipfile.lock": "Python / Pipenv",
    "go.sum": "Go",
    "Gemfile.lock": "Ruby / Bundler",
    "composer.lock": "PHP / Composer",
}

# The API caps a commit's `files` array here and says nothing about the rest, so
# file 301 is absent and indistinguishable from one that is in scope. Same
# failure as `contexts(first:100)` in `ci_state.py`, one endpoint along.
FILES_CAP = 300

# `actions.md`: "every changed line across them is a `uses:` line or its trailing
# version comment. That is the invariant." Not the number of files — a grouped
# bump moves several actions and an action is pinned in every workflow using it.
USES_LINE = re.compile(r"^\s*-?\s*uses:\s*\S+\s*(#.*)?$")

# `pre-commit`'s equivalent, and the gate is the same shape: a bump may move the
# pin and nothing else. `\S+` covers the quoted forms and the `# frozen: <sha>`
# comment `pre-commit autoupdate --freeze` writes.
#
# Deliberately not `repo:`. Repointing a hook at a different repository is not a
# version bump, and it is exactly what this gate should refuse to wave through.
REV_LINE = re.compile(r"^\s*rev:\s*\S+\s*(#.*)?$")

# The trailing version comment is not always trailing. A compiler that emits
# workflows records the pins it wrote in a header block, so a correct bump
# changes the `uses:` line *and* the comment naming the same pin — measured on
# `cli/cli` #13981 and #14147, both merged:
#
#     -#   - actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0 # v7.0.0
#     +#   - actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
#
# Gated as a line beyond the pin that fires on two of the three PRs `actions.md`
# cites as the measurement for its own rule. A YAML comment cannot execute, so it
# is inside the pin for a gate that asks what the diff makes *run* — and it is
# counted rather than ignored, because a pin manifest is how a generated workflow
# announces itself, which is a Phase 7 row of its own.
COMMENT_LINE = re.compile(r"^\s*#")


def fail(what: str) -> NoReturn:
    """Exit 2. Reserved for "could not run", never for "ran and found something"."""
    print(f"error: {what}", file=sys.stderr)
    raise SystemExit(2)


def _gh(args: list[str]) -> tuple[int, str]:
    """Run `gh`; return (exit code, stdout). The code is the signal.

    `gh` writes an API error body to **stdout** and still exits non-zero, so a
    caller that reads only stdout gets a well-formed JSON object asserting
    something false. That is how a 404 once became "no required checks", and how
    a failed permissions call reads as a `pull`-only account. Every caller below
    gates on the code.
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
        fail("`gh` is not on PATH; this phase is entirely GitHub API calls")
    except subprocess.TimeoutExpired:
        fail(f"`gh {' '.join(args[:2])}` exceeded {TIMEOUT}s")
    return proc.returncode, proc.stdout


def _json_or_none(args: list[str]) -> Any | None:
    """Parsed JSON, or None when the call failed — never a parsed error body."""
    code, out = _gh(args)
    if code != 0:
        return None
    try:
        return json.loads(out) if out.strip() else None
    except json.JSONDecodeError:
        return None


def _required(args: list[str], what: str) -> Any:
    """For the calls with no useful audit without them."""
    got = _json_or_none(args)
    if got is None:
        fail(f"could not read {what} — `gh {' '.join(args[:2])}` failed")
    return got


def state(value: Any) -> str:
    """Which of the three a derived value is in."""
    if value is None:
        return UNDERIVABLE
    if value == "" or value == []:
        return ABSENT
    return DERIVED


def classify(pull: dict[str, Any], perms: dict[str, Any] | None) -> dict[str, Any]:
    """Whether Phases 4 and 5 may run, and why not when they may not.

    Two separate questions, and both have to answer yes: did a bot open this, and
    is this a repository you control. Dependabot and Renovate push their branches
    *into* the repository, so a bump arriving from a fork did not come from the
    bot — and a PR you cannot merge is one whose code you had no plan to run,
    where the usual defence ("CI would run it anyway") stops holding: CI runs it
    in a fresh container with a scoped token.
    """
    author = ((pull.get("user") or {}).get("login")) or ""
    cross = bool((pull.get("head") or {}).get("repo", {}).get("fork"))
    reasons: list[str] = []
    if cross:
        reasons.append("cross-repository: a fork PR, and neither bot opens one")
    if author not in BOTS:
        reasons.append(f"author is `{author}`, not a bot — a human PR shaped like a bump")
    if perms is None:
        reasons.append("permissions underivable — cannot establish that you control this repo")
    elif not perms.get("push"):
        # One string, not two lines. `findings` quotes the first reason verbatim,
        # and a reason split across entries truncates there into half a sentence.
        reasons.append(
            "no `push` here — a repo you cannot merge into is one whose code you had no plan to run"
        )
    return {
        "author": author,
        "is_bot": author in BOTS,
        "cross_repository": cross,
        "execute": not reasons,
        "reasons": reasons,
    }


def branch_point(
    owner: str,
    name: str,
    number: int,
    base_sha: str,
    commits: list[dict[str, Any]] | None,
    *,
    bot_authored: bool,
) -> dict[str, Any]:
    """Whether `$BASE_SHA` is where the bot branched, and what says so.

    `git merge-base` always returns *a* commit, and when the base branch has been
    rewritten under an open PR it returns one that is far too old — silently, with
    every later phase consuming it as fact. Three signals, and they are not
    interchangeable:

      a `base_ref_force_pushed` event
          the base was rewritten. **The authority** — GitHub states it, with an
          actor and a timestamp.
      a non-bot commit above the base with ONE parent
          corroborates a rewritten base; not sufficient alone.
      a non-bot commit above the base with TWO
          someone merged the base *into* the bot's branch. The base is still the
          branch point, and the substitutions must **not** fire.

    That last case is why the author scan is corroboration rather than the test.
    Measured on `cli/cli` #14049, whose head is exactly that merge: zero
    force-push events, and a correct two-file scope diff from the merge base. Read
    as a moved base it would substitute the `pr-<N>^` diff — 20 files, 1,101 lines
    — and halt the audit on a bump that changes four workflow lines.
    """
    events = _json_or_none([
        "api", f"repos/{owner}/{name}/issues/{number}/events", "--paginate",
    ])  # fmt: skip
    forced = (
        None if events is None else [e for e in events if e.get("event") == "base_ref_force_pushed"]
    )

    above: list[dict[str, Any]] = []
    head_is_merge = False
    if commits:
        for index, commit in enumerate(commits):
            author = (commit.get("author") or {}).get("login") or (
                (commit.get("commit") or {}).get("author", {}).get("name")
            )
            parents = len(commit.get("parents") or [])
            if index == len(commits) - 1:
                head_is_merge = parents > 1
            above.append({
                # Full length. The report abbreviates for reading; Phase 1 hands
                # these to `git show`, and Phase 0's rule against transcribing a
                # 40-character SHA by hand is the same rule one artifact along.
                "sha": commit.get("sha", ""),
                "author": author or "(unknown)",
                "parents": parents,
                "bot": (author or "") in BOTS,
                "subject": ((commit.get("commit") or {}).get("message") or "").split("\n")[0],
            })  # fmt: skip

    # Only meaningful on a bot PR. "A genuine bot PR is one commit by the bot" is
    # the expectation a human commit departs from — on a *human* PR, human commits
    # are the definition of the PR rather than an anomaly. Found by replaying this
    # plugin's own #26: five human commits, no force-push, reported SUSPECT on a
    # branch nobody had touched. Applied to human PRs it fires on every one, which
    # is the fastest way to train a reader to skip the row that matters.
    foreign = [c for c in above if not c["bot"] and c["parents"] == 1] if bot_authored else []

    if forced is None:
        verdict, why = UNDERIVABLE, "the PR's event list could not be read"
    elif forced:
        actor = forced[-1].get("actor", {}).get("login", "(unknown)")
        when = forced[-1].get("created_at", "(unknown)")
        verdict = "rewritten"
        why = f"base_ref_force_pushed by {actor} at {when} — GitHub says so"
    elif head_is_merge:
        verdict = "ok"
        why = (
            "the head is a merge commit, so `pr-<N>^` is the branch *tip*, not the "
            "branch point — the merge base is correct and must NOT be substituted"
        )
    elif foreign:
        verdict = "suspect"
        why = (
            f"{len(foreign)} non-bot commit(s) above the base and no force-push "
            "event; corroboration without the authority"
        )
    elif bot_authored:
        verdict, why = "ok", "no force-push event, and every commit above the base is the bot's"
    else:
        # The `ok` above is about the bot's branch shape and cannot be claimed
        # here: a human PR has no bot commits to say it of. Suppressing the
        # corroboration scan for human PRs and leaving this text was a correct
        # verdict carried by a false sentence — the same family as a red check
        # reported without its attribution.
        verdict = "ok"
        why = (
            "no force-push event; this is not a bot PR, so the one-commit-by-the-bot "
            "expectation the corroboration scan tests does not apply"
        )

    return {
        "verdict": verdict,
        "why": why,
        "base_sha": base_sha,
        "head_is_merge_commit": head_is_merge,
        "commits_above_base": above,
        # Three states here too: `above` is empty both when the branch genuinely
        # carries nothing above the base and when the call failed. Phase 1 gates
        # on this split, and an empty gate list passes trivially — so which of
        # the two it is has to survive to the shell output.
        "commits_underivable": commits is None,
        "force_pushes": None if forced is None else len(forced),
    }


def _changed_lines(patch: str) -> list[str]:
    """The lines a patch added or removed, sign stripped, headers dropped."""
    out: list[str] = []
    for line in patch.splitlines():
        if not line or line.startswith(("+++", "---", "@@")):
            continue
        if line[0] in "+-":
            out.append(line[1:])
    return out


def bump_files(
    owner: str,
    name: str,
    bot_commits: list[str],
    compare: dict[str, Any] | None,
    *,
    commits_underivable: bool,
    rewritten: bool,
) -> tuple[list[dict[str, Any]] | None, str]:
    """What the bump changed, or None and why it could not be established.

    Phase 1 gates on **the bot's own commits**, not on the branch: a maintainer
    can land the fixup the bump requires on the bot's branch, and gated on the
    union that reads as a bump reaching into source. So each bot commit is read
    on its own, which is also what makes this immune to a rewritten base — the
    commit carries its own diff and no range is involved.

    The fallback is the whole `$BASE_SHA..pr-<N>` diff, and it is the right
    fallback rather than the right default. Under a **rewritten** base that diff
    is the entire divergence — the input that once reported a two-file bump as
    fourteen files and 3,682 deletions — so there it is refused outright.
    """
    if bot_commits:
        merged: dict[str, dict[str, Any]] = {}
        for sha in bot_commits:
            body = _json_or_none(["api", f"repos/{owner}/{name}/commits/{sha}"])
            if body is None or not isinstance(body.get("files"), list):
                return None, f"the file list for the bot's commit {sha[:9]} could not be read"
            files = body["files"]
            if len(files) >= FILES_CAP:
                return None, (
                    f"commit {sha[:9]} reports {len(files)} files, at the API's cap of "
                    f"{FILES_CAP} — the rest are absent, not shown to be in scope"
                )
            for entry in files:
                filename = entry.get("filename") or ""
                # A file touched by two of the bot's commits arrives twice, and
                # the gate has to see every line either changed — so the patches
                # accumulate rather than the later record winning.
                if filename in merged:
                    have, incoming = merged[filename].get("patch"), entry.get("patch")
                    # Withheld on either side is withheld for the union. Keeping
                    # the half that *is* readable reads as "these are the lines
                    # that changed", and the lines the API never sent are the
                    # ones the gate would have objected to.
                    merged[filename]["patch"] = (
                        None if have is None or incoming is None else f"{have}\n{incoming}"
                    )
                else:
                    merged[filename] = dict(entry)
        return list(merged.values()), "the bot's own commits"

    why_split = (
        "the authorship split was underivable"
        if commits_underivable
        else "no commit above the base is the bot's"
    )
    if rewritten:
        return None, (
            f"{why_split}, and the base was rewritten — the whole-diff fallback would be "
            "the entire divergence rather than the bump"
        )
    if compare is None or not isinstance(compare.get("files"), list):
        return None, f"{why_split}, and the PR's own file list could not be read"
    files = compare["files"]
    if len(files) >= FILES_CAP:
        return None, (
            f"{why_split}, and the diff reports {len(files)} files, at the API's cap of {FILES_CAP}"
        )
    return files, f"the whole $BASE_SHA..pr-<N> diff — {why_split}"


def _ecosystem(names: list[str]) -> tuple[str, str]:
    """Which Phase 1 method applies, from the filenames alone."""
    for filename in names:
        base = filename.rsplit("/", 1)[-1]
        if base in UNSUPPORTED_LOCKFILES:
            return "unsupported", (
                f"the diff carries a {base} ({UNSUPPORTED_LOCKFILES[base]}). This plugin "
                "verifies uv.lock, GitHub Actions and pre-commit end to end and nothing "
                "else — report "
                "that boundary, and do not improvise a recipe from the shape of the ones "
                "that are here"
            )
    if any(filename.rsplit("/", 1)[-1] == "uv.lock" for filename in names):
        return "uv.lock", ""
    if all(filename.startswith(WORKFLOW_DIRS) for filename in names):
        return "github-actions", ""
    # After both, and the order is the answer to a real case: a repo that pins
    # its tools in `uv.lock` *and* runs them through pre-commit gets the
    # ecosystem with the artifact hashes, which is the stronger Phase 1 method.
    if any(filename.rsplit("/", 1)[-1] == PRE_COMMIT_CONFIG for filename in names):
        return "pre-commit", ""
    return "unknown", ""


# The two ecosystems whose pin is a *line* rather than a file. Both gates are the
# same shape and differ only in which line is allowed, so they share `_line_gate`
# below: an action is `uses:`, a pre-commit hook is `rev:`.
PIN_LINE = {"github-actions": ("`uses:`", USES_LINE), "pre-commit": ("`rev:`", REV_LINE)}


def _line_gate(
    files: list[dict[str, Any]], ecosystem: str, common: dict[str, Any]
) -> dict[str, Any]:
    """Phase 1's gate for an ecosystem pinned by a line, not by a file.

    The lockfile rule reads names; this one reads *lines*, so this is the only
    shape that needs the patch — and a withheld patch is therefore only
    underivable here.
    """
    what, pattern = PIN_LINE[ecosystem]
    withheld = [entry.get("filename") or "" for entry in files if entry.get("patch") is None]
    if withheld:
        return {
            "verdict": UNDERIVABLE,
            "ecosystem": ecosystem,
            "beyond": [],
            "why": (
                f"the API withheld the patch for {', '.join(sorted(withheld))} — binary, "
                "or past its size limit. No lines to read is not 'no lines beyond the pin'"
            ),
            **common,
        }
    beyond = []
    comments = 0
    for entry in files:
        for line in _changed_lines(entry.get("patch") or ""):
            if pattern.match(line):
                continue
            if COMMENT_LINE.match(line):
                comments += 1
                continue
            beyond.append(f"{entry.get('filename')}: {line.strip()}")
    return {
        **common,
        "verdict": "beyond" if beyond else "clean",
        "ecosystem": ecosystem,
        "beyond": beyond,
        # After the spread: `common` carries the 0 every other branch wants, and a
        # key repeated in a literal takes its last value.
        "comment_lines": comments,
        "why": (
            f"a changed line is neither a {what} pin nor a comment"
            if beyond
            else f"every changed line across every file is a {what} line or a comment"
        ),
    }


def scope(files: list[dict[str, Any]] | None, source: str) -> dict[str, Any]:
    """Phase 1's gate: does the diff stay inside what a bump is allowed to touch?

    Three states like everything else here, and the third is the one that
    matters. A gate that cannot be evaluated must not answer **clean** — that is
    the answer which lets Phases 4 and 5 run.
    """
    empty: dict[str, Any] = {"files": [], "beyond": [], "comment_lines": 0, "source": source}
    if files is None:
        return {"verdict": UNDERIVABLE, "ecosystem": UNDERIVABLE, "why": source, **empty}

    names = sorted(entry.get("filename") or "" for entry in files)
    if not names:
        return {
            "verdict": UNDERIVABLE,
            "ecosystem": UNDERIVABLE,
            "why": (
                f"no changed files came back from {source}. An empty list objects to "
                "nothing and establishes nothing — the same shape as a gate list that "
                "iterates zero times"
            ),
            **empty,
        }

    ecosystem, why = _ecosystem(names)
    common = {"files": names, "comment_lines": 0, "source": source}

    if ecosystem == "unsupported":
        # Not `beyond`: nothing here says the bump reached into source, only that
        # the rule for judging it is not in this plugin.
        return {"verdict": UNDERIVABLE, "ecosystem": ecosystem, "why": why, "beyond": [], **common}

    if ecosystem == "uv.lock":
        beyond = [n for n in names if n.rsplit("/", 1)[-1] not in MANIFESTS]
        return {
            "verdict": "beyond" if beyond else "clean",
            "ecosystem": ecosystem,
            "beyond": beyond,
            "why": (
                "the diff reaches past the manifest and the lockfile"
                if beyond
                else "every changed file is the manifest or the lockfile"
            ),
            **common,
        }

    if ecosystem in PIN_LINE:
        return _line_gate(files, ecosystem, common)

    beyond = [n for n in names if n.rsplit("/", 1)[-1] not in MANIFESTS]
    if not beyond:
        # Every file is a manifest this plugin knows and none is a lockfile — a
        # `pyproject.toml`-only bump, pip with nothing locked. The filter empties
        # and `beyond` naming nothing is the one verdict a reader cannot act on:
        # it reaches the report as "this bump reaches past the manifest" over a
        # blank list. No rule applied is the honest answer.
        return {
            "verdict": UNDERIVABLE,
            "ecosystem": ecosystem,
            "beyond": [],
            "why": (
                "every changed file is a manifest and none of them is a lockfile this "
                "plugin covers — there is no scope rule to apply, so the gate was not "
                "evaluated. Report the boundary"
            ),
            **common,
        }
    return {
        "verdict": "beyond",
        "ecosystem": ecosystem,
        "beyond": beyond,
        "why": (
            "the diff matches no manifest this plugin knows and is not confined to workflow files"
        ),
        **common,
    }


def discover(owner: str, name: str, number: int) -> dict[str, Any]:
    repo = _required(["api", f"repos/{owner}/{name}"], f"{owner}/{name}")
    pull = _required(["api", f"repos/{owner}/{name}/pulls/{number}"], f"PR #{number}")

    head_sha = (pull.get("head") or {}).get("sha") or ""
    base_ref = (pull.get("base") or {}).get("sha") or ""

    # GitHub's own merge base, which is right whether or not the PR has landed.
    # `git merge-base "$DEFAULT" pr-<N>` is not: once a PR merges, its head is an
    # ancestor of the default branch and the merge base of the two is the head.
    compare = (
        _json_or_none(["api", f"repos/{owner}/{name}/compare/{base_ref}...{head_sha}"])
        if base_ref and head_sha
        else None
    )
    base_sha = ((compare or {}).get("merge_base_commit") or {}).get("sha") if compare else None

    commits = _json_or_none([
        "api", f"repos/{owner}/{name}/pulls/{number}/commits", "--paginate",
    ])  # fmt: skip

    # `.permissions` is absent for an unauthenticated or coarse read rather than
    # false, so None here means underivable and must not read as `pull`-only.
    perms = repo.get("permissions")
    classification = classify(pull, perms)

    bp = branch_point(
        owner,
        name,
        number,
        base_sha or "",
        commits,
        bot_authored=classification["is_bot"],
    )
    files, source = bump_files(
        owner,
        name,
        [c["sha"] for c in bp["commits_above_base"] if c["bot"]],
        compare,
        commits_underivable=bp["commits_underivable"],
        rewritten=bp["verdict"] == "rewritten",
    )

    return {
        "owner": owner,
        "name": name,
        "number": number,
        "default_branch": repo.get("default_branch") or "",
        "head_sha": head_sha,
        "base_ref": base_ref,
        "base_sha": base_sha,
        "pr_state": "MERGED" if pull.get("merged") else (pull.get("state") or "").upper(),
        "created_at": pull.get("created_at") or "",
        "perms": perms,
        "classification": classification,
        "branch_point": bp,
        "scope": scope(files, source),
    }


def render(report: dict[str, Any]) -> None:
    bp = report["branch_point"]
    cls = report["classification"]

    print(f"{report['owner']}/{report['name']}#{report['number']}  [{report['pr_state']}]\n")

    rows = [
        ("$DEFAULT", report["default_branch"]),
        ("$HEAD_SHA", report["head_sha"]),
        ("$BASE_REF", report["base_ref"]),
        ("$BASE_SHA", report["base_sha"]),
        ("$OWNER/$NAME", f"{report['owner']}/{report['name']}"),
        ("createdAt", report["created_at"]),
    ]
    for label, value in rows:
        tag = state(value)
        mark = "OK " if tag == DERIVED else ("-- " if tag == ABSENT else "!! ")
        print(f"  {mark}{label:14} {value if value else f'({tag})':<44} {tag}")

    perms = report["perms"]
    if perms is None:
        print(f"  !! {'$PERMS':14} {'(underivable)':<44} underivable")
        print("     The call failed. `gh` writes an API error body to stdout, so a")
        print("     capture succeeds and `push` reads as false — indistinguishable")
        print("     from a pull-only account. Failing closed is right; reporting")
        print("     'you lack push here' when it could not tell is not.")
    else:
        flags = " ".join(f"{k}={str(v).lower()}" for k, v in sorted(perms.items()))
        print(f"  OK {'$PERMS':14} {flags:<44} derived")

    print(f"\n=== branch point: {bp['verdict'].upper()}")
    print(f"    {bp['why']}")
    if bp["force_pushes"]:
        print(f"    {bp['force_pushes']} force-push event(s) on this PR")
    if bp["commits_above_base"]:
        print("    commits above the base:")
        for c in bp["commits_above_base"]:
            kind = "bot" if c["bot"] else "HUMAN"
            print(f"      {c['sha'][:9]}  parents={c['parents']}  {kind:5}  {c['subject'][:46]}")
        if any(not c["bot"] for c in bp["commits_above_base"]):
            print("\n    A HUMAN commit sits on this branch. Phase 1 gates on $BOT_COMMITS,")
            print("    not on the merge-base diff: a maintainer landing the fixup the bump")
            print("    requires is a finding to report, and never a Hold. $HUMAN_COMMITS")
            print("    carries the other half — read it before merging.")

    if bp["verdict"] == "rewritten":
        print("\n    SUBSTITUTE, and report the rewritten base as its own finding:")
        print("      Phase 1's gate is unaffected — it reads the bot's own commits")
        print("      Phase 4 measures in $SCRATCH/tip-<N>, not $SCRATCH/base-<N>")
        print("    'The base branch was rewritten' and 'this bump reaches beyond the")
        print("    manifest' produce the same diff and are not the same finding.")
    elif bp["head_is_merge_commit"]:
        print("\n    Do NOT substitute. `pr-<N>^` is the branch tip here, and its diff")
        print("    is the whole divergence rather than the bump.")
    elif bp["verdict"] == "suspect":
        print("\n    Not sufficient to substitute on: a merge of the base *into* the")
        print("    branch looks similar and leaves the merge base correct. Read the")
        print("    commits above before deciding.")

    sc = report["scope"]
    print(f"\n=== scope: {sc['verdict'].upper()}  [{sc['ecosystem']}]")
    print(f"    {sc['why']}")
    print(f"    read from {sc['source']}")
    for filename in sc["files"]:
        print(f"      {filename}")
    if sc["comment_lines"]:
        print(f"    {sc['comment_lines']} changed line(s) are comments, inside the pin but")
        print("    worth reading: a compiler that emits workflows records its pins in")
        print("    one, and a generated file makes this bump transient (Phase 7 row).")
    if sc["beyond"] and sc["beyond"] != sc["files"]:
        # Suppressed when they are the same list: on an unrecognised manifest
        # every file is beyond the pin, and printing the set twice pads the one
        # output a reader is most likely to quote into the report.
        print("    beyond the pin:")
        for line in sc["beyond"]:
            print(f"      {line}")
    if sc["beyond"] or sc["verdict"] == "beyond":
        print("\n    Phase 1 is a gate. Report this and STOP before Phase 4 — the")
        print("    read-only phases refusing to hand a shell to the PR is what the")
        print("    ordering buys, and continuing anyway spends it for nothing.")
    elif sc["verdict"] == UNDERIVABLE:
        print("\n    Not a clean scope. The gate could not be evaluated, and `clean`")
        print("    is the answer that lets Phases 4 and 5 run — say so in the report")
        print("    rather than reading silence as nothing to object to.")

    print(f"\n=== execution: {'PHASES 4 AND 5 MAY RUN' if cls['execute'] else 'USE --no-execute'}")
    print(f"    author {cls['author']}, cross-repository={str(cls['cross_repository']).lower()}")
    for reason in cls["reasons"]:
        print(f"    - {reason}")
    if not cls["execute"]:
        print("    Those phases run code from the PR. Say in the report that they")
        print("    did not run, and what running them would have added.")

    findings = report["findings"]
    print(f"\nRESULT: {'NEEDS REVIEW' if findings else 'ORDINARY'} — {len(findings)} finding(s)")
    for f in findings:
        print(f"        - {f}")
    print("Phase 0's derivation half only. Classifying the bot config, and reading")
    print("this repo's own gates and their scopes, stay in SKILL.md.")


def analyse(report: dict[str, Any]) -> dict[str, Any]:
    findings: list[str] = []
    bp, cls = report["branch_point"], report["classification"]
    for label, value in (
        ("$DEFAULT", report["default_branch"]),
        ("$HEAD_SHA", report["head_sha"]),
        ("$BASE_SHA", report["base_sha"]),
    ):
        if state(value) != DERIVED:
            findings.append(f"{label} is {state(value)} — no later phase may consume it")
    if report["perms"] is None:
        findings.append("$PERMS underivable")
    if bp["verdict"] == "rewritten":
        findings.append("the base branch was rewritten under this PR")
    elif bp["verdict"] == UNDERIVABLE:
        findings.append("could not establish whether the merge base is the branch point")
    elif bp["verdict"] == "suspect":
        findings.append("non-bot commits above the base, with no force-push event")
    if not cls["execute"]:
        findings.append("Phases 4 and 5 must not run: " + "; ".join(cls["reasons"][:1]))
    sc = report["scope"]
    if sc["verdict"] == "beyond":
        findings.append("Phase 1's scope gate fired: " + sc["why"])
    elif sc["verdict"] == UNDERIVABLE:
        findings.append("Phase 1's scope gate could not be evaluated: " + sc["why"])
    report["findings"] = findings
    return report


def shell(report: dict[str, Any]) -> None:
    """Phase 0's outputs as `NAME=value`, for sourcing.

    The alternative is the reader copying four 40-character SHAs out of a table
    by hand, into commands where a wrong one is not detectable: a truncated
    `$HEAD_SHA` matches no CI run and reads exactly like "CI never ran", and a
    wrong `$BASE_SHA` produces a scope diff that is wrong rather than empty.

    **Only derived values are emitted.** An underivable output is written as a
    commented line, so sourcing this leaves the variable unset and a later phase
    fails loudly on an empty value instead of quietly on a plausible one — which
    is the whole distinction Phase 0 exists to preserve.
    """
    pairs = [
        ("DEFAULT", report["default_branch"]),
        ("HEAD_SHA", report["head_sha"]),
        ("BASE_REF", report["base_ref"]),
        ("BASE_SHA", report["base_sha"]),
        ("OWNER", report["owner"]),
        ("NAME", report["name"]),
        # Phase 2's other half. The cooldown question is whether the release was
        # less than three days old *when the bot opened the PR*, so it needs the
        # PR's own timestamp and not only the release's. It was rendered in the
        # report and never emitted, which made it an input that crossed by being
        # on screen.
        ("CREATED_AT", report["created_at"]),
    ]
    print("# Phase 0 outputs. Sourced, not transcribed.")

    # Where this plugin's scripts live, derived from the file emitting the line
    # rather than named by anyone. It is the one output that is not about the PR,
    # and it is here because it is the only place the answer is known for certain.
    #
    # `${CLAUDE_PLUGIN_ROOT}` is substituted into `SKILL.md`'s *text* at skill
    # load, so it resolves there and nowhere else. `references/*.md` are read off
    # disk; the token reaches the shell intact, where the variable is empty and
    # the path collapses to `/skills/dependabot-audit/scripts/…`. Every reference
    # block already reloads this handoff, so this is what lets one name a script.
    #
    # Deliberately not routed through `state()`: the other outputs can be
    # underivable, and this one cannot — the script answering is the script being
    # located. That also settles the version question 0.23.0 raised, where an
    # invented `export CLAUDE_PLUGIN_ROOT=…/0.22.1` pinned a release into a cache
    # that keeps every older copy and ran a stale plugin silently. A path taken
    # from the running file cannot name a version other than the one running.
    print(f"SCRIPTS={os.path.dirname(os.path.realpath(__file__))}")

    for key, value in pairs:
        if state(value) == DERIVED:
            print(f"{key}={value}")
        else:
            print(f"# {key} is {state(value)} — deliberately unset, so a later")
            print("#   phase fails on an empty value rather than a plausible one")
    bp, cls, sc = report["branch_point"], report["classification"], report["scope"]
    print(f"BRANCH_POINT={bp['verdict']}")
    print(f"MAY_EXECUTE={'yes' if cls['execute'] else 'no'}")
    # Phase 1's gate and the ecosystem it was judged under. Both were the
    # reader's, and both decide whether Phases 4 and 5 get a shell.
    print(f"ECOSYSTEM={sc['ecosystem']}")
    print(f"SCOPE_GATE={sc['verdict']}")
    if sc["verdict"] != "clean":
        print("#   Not clean. Phase 1 stops the audit here; see the report output")
        print("#   for which files or lines, and say so rather than continuing")

    # Phase 1's scope gate is about what the *bump* changed, and a bot PR's
    # branch is not always all bot: a maintainer can land the fixup the bump
    # requires on the bot's own branch, so a required check goes green. Gating on
    # the union calls that a Hold. Both halves are already derived above.
    #
    # Emitted through `state()` like every other output, and here that matters
    # more than anywhere else: an *empty* BOT_COMMITS makes `for c in
    # $BOT_COMMITS` iterate zero times, so the gate passes trivially — clean
    # rather than erroring, on the one phase whose whole job is to refuse.
    # Only the human half crosses. `$BOT_COMMITS` was emitted for Phase 1 to
    # iterate, and `$SCOPE_GATE` above is the answer that loop was computing — so
    # emitting it as well would leave the shell holding everything it needs to
    # roll a second gate by hand, which is how the prose copy and the script copy
    # drift. It stays in the report output and in `--json`, where it is evidence
    # rather than an input. The human half has no script consumer and still does.
    humans = " ".join(c["sha"] for c in bp["commits_above_base"] if not c["bot"])
    if not bp["commits_underivable"] and state(humans) == DERIVED:
        print(f'HUMAN_COMMITS="{humans}"')
    else:
        print(f"# HUMAN_COMMITS is {'underivable' if bp['commits_underivable'] else ABSENT}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, metavar="OWNER/NAME")
    parser.add_argument("--number", required=True, type=int)
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--shell", action="store_true", help="emit NAME=value for sourcing, instead of a report"
    )
    args = parser.parse_args()

    if "/" not in args.repo:
        fail(f"--repo takes OWNER/NAME, got {args.repo!r}")
    owner, _, name = args.repo.partition("/")

    report = analyse(discover(owner, name, args.number))
    if args.json:
        print(json.dumps(report, indent=2))
    elif args.shell:
        shell(report)
    else:
        render(report)
    return 1 if report["findings"] else 0


def cli() -> NoReturn:
    """Entry point. Anything unforeseen becomes exit 2, never exit 1.

    Exit 1 here means Phase 0 found something that changes the shape of the audit.
    An unhandled exception exits 1 too, so without this a crash reads as "the base
    was rewritten" — and the inputs are API responses, the shape least entitled to
    be well-formed.

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
