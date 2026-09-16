#!/usr/bin/env python3
"""Phase 6, mechanised: what CI says about this commit, and whether the bump did it.

Three questions, and the prose asked a reader to hold all three apart by hand:

1. **Which checks gate merge?** `isRequired` per context, evaluated by GitHub for
   this PR against whatever enforces it, readable at `pull`. Never an authored
   list — one shipped with a specific repo's check names filled in, and reused
   elsewhere it matched nothing, which reads exactly like "no required checks".
2. **Does anything still block?** `mergeStateStatus`, because `isRequired` only
   sees contexts that *reported* — a required check that never ran is absent from
   the list entirely, and no count catches it.
3. **Is a red check the bump's fault?** Compared against the commit the bot
   branched from, which is `pr-<N>^` and not the merge base.

Why a script rather than prose: three of the seven defects that have shipped in
`SKILL.md` were in this phase, and all three were the *same* kind of mistake —
asking a real endpoint the wrong question, and getting a well-formed answer back.
A hand-run query cannot be regression-tested; this can. CONTRIBUTING puts it as
"a trap a script refuses cannot be skipped, one in prose is silently skipped".

Exit codes follow the other scripts here, and the distinction is load-bearing:

    0   ran, and the CI state carries no finding
    1   ran, and found something
    2   could not run

An unhandled exception exits 1, which would read as "CI is red". `cli()` is the
backstop that stops that.

    python3 ci_state.py --owner OWNER --name NAME --number N \\
        --head-sha SHA --parent SHA [--base-sha SHA] [--json]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import textwrap
from datetime import datetime
from typing import Any, NoReturn

TIMEOUT = 120

# What counts as a failing result, across both context shapes. CheckRun uses
# `conclusion` and StatusContext uses `state`, with different vocabularies:
# a CheckRun says FAILURE/TIMED_OUT/CANCELLED, a StatusContext says FAILURE/ERROR.
FAILING = frozenset({"FAILURE", "TIMED_OUT", "CANCELLED", "STARTUP_FAILURE", "ERROR"})

# Not failures. NEUTRAL and SKIPPED are the normal result of a security scan on a
# diff that does not touch the scanned surface — treating them as red makes the
# row noise on most bumps, which trains the reader to skip the row that matters.
PASSING = frozenset({"SUCCESS", "NEUTRAL", "SKIPPED", "EXPECTED"})

# `mergeStateStatus`, classified **exhaustively**. The enum is closed — eight
# values, introspected — and both sets are written out so that anything else is
# neither blocking nor mergeable but *unrecognised*, which routes to underivable
# below. The previous shape was an allowlist of three with everything else
# falling through to "nothing blocks", and that is how `BEHIND` came to be read
# as a clear merge: not in the set, therefore fine.
#
#   BEHIND      blocks. The head is out of date, and GitHub returns it only where
#               the base *requires* branches to be up to date — so it is evidence
#               the repo enforces something, never the absence of enforcement.
#               The ordinary cause is a sibling merge: land one bot PR and every
#               other one goes BEHIND. Remedied by updating the branch, which
#               moves the head and invalidates every row of an audit in flight.
#   BLOCKED     blocks. Something gates it that the context list may not show.
#   DIRTY       blocks. The merge commit cannot be cleanly created.
#   DRAFT       blocks, on the PR's own state rather than the repo's rules.
#   CLEAN       mergeable, and passing.
#   HAS_HOOKS   mergeable; pre-receive hooks run on merge. Not a gate we can read.
#   UNSTABLE    mergeable: every *required* check is green and something
#               non-required is unsettled.
#   UNKNOWN     neither. Computed lazily, and what a merged PR returns — "not
#               established", which is a third answer and not either of the two.
BLOCKING = frozenset({"BEHIND", "BLOCKED", "DIRTY", "DRAFT"})
MERGEABLE = frozenset({"CLEAN", "HAS_HOOKS", "UNSTABLE"})

# What the two sets above cover between them.
CLASSIFIED = BLOCKING | MERGEABLE

# Read, and deliberately in neither set: "not established" is a third answer
# about the merge, not a third kind of merge state. Separate from CLASSIFIED
# because the two are asked different questions — `blocked` asks what is in
# CLASSIFIED, "have I heard of this?" asks what is in KNOWN. Collapsing them
# reported every merged PR, which returns UNKNOWN, as an unrecognised value:
# caught on a live replay of fpga-board-sim#437, after two unit tests passed it
# because the wrong branch's text also contains "not established".
UNESTABLISHED = frozenset({"UNKNOWN"})

# Every value this script has actually read. Outside it is one GitHub added after
# this was written, and the whole point of classifying exhaustively is that such a
# value reads as "not established" rather than as "nothing blocks".
# `integration/test_live_merge_state.py` is what notices it arriving.
KNOWN = CLASSIFIED | UNESTABLISHED

ROLLUP_QUERY = """
query($owner:String!, $name:String!, $number:Int!, $cursor:String) {
  repository(owner:$owner, name:$name) {
    pullRequest(number:$number) {
      mergeable mergeStateStatus reviewDecision merged
      # `DRAFT` in mergeStateStatus is deprecated -- "DRAFT state will be removed
      # from this enum and `isDraft` should be used instead", with an announced
      # removal of 2021-01-01 that has not happened. Both are read: the enum
      # while it still answers, this for when it stops. Found by the enum
      # tripwire in `integration/test_live_merge_state.py` on its first run.
      isDraft
      baseRef { name target { ... on Commit { oid committedDate } } }
      commits(last:1) { nodes { commit { oid committedDate statusCheckRollup { state
        contexts(first:100, after:$cursor) {
          totalCount
          pageInfo { hasNextPage endCursor }
          nodes {
            # The start time is `base_drift`'s only input, and the two shapes
            # name the same thing differently. Wrapped, not shortened: the
            # columns are what make the two lines comparable at a glance.
            ... on CheckRun      { name    conclusion startedAt
                                   isRequired(pullRequestNumber:$number) }
            ... on StatusContext { context state      createdAt
                                   isRequired(pullRequestNumber:$number) }
          }
        } } } } }
    }
  }
}
"""


def fail(what: str) -> NoReturn:
    """Exit 2. Reserved for "could not run", never for "ran and found something"."""
    print(f"error: {what}", file=sys.stderr)
    raise SystemExit(2)


def _gh(args: list[str]) -> str:
    """Run `gh` and return stdout, or exit 2 saying which call failed.

    `gh` writes an API error body to **stdout** and still exits non-zero, so the
    exit code is the signal and the body is the explanation. Reading the body
    without the exit code is how a 404 became a well-formed "no required checks"
    once already — see Phase 0's note on branch protection.
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
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        fail(f"`gh {' '.join(args[:2])}` failed: {detail[0] if detail else 'no output'}")
    return proc.stdout


