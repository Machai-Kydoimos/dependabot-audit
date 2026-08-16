"""Regression tests for discover.py.

No network: `_gh` is the single seam every call goes through, so the fakes below
drive the real derivation, branch-point and classification logic and only the
subprocess is replaced.

Phase 0's two shipped defects were both in `$BASE_SHA` — a rewritten base sending
`git merge-base` back nineteen months, and a merged PR collapsing the base onto
the head. Both fail into a *plausible* value rather than an error, which is what
makes them worth a script and worth these cases.

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import contextlib
import io
import json
import pathlib
import sys
import unittest
from typing import Any
from unittest import mock

sys.path.insert(
    0, str(pathlib.Path(__file__).resolve().parent.parent / "skills/dependabot-audit/scripts")
)

from discover import cli, main

HEAD = "h" * 40
BASE = "b" * 40
MERGE_BASE = "m" * 40
BOT = "dependabot[bot]"


def commit(sha: str, author: str, *, parents: int = 1, subject: str = "bump x") -> dict[str, Any]:
    return {
        "sha": sha,
        "author": {"login": author},
        "parents": [{"sha": "x" * 40}] * parents,
        "commit": {"message": subject, "author": {"name": author}},
    }


def repo(**perms: bool) -> dict[str, Any]:
    base = {"admin": False, "maintain": False, "push": True, "triage": False, "pull": True}
    base.update(perms)
    return {"default_branch": "main", "permissions": base}


def pull(
    *, author: str = BOT, fork: bool = False, merged: bool = False, state: str = "open"
) -> dict[str, Any]:
    return {
        "user": {"login": author},
        "head": {"sha": HEAD, "repo": {"fork": fork}},
        "base": {"sha": BASE},
        "merged": merged,
        "state": state,
        "created_at": "2026-08-01T00:00:00Z",
    }


class DiscoverHarness(unittest.TestCase):
    def _fake(
        self,
        *,
        repo_json: dict[str, Any] | None = None,
        pull_json: dict[str, Any] | None = None,
        merge_base: str | None = MERGE_BASE,
        commits: list[dict[str, Any]] | None = None,
        force_pushes: int = 0,
        fails: tuple[str, ...] = (),
    ) -> tuple[Any, list[str]]:
        """A `_gh` returning (exit code, stdout), dispatching on the call shape.

        `fails` names substrings whose call should report a non-zero exit *and*
        an error body on stdout — which is what `gh` really does, and the whole
        reason the script gates on the code rather than on the text.
        """
        repo_json = repo() if repo_json is None else repo_json
        pull_json = pull() if pull_json is None else pull_json
        commits = [commit(HEAD, BOT)] if commits is None else commits
        events = [{"event": "base_ref_force_pushed", "actor": {"login": "someone"},
                   "created_at": "2026-08-02T00:00:00Z"}] * force_pushes  # fmt: skip
        calls: list[str] = []

        def fake(args: list[str]) -> tuple[int, str]:
            joined = " ".join(args)
            calls.append(joined)
            error = json.dumps({"message": "Not Found", "status": "404"})
            for marker in fails:
                if marker in joined:
                    return 1, error
            # `/commits` before `/pulls/`: the commits call is a `/pulls/N/commits`
            # URL with `--paginate` after it, so an `endswith` check misses and it
            # falls through to the pull body.
            if "/commits" in joined:
                return 0, json.dumps(commits)
            if "/pulls/" in joined:
                return 0, json.dumps(pull_json)
            if "/compare/" in joined:
                body: dict[str, Any] = {}
                if merge_base is not None:
                    body["merge_base_commit"] = {"sha": merge_base}
                return 0, json.dumps(body)
            if "/issues/" in joined:
                return 0, json.dumps(events)
            return 0, json.dumps(repo_json)

        return fake, calls

    def _run(
        self, fake: Any, extra: list[str] | None = None, entry_point: Any = None
    ) -> tuple[Any, str, str]:
        argv = ["discover.py", "--repo", "o/r", "--number", "1", *(extra or [])]
        out, err = io.StringIO(), io.StringIO()
        with (
            mock.patch("discover._gh", fake),
            mock.patch.object(sys, "argv", argv),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            try:
                code: int | str | None = (entry_point or main)()
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue(), err.getvalue()

    def _json(self, fake: Any) -> dict[str, Any]:
        _, out, _ = self._run(fake, ["--json"])
        loaded: dict[str, Any] = json.loads(out)
        return loaded


class TestTheBranchPointIsProvedRatherThanAssumed(DiscoverHarness):
    """`git merge-base` always returns *a* commit, even a badly wrong one.

    Observed: a two-file `Cargo.toml` / `Cargo.lock` bump whose merge-base diff
    was 14 files and 3,682 deletions, appearing to delete the repo's entire
    vendored `supply-chain/` tree. The base branch had been force-pushed eleven
    minutes after the PR opened, and `merge-base` fell back to an ancestor
    nineteen months earlier.
    """

    def test_a_force_push_event_is_the_authority(self):
        fake, _ = self._fake(
            force_pushes=1,
            commits=[commit("c1", "a-human"), commit(HEAD, BOT)],
        )
        report = self._json(fake)
        self.assertEqual(report["branch_point"]["verdict"], "rewritten")
        self.assertIn("base_ref_force_pushed", report["branch_point"]["why"])

    def test_a_rewritten_base_names_both_substitutions(self):
        """ "The base was rewritten" and "this bump reaches beyond the manifest"
        produce the same diff and are not the same finding."""
        fake, _ = self._fake(force_pushes=1)
        _, out, _ = self._run(fake)
        self.assertIn("pr-<N>^..pr-<N>", out)
        self.assertIn("tip-<N>", out)

    def test_a_merge_commit_head_does_not_trigger_the_substitution(self):
        """Measured on `cli/cli` #14049, whose head is a maintainer's merge of
        the base branch *into* the bot's. Zero force-push events, and a correct
        two-file scope diff from the merge base. Read as a moved base it would
        substitute the `pr-<N>^` diff — 20 files, 1,101 lines — and halt the
        audit on a bump that changes four workflow lines."""
        fake, _ = self._fake(
            commits=[commit("c1", BOT), commit(HEAD, "a-human", parents=2, subject="Merge branch")]
        )
        report = self._json(fake)
        self.assertEqual(report["branch_point"]["verdict"], "ok")
        self.assertTrue(report["branch_point"]["head_is_merge_commit"])
        self.assertIn(
            "merge commit",
            report["branch_point"]["why"],
            "an `ok` verdict reached for the wrong reason is the "
            "individually-accurate, collectively-misleading shape — the reader "
            "needs to know it is `pr-<N>^` that is unusable here, not the base",
        )
        _, out, _ = self._run(fake)
        self.assertIn("Do NOT substitute", out)

    def test_a_merge_commit_head_outranks_a_human_commit_below_it(self):
        """Order matters, and only this shape exposes it.

        `cli/cli` #14049 is a bot commit under a maintainer's merge, so the
        corroboration scan finds nothing and any ordering works. Put a *human*
        one-parent commit on the branch as well and the two rules disagree:
        read as `suspect` the report says the base may have moved, when what
        actually happened is someone merged the base *into* the branch and the
        merge base is exactly right.
        """
        fake, _ = self._fake(
            commits=[
                commit("c1", "a-human"),
                commit("c2", BOT),
                commit(HEAD, "a-human", parents=2, subject="Merge branch 'main' into dependabot/x"),
            ]
        )
        report = self._json(fake)
        self.assertEqual(
            report["branch_point"]["verdict"],
            "ok",
            "a merged-in base leaves the merge base correct; the corroboration "
            "scan must not outrank the two-parent head",
        )

    def test_a_human_commit_under_the_bots_without_a_force_push_is_suspect(self):
        """Corroboration without the authority. A genuine bot PR is one commit by
        the bot, so a human commit under it is anomalous — but a merge of the base
        *into* the branch looks similar and leaves the merge base correct, so this
        is not enough to substitute on."""
        fake, _ = self._fake(commits=[commit("c1", "a-human"), commit(HEAD, BOT)])
        report = self._json(fake)
        self.assertEqual(report["branch_point"]["verdict"], "suspect")

    def test_a_human_pr_is_not_suspect_for_having_human_commits(self):
        """Found by replaying this plugin's own #26, not by reasoning about it.

        The corroboration signal is "a non-bot commit above the base", and on a
        *human* PR that is the definition of the PR rather than an anomaly. Every
        PR in this repository fired it — five human commits, no force-push,
        reported as `SUSPECT` on a branch nobody had touched.

        The signal only carries information when the PR is bot-authored, because
        that is the only case where "one commit, by the bot" is the expectation it
        departs from. Applied to a human PR it manufactures a finding on every
        single one, which is the fastest way to train a reader to skip the row.
        """
        fake, _ = self._fake(
            pull_json=pull(author="a-human"),
            commits=[commit("c1", "a-human"), commit("c2", "a-human"), commit(HEAD, "a-human")],
        )
        report = self._json(fake)
        self.assertEqual(
            report["branch_point"]["verdict"],
            "ok",
            "a human PR's human commits are not evidence the base moved",
        )
        self.assertNotIn(
            "non-bot commits above the base",
            " ".join(report["findings"]),
        )

    def test_a_human_prs_ok_verdict_is_not_explained_as_a_bot_branch(self):
        """The first fix got the verdict right and the reason wrong.

        Suppressing the corroboration scan on human PRs dropped `#26` into the
        `else` branch, whose text reads "every commit above the base is the
        bot's" — false on a PR with no bot commits at all. A correct verdict
        carried by a false sentence is the same family as the unattributed red
        check: every cell true except the one doing the work.
        """
        fake, _ = self._fake(
            pull_json=pull(author="a-human"),
            commits=[commit("c1", "a-human"), commit(HEAD, "a-human")],
        )
        why = self._json(fake)["branch_point"]["why"]
        self.assertNotIn(
            "every commit above the base is the bot's",
            why,
            "there are no bot commits on this branch to say that about",
        )
        self.assertIn("not a bot PR", why)

    def test_an_unreadable_event_list_is_underivable_not_ok(self):
        """The precondition did not hold, which is the third state — not "no
        force-push happened"."""
        fake, _ = self._fake(fails=("/issues/",))
        report = self._json(fake)
        self.assertEqual(report["branch_point"]["verdict"], "underivable")
        self.assertIn(
            "could not establish whether the merge base is the branch point",
            report["findings"],
        )


