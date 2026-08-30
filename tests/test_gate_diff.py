"""Regression tests for gate_diff.py.

No network and no real linters: the gate commands are shell one-liners that
stand in for "a tool that touched these files". What is under test is the
mechanics — snapshotting, restoring between runs, and the three ways a bump can
move a gate — because those are what go wrong when improvised.

    python3 -m unittest discover -s tests -v

The safety-critical case is `test_the_tree_is_restored_between_runs`: without
it, run two inherits run one's edits and every comparison after that is fiction.
"""

from __future__ import annotations

import contextlib
import io
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(
    0, str(pathlib.Path(__file__).resolve().parent.parent / "skills/dependabot-audit/scripts")
)

from gate_diff import cli, main


def git_repo(test: unittest.TestCase) -> pathlib.Path:
    """A throwaway git repo with one committed file, removed when the test ends."""
    directory = pathlib.Path(tempfile.mkdtemp())
    test.addCleanup(shutil.rmtree, directory, ignore_errors=True)
    (directory / "tracked.txt").write_text("original\n", encoding="utf-8")
    for args in (
        ["init", "-q", "-b", "main"],
        ["add", "-A"],
        ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base"],
    ):
        subprocess.run(["git", "-C", str(directory), *args], check=True, capture_output=True)
    return directory


