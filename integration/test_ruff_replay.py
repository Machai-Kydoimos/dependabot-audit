"""The end-to-end evidence for gate_diff.py, made re-runnable.

The README used to claim this as validation while nothing re-ran it:

    replaying Dependabot's ruff 0.15.22 -> 0.16.0 PR against the tree as it
    stood that day reproduces the six Markdown files the newer version started
    formatting -- while both versions exit 0

That was true, verified by hand, once, against a tree in a different repository
at a commit not referenced here. It is the load-bearing evidence for this
plugin's most novel component, and a reader could not re-run it, inspect the
inputs, or confirm the numbers. It was also the claim most likely to rot.

This is that replay, against a fixture small enough to check in. It is the only
case in either suite that exercises a real tool rather than a shell one-liner,
which is exactly why it cannot live in the hermetic suite: it needs the network
to fetch two historical ruff releases.

    RUN_NETWORK_TESTS=1 python3 -m unittest discover -s integration -v

Both versions are historical and immutable on PyPI, so this does not drift.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
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

from gate_diff import main

FIXTURE = pathlib.Path(__file__).resolve().parent / "fixtures/ruff-md-fences"
LOCKED = "0.15.22"
PROPOSED = "0.16.0"

requires_network = unittest.skipUnless(
    os.environ.get("RUN_NETWORK_TESTS"),
    "set RUN_NETWORK_TESTS=1; this fetches two ruff releases from PyPI",
)


@requires_network
class TestTheRuffReplay(unittest.TestCase):
    """ruff 0.16 started formatting Python code fences inside Markdown.

    The point is not that ruff changed. It is that *no* signal a reader would
    normally check reports it: both versions exit 0, and the output text is not
    comparable across them because the renderer changed too. Only the tree
    delta says what happened.
    """

    def setUp(self):
        self.tree = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tree, ignore_errors=True)
        shutil.copytree(FIXTURE, self.tree, dirs_exist_ok=True)
        for args in (
            ["init", "-q", "-b", "main"],
            ["add", "-A"],
            ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "the tree that day"],
        ):
            subprocess.run(["git", "-C", str(self.tree), *args], check=True, capture_output=True)

    def _replay(self):
        argv = [
            "gate_diff.py",
            "--tree",
            str(self.tree),
            "--json",
            "--run",
            "locked",
            f"uv run --no-project --with ruff=={LOCKED} ruff format .",
            "--run",
            "proposed",
            f"uv run --no-project --with ruff=={PROPOSED} ruff format .",
        ]
        out = io.StringIO()
        with (
            mock.patch.object(sys, "argv", argv),
            contextlib.redirect_stdout(out),
        ):
            try:
                code: int | str | None = main()
            except SystemExit as exc:
                code = exc.code
        return code, json.loads(out.getvalue())

    def test_the_scope_moves_while_both_versions_exit_zero(self):
        code, report = self._replay()
        locked, proposed = report["runs"]

        self.assertEqual(locked["exit"], 0, "the older version passes this tree")
        self.assertEqual(proposed["exit"], 0, "and so does the newer one")
        self.assertEqual(locked["changed"], {}, f"ruff {LOCKED} must leave the Markdown alone")

        markdown = sorted(p for p in proposed["changed"] if p.endswith(".md"))
        self.assertEqual(len(markdown), 6, f"expected six Markdown files, got {markdown}")
        self.assertEqual(
            sorted(proposed["changed"]),
            markdown,
            "only the Markdown moved; the .py file was already formatted",
        )

        self.assertEqual(code, 1, "a moved scope is a difference")
        self.assertEqual(report["comparisons"][0]["only_in_other"], markdown)

    def test_the_exit_codes_alone_report_nothing(self):
        """The finding this tool exists for, stated as an assertion.

        Comparing the two versions by exit code — the obvious approach — reports
        no difference at all on precisely the case worth catching.
        """
        _, report = self._replay()
        self.assertFalse(
            report["comparisons"][0]["exit_changed"],
            "if this ever fails, the exit code caught it and the tree diff was not needed",
        )


if __name__ == "__main__":
    unittest.main()