class TestTheMergeBaseSurvivesThePrHavingLanded(DiscoverHarness):
    """A merged PR's head is an ancestor of the default branch, so a merge base
    taken against that branch *is* the head — Phase 1's scope diff comes back
    empty, Phase 4 measures the PR against itself, and Phase 6 cross-checks the
    head against itself. All three report the reassuring answer.

    Taken from GitHub's `compare` endpoint it is the real branch point in both
    states. Verified live on `cli/cli` #14049 (merged) and #14148 (open).
    """

    def test_the_base_comes_from_compare_and_not_from_the_head(self):
        fake, calls = self._fake(pull_json=pull(merged=True, state="closed"))
        report = self._json(fake)
        self.assertEqual(report["base_sha"], MERGE_BASE)
        self.assertNotEqual(report["base_sha"], report["head_sha"])
        self.assertTrue(any("/compare/" in c for c in calls))
        self.assertEqual(report["pr_state"], "MERGED")

    def test_an_unreadable_compare_leaves_the_base_underivable(self):
        fake, _ = self._fake(fails=("/compare/",))
        report = self._json(fake)
        self.assertIsNone(report["base_sha"])
        self.assertTrue(any("$BASE_SHA is underivable" in f for f in report["findings"]))


class TestTheScopeDiffIsSplitByAuthorship(DiscoverHarness):
    """A bot PR's branch is not always all bot, and Phase 1 gated on the union.

    `fpga-board-sim` #334: the bot's bump, then a maintainer's `style: reformat
    docs for ruff 0.16's markdown code-fence formatting` **on the bot's own
    branch** so a required CI check goes green again, then a merge of `main`.
    Phase 0 read the authorship of all three and printed `HUMAN` against two.
    Phase 1 then took its diff from the merge base, saw eight files, and fired
    the scope gate — reported as "this bump reaches beyond the manifest and
    lockfile", which is not what happened. Merging it was correct.

    The cost is more than the wrong verdict: the gate stops the audit **before
    Phase 4**, and Phase 4 is the phase that would have measured this very bump.
    ruff 0.15.22 -> 0.16.0 is this plugin's founding Phase 4 observation occurring
    for real, and Phase 4 measures on the merge base precisely because a PR
    carrying the fixup reports no difference on its own tree. Of the five PRs in
    that batch it is the only one where Phase 4 had something to find, and the
    only one where it did not run.

    Same family as #19 and the same shape — the gate stopping the audit for a
    reason that is not true, in language that reads exactly like a bump reaching
    into source. #19 was a rewritten base; this is a human commit on the bot's
    own branch. They present identically and take different fixes.
    """

    BOT_SHA = "1" * 40
    HUMAN_SHA = "2" * 40
    MERGE_SHA = "3" * 40

    def _mixed(self) -> Any:
        """#334's branch shape: bot, human, then a merge of the base branch."""
        fake, _ = self._fake(
            commits=[
                commit(self.BOT_SHA, BOT, subject="chore(deps-dev): Bump the python-deps group"),
                commit(self.HUMAN_SHA, "a-human", subject="style: reformat docs for ruff 0.16"),
                commit(
                    self.MERGE_SHA, "a-human", parents=2, subject="Merge remote-tracking branch"
                ),
            ]
        )
        return fake

    def test_the_two_halves_are_emitted_for_phase_1_to_gate_on(self):
        _, out, _ = self._run(self._mixed(), ["--shell"])
        self.assertIn(
            f'BOT_COMMITS="{self.BOT_SHA}"',
            out,
            "the gate is about what the *bump* changed, and only the bot's own commits are that",
        )
        self.assertIn(
            f'HUMAN_COMMITS="{self.HUMAN_SHA} {self.MERGE_SHA}"',
            out,
            "a maintainer's commit on this branch is a finding to report, not a "
            "Hold — and it has to reach Phase 1 to be reported at all",
        )

    def test_a_merge_commit_is_carried_into_the_split_but_not_into_the_verdict(self):
        """The commit list has two consumers and they filter differently.

        The branch-point scan drops two-parent commits deliberately — someone
        merging the base *into* the branch leaves the merge base correct, and
        substituting there halts the audit on `cli/cli` #14049's four workflow
        lines. Reusing that `parents == 1` filter for the authorship split would
        be the obvious move and is wrong: `git show` on a clean merge prints
        nothing, and on an **evil** merge prints what the merge itself changed.
        Dropping merges makes that invisible; carrying them costs nothing.
        """
        _, out, _ = self._run(self._mixed(), ["--shell"])
        self.assertIn(self.MERGE_SHA, out, "an evil merge would be invisible to Phase 1")
        report = self._json(self._mixed())
        self.assertEqual(
            report["branch_point"]["verdict"],
            "ok",
            "and the same merge must still leave the merge base alone",
        )

    def test_the_emitted_shas_are_full_length(self):
        """The report truncates to nine characters for reading; a ref handed to
        `git show` is Phase 0's own rule about transcribing SHAs, one artifact
        along. Rendered short and emitted short are different requirements."""
        report = self._json(self._mixed())
        self.assertEqual(report["branch_point"]["commits_above_base"][0]["sha"], self.BOT_SHA)
        _, out, _ = self._run(self._mixed())
        self.assertIn(self.BOT_SHA[:9], out, "the human-readable report still abbreviates")

    def test_an_unreadable_commit_list_leaves_both_unset(self):
        """Phase 0's third state. The split is underivable, not empty, and Phase
        1's fallback is the old whole-diff gate."""
        fake, _ = self._fake(fails=("/commits",))
        _, out, _ = self._run(fake, ["--shell"])
        self.assertNotRegex(out, r"(?m)^BOT_COMMITS=")
        self.assertNotRegex(out, r"(?m)^HUMAN_COMMITS=")
        self.assertIn("# BOT_COMMITS", out, "an underivable output is emitted commented-out")

    def test_no_bot_commits_leaves_the_gate_list_unset_rather_than_empty(self):
        """The dangerous shape, and the reason this cannot be a plain join.

        `for c in $BOT_COMMITS` over an empty string iterates zero times, so the
        file list comes back empty and the gate passes **trivially** — reporting
        clean rather than erroring, on the one phase whose entire job is to
        refuse. Unset instead: Phase 1 then falls back to the merge-base diff and
        says the split was underivable.
        """
        fake, _ = self._fake(
            pull_json=pull(author="someone"),
            commits=[commit(HEAD, "someone")],
        )
        _, out, _ = self._run(fake, ["--shell"])
        self.assertNotRegex(
            out,
            r"(?m)^BOT_COMMITS=",
            "an empty gate list is worse than no gate list: it passes silently",
        )