class GateDiffHarness(unittest.TestCase):
    def _run(self, tree, runs, as_json=False, entry_point=None):
        argv = ["gate_diff.py", "--tree", str(tree)]
        if as_json:
            argv.append("--json")
        for label, command in runs:
            argv += ["--run", label, command]
        out, err = io.StringIO(), io.StringIO()
        with (
            mock.patch.object(sys, "argv", argv),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            try:
                code: int | str | None = (entry_point or main)()
            except SystemExit as exc:  # fail() raises rather than returns
                code = exc.code
        return code, out.getvalue(), err.getvalue()


class TestTheThreeWaysAGateMoves(GateDiffHarness):
    def test_widened_scope_is_reported(self):
        """The ruff 0.16 case: the newer version acts on a file the older ignores."""
        tree = git_repo(self)
        code, out, _ = self._run(tree, [("locked", "true"), ("proposed", "printf x > doc.md")])
        self.assertEqual(code, 1)
        self.assertIn("doc.md", out)
        self.assertIn("acted on by proposed only", out)

    def test_narrowed_scope_is_reported(self):
        tree = git_repo(self)
        code, out, _ = self._run(tree, [("locked", "printf x > doc.md"), ("proposed", "true")])
        self.assertEqual(code, 1)
        self.assertIn("acted on by locked only", out)

    def test_same_file_different_result_is_reported(self):
        """The destructive-fix case: both versions rewrite it, differently."""
        tree = git_repo(self)
        code, out, _ = self._run(
            tree,
            [
                ("locked", "printf 'kept\\n' > tracked.txt"),
                ("proposed", "printf 'deleted\\n' > tracked.txt"),
            ],
        )
        self.assertEqual(code, 1)
        self.assertIn("different result", out)
        self.assertIn("tracked.txt", out)

    def test_a_deleted_file_counts_as_a_change(self):
        """Deleting a file is exactly the fix-mode behaviour worth catching."""
        tree = git_repo(self)
        code, out, _ = self._run(tree, [("locked", "true"), ("proposed", "rm tracked.txt")])
        self.assertEqual(code, 1)
        self.assertIn("tracked.txt", out)

    def test_identical_runs_agree(self):
        tree = git_repo(self)
        code, out, _ = self._run(
            tree,
            [("locked", "printf same > f.txt"), ("proposed", "printf same > f.txt")],
        )
        self.assertEqual(code, 0)
        self.assertIn("GATES AGREE", out)

    def test_an_exit_code_change_is_reported_even_with_no_file_change(self):
        tree = git_repo(self)
        code, out, _ = self._run(tree, [("locked", "true"), ("proposed", "false")])
        self.assertEqual(code, 1)
        self.assertIn("the gate's answer changed", out)


class TestSafety(GateDiffHarness):
    def test_the_tree_is_restored_between_runs(self):
        """Without this every comparison after the first run is fiction.

        Run one leaves a stray file behind; run two asserts it is gone. A failed
        restore shows up as run two exiting non-zero.
        """
        tree = git_repo(self)
        _, out, _ = self._run(
            tree,
            [("first", "printf x > stray.txt"), ("second", "test ! -e stray.txt")],
            as_json=True,
        )
        second = json.loads(out)["runs"][1]
        self.assertEqual(second["exit"], 0, "run two saw run one's leftovers")
        self.assertFalse((tree / "stray.txt").exists(), "tree left dirty after the run")

    def test_a_staged_change_is_restored_between_runs(self):
        """The index is the one thing `checkout -- .` cannot undo.

        `git checkout -- .` restores the worktree *from the index*, so anything a
        gate staged survives it, and `clean -fd` will not remove a tracked file.
        Run one stages an edit; run two does nothing at all. Without a restore
        that resets the index, run two inherits the edit and is credited with it,
        and the tool reports GATES AGREE — the wrong direction to fail in for
        something whose whole job is reporting that two versions differ.

        Not exotic: `pre-commit` interacts with the index directly, and it is
        among the likeliest gate commands Phase 4 is handed for a Python repo.
        """
        tree = git_repo(self)
        code, out, _ = self._run(
            tree,
            [
                ("first", "printf 'MUTATED\\n' > tracked.txt && git add tracked.txt"),
                ("second", "true"),
            ],
        )
        self.assertEqual(code, 1, "run two did nothing and must not be credited with run one")
        self.assertIn("acted on by first only", out)
        left = subprocess.run(
            ["git", "-C", str(tree), "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(left.stdout.strip(), "", "a staged change was left in the index")
        self.assertEqual(
            (tree / "tracked.txt").read_text(encoding="utf-8"),
            "original\n",
            "the staged content survived the restore",
        )

    def test_the_tree_is_clean_when_the_tool_finishes(self):
        tree = git_repo(self)
        self._run(tree, [("a", "printf x > f.txt"), ("b", "printf 'edited' > tracked.txt")])
        left = subprocess.run(
            ["git", "-C", str(tree), "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(left.stdout.strip(), "", "the worktree must be left as found")

    def test_a_dirty_tree_is_refused(self):
        """The restore would discard uncommitted work, so it never starts."""
        tree = git_repo(self)
        (tree / "precious.txt").write_text("unsaved\n", encoding="utf-8")
        code, _, err = self._run(tree, [("a", "true"), ("b", "true")])
        self.assertEqual(code, 2)
        self.assertIn("refusing to run", err)
        self.assertTrue((tree / "precious.txt").exists(), "must not touch it")

    def test_a_non_git_tree_is_refused(self):
        directory = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        code, _, err = self._run(directory, [("a", "true"), ("b", "true")])
        self.assertEqual(code, 2)
        self.assertIn("not a git worktree", err)

    def test_an_unforeseen_exception_exits_2_not_1(self):
        """Exit 1 here means the runs disagreed. An unhandled exception exits 1
        too, so a crash reads as a finding unless the boundary is guarded."""
        tree = git_repo(self)
        with mock.patch("gate_diff.snapshot_changes", side_effect=RuntimeError("boom")):
            code, out, err = self._run(tree, [("a", "true"), ("b", "true")], entry_point=cli)
        self.assertEqual(code, 2)
        self.assertIn("RuntimeError", err)
        self.assertNotIn("RESULT", out)

    def test_the_guard_does_not_swallow_a_real_disagreement(self):
        """SystemExit re-raises first, or exit 1 and exit 2 both become 2."""
        tree = git_repo(self)
        code, out, _ = self._run(
            tree, [("locked", "true"), ("proposed", "printf x > doc.md")], entry_point=cli
        )
        self.assertEqual(code, 1)
        self.assertIn("GATES DIFFER", out)

    def test_a_single_run_is_refused(self):
        tree = git_repo(self)
        code, _, err = self._run(tree, [("only", "true")])
        self.assertEqual(code, 2)
        self.assertIn("at least two", err)


class TestNothingTouched(GateDiffHarness):
    def test_a_run_that_changed_nothing_says_so(self):
        """Measuring a --check invocation measures the weaker signal; say it."""
        tree = git_repo(self)
        _, out, _ = self._run(tree, [("locked", "echo a"), ("proposed", "echo b")])
        self.assertIn("no run changed any file", out)

    def test_the_note_does_not_assert_which_of_the_three_causes_it_was(self):
        """Observed live: on a repo already compliant with every version under
        test, the old note told the operator they had "measured the wrong thing"
        and to re-run with the write mode they had already given. An already-clean
        tree is a real agreement, not a mistake."""
        tree = git_repo(self)
        _, out, _ = self._run(tree, [("locked", "true"), ("proposed", "true")])
        note = out.split("NOTE:", 1)[1]
        self.assertIn("read-only mode", note, "cause 1: the wrong invocation")
        self.assertIn("already satisfies every version", note, "cause 2: a compliant tree")
        self.assertIn("no write mode", note, "cause 3: nothing to write")
        self.assertIn("decide which", note, "the note must hand the choice over, not make it")


class TestTheSnapshotReadsEveryPorcelainField(GateDiffHarness):
    """`--porcelain -z` emits a staged rename as *two* fields, not one.

    The format is `R  <new>\0<old>\0`, and only the first field carries the `XY `
    status prefix. A slice that strips three characters from every field turns
    `tracked.txt` into `cked.txt` — a path that does not exist, reported as
    deleted, while the real deletion of the source goes unreported. Measured
    against git's own output:

        field='R  renamed.txt'  -> line[3:]='renamed.txt'
        field='tracked.txt'     -> line[3:]='cked.txt'

    Both halves fail in the reporting direction this repo cares about: the run
    invents a change it did not observe, and drops one it did.

    Not exotic. `git status --porcelain` reports a rename only from the *index*,
    and `pre-commit` stages directly — `restore()`'s own docstring already names
    it as among the likeliest gate commands Phase 4 is handed.
    """

    def _changed(self, tree: pathlib.Path, command: str) -> dict[str, str]:
        _, out, _ = self._run(tree, [("locked", "true"), ("proposed", command)], as_json=True)
        changed: dict[str, str] = json.loads(out)["runs"][1]["changed"]
        return changed

    def test_a_staged_rename_does_not_manufacture_a_truncated_path(self):
        changed = self._changed(git_repo(self), "git mv tracked.txt renamed.txt")
        self.assertNotIn(
            "cked.txt",
            changed,
            "the old path was sliced as though it carried a status prefix",
        )

    def test_a_staged_rename_reports_both_of_its_real_paths(self):
        changed = self._changed(git_repo(self), "git mv tracked.txt renamed.txt")
        self.assertIn("renamed.txt", changed, "the destination is a real addition")
        self.assertIn("tracked.txt", changed, "the source is a real deletion")
        self.assertEqual(changed["tracked.txt"], "<deleted>")

    def test_an_unstaged_delete_and_add_is_unaffected(self):
        """The path the old slice handled correctly must keep working."""
        changed = self._changed(git_repo(self), "rm tracked.txt && printf x > fresh.txt")
        self.assertEqual(changed["tracked.txt"], "<deleted>")
        self.assertIn("fresh.txt", changed)


class TestGateResidueThatIsNotAFinding(GateDiffHarness):
    """A project-importing gate writes its own bookkeeping into the measured tree.

    Phase 4 drops `--no-project` for a type checker or a test suite, which is
    what makes this live: those gates leave caches and data files behind. The
    intuition is that any of it manufactures a difference, and the measurement
    says only one shape of it does.

    A directory does not, because two mechanisms have to line up and only ever
    one does: `git status --porcelain` collapses a wholly untracked directory to
    `dir/`, and `_content_key` returns None for anything that is not a file, so
    the entry is dropped before it can be compared. A file does, always -- a
    real `coverage 7.6.1 -> 7.13.0` bump reports `~ .coverage`, *both act,
    different result*, which reads as the destructive-fix case and is nothing
    but coverage's own data file.

    These pin the table in `references/uv-lock.md` Phase 4. Both halves matter:
    without the first, the reference tells auditors to go and gitignore caches
    that were never visible; without the second, the residue that does invent a
    finding looks equally harmless.
    """

    def test_an_untracked_directory_the_gate_leaves_behind_is_not_a_change(self):
        """Differing contents, and still no difference: the collapse hides it."""
        tree = git_repo(self)
        code, out, _ = self._run(
            tree,
            [
                ("locked", "mkdir -p .cache/v && printf one > .cache/v/meta.json"),
                ("proposed", "mkdir -p .cache/v && printf two > .cache/v/meta.json"),
            ],
        )
        self.assertEqual(code, 0, "a collapsed directory has no content to compare")
        self.assertIn("no run changed any file", out)

    def test_a_file_the_gate_leaves_behind_is_a_change(self):
        """The `.coverage` shape: a file at the root reaches the comparison."""
        tree = git_repo(self)
        code, out, _ = self._run(
            tree,
            [("locked", "printf one > .coverage"), ("proposed", "printf two > .coverage")],
        )
        self.assertEqual(code, 1)
        self.assertIn(".coverage", out)
        self.assertIn("different result", out)

    def test_expanding_untracked_files_surfaces_the_directory_residue(self):
        """`status.showUntrackedFiles=all` un-collapses it, and then it does fire.

        Measured on a real `pytest 8.4.2 -> 9.1.1` bump: pytest writes its own
        version into the name of every file it rewrites, so the two runs leave
        `...-pytest-8.4.2.pyc` and `...-pytest-9.1.1.pyc` and the comparison
        reports a `+`/`-` pair -- *widened scope* and *narrowed scope* -- for a
        gate that touched no code at all.
        """
        tree = git_repo(self)
        subprocess.run(
            ["git", "-C", str(tree), "config", "status.showUntrackedFiles", "all"],
            check=True,
            capture_output=True,
        )
        code, out, _ = self._run(
            tree,
            [
                ("locked", "mkdir -p .cache && printf x > .cache/a-1.0.pyc"),
                ("proposed", "mkdir -p .cache && printf x > .cache/a-2.0.pyc"),
            ],
        )
        self.assertEqual(code, 1, "the repo config is what decides this, not the gate")
        self.assertIn("acted on by proposed only", out)
        self.assertIn("acted on by locked only", out)


if __name__ == "__main__":
    unittest.main()