def _gh_json(args: list[str]) -> Any:
    try:
        return json.loads(_gh(args))
    except json.JSONDecodeError as exc:
        fail(f"`gh {' '.join(args[:2])}` returned unparseable JSON: {exc}")


def _gh_lines(args: list[str]) -> list[Any]:
    """A `--jq` filter emitting one value per line, parsed. Empty is a real answer."""
    out = _gh(args).strip()
    if not out:
        return []
    try:
        return [json.loads(line) for line in out.splitlines() if line.strip()]
    except json.JSONDecodeError as exc:
        fail(f"`gh {' '.join(args[:2])}` returned unparseable JSON lines: {exc}")


def _gh_soft(args: list[str]) -> str:
    """stdout, or "" when the call failed. For reads that qualify a row.

    `_gh` exits 2 on any failure, which is right for the calls the phase cannot
    proceed without. The attribution interval is not one of them: it is a hedge
    on a claim rather than the claim, so a read that cannot be made must weaken
    the row and never turn Phase 6 into a "could not run".
    """
    try:
        return _gh(args)
    except SystemExit:
        return ""


def committed_at(owner: str, name: str, sha: str) -> str:
    """A commit's own timestamp, or "" — the second half of an attribution row.

    The head's comes free with the rollup query; a comparison point needs this.
    """
    if not sha:
        return ""
    return _gh_soft([
        "api", f"repos/{owner}/{name}/commits/{sha}", "--jq", ".commit.committer.date",
    ]).strip()  # fmt: skip


def interval(later: str, earlier: str) -> str:
    """ "3d 17h" between two ISO-8601 timestamps, or "" if that is not derivable.

    Empty covers three cases deliberately, and the caller says so rather than
    printing a number it does not have: either timestamp missing, either
    unparseable, and `earlier` not actually earlier — which a rebase produces,
    since it rewrites committer dates, and where the span means nothing.
    """
    if not later or not earlier:
        return ""
    try:
        delta = datetime.fromisoformat(later) - datetime.fromisoformat(earlier)
    except ValueError:
        return ""
    seconds = int(delta.total_seconds())
    if seconds < 0:
        return ""
    if seconds < 60:
        return "under a minute"
    minutes, hours = seconds // 60 % 60, seconds // 3600
    if hours < 1:
        return f"{minutes}m"
    if hours < 24:
        return f"{hours}h {minutes}m"
    return f"{hours // 24}d {hours % 24}h"