class TestExecutionRequiresABotPrOnARepoYouControl(DiscoverHarness):
    def test_an_ordinary_bot_pr_on_your_own_repo_may_execute(self):
        fake, _ = self._fake()
        code, out, _ = self._run(fake)
        self.assertEqual(code, 0, "an ordinary bump is not a finding")
        self.assertIn("PHASES 4 AND 5 MAY RUN", out)

    def test_a_fork_pr_may_not(self):
        fake, _ = self._fake(pull_json=pull(fork=True))
        code, out, _ = self._run(fake)
        self.assertEqual(code, 1)
        self.assertIn("cross-repository", out)

    def test_a_human_authored_bump_may_not(self):
        fake, _ = self._fake(pull_json=pull(author="someone"))
        _, out, _ = self._run(fake)
        self.assertIn("not a bot", out)
        self.assertIn("USE --no-execute", out)

    def test_a_pull_only_account_may_not(self):
        fake, _ = self._fake(repo_json=repo(push=False))
        _, out, _ = self._run(fake)
        self.assertIn("no `push` here", out)

    def test_underivable_permissions_do_not_read_as_pull_only(self):
        """`gh` writes an API error body to stdout and still exits non-zero, so a
        caller reading only stdout gets `{"message": "Not Found"}` — at which
        point `push` is not true and reads exactly like a pull-only account.
        Failing closed is right; reporting "you lack push here" when the audit
        could not tell is not."""
        fake, _ = self._fake(repo_json={"default_branch": "main"})
        report = self._json(fake)
        self.assertIsNone(report["perms"])
        _, out, _ = self._run(fake)
        self.assertIn("underivable", out)
        self.assertNotIn("no `push` here", out)


