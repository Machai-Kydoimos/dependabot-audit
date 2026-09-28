"""exercised.py against GitHub's own record of two merged PRs (#176).

The hermetic suite fakes `gh`, so it can only agree with what this repo believes
the runs, jobs and steps APIs return, and how GitHub names an unnamed step. These
two PRs are merged, so their runs on the head commit do not change:

  - `fpga-board-sim` #438, a `uv.lock` bump. Every CI job ran `uv sync --group
    dev`. `install-docs.yml` filters its `pull_request` trigger by path and did
    not run, which the trigger read Phase 6 used to do would not have seen;
  - `cli/cli` #14486, the `codeql-actions` group. `codeql.yml` ran the bumped
    `init`; `govulncheck.yml` starts on `schedule` and `workflow_dispatch` only,
    so its bumped `upload-sarif` ran nowhere on the PR.

    RUN_NETWORK_TESTS=1 python3 -m unittest discover -s integration -v

Needs `gh` with a token. Run records outlive their logs, so these hold as long as
the runs exist; a PR whose runs were deleted would fail here, loudly.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys
import unittest

SCRIPT = (
    pathlib.Path(__file__).resolve().parent.parent / "skills/dependabot-audit/scripts/exercised.py"
)

live = unittest.skipUnless(
    os.environ.get("RUN_NETWORK_TESTS") and shutil.which("gh"),
    "set RUN_NETWORK_TESTS=1, with gh on PATH; this reads GitHub's run records",
)


def exercised(repo: str, number: int, head: str) -> tuple[int, str]:
    owner, name = repo.split("/")
    done = subprocess.run(
        [sys.executable, str(SCRIPT), "--owner", owner, "--name", name,
         "--number", str(number), "--head-sha", head],
        capture_output=True, text=True, check=False, timeout=600,
    )  # fmt: skip
    return done.returncode, done.stdout + done.stderr


@live
class TestTheRunsThatHappened(unittest.TestCase):
    def test_a_lockfile_bump_every_job_synced(self) -> None:
        code, out = exercised(
            "Machai-Kydoimos/fpga-board-sim", 438, "4f01a2ccb41d399b0c5cc4253debb9cbe66c9e48"
        )
        self.assertEqual(code, 0, out)
        self.assertIn("uv.lock -- installed from by `uv sync` or `uv run`: EXERCISED", out)
        self.assertIn("step `Run uv sync --group dev` success", out)
        flat = " ".join(out.split())
        self.assertIn(
            "NO RUN .github/workflows/install-docs.yml -- no run on this commit; it starts on:",
            flat,
        )
        self.assertIn("pull_request [paths: docs/install.md", flat)

    def test_a_workflow_that_never_runs_on_a_pr_never_ran_the_bump(self) -> None:
        code, out = exercised("cli/cli", 14486, "d57803733dbaebb919ca873c6b381ea64b0bbc34")
        self.assertEqual(code, 1, out)
        self.assertIn(".github/workflows/govulncheck.yml -- changes", out)
        self.assertIn("govulncheck.yml -- no run on this commit; it starts on: schedule", out)
        self.assertIn("step `Initialize CodeQL` success", out)


if __name__ == "__main__":
    unittest.main()
