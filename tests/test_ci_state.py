"""Regression tests for ci_state.py.

No network: `_gh` is the single seam every call goes through, so the fakes below
drive the real parsing, pagination and attribution logic and only the subprocess
is replaced.

Every case corresponds to a defect that shipped in `SKILL.md`'s Phase 6, or to a
failure the phase exists to detect. Three of the seven prose defects were here,
and all three were the same mistake — asking a real endpoint the wrong question
and getting a well-formed answer back, which is precisely what a test can pin and
a careful reading cannot.

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import contextlib
import io
import json
import pathlib
import sys
import unittest
from typing import Any, ClassVar
from unittest import mock

sys.path.insert(
    0, str(pathlib.Path(__file__).resolve().parent.parent / "skills/dependabot-audit/scripts")
)

from ci_state import cli, main

HEAD = "h" * 40
PARENT = "p" * 40
BASE = "b" * 40


def check_run(name: str, conclusion: str, *, required: bool = False) -> dict[str, Any]:
    return {"name": name, "conclusion": conclusion, "isRequired": required}


def status_context(context: str, state: str, *, required: bool = False) -> dict[str, Any]:
    return {"context": context, "state": state, "isRequired": required}


def page(
    nodes: list[dict[str, Any]],
    *,
    total: int | None = None,
    next_cursor: str | None = None,
    merge_state: str = "CLEAN",
    review: str = "APPROVED",
    rollup_state: str = "SUCCESS",
    oid: str = HEAD,
    committed: str = "2026-08-01T00:00:00Z",
) -> dict[str, Any]:
    """One GraphQL response. `total` defaults to the node count (nothing truncated)."""
    return {
        "data": {
            "repository": {
                "pullRequest": {
                    "mergeable": "MERGEABLE",
                    "mergeStateStatus": merge_state,
                    "reviewDecision": review,
                    "commits": {
                        "nodes": [
                            {
                                "commit": {
                                    "oid": oid,
                                    "committedDate": committed,
                                    "statusCheckRollup": {
                                        "state": rollup_state,
                                        "contexts": {
                                            "totalCount": total
                                            if total is not None
                                            else len(nodes),
                                            "pageInfo": {
                                                "hasNextPage": next_cursor is not None,
                                                "endCursor": next_cursor,
                                            },
                                            "nodes": nodes,
                                        },
                                    },
                                }
                            }
                        ]
                    },
                }
            }
        }
    }


class CiStateHarness(unittest.TestCase):
    def _fake_gh(
        self,
        pages: list[dict[str, Any]],
        runs: dict[str, list[tuple[str, str]]] | None = None,
        statuses: dict[str, list[tuple[str, str]]] | None = None,
        dates: dict[str, str] | None = None,
        date_fails: bool = False,
    ) -> tuple[Any, list[str]]:
        """A `_gh` that dispatches on the call shape, plus the call log.

        `dates` serves the commit-timestamp read behind the attribution
        interval; `date_fails` makes that one call exit 2 the way a real `gh`
        failure does, which must weaken the row rather than end the phase.
        """
        runs = runs or {}
        statuses = statuses or {}
        dates = dates or {}
        calls: list[str] = []
        remaining: list[dict[str, Any]] = []

        def fake(args: list[str]) -> str:
            from ci_state import fail

            joined = " ".join(args)
            calls.append(joined)
            if "graphql" in joined:
                # Pagination needs a distinct response per call, so pop — but a
                # test may drive `main()` more than once (text and --json), and
                # re-issuing the same query has to return the same thing rather
                # than running the harness out of pages.
                return json.dumps(remaining.pop(0) if len(remaining) > 1 else remaining[0])
            for sha, rows in runs.items():
                if f"/commits/{sha}/check-runs" in joined:
                    return "\n".join(json.dumps({"name": n, "result": c}) for n, c in rows)
            for sha, rows in statuses.items():
                if f"/commits/{sha}/status" in joined:
                    return "\n".join(json.dumps({"name": n, "result": s}) for n, s in rows)
            if "committer.date" in joined:
                if date_fails:
                    fail("`gh api` failed: HTTP 404")
                return next((d for sha, d in dates.items() if f"/commits/{sha}" in joined), "")
            return ""

        def reset() -> None:
            remaining[:] = list(pages)

        reset()
        fake.reset = reset  # type: ignore[attr-defined]
        return fake, calls

    def _run(
        self, fake: Any, argv: list[str] | None = None, entry_point: Any = None
    ) -> tuple[Any, str, str]:
        if hasattr(fake, "reset"):
            fake.reset()
        args = argv if argv is not None else ["--parent", PARENT]
        full = [
            "ci_state.py", "--owner", "o", "--name", "r", "--number", "1",
            "--head-sha", HEAD, *args,
        ]  # fmt: skip
        out, err = io.StringIO(), io.StringIO()
        with (
            mock.patch("ci_state._gh", fake),
            mock.patch.object(sys, "argv", full),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            try:
                code: int | str | None = (entry_point or main)()
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue(), err.getvalue()

    def _json(self, fake: Any, argv: list[str] | None = None) -> dict[str, Any]:
        args = [*(argv if argv is not None else ["--parent", PARENT]), "--json"]
        _, out, _ = self._run(fake, args)
        loaded: dict[str, Any] = json.loads(out)
        return loaded


class TestTheRequiredSetComesFromTheApi(CiStateHarness):
    """Phase 6 once shipped one repo's check names in a runnable snippet.

    Reused against a repo whose checks are named anything else, the match yields
    nothing for every context — indistinguishable from "no required checks
    configured". The phase then verifies nothing while the report says CI was
    checked. Counting rows does not catch it, because the count agrees with the
    wrong list.
    """

    def test_only_the_contexts_github_marks_required_are_required(self):
        fake, _ = self._fake_gh([
            page([
                check_run("Test (Python 3.11)", "SUCCESS", required=True),
                check_run("Lint & type-check", "SUCCESS", required=True),
                check_run("coverage/coveralls", "SUCCESS"),
                check_run("optional-fuzz", "FAILURE"),
            ])
        ])  # fmt: skip
        report = self._json(fake)
        self.assertEqual([c["name"] for c in report["required"]],
                         ["Test (Python 3.11)", "Lint & type-check"])  # fmt: skip
        self.assertEqual(report["required_red"], [], "a non-required red must not gate merge")

    def test_a_name_with_spaces_and_an_ampersand_survives_whole(self):
        """`awk '{print $1}'` turns `Lint & type-check` into `Lint`."""
        fake, _ = self._fake_gh([page([check_run("Lint & type-check", "FAILURE", required=True)])])
        report = self._json(fake)
        self.assertEqual(report["required_red"][0]["name"], "Lint & type-check")

    def test_the_base_query_never_reaches_for_gh_run_list(self):
        """`gh run list --json name` returns the *workflow* name, one row reading
        `CI`, while the contexts here are job names like `test (ubuntu-latest)`.
        Matching one against the other is empty for every matrix job, and an empty
        result reads as "no run at the base" — so the obvious query fails in
        exactly the direction this comparison exists to correct."""
        fake, calls = self._fake_gh(
            [page([check_run("test (ubuntu-latest)", "FAILURE", required=True)])],
            runs={PARENT: [("test (ubuntu-latest)", "FAILURE")]},
        )
        self._run(fake)
        self.assertTrue(calls, "no calls were made")
        for call in calls:
            self.assertNotIn("run list", call)
        self.assertTrue(any("/check-runs" in c for c in calls), "must read check-runs by name")


class TestTruncationIsUnderivableRatherThanEmpty(CiStateHarness):
    """`contexts(first:100)` is a page. A required check at 101 is simply absent.

    Same shape as the hand-joined required list `isRequired` replaced, one level
    up: the missing contexts read as passing, and no count catches it because
    there is no authored list to count against.
    """

    def test_a_truncated_list_is_not_reported_as_complete(self):
        nodes = [check_run(f"job-{i}", "SUCCESS") for i in range(100)]
        # hasNextPage true but no cursor: it cannot be followed, and must not be
        # passed off as the whole list either.
        truncated = page(nodes, total=150)
        truncated["data"]["repository"]["pullRequest"]["commits"]["nodes"][0]["commit"][
            "statusCheckRollup"
        ]["contexts"]["pageInfo"] = {"hasNextPage": True, "endCursor": None}
        fake, _ = self._fake_gh([truncated])
        code, out, _ = self._run(fake)
        self.assertIn("TRUNCATED", out)
        self.assertIn("UNDERIVABLE", out)
        self.assertEqual(code, 0, "truncation is not itself a red check")

    def test_pagination_is_followed_to_exhaustion(self):
        first = [check_run(f"job-{i}", "SUCCESS") for i in range(100)]
        second = [check_run(f"job-{i}", "SUCCESS") for i in range(100, 150)]
        fake, calls = self._fake_gh([
            page(first, total=150, next_cursor="CUR"),
            page(second, total=150),
        ])  # fmt: skip
        report = self._json(fake)
        self.assertEqual(len(report["contexts"]), 150)
        self.assertTrue(report["complete"])
        self.assertEqual(sum("graphql" in c for c in calls), 2, "the second page was not fetched")
        self.assertTrue(any("cursor=CUR" in c for c in calls), "the cursor was not passed")


class TestARedCheckIsAttributedBeforeItCarriesAVerdict(CiStateHarness):
    """Observed on `BIRSAx2/mdcat` #6, replayed again for this script.

    `test (ubuntu-latest)` red beside two green siblings reads exactly like a
    bump breaking one platform. It was a rustdoc intra-doc-link error under
    `#[deny(warnings)]`, failing identically on the base. A Hold driven by that
    row is correct only by accident and unfalsifiable in the report: every cell
    true, the causal claim never established.

    It is also the direction that costs least to be wrong in, so it draws the
    least scrutiny — a false Hold looks conservative.
    """

    RED: ClassVar[list[dict[str, Any]]] = [
        check_run("test (ubuntu-latest)", "FAILURE", required=True)
    ]

    def test_red_at_the_parent_too_is_pre_existing(self):
        fake, _ = self._fake_gh(
            [page(self.RED, rollup_state="FAILURE")],
            runs={PARENT: [("test (ubuntu-latest)", "failure")]},
        )
        code, out, _ = self._run(fake)
        report_label = "pre-existing"
        self.assertIn(report_label.upper(), out)
        self.assertIn("must not produce a", out)
        self.assertEqual(code, 1, "a red required check is still a finding to report")

    def test_green_at_the_parent_is_attributable(self):
        fake, _ = self._fake_gh(
            [page(self.RED, rollup_state="FAILURE")],
            runs={PARENT: [("test (ubuntu-latest)", "success")]},
        )
        _, out, _ = self._run(fake)
        self.assertIn("ATTRIBUTABLE", out)
        self.assertNotIn("PRE-EXISTING", out)

    def test_a_parent_with_no_runs_falls_back_to_base_and_says_it_is_weaker(self):
        """This repo's own PR #26: `pr-26^` is an intermediate commit of the
        branch and carries zero check runs, so the parent has nothing to compare
        against while the merge base, being on the default branch, does.

        The fallback answers a *different* question — red at `$BASE_SHA` means red
        before this **branch**, so everything below the bump is inside the claim.
        Reaching for it is legitimate; passing it off as the parent's answer is
        the failure."""
        fake, _ = self._fake_gh(
            [page(self.RED, rollup_state="FAILURE")],
            runs={BASE: [("test (ubuntu-latest)", "failure")]},
        )
        report = self._json(fake, ["--parent", PARENT, "--base-sha", BASE])
        attr = report["red"][0]["attribution"]
        self.assertEqual(attr["label"], "pre-existing")
        self.assertTrue(attr["weakened"], "the weaker basis must be marked as such")
        self.assertIn("branch", attr["basis"])
        _, out, _ = self._run(fake, ["--parent", PARENT, "--base-sha", BASE])
        self.assertIn("weakened basis", out, "the report must say the claim is weaker")

    def test_a_check_absent_at_the_parent_is_underivable_not_attributable(self):
        """Names drift between branches: `mdcat`'s `main` reports `test` and
        `test-windows` where the PR reports `test (ubuntu-latest)`. A name match
        against a distant commit finds nothing and reads as "never ran", which
        would hand the red row back to the bump by default."""
        fake, _ = self._fake_gh(
            [page(self.RED, rollup_state="FAILURE")],
            runs={PARENT: [("test", "success"), ("test-windows", "success")]},
        )
        report = self._json(fake)
        self.assertEqual(report["red"][0]["attribution"]["label"], "underivable")

    def test_the_whole_name_list_at_the_comparison_point_is_printed(self):
        """Comparing one name is how drift reads as absence; show them all."""
        fake, _ = self._fake_gh(
            [page(self.RED, rollup_state="FAILURE")],
            runs={PARENT: [("test", "success"), ("test-windows", "success")]},
        )
        _, out, _ = self._run(fake)
        self.assertIn("test-windows", out)
        self.assertIn("drift", out)

    def test_a_red_status_context_is_attributed_from_the_status_list(self):
        """CheckRun and StatusContext live in separate lists at a commit, so
        reading only `check-runs` answers correctly about half the possible reds."""
        fake, _ = self._fake_gh(
            [page([status_context("ci/circleci", "FAILURE", required=True)],
                  rollup_state="FAILURE")],
            statuses={PARENT: [("ci/circleci", "failure")]},
        )  # fmt: skip
        report = self._json(fake)
        self.assertEqual(report["red"][0]["kind"], "StatusContext")
        self.assertEqual(report["red"][0]["attribution"]["label"], "pre-existing")


class TestAnAttributableRowSaysWhatItRestsOn(CiStateHarness):
    """`ATTRIBUTABLE` is the only label that produces a Hold, and said least.

    `PRE-EXISTING` ships with a caveat and `underivable` gets a paragraph;
    attribution was a bare assertion. Observed on `fpga-board-sim` #332,
    `actions/checkout` 7.0.0 -> 7.0.1:

        RED  Board-data drift  FAILURE  [CheckRun]
             ATTRIBUTABLE — green at 3a5b0b4ed (pr-<N>^)

    Every cell true. The causal reading it invites is false. That job re-syncs
    generated board sources from *other people's repositories* through the API
    and requires a zero diff; the cause was an upstream ref moving, fixed in that
    repo's own #335 and #336. `actions/checkout` 7.0.1 is "skip running unsafe pr
    check if input is default", "trim only ascii whitespace for branch" and
    "escape values passed to `--unset`" — none of which changes what
    `litex-boards` serves.

    **The comparison could not have settled it.** `pr-332^` is from
    2026-07-23T20:07:40Z and the head from 2026-07-27T13:09:25Z — **3d 17h**, on
    a check whose inputs live upstream. `PRE-EXISTING` survives that gap: if the
    check was already red the bump is exonerated regardless of what else moved.
    `ATTRIBUTABLE` does not — green-then-red across 3d 17h is consistent with the
    bump, with an upstream change, with a runner image roll, or with a flake, and
    the comparison distinguishes none of them. That asymmetry is the defect: the
    two labels are not equally strong evidence and were presented as though they
    were.

    No Hold fired only because `Board-data drift` is not required. Had the repo
    marked it so, this would have Held a security backport released across six
    majors inside 34 minutes, on an upstream board-data change. The guard was the
    audited repo's branch-protection configuration, not anything in the procedure.
    """

    RED: ClassVar[list[dict[str, Any]]] = [check_run("Board-data drift", "FAILURE", required=True)]

    def _attributable(
        self, *, head: str = "2026-07-27T13:09:25Z", parent: str | None = None
    ) -> Any:
        return self._fake_gh(
            [page(self.RED, rollup_state="FAILURE", committed=head)],
            runs={PARENT: [("Board-data drift", "success")]},
            dates={PARENT: parent} if parent is not None else {PARENT: "2026-07-23T20:07:40Z"},
        )[0]

    def test_the_interval_the_comparison_spans_is_printed(self):
        """Minutes apart on a one-commit bot PR is a strong claim; most of a week
        is not. The reader cannot discount what they are not shown."""
        _, out, _ = self._run(self._attributable())
        self.assertIn("ATTRIBUTABLE", out)
        self.assertIn("3d 17h", out, "the row must say how far apart the two commits are")

    def test_the_attributable_row_carries_a_hedge(self):
        """As the other two labels do. This is the one that can carry a Hold."""
        _, out, _ = self._run(self._attributable())
        self.assertIn("CONSISTENT WITH", out)
        self.assertIn("log at both commits", out)

    def test_a_pre_existing_row_is_not_hedged_the_same_way(self):
        """It survives a wide interval by construction: if the check was already
        red, the bump is exonerated whatever else moved in between. Hedging both
        identically would train the reader to discount the row that matters."""
        fake, _ = self._fake_gh(
            [page(self.RED, rollup_state="FAILURE")],
            runs={PARENT: [("Board-data drift", "failure")]},
            dates={PARENT: "2026-07-23T20:07:40Z"},
        )
        _, out, _ = self._run(fake)
        self.assertIn("PRE-EXISTING", out)
        self.assertNotIn("CONSISTENT WITH", out)

    def test_an_underivable_interval_says_so_rather_than_reading_as_minutes(self):
        """Phase 0's third state, one artifact along. A missing interval must not
        be indistinguishable from a tight one — that is the whole complaint about
        the unhedged row, reproduced in the hedge."""
        _, out, _ = self._run(self._attributable(parent=""))
        self.assertIn("ATTRIBUTABLE", out)
        self.assertIn("interval underivable", out.lower())
        self.assertIn("CONSISTENT WITH", out, "the hedge does not depend on the interval")

    def test_a_failed_date_read_does_not_end_the_phase(self):
        """The interval is a hedge on a claim, not the claim. A read that cannot
        be made weakens the row; it must not turn Phase 6 into an exit 2."""
        fake, _ = self._fake_gh(
            [page(self.RED, rollup_state="FAILURE")],
            runs={PARENT: [("Board-data drift", "success")]},
            date_fails=True,
        )
        code, out, _ = self._run(fake)
        self.assertEqual(code, 1, "a red required check is a finding, not a failure to run")
        self.assertIn("ATTRIBUTABLE", out)


class TestMergeStateIsReadAsThreeStates(CiStateHarness):
    """`isRequired` only sees contexts that *reported*.

    A required check that never ran is absent from the list entirely — the
    failure the hand-written join existed to catch. `mergeStateStatus` closes it,
    because an unsatisfied required check yields `BLOCKED` and never `CLEAN`.
    """

    def test_a_green_rollup_with_blocked_is_still_a_finding(self):
        """Verified on a real PR: 39 contexts, 3 required, every one SUCCESS,
        rollup SUCCESS — and mergeStateStatus BLOCKED with reviewDecision
        REVIEW_REQUIRED. A procedure that stops at the required contexts reports
        all-green and recommends a merge GitHub will refuse."""
        fake, _ = self._fake_gh([
            page([check_run("test", "SUCCESS", required=True)],
                 merge_state="BLOCKED", review="REVIEW_REQUIRED")
        ])  # fmt: skip
        code, out, _ = self._run(fake)
        self.assertEqual(code, 1)
        self.assertIn("REVIEW_REQUIRED", out)

    def test_unstable_is_mergeable(self):
        """Every *required* check green and something non-required unsettled."""
        fake, _ = self._fake_gh([
            page([check_run("test", "SUCCESS", required=True),
                  check_run("flaky", "FAILURE")], merge_state="UNSTABLE")
        ])  # fmt: skip
        code, _, _ = self._run(fake)
        self.assertEqual(code, 0, "UNSTABLE means nothing *required* is unsatisfied")

    def test_unknown_is_not_nothing_blocks(self):
        """It is what a merged PR returns, and what an open one returns before
        GitHub has computed it. Not established, not clear."""
        fake, _ = self._fake_gh([
            page([check_run("test", "SUCCESS", required=True)], merge_state="UNKNOWN")
        ])  # fmt: skip
        report = self._json(fake)
        self.assertTrue(report["merge_state_underivable"])
        _, out, _ = self._run(fake)
        self.assertIn("not established", out)

    def test_zero_required_with_blocked_is_underivable_not_unenforced(self):
        fake, _ = self._fake_gh([page([check_run("test", "SUCCESS")], merge_state="BLOCKED")])
        _, out, _ = self._run(fake)
        self.assertIn("UNDERIVABLE", out)
        self.assertNotIn("enforces\n     nothing", out)

    def test_zero_required_with_nothing_blocking_is_a_real_finding(self):
        fake, _ = self._fake_gh([page([check_run("test", "SUCCESS")])])
        code, out, _ = self._run(fake)
        self.assertEqual(code, 0)
        self.assertIn("enforces", out)

    def test_zero_required_with_an_unknown_merge_state_claims_nothing(self):
        """Found by replaying this repo's own #26, not by reasoning about it.

        The script printed both of these, four lines apart:

            !! mergeStateStatus is UNKNOWN ... *not established*, not 'nothing blocks'
            -- zero required contexts, and nothing blocks: this repo enforces nothing

        The second asserts the very thing the first says was never established.
        `blocked` is False both when the merge state is genuinely clear and when
        it is UNKNOWN, so the zero-required branch read a two-state answer off a
        three-state field — the exact collapse the whole discipline exists to
        prevent, reproduced inside the script written to enforce it.

        "This repo enforces nothing" is a strong claim about a repository. It
        needs the merge state to have been *read*, not merely to be un-blocking.
        """
        fake, _ = self._fake_gh([page([check_run("test", "SUCCESS")], merge_state="UNKNOWN")])
        _, out, _ = self._run(fake)
        self.assertNotIn(
            "enforces",
            out,
            "an unestablished merge state cannot support 'this repo enforces nothing'",
        )
        self.assertIn("UNDERIVABLE", out)


class TestNeutralResultsAreNotFailures(CiStateHarness):
    """Normal on diffs that do not touch the scanned surface.

    Two ways to get this wrong and they are not the same. Counting NEUTRAL as
    **failing** turns a clean bump into a Hold. Counting it as **unsettled**
    reports a settled check as still pending — quieter, and it still puts a row
    in front of the reader that says something untrue. The first mutation check
    of this case only caught the first, which is how the second got asserted.
    """

    def _neutral(self):
        fake, _ = self._fake_gh([
            page([check_run("CodeQL", "NEUTRAL", required=True),
                  check_run("secret-scan", "SKIPPED", required=True)])
        ])  # fmt: skip
        return fake

    def test_a_skipped_security_scan_is_not_red(self):
        code, _, _ = self._run(self._neutral())
        self.assertEqual(code, 0)
        self.assertEqual(self._json(self._neutral())["red"], [])

    def test_a_skipped_security_scan_is_not_unsettled_either(self):
        """NEUTRAL and SKIPPED are conclusions. The check finished."""
        report = self._json(self._neutral())
        self.assertEqual(
            [c["name"] for c in report["unsettled"]],
            [],
            "a concluded check reported as pending is a row that says something untrue",
        )
        _, out, _ = self._run(self._neutral())
        self.assertNotIn("not settled", out)


class TestTheRollupBelongsToTheCommitUnderAudit(CiStateHarness):
    def test_a_rollup_for_another_commit_is_flagged(self):
        """Bots rebase. A rollup for the new head, read into a report whose other
        rows describe the old one, silently asserts they agree."""
        fake, _ = self._fake_gh([page([check_run("test", "SUCCESS", required=True)], oid="z" * 40)])
        _, out, _ = self._run(fake)
        self.assertIn("DIFFERENT COMMIT", out)


class TestFailureIsNotAFinding(CiStateHarness):
    """Exit 1 means CI carries a finding. A crash exits 1 too unless guarded, so
    without the boundary an unparseable response reports as a red required check."""

    def test_a_failed_gh_call_exits_2(self):
        def fake(args: list[str]) -> str:
            from ci_state import fail

            fail("`gh api` failed: HTTP 403")

        code, out, err = self._run(fake)
        self.assertEqual(code, 2)
        self.assertNotIn("RESULT", out)
        self.assertIn("403", err)

    def test_unparseable_json_exits_2(self):
        def broken(args: list[str]) -> str:
            return "not json at all"

        code, _, err = self._run(broken)
        self.assertEqual(code, 2)
        self.assertIn("unparseable", err)

    def test_an_unforeseen_exception_exits_2_not_1(self):
        def boom(args: list[str]) -> str:
            raise RuntimeError("boom")

        code, out, err = self._run(boom, entry_point=cli)
        self.assertEqual(code, 2)
        self.assertIn("RuntimeError", err)
        self.assertNotIn("RESULT", out)

    def test_the_guard_does_not_swallow_a_real_finding(self):
        """SystemExit re-raises first, or exit 1 and exit 2 both become 2."""
        fake, _ = self._fake_gh([
            page([check_run("test", "FAILURE", required=True)], rollup_state="FAILURE")
        ])  # fmt: skip
        code, out, _ = self._run(fake, ["--parent", PARENT], entry_point=cli)
        self.assertEqual(code, 1)
        self.assertIn("RESULT: NEEDS REVIEW", out)

    def test_a_missing_pull_request_exits_2(self):
        fake, _ = self._fake_gh([{"data": {"repository": {"pullRequest": None}}}])
        code, _, err = self._run(fake)
        self.assertEqual(code, 2)
        self.assertIn("not readable", err)


if __name__ == "__main__":
    unittest.main()
