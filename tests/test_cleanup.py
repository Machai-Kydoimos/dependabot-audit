"""Regression tests for cleanup.py.

No network. Every case is a real git worktree in a throwaway repo, because what
is under test is git's own behaviour — which of `remove`'s refusals are real, and
which one two separate audits inferred and got wrong.

    python3 -m unittest discover -s tests -v

The case that matters most is `test_residue_is_written_before_the_tree_goes`:
without it, forcing the removal discards the evidence the audit produced and has
not yet reported, which is the failure `gate_diff.py`'s `restore()` reasons about
when it declines `-x`.

One case deliberately uses a **real** `uv` venv rather than a hand-written
`.gitignore`, and skips where `uv` is absent. CONTRIBUTING's rule is that a
fixture built from the rule can only ever agree with it, and the rule here is a
claim about what uv writes — so at least one test has to ask uv rather than
restate it. The hand-built equivalent stays too: it is what CI on four
interpreters actually runs.
"""

from __future__ import annotations

import contextlib
import io
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

from cleanup import cli, main

PR = "7"


def git(directory: pathlib.Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(directory), *args], capture_output=True, text=True, check=False
    )


class CleanupHarness(unittest.TestCase):
    """A repo with `pr-7` and `base-7` worktrees under a scratch directory."""

    def setUp(self):
        root = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        self.repo = root / "repo"
        self.scratch = root / "scratch"
        self.scratch.mkdir(parents=True)
        self.repo.mkdir()
        (self.repo / "tracked.txt").write_text("original\n", encoding="utf-8")
        for args in (
            ["init", "-q", "-b", "main"],
            ["add", "-A"],
            ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base"],
        ):
            git(self.repo, *args)

    def add_worktrees(self, *names: str) -> None:
        for name in names:
            path = self.scratch / f"{name}-{PR}"
            if name == "pr":
                git(self.repo, "worktree", "add", "-q", str(path), "-b", f"pr-{PR}")
            else:
                git(self.repo, "worktree", "add", "-q", "--detach", str(path), "HEAD")

    def run_cleanup(self) -> tuple[int, str]:
        argv = ["cleanup.py", "--scratch", str(self.scratch), "--pr", PR, "--repo", str(self.repo)]
        out = io.StringIO()
        with (
            mock.patch.object(sys, "argv", argv),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            code = main()
        return code, out.getvalue()

    def worktrees(self) -> str:
        return git(self.repo, "worktree", "list").stdout

    def residue(self, name: str) -> pathlib.Path:
        return self.scratch / f"residue-{name}-{PR}.diff"


class TestACleanWorktreeJustGoes(CleanupHarness):
    def test_both_worktrees_and_the_branch_are_removed(self):
        self.add_worktrees("pr", "base")
        code, out = self.run_cleanup()
        self.assertEqual(code, 0, out)
        self.assertNotIn(f"pr-{PR}", self.worktrees())
        self.assertNotIn(f"base-{PR}", self.worktrees())
        self.assertEqual(git(self.repo, "branch", "--list", f"pr-{PR}").stdout.strip(), "")
        self.assertIn("no residue", out)

    def test_a_missing_path_is_not_an_error(self):
        """Measured for #90: `remove` on a path that is gone exits 0."""
        self.add_worktrees("pr", "base")
        shutil.rmtree(self.scratch / f"pr-{PR}")
        code, out = self.run_cleanup()
        self.assertEqual(code, 0, out)
        self.assertNotIn(f"pr-{PR}", self.worktrees())

    def test_an_actions_bump_has_only_a_branch_to_remove(self):
        """Phase 0 creates no worktree for an actions bump; the branch still exists."""
        git(self.repo, "branch", f"pr-{PR}")
        code, out = self.run_cleanup()
        self.assertEqual(code, 0, out)
        self.assertIn(f"branch pr-{PR}", out)
        self.assertEqual(git(self.repo, "branch", "--list", f"pr-{PR}").stdout.strip(), "")

    def test_a_rewritten_base_leaves_a_tip_worktree_to_remove(self):
        self.add_worktrees("pr", "base", "tip")
        code, out = self.run_cleanup()
        self.assertEqual(code, 0, out)
        self.assertIn(f"tip-{PR}", out)
        self.assertNotIn(f"tip-{PR}", self.worktrees())


class TestIgnoredResidueIsNotResidue(CleanupHarness):
    """The claim two separate hand-backs got wrong, in both its forms."""

    def test_a_self_ignoring_venv_does_not_count(self):
        self.add_worktrees("pr", "base")
        venv = self.scratch / f"pr-{PR}" / ".venv"
        venv.mkdir()
        (venv / ".gitignore").write_text("*\n", encoding="utf-8")
        (venv / "pyvenv.cfg").write_text("home = /usr\n", encoding="utf-8")
        code, out = self.run_cleanup()
        self.assertEqual(code, 0, out)
        self.assertIn("no residue", out)
        self.assertFalse(self.residue("pr").exists())

    @unittest.skipUnless(shutil.which("uv"), "uv not installed")
    def test_a_real_uv_venv_does_not_count_either(self):
        """Ask uv what it writes rather than restating it.

        The hand-built case above encodes this repo's *claim* about uv. If uv
        stopped writing a self-ignoring `.venv`, that test would keep passing and
        the plugin's prose would be wrong with a green suite behind it.
        """
        self.add_worktrees("pr")
        tree = self.scratch / f"pr-{PR}"
        made = subprocess.run(
            ["uv", "venv", "--quiet"], cwd=tree, capture_output=True, text=True, check=False
        )
        if made.returncode != 0:  # pragma: no cover - uv present but unusable
            self.skipTest(f"uv venv failed: {made.stderr.strip()}")
        self.assertTrue((tree / ".venv" / ".gitignore").exists(), "uv no longer self-ignores")
        code, out = self.run_cleanup()
        self.assertEqual(code, 0, out)
        self.assertIn("no residue", out)

    def test_an_untracked_file_with_no_ignore_rule_does_count(self):
        """`__pycache__` is the one piece of test residue that writes no self-ignore."""
        self.add_worktrees("pr")
        cache = self.scratch / f"pr-{PR}" / "tests" / "__pycache__"
        cache.mkdir(parents=True)
        (cache / "t.pyc").write_bytes(b"\x00")
        code, out = self.run_cleanup()
        self.assertEqual(code, 1, out)
        self.assertIn("0 tracked file(s) changed, 1 untracked", out)


class TestResidueIsReportedAndSaved(CleanupHarness):
    """Phase 5 runs the repo's own gates in `pr-<N>` and nothing restores it."""

    def dirty_tracked(self) -> pathlib.Path:
        tree = self.scratch / f"pr-{PR}"
        (tree / "tracked.txt").write_text("reformatted by a fix-mode gate\n", encoding="utf-8")
        return tree

    def test_residue_is_written_before_the_tree_goes(self):
        self.add_worktrees("pr")
        self.dirty_tracked()
        code, out = self.run_cleanup()
        self.assertEqual(code, 1, "residue is a finding, not a cleanup failure")
        self.assertNotIn(f"pr-{PR}", self.worktrees(), "the tree must still be removed")
        saved = self.residue("pr")
        self.assertTrue(saved.exists(), "the evidence must outlive the tree that held it")
        body = saved.read_text(encoding="utf-8")
        self.assertIn("M tracked.txt", body)
        self.assertIn("reformatted by a fix-mode gate", body, "the diff is the finding")

    def test_a_staged_fix_is_residue_too(self):
        """`pre-commit` stages its own edits, per gate_diff's restore() docstring."""
        self.add_worktrees("pr")
        tree = self.dirty_tracked()
        git(tree, "add", "tracked.txt")
        code, out = self.run_cleanup()
        self.assertEqual(code, 1, out)
        self.assertIn("1 tracked file(s) changed", out)
        self.assertIn("reformatted by a fix-mode gate", self.residue("pr").read_text())

    def test_the_exit_code_says_finding_not_failure(self):
        """0 = clean, 1 = ran and found something, 2 = could not run."""
        self.add_worktrees("pr")
        clean, _ = self.run_cleanup()
        self.assertEqual(clean, 0)
        self.add_worktrees("pr")
        self.dirty_tracked()
        dirty, _ = self.run_cleanup()
        self.assertEqual(dirty, 1)

    def test_a_dirty_base_gets_its_own_file(self):
        """`base-<N>` dirty means gate_diff's restore did not hold — a different finding."""
        self.add_worktrees("pr", "base")
        self.dirty_tracked()
        (self.scratch / f"base-{PR}" / "tracked.txt").write_text("gate residue\n", encoding="utf-8")
        code, _ = self.run_cleanup()
        self.assertEqual(code, 1)
        self.assertTrue(self.residue("pr").exists())
        self.assertTrue(self.residue("base").exists())
        self.assertNotIn("gate residue", self.residue("pr").read_text())


class TestItRefusesRatherThanGuessing(CleanupHarness):
    def test_a_non_repo_is_exit_2(self):
        argv = ["cleanup.py", "--scratch", str(self.scratch), "--pr", PR, "--repo", str(self.scratch)]
        with (
            mock.patch.object(sys, "argv", argv),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()) as err,
        ):
            with self.assertRaises(SystemExit) as raised:
                main()
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("not a git repository", err.getvalue())

    def test_a_missing_scratch_is_exit_2_not_a_silent_success(self):
        argv = ["cleanup.py", "--scratch", str(self.scratch / "gone"), "--pr", PR,
                "--repo", str(self.repo)]
        with (
            mock.patch.object(sys, "argv", argv),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()) as err,
        ):
            with self.assertRaises(SystemExit) as raised:
                main()
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("re-derive $SCRATCH", err.getvalue())

    def test_an_unexpected_error_is_exit_2_never_1(self):
        """Exit 1 means residue. A crash reported as 1 reads as a gate rewriting files."""
        with (
            mock.patch("cleanup.main", side_effect=RuntimeError("boom")),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()) as err,
        ):
            with self.assertRaises(SystemExit) as raised:
                cli()
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("bug, not a finding", err.getvalue())
