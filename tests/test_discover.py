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


def changed(filename: str, patch: str | None = None) -> dict[str, Any]:
    """One entry of the API's `files` array. `patch` absent is the real shape.

    GitHub omits `patch` for a binary file, and for any file whose diff exceeds
    its size limit. An omitted patch is the case the gate must not read as "no
    lines beyond the pin".
    """
    record: dict[str, Any] = {"filename": filename, "status": "modified"}
    if patch is not None:
        record["patch"] = patch
    return record


def uses_bump(action: str = "actions/checkout") -> str:
    """The whole content of an ordinary actions bump's diff for one file."""
    return (
        "@@ -12,7 +12,7 @@ jobs:\n"
        "     steps:\n"
        f"-      - uses: {action}@" + "a" * 40 + "  # v7.0.0\n"
        f"+      - uses: {action}@" + "b" * 40 + "  # v7.0.1\n"
        "       - run: make\n"
    )


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
        commit_files: dict[str, list[dict[str, Any]]] | None = None,
        compare_files: list[dict[str, Any]] | None = None,
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

        # An ordinary bump, so the pre-existing cases model one. A fake with no
        # files hands the gate an empty list, which is correctly underivable and
        # would make every unrelated case fail for the wrong reason.
        default_files = [changed("uv.lock"), changed("pyproject.toml")]

        def fake(args: list[str]) -> tuple[int, str]:
            joined = " ".join(args)
            calls.append(joined)
            error = json.dumps({"message": "Not Found", "status": "404"})
            for marker in fails:
                if marker in joined:
                    return 1, error
            # The single-commit call is `repos/O/N/commits/<sha>` and the list is
            # `repos/O/N/pulls/N/commits` — both contain "/commits", so the
            # single one has to be matched first or it falls through to the list
            # and the gate reads a commit array as a file array.
            if "/commits/" in joined:
                sha = joined.rsplit("/commits/", 1)[1].split()[0]
                served = default_files if commit_files is None else commit_files.get(sha, [])
                return 0, json.dumps({"files": served})
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
                if compare_files is not None:
                    body["files"] = compare_files
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
        self.assertIn(
            "reads the bot's own commits",
            out,
            "Phase 1 needs no substitution from 0.29.0 — a commit carries its own "
            "diff, so there is no range to be wrong about. Saying it still takes "
            "`pr-<N>^..pr-<N>` would send a reader to build a range by hand",
        )
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
        self.assertNotIn(
            "BOT_COMMITS=",
            out,
            "the bot half stopped crossing in 0.29.0: `$SCOPE_GATE` is the answer the "
            "loop that consumed it was computing, and emitting both leaves the shell "
            "holding everything it needs to roll a second gate by hand",
        )
        self.assertIn(
            "SCOPE_GATE=clean",
            out,
            "the gate is about what the *bump* changed, and only the bot's own commits "
            "are that — here the human's two commits touch nothing the gate may see",
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
        self.assertNotRegex(out, r"(?m)^HUMAN_COMMITS=")
        self.assertIn("# HUMAN_COMMITS", out, "an underivable output is emitted commented-out")
        self.assertIn(
            "SCOPE_GATE=underivable",
            out,
            "and the half that used to be Phase 1's loop says so in its own three "
            "states rather than handing over an empty list that iterates zero times",
        )

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


class TestThePhase1ScopeGateIsDerivedRatherThanJudged(DiscoverHarness):
    """Phase 1's gate decided whether Phases 4 and 5 get a shell, by eye.

    The bash block printed a sorted file list and the reader decided whether it
    was "only the manifest and the lockfile" or "only `uses:` lines". Both rules
    are mechanical, both fail quietly when misread, and the gate is the one
    branch in this procedure whose wrong answer hands a shell to code that
    should not have had one.

    Every case below is a failure the prose already names.
    """

    BOT_SHA = "1" * 40
    HUMAN_SHA = "2" * 40

    def _bot_pr(self, files: list[dict[str, Any]], **kw: Any) -> Any:
        fake, _ = self._fake(
            commits=[commit(self.BOT_SHA, BOT)],
            commit_files={self.BOT_SHA: files},
            **kw,
        )
        return fake

    def test_four_workflow_files_of_uses_lines_is_clean_scope(self):
        """The count-of-files trap, and the one that refuses the ordinary case.

        `actions.md`: "An action is pinned in every workflow that uses it, and a
        grouped bump moves several actions at once. Measured on `cli/cli`, all
        three merged: #14091 two files, #13981 three, #14147 four." A gate
        phrased as "one workflow file" refuses all three, in the report's
        language for a bump reaching into source.
        """
        fake = self._bot_pr(
            [
                changed(f".github/workflows/{name}.yml", uses_bump())
                for name in ("ci", "release", "docs", "nightly")
            ]
        )
        report = self._json(fake)
        self.assertEqual(report["scope"]["verdict"], "clean")
        self.assertEqual(report["scope"]["ecosystem"], "github-actions")

    def test_a_changed_line_that_is_not_a_uses_line_fires_the_gate(self):
        """The invariant is the kind of line, so a `with:` edit is beyond it."""
        fake = self._bot_pr(
            [
                changed(
                    ".github/workflows/ci.yml",
                    "@@ -3,3 +3,4 @@\n"
                    "-      - uses: actions/setup-python@" + "a" * 40 + "  # v7.0.0\n"
                    "+      - uses: actions/setup-python@" + "b" * 40 + "  # v7.0.1\n"
                    "+        with:\n"
                    "+          python-version: '3.13'\n",
                )
            ]
        )
        report = self._json(fake)
        self.assertEqual(report["scope"]["verdict"], "beyond")
        self.assertTrue(
            any("with:" in line for line in report["scope"]["beyond"]),
            "the gate has to name the line it fired on, or the report cannot say why",
        )

    def test_a_generated_workflows_pin_manifest_is_not_beyond_the_pin(self):
        """Measured on `cli/cli` #13981 and #14147, both merged actions bumps.

        `gh-aw` compiles a `.lock.yml` whose header records every action it
        pinned, so a correct bump changes the `uses:` line **and** the comment
        recording the same pin:

            @@ -44,7 +44,7 @@
             # Custom actions used:
            -#   - actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0 # v7.0.0
            +#   - actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1

        Read as a line beyond the pin, the gate fires on two of the three PRs
        `actions.md` cites as the measurement for its own rule — the same false
        Hold the file-count rule exists to prevent, reached a different way. A
        YAML comment cannot execute, and the pin it records is the one the
        `uses:` line already carried into the check.
        """
        fake = self._bot_pr(
            [
                changed(
                    ".github/workflows/issue-triage.lock.yml",
                    "@@ -44,7 +44,7 @@\n"
                    " # Custom actions used:\n"
                    "-#   - actions/checkout@" + "9" * 40 + " # v7.0.0\n"
                    "+#   - actions/checkout@" + "3" * 40 + " # v7.0.1\n"
                    "@@ -212,7 +212,7 @@ jobs:\n"
                    "-        uses: actions/checkout@" + "9" * 40 + " # v7.0.0\n"
                    "+        uses: actions/checkout@" + "3" * 40 + " # v7.0.1\n",
                )
            ]
        )
        report = self._json(fake)
        self.assertEqual(report["scope"]["verdict"], "clean", report["scope"]["beyond"])
        self.assertEqual(
            report["scope"]["comment_lines"],
            2,
            "counted and reported rather than waved through: a comment manifest is "
            "how a generated workflow announces itself, which decides a Phase 7 row",
        )

    def test_a_manifest_and_lockfile_bump_is_clean_scope(self):
        fake = self._bot_pr([changed("uv.lock"), changed("pyproject.toml")])
        report = self._json(fake)
        self.assertEqual(report["scope"]["verdict"], "clean")
        self.assertEqual(report["scope"]["ecosystem"], "uv.lock")

    def test_a_lockfile_bump_reaching_into_source_fires_the_gate(self):
        fake = self._bot_pr([changed("uv.lock"), changed("src/app/core.py")])
        report = self._json(fake)
        self.assertEqual(report["scope"]["verdict"], "beyond")
        self.assertIn("src/app/core.py", report["scope"]["beyond"])

    def test_the_gate_reads_the_bots_commits_and_not_the_whole_branch(self):
        """`fpga-board-sim` #334, mechanised.

        A maintainer landed the fixup the bump required on the bot's own branch.
        Gated on the union the audit reported "this bump reaches beyond the
        manifest and lockfile", stopped before Phase 4 — and merging it was
        correct. The split already reached the shell in 0.28.0; the gate that
        consumes it was still the reader's.
        """
        fake, _ = self._fake(
            commits=[
                commit(self.BOT_SHA, BOT),
                commit(self.HUMAN_SHA, "a-human", subject="style: reformat docs"),
            ],
            commit_files={
                self.BOT_SHA: [changed("uv.lock"), changed("pyproject.toml")],
                self.HUMAN_SHA: [changed("docs/guide.md"), changed("src/app/core.py")],
            },
        )
        report = self._json(fake)
        self.assertEqual(
            report["scope"]["verdict"],
            "clean",
            "the human's files are a finding to report, never the bump's scope",
        )

    def test_a_file_whose_patch_the_api_withheld_is_underivable_not_clean(self):
        """No patch is no evidence, and no evidence is not evidence of nothing.

        GitHub omits `patch` for binary files and for any diff past its size
        limit. Read as "no lines beyond the pin" it passes the gate on the file
        least entitled to pass it.
        """
        fake = self._bot_pr(
            [
                changed(".github/workflows/ci.yml", uses_bump()),
                changed(".github/workflows/big.yml"),
            ]
        )
        report = self._json(fake)
        self.assertEqual(report["scope"]["verdict"], "underivable")

    def test_a_patch_withheld_in_one_commit_is_not_covered_by_another(self):
        """Two of the bot's commits touch one file and only one carries a patch.

        The union has to inherit the *withheld* state, not the readable one. A
        merged record that keeps the patch it does have reads as "these are the
        lines that changed", and the lines the API never sent are the ones the
        gate would have objected to.
        """
        second = "4" * 40
        fake, _ = self._fake(
            commits=[commit(self.BOT_SHA, BOT), commit(second, BOT)],
            commit_files={
                self.BOT_SHA: [changed(".github/workflows/ci.yml")],
                second: [changed(".github/workflows/ci.yml", uses_bump())],
            },
        )
        report = self._json(fake)
        self.assertEqual(report["scope"]["verdict"], "underivable", report["scope"]["why"])

    def test_an_empty_file_list_is_underivable_rather_than_clean(self):
        """The zero-iteration trap one artifact along.

        0.28.0 found `for c in $BOT_COMMITS` over an unset variable iterating
        zero times, so the gate's file list was empty and it passed. An empty
        list of changed files is the same shape: nothing to object to, and
        nothing established.
        """
        fake = self._bot_pr([])
        report = self._json(fake)
        self.assertEqual(report["scope"]["verdict"], "underivable")

    def test_a_truncated_file_list_is_underivable_rather_than_clean(self):
        """The `contexts(first:100)` failure, in a different endpoint.

        The API caps a commit's `files` at 300 and says nothing about the rest,
        so file 301 is absent and indistinguishable from one that is in scope.
        """
        fake = self._bot_pr(
            [changed(f".github/workflows/w{i}.yml", uses_bump()) for i in range(300)]
        )
        report = self._json(fake)
        self.assertEqual(report["scope"]["verdict"], "underivable")

    def test_a_manifest_with_no_lockfile_is_underivable_rather_than_a_silent_beyond(self):
        """A `pyproject.toml`-only bump — pip without a lockfile — names no ecosystem.

        Every changed file is a manifest this plugin knows, so the beyond list
        filters to empty; the verdict was `beyond` anyway. A gate that fires
        while naming nothing is the one shape a reader cannot act on, and it
        would reach the report as "this bump reaches beyond the manifest" with
        an empty list under it.
        """
        fake = self._bot_pr([changed("pyproject.toml")])
        report = self._json(fake)
        self.assertEqual(report["scope"]["verdict"], "underivable")
        self.assertEqual(report["scope"]["beyond"], [])

    def test_an_unsupported_ecosystem_is_named_rather_than_gated(self):
        """A `Cargo.lock` is a boundary, not a scope finding.

        Followed faithfully against a real Cargo bump an improvised recipe
        returned matching checksums and a clean OSV batch, on a PR that raised
        the project's minimum Rust version past its own declared floor.
        """
        fake = self._bot_pr([changed("Cargo.lock"), changed("Cargo.toml")])
        report = self._json(fake)
        self.assertEqual(report["scope"]["ecosystem"], "unsupported")
        self.assertIn("Cargo.lock", report["scope"]["why"])

    def test_a_rewritten_base_with_no_split_leaves_the_scope_underivable(self):
        """#19's false Hold, in the fallback path.

        With `$BOT_COMMITS` underivable the gate falls back to the whole diff —
        and under a rewritten base that diff is the entire divergence, which is
        exactly the input that reported a two-file bump as fourteen files and
        3,682 deletions.
        """
        # `commits=None` means "use the harness default"; an unreadable list is
        # a failing call, which is the state the shell emits commented-out.
        fake, _ = self._fake(
            fails=("/pulls/1/commits",),
            force_pushes=1,
            compare_files=[changed("uv.lock"), changed("src/app/core.py")],
        )
        report = self._json(fake)
        self.assertEqual(report["scope"]["verdict"], "underivable")

    def test_the_gate_reaches_the_shell_for_phase_1_to_read(self):
        fake = self._bot_pr([changed("uv.lock"), changed("pyproject.toml")])
        _, out, _ = self._run(fake, ["--shell"])
        self.assertIn("ECOSYSTEM=uv.lock", out)
        self.assertIn("SCOPE_GATE=clean", out)

    def test_a_fired_gate_is_a_finding_and_a_clean_one_is_not(self):
        beyond = self._bot_pr([changed("uv.lock"), changed("src/app/core.py")])
        code, out, _ = self._run(beyond)
        self.assertEqual(code, 1)
        self.assertIn("=== scope: BEYOND", out)

        clean = self._bot_pr([changed("uv.lock"), changed("pyproject.toml")])
        code, out, _ = self._run(clean)
        self.assertEqual(code, 0)
        self.assertIn("=== scope: CLEAN", out)


if __name__ == "__main__":
    unittest.main()