def _span(span: str | None) -> str:
    """How the interval reads in a basis line, including when it is not one.

    "interval underivable" rather than silence: a missing span must not be
    indistinguishable from a tight one, which is the same complaint that put the
    interval on this row in the first place.
    """
    return f"{span} earlier" if span else "interval underivable"


def _context(node: dict[str, Any]) -> dict[str, Any] | None:
    """Normalise the two context shapes into one, or None for an empty node.

    A CheckRun carries `name`/`conclusion`; a StatusContext carries
    `context`/`state`. They live in the same list and are routinely conflated —
    and a red StatusContext is invisible to the check-runs endpoint, which is why
    the base comparison below reads both.
    """
    name = node.get("name") or node.get("context")
    if not name:
        return None
    result = node.get("conclusion") or node.get("state") or ""
    return {
        "name": name,
        "result": (result or "PENDING").upper(),
        "required": bool(node.get("isRequired")),
        "kind": "CheckRun" if node.get("name") else "StatusContext",
        # The two shapes name it differently, and reading only `startedAt` leaves
        # every StatusContext permanently underivable in `base_drift` below.
        "started": node.get("startedAt") or node.get("createdAt") or "",
    }


def rollup(owner: str, name: str, number: int) -> dict[str, Any]:
    """The PR's merge state and every reported context, paginated to exhaustion.

    `contexts(first:100)` is a page, not the answer. A repo reporting more than a
    hundred returns the first hundred and says nothing about the rest, so a
    required check at position 101 is absent — indistinguishable from one that
    passed, which is the same failure as the hand-joined required list this query
    replaced, one level up.
    """
    contexts: list[dict[str, Any]] = []
    cursor: str | None = None
    head_oid = ""
    head_committed = ""
    total = 0
    state = ""
    pull: dict[str, Any] = {}
    while True:
        args = [
            "api", "graphql", "-f", f"query={ROLLUP_QUERY}",
            "-F", f"owner={owner}", "-F", f"name={name}", "-F", f"number={number}",
        ]  # fmt: skip
        if cursor:
            args += ["-F", f"cursor={cursor}"]
        data = _gh_json(args)
        pull = (((data.get("data") or {}).get("repository") or {}).get("pullRequest")) or {}
        if not pull:
            fail(f"no pull request {owner}/{name}#{number}, or it is not readable")
        nodes = ((pull.get("commits") or {}).get("nodes")) or []
        if not nodes:
            fail(f"{owner}/{name}#{number} has no commits")
        commit = nodes[0].get("commit") or {}
        head_oid = commit.get("oid") or ""
        head_committed = commit.get("committedDate") or ""
        rollup_obj = commit.get("statusCheckRollup")
        if not rollup_obj:
            # No checks have reported at all. A real answer, not a failure.
            break
        state = rollup_obj.get("state") or ""
        page = rollup_obj.get("contexts") or {}
        total = page.get("totalCount") or 0
        for node in page.get("nodes") or []:
            got = _context(node)
            if got:
                contexts.append(got)
        info = page.get("pageInfo") or {}
        if not info.get("hasNextPage"):
            break
        cursor = info.get("endCursor")
        if not cursor:
            # hasNextPage without a cursor cannot be followed. Say so rather than
            # looping forever or reporting the partial list as complete.
            break
    base = pull.get("baseRef") or {}
    base_target = base.get("target") or {}
    return {
        "head_oid": head_oid,
        "head_committed": head_committed,
        # The branch this PR merges *into*, as it is now — not `$BASE_SHA`, which
        # is where the PR branched off and does not move. `base_drift` needs the
        # tip, because a fix landing there invalidates the checks below.
        "base_ref": base.get("name") or "",
        "base_oid": base_target.get("oid") or "",
        "base_committed": base_target.get("committedDate") or "",
        "rollup_state": state,
        "merge_state": pull.get("mergeStateStatus") or "",
        # `Boolean!`, so a successful query always carries it. Kept as None when
        # somehow absent rather than coerced: `bool(None)` is False, and "still
        # open" is not something a missing field established.
        "merged": pull.get("merged"),
        # `Boolean!` in the schema, so a successful query always carries it. Kept
        # as None rather than coerced when it is somehow absent: `bool(None)` is
        # False, and "not a draft" is not something a missing field established.
        "is_draft": pull.get("isDraft"),
        "mergeable": pull.get("mergeable") or "",
        "review_decision": pull.get("reviewDecision") or "",
        "contexts": contexts,
        "total_contexts": total,
        # Every context the connection claims, against what we actually hold.
        "complete": total <= len(contexts),
    }


