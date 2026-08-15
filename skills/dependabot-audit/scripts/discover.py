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
import subprocess
import sys
from typing import Any, NoReturn

TIMEOUT = 60

BOTS = frozenset({"dependabot[bot]", "renovate[bot]", "dependabot", "renovate"})

# Three states, and the third is the whole reason this script exists.
DERIVED = "derived"
ABSENT = "absent"
UNDERIVABLE = "underivable"


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
                "sha": commit.get("sha", "")[:9],
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
        "force_pushes": None if forced is None else len(forced),
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
        "branch_point": branch_point(
            owner,
            name,
            number,
            base_sha or "",
            commits,
            bot_authored=classification["is_bot"],
        ),
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
            print(f"      {c['sha']}  parents={c['parents']}  {kind:5}  {c['subject'][:46]}")

    if bp["verdict"] == "rewritten":
        print("\n    SUBSTITUTE, and report the rewritten base as its own finding:")
        print("      Phase 1 takes its scope diff from `pr-<N>^..pr-<N>`")
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
    ]
    print("# Phase 0 outputs. Sourced, not transcribed.")
    for key, value in pairs:
        if state(value) == DERIVED:
            print(f"{key}={value}")
        else:
            print(f"# {key} is {state(value)} — deliberately unset, so a later")
            print("#   phase fails on an empty value rather than a plausible one")
    bp, cls = report["branch_point"], report["classification"]
    print(f"BRANCH_POINT={bp['verdict']}")
    print(f"MAY_EXECUTE={'yes' if cls['execute'] else 'no'}")


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