class TestFailureIsNotAFinding(DiscoverHarness):
    """Exit 1 means Phase 0 found something. A crash exits 1 too unless guarded,
    so without the boundary an unparseable response reads as a rewritten base."""

    def test_an_unreadable_pull_request_exits_2(self):
        fake, _ = self._fake(fails=("/pulls/1",))
        code, out, err = self._run(fake)
        self.assertEqual(code, 2)
        self.assertNotIn("RESULT", out)
        self.assertIn("could not read", err)

    def test_an_unforeseen_exception_exits_2_not_1(self):
        def boom(args: list[str]) -> tuple[int, str]:
            raise RuntimeError("boom")

        code, out, err = self._run(boom, entry_point=cli)
        self.assertEqual(code, 2)
        self.assertIn("RuntimeError", err)
        self.assertNotIn("RESULT", out)

    def test_the_guard_does_not_swallow_a_real_finding(self):
        fake, _ = self._fake(force_pushes=1)
        code, out, _ = self._run(fake, entry_point=cli)
        self.assertEqual(code, 1)
        self.assertIn("RESULT: NEEDS REVIEW", out)

    def test_a_malformed_repo_argument_exits_2(self):
        fake, _ = self._fake()
        argv = ["discover.py", "--repo", "no-slash", "--number", "1"]
        with (
            mock.patch("discover._gh", fake),
            mock.patch.object(sys, "argv", argv),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()) as err,
            self.assertRaises(SystemExit) as raised,
        ):
            main()
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("OWNER/NAME", err.getvalue())


if __name__ == "__main__":
    unittest.main()