def conclusions_at(owner: str, name: str, sha: str) -> tuple[dict[str, str], bool]:
    """Every check result at a commit, by context name, from **both** lists.

    `gh run list --json name` answers a different question — it returns the
    *workflow* name, one row reading `CI`, while the contexts in the rollup are
    job names like `test (ubuntu-latest)`. Matching one against the other yields
    nothing for every matrix job, and an empty result reads as "no run at the
    base", which marks a real failure underivable. Measured on a repo whose five
    contexts are `Test (Python 3.11)` through `Lint & type-check`: `gh run list`
    returns a single `CI`; `check-runs` returns all five by context name.

    Statuses are a separate list from check runs, and a red StatusContext appears
    only in the second — so reading one of the two answers correctly about half
    the possible reds.
    """
    found: dict[str, str] = {}
    # `has(...)` rather than trusting the projection (#118). `{result: .conclusion}`
    # emits `result: null` both when the check is genuinely pending and when the
    # key is **gone from the payload**, and the two are opposite facts. Collapsed,
    # a renamed key makes every row read PENDING, which is not in `FAILING`, so
    # `attribute()` labels every red check **attributable** — a Hold on the bump,
    # manufactured from a field nobody read. That is the direction its own
    # docstring names as costing least to be wrong in and therefore drawing the
    # least scrutiny, reached without a single failing call.
    runs = _gh_lines([
        "api", f"repos/{owner}/{name}/commits/{sha}/check-runs?per_page=100",
        "--paginate", "--jq",
        '.check_runs[] | {name, result: .conclusion, shaped: has("conclusion")}',
    ])  # fmt: skip
    statuses = _gh_lines([
        "api", f"repos/{owner}/{name}/commits/{sha}/status",
        "--jq", '.statuses[] | {name: .context, result: .state, shaped: has("state")}',
    ])  # fmt: skip
    rows = runs + statuses
    # Rows exist and not one carries the key the comparison reads. Empty is a real
    # answer — a commit with no checks — and only the non-empty case is a finding.
    unshaped = bool(rows) and not any(r.get("shaped") for r in rows)
    for row in rows:
        # A row without the key is dropped rather than defaulted: absent from the
        # map, `attribute()` returns `underivable`, which is the honest answer and
        # the safe direction. Defaulting it to PENDING is the unsafe one.
        if row.get("name") and row.get("shaped"):
            found[row["name"]] = (row.get("result") or "PENDING").upper()
    return found, unshaped


def attribute(
    context: dict[str, Any],
    parent: dict[str, str],
    base: dict[str, str],
    parent_sha: str,
    base_sha: str,
    spans: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Label one red context: attributable, pre-existing, or underivable.

    Three states and never two. "This check is red" is established; "this bump
    broke it" is a causal claim, and a false Hold is the direction that costs
    least to be wrong in and therefore draws the least scrutiny — it looks
    conservative, so nobody goes back to check.

    `pr-<N>^` is `$BASE_SHA` for a genuine one-commit bot PR, so preferring the
    parent costs nothing in the ordinary case and is right in the case that is
    not: a branch carrying a human commit under the bot's, where the merge base
    attributes to the bump everything that happened beneath it.

    **The two labels are not equally strong evidence, and the interval is why.**
    `pre-existing` survives any gap: if the check was already red, the bump is
    exonerated regardless of what else moved in between. `attributable` does not
    — green-then-red across days is consistent with the bump, with an upstream
    change, with a runner image roll, or with a flake, and this comparison
    distinguishes none of them. So the interval travels with the row. Measured on
    `fpga-board-sim` #332, where it was **3d 17h** on a check that re-syncs
    generated sources from other people's repositories.
    """
    spans = spans or {}
    name = context["name"]
    if name in parent:
        pre = parent[name] in FAILING
        return {
            "label": "pre-existing" if pre else "attributable",
            "basis": f"red at {parent_sha[:9]} (pr-<N>^) too" if pre
            else f"green at {parent_sha[:9]} (pr-<N>^), {_span(spans.get('parent'))}",
            "compared": "parent",
            "span": spans.get("parent") or "",
            "weakened": False,
        }  # fmt: skip
    # The parent having no runs at all is common for an intermediate commit of a
    # multi-commit branch, where CI ran on the head and nowhere else. The merge
    # base does have runs, and answers a *different* question: red there means
    # red before this **branch**, not before this **commit**, so everything below
    # the bump is inside the claim.
    if not parent and name in base:
        pre = base[name] in FAILING
        weakened = (
            "red before this *branch*, not this commit" if pre
            else f"a weaker claim than the parent would have given, {_span(spans.get('base'))}"
        )  # fmt: skip
        return {
            "label": "pre-existing" if pre else "attributable",
            "basis": f"no runs at pr-<N>^; compared against $BASE_SHA {base_sha[:9]} — {weakened}",
            "compared": "base",
            "span": spans.get("base") or "",
            "weakened": True,
        }
    return {
        "label": "underivable",
        "basis": "no run at the base, or no check by that name — the commit may "
        "predate the workflow, its run may have aged out, or the check may simply "
        "be named something else there",
        "compared": "none",
        "span": "",
        "weakened": False,
    }


def base_drift(report: dict[str, Any]) -> dict[str, Any]:
    """Did these check results include the base branch as it is **now**?

    CI runs on `refs/pull/<N>/merge` — the base merged with the head — so a commit
    landing on the default branch invalidates every result on the PR, with no
    event on the PR to re-trigger them. `attribute()` cannot see this: its
    comparison is the head against `pr-<N>^` and **both of those commits are
    unchanged**. It labels the row `attributable`, correctly, about a merge that
    no longer exists.

    Observed on this plugin's own #99. The fix for what the check caught merged to
    `main` as `09911c1` at 2026-09-03T17:43:31Z; the red `Lint & type-check` on the
    PR had started at 2026-09-01T01:14:41Z, **2d 16h earlier**, so it could not
    have contained its own fix. A report that stopped at Phase 6 carried a
    substantive Hold on a PR whose only blocker had already been repaired.

    **Why a timestamp and not the merge ref.** `potentialMergeCommit` and
    `refs/pull/<N>/merge` are recomputed lazily, and *querying* `mergeable` is
    what pokes them — so by the time either is read it usually names the current
    base while the check results still do not. Measured on the same PR: the
    parents of `potentialMergeCommit` had already caught up. Commit dates are not
    recomputed by being read, and this needs no local git, which keeps the
    no-fetch property this script is written to.

    Three states, and the third is the one that matters. "Could not compare" must
    not arrive as "every check included the current base" — the same asymmetry as
    `$SCOPE_GATE` and the truncated context list above.

    **A fourth, and it is about the question rather than the answer (#122).** Once
    a PR merges, the base tip *is* its own merge commit, dated at merge time and
    therefore necessarily after its own checks started. So the comparison cannot
    come out any other way for any merged PR, ever — it fired on all 33 contexts
    of `fpga-board-sim` #437 naming `88ec4c267`, which is #437's own merge. The
    row is not wrong, it is **void**: the action it prints, *simulate the merge and
    re-gate*, is about deciding whether to trust these checks before merging, and
    that decision has been made. Replaying merged PRs is most of the live exercise
    this plugin gets, so the loudest signal in the phase was firing on most runs —
    the "trains the reader to skip the row that matters" failure, aimed at the row
    that matters.
    """
    if report.get("merged") is True:
        return {
            "state": "merged", "stale": [], "behind": "",
            "base_ref": report.get("base_ref") or "",
            "base_oid": report.get("base_oid") or "",
            "base_committed": report.get("base_committed") or "",
            "why": (
                "the PR is merged, so the base contains its own merge commit — "
                "drift is not derivable after the fact, and not a finding"
            ),
        }  # fmt: skip
    when = report.get("base_committed") or ""
    settled = [c for c in report["contexts"] if c["result"] in FAILING | PASSING]
    unknown: dict[str, Any] = {
        "state": "underivable", "stale": [], "behind": "",
        "why": "", "base_ref": report.get("base_ref") or "",
        "base_oid": report.get("base_oid") or "", "base_committed": when,
    }  # fmt: skip
    if not when:
        return {**unknown, "why": "the base branch's tip was not readable"}
    # A queued context has no start time and never will until it starts, so
    # reading that as underivable would fire on every PR with a pending job and
    # retire the signal. A context that has *concluded* and still has no start
    # time is a comparison genuinely not made.
    blind = [c["name"] for c in settled if not c["started"]]
    if blind:
        return {
            **unknown,
            "why": f"no start time for {', '.join(sorted(blind))}, which have concluded",
        }
    stale = [c["name"] for c in settled if c["started"] < when]
    if not stale:
        return {
            **unknown,
            "state": "no",
            "why": "every settled check started after the base branch last moved",
        }
    return {
        **unknown,
        "state": "yes",
        "stale": sorted(stale),
        "behind": interval(when, min(c["started"] for c in settled if c["name"] in stale)),
        "why": (
            f"{report.get('base_ref') or 'the base branch'} moved to "
            f"{(report.get('base_oid') or '')[:9]} after these checks started"
        ),
    }


def analyse(report: dict[str, Any]) -> dict[str, Any]:
    """Findings, and the three states, without deciding the verdict.

    Deliberately stops short of a verdict: Phase 7 owns that mapping, and a
    script that emitted one would put the rule in two places.
    """
    contexts = report["contexts"]
    required = [c for c in contexts if c["required"]]
    red = [c for c in contexts if c["result"] in FAILING]
    unsettled = [c for c in contexts if c["result"] not in FAILING | PASSING]
    report["required"] = required
    report["red"] = red
    report["unsettled"] = unsettled
    report["required_red"] = [c for c in required if c["result"] in FAILING]
    # `or is_draft` and not `in BLOCKING` alone: a draft PR is unmergeable
    # whichever of the two fields says so, and the enum half is on notice.
    report["blocked"] = report["merge_state"] in BLOCKING or report.get("is_draft") is True
    # UNKNOWN is what a merged PR returns and what an open one returns before
    # GitHub has computed it. Not "nothing blocks" — not established. A value
    # this script does not classify lands here too, for the same reason: an enum
    # GitHub grew after this was written is unread, and unread is not clear.
    report["merge_state_unrecognised"] = bool(report["merge_state"]) and (
        report["merge_state"] not in KNOWN
    )
    report["merge_state_underivable"] = (
        not report["merge_state"]
        or report["merge_state"] in UNESTABLISHED
        or report["merge_state_unrecognised"]
    )
    report["findings"] = bool(report["required_red"]) or report["blocked"]
    # Deliberately after `findings` and deliberately not part of it. Drift
    # qualifies rows; it never invents one. A default branch that moved while an
    # all-green PR sat there carries no finding about the bump, and folding it in
    # would exit 1 on every audit of an active repository — which is how a signal
    # stops being read.
    report["base_drift"] = base_drift(report)
    return report


def render(report: dict[str, Any]) -> None:
    print(f"commit: {report['head_oid'] or '(unknown)'}")
    if report["expected_head"] and report["head_oid"] != report["expected_head"]:
        print(f"  !! ROLLUP IS FOR A DIFFERENT COMMIT than --head-sha {report['expected_head']}")
        print("     The PR moved. Every row above this phase describes the old one.\n")

    print(
        f"  rollup {report['rollup_state'] or '(none reported)'}"
        f"   mergeStateStatus {report['merge_state'] or 'UNKNOWN'}"
        f"   reviewDecision {report['review_decision'] or '(none)'}"
    )
    held = len(report["contexts"])
    print(f"  {held} of {report['total_contexts']} context(s), {len(report['required'])} required")
    if not report["complete"]:
        print("  !! CONTEXT LIST TRUNCATED — the required set is UNDERIVABLE, not empty.")
        print("     Do not report the visible contexts as though they were all of them.\n")

    if report["merge_state_unrecognised"]:
        # Distinct from UNKNOWN on purpose: UNKNOWN is GitHub saying "not yet",
        # this is GitHub saying something this script has never heard of. Both
        # are underivable; only one of them is a bug report.
        print(f"  !! mergeStateStatus {report['merge_state']} is not a value this script")
        print("     classifies. Treated as *not established* rather than as 'nothing")
        print("     blocks' — but the classification is out of date; please report it.")
    elif report["merge_state_underivable"]:
        print("  !! mergeStateStatus is UNKNOWN: computed lazily, and this is also what")
        print("     a merged PR returns. That is *not established*, not 'nothing blocks'.")
    elif report["blocked"] and report["merge_state"] == "BEHIND":
        # Conditioned on `blocked` and not on the string alone: the wording below
        # asserts GitHub will refuse the merge, and that claim has to come from
        # the same classification the exit code does, or the two drift and one of
        # them is wrong. Its own row rather than folding into `blocked`'s
        # generic text because the remedy differs.
        # BLOCKED asks what gates the PR; BEHIND names it and says to press the
        # button — and the button moves the head, which is what makes this an
        # audit-invalidating state rather than a merge-button one.
        print("  !! mergeStateStatus is BEHIND: the head is out of date and this base")
        print("     REQUIRES it to be current, so GitHub will refuse the merge. That is")
        print("     enforcement, not the absence of it. Updating the branch MOVES THE")
        print("     HEAD — every row of this audit describes the commit before it, and")
        print("     the audit has to be re-run from Phase 1 against the new head.")
        print("     Landing a sibling bot PR is the ordinary cause.")

    drift = report["base_drift"]
    if drift["state"] == "yes":
        print(f"\n  !! THE BASE MOVED UNDER THESE RESULTS — {drift['why']}")
        print("     CI runs on refs/pull/<N>/merge, so every result below predates")
        print(f"     {drift['base_oid'][:9]} by {drift['behind']} and describes a merge")
        print("     that no longer exists. Nothing on the PR re-triggers it.")
        # Wrapped, and not truncated to the first few. Measured on `cli/cli`
        # #14196, where seven context names ran to 190 characters on one line and
        # the warning above them stopped being readable.
        print("     Started before it:")
        # `break_on_hyphens=False`: check names are full of them, and
        # `close-from-default-branch` split across two lines reads as two
        # different checks.
        for line in textwrap.wrap(", ".join(drift["stale"]), width=66, break_on_hyphens=False):
            print(f"       {line}")
        print("     Simulate the merge and re-gate before a red row carries a Hold, or")
        print("     a green one carries a merge — see Phase 6's fourth trap.")
    elif drift["state"] == "underivable":
        print(f"\n  !! BASE STALENESS UNDERIVABLE — {drift['why']}.")
        print("     Not 'these results include the current base': never established.")
    elif drift["state"] == "merged":
        # Quiet, and deliberately not `!!`. The warning it replaces was the
        # loudest thing in the phase and fired on every merged replay, which is
        # most of this plugin's live exercise. Wrapped like every other block
        # here: the one-line version ran to 118 characters.
        print("\n  -- base staleness N/A — this PR is merged, so the base contains its")
        print("     own merge commit. Drift is not derivable after the fact, and it is")
        print("     not a finding: the decision these checks informed has been made.")

    for ctx in report["required"]:
        mark = "OK " if ctx["result"] in PASSING else "BAD"
        print(f"  {mark} required  {ctx['name']}  {ctx['result']}")
    if not report["required"]:
        # Three states, not two. `blocked` is False both when the merge state is
        # genuinely clear and when it was never established, so reading it as a
        # boolean here turns "could not tell" into "this repo enforces nothing" —
        # a strong claim about a repository, drawn from a field that was not read.
        # Found by replaying this plugin's own #26, where the script printed the
        # UNKNOWN warning and that conclusion four lines apart.
        if report["blocked"] and report["merge_state"] == "BEHIND":
            # "Something you cannot see" is wrong here and the distinction is the
            # finding: BEHIND names its own gate. A base that requires branches to
            # be current is a repo that enforces something, which is the opposite
            # of what this branch used to print for it.
            print("  !! zero required contexts AND mergeStateStatus BEHIND: this base")
            print("     requires branches to be up to date, so it enforces something even")
            print("     though nothing required reported. NOT 'nothing enforced'.")
        elif report["blocked"]:
            print("  !! zero required contexts AND mergeStateStatus blocks: something")
            print("     gates this PR that you cannot see. UNDERIVABLE, not 'nothing enforced'.")
        elif report["merge_state_underivable"]:
            print("  !! zero required contexts, and the merge state was never established.")
            print("     UNDERIVABLE: nothing here settles what gates this repo, and a")
            print("     merged PR reaches this branch every time.")
        else:
            print("  -- zero required contexts, and nothing blocks: this repo enforces")
            print("     nothing, which changes what a green run is worth.")

    for ctx in report["red"]:
        attr = ctx["attribution"]
        print(f"\n  RED  {ctx['name']}  {ctx['result']}  [{ctx['kind']}]")
        print(f"       {attr['label'].upper()} — {attr['basis']}")
        if attr["label"] == "pre-existing":
            print("       A real finding, and a DIFFERENT one: it must not produce a")
            print("       Hold on this bump. The PR still cannot merge until it is fixed.")
        if attr["label"] == "attributable":
            # The only label that can carry a Hold, and it used to say the least.
            # A pre-existing row survives a wide interval; this one does not, and
            # presenting the two as equally strong evidence is what produced a
            # Hold on `actions/checkout` 7.0.1 for an upstream board-data change.
            print("       Green-then-red across that interval is CONSISTENT WITH the bump,")
            print("       not proof of it. Read the failing step's log at both commits")
            print("       before this row carries a Hold — especially where the check has")
            print("       inputs outside this repo.")
        if attr["weakened"]:
            print("       Say the weakened basis out loud; passing it off as the")
            print("       parent's answer is the failure this comparison exists to avoid.")
        if ctx["name"] in report["base_drift"]["stale"]:
            # The label above is still correct — both commits it compares are
            # unchanged. What it cannot say is that the result describes a merge
            # that has been superseded, and that is what a reader takes the Hold
            # from.
            print("       AND THE BASE MOVED: this result is for a merge that no longer")
            print("       exists, so the label above is right about two commits that did")
            print("       not change and silent about the one that did. Procedural, not")
            print("       substantive, until the merged tree is re-gated.")

    if report["red"] and report["parent_names"]:
        missing = [c["name"] for c in report["red"] if c["name"] not in report["parent_names"]]
        if missing:
            print(f"\n  names at the comparison point ({len(report['parent_names'])}):")
            print(f"    {', '.join(sorted(report['parent_names'])[:12])}")
            print("  Check names drift between branches. A name absent here is not")
            print("  proof the check never ran — compare the whole list, not one name.")

    if report["unsettled"]:
        names = ", ".join(c["name"] for c in report["unsettled"][:6])
        print(f"\n  {len(report['unsettled'])} context(s) not settled: {names}")

    # Three states here too, and this is the line most likely to be read alone.
    # "CLEAN" is GitHub's own word for a specific `mergeStateStatus`, so printing
    # it next to `merge state UNKNOWN` asserts the one thing the row above just
    # said was not established — the warning and the conclusion four lines apart,
    # which is the #26 complaint at the bottom of the report instead of the
    # middle. The exit code deliberately does NOT move with it: a merged PR
    # returns UNKNOWN, replaying one is routine, and exit 1 on every replay is
    # how a signal stops being read.
    if report["findings"]:
        verdict = "NEEDS REVIEW"
    elif report["merge_state_underivable"]:
        verdict = "NO FINDING, AND THE MERGE STATE WAS NEVER ESTABLISHED"
    else:
        verdict = "CLEAN"
    print(
        f"\nRESULT: {verdict}"
        f" — {len(report['required_red'])} required check(s) failing,"
        f" merge state {report['merge_state'] or 'UNKNOWN'}"
    )
    print("This is Phase 6's mechanical half. Whether a red check carries the")
    print("verdict is Phase 7's table, and a pre-existing one does not.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--number", required=True, type=int)
    parser.add_argument("--head-sha", default="", help="full 40 chars; cross-checks the rollup")
    parser.add_argument("--parent", default="", help="pr-<N>^ — the commit the bot branched from")
    parser.add_argument("--base-sha", default="", help="fallback when --parent has no runs")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    report = rollup(args.owner, args.name, args.number)
    report["expected_head"] = args.head_sha
    # Before the reads below, because every one of them is gated on what it
    # decides. The rollup already carries everything `analyse` needs.
    analyse(report)

    # Attribution is the only consumer of any of this, and it runs once per red
    # context — so with nothing red there is no row for an answer to qualify and
    # no call worth making. Measured before this guard existed: five calls on an
    # all-green PR, one of them read. The same reasoning already applied to
    # `committed_at` below; it had never been applied to the pair above it, which
    # is four calls rather than two and paginates.
    parent: dict[str, str] = {}
    base: dict[str, str] = {}
    spans: dict[str, str] = {}
    report["parent_names"] = []
    if report["red"]:
        if args.parent:
            parent, parent_unshaped = conclusions_at(args.owner, args.name, args.parent)
            report["base_results_unshaped"] = parent_unshaped
        # The merge base answers a *different*, weaker question — red before this
        # branch rather than before this commit — and `attribute` reaches for it
        # only when the parent has no runs at all. Fetching it while the parent
        # can answer buys nothing.
        if not parent and args.base_sha:
            base, base_unshaped = conclusions_at(args.owner, args.name, args.base_sha)
            report["base_results_unshaped"] = report.get("base_results_unshaped") or base_unshaped
        report["parent_names"] = sorted(parent) or sorted(base)
        head_at = report["head_committed"]
        if parent:
            spans["parent"] = interval(head_at, committed_at(args.owner, args.name, args.parent))
        if base:
            spans["base"] = interval(head_at, committed_at(args.owner, args.name, args.base_sha))
    for ctx in report["red"]:
        ctx["attribution"] = attribute(ctx, parent, base, args.parent, args.base_sha, spans)

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        render(report)
    return 1 if report["findings"] else 0


def cli() -> NoReturn:
    """Entry point. Anything unforeseen becomes exit 2, never exit 1.

    Exit 1 here means CI carries a finding. An unhandled exception exits 1 too,
    so without this a crash reports as a red required check — and this script's
    inputs are API responses, which are the shape least entitled to be
    well-formed.

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
