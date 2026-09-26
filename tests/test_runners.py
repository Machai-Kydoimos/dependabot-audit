"""Regression tests for runners.py -- which runner every job lands on (#166).

`actions.md` § Phase 4 reads an environment variable's silence as `inert here` only
if every runner is GitHub-hosted, and until 0.56.0 Row 3 was a grep whose line for
`runs-on: ${{ matrix.os }}` said nothing about where the job runs. A matrix that
carries a self-hosted label behind the expression read, from the grep alone, as a
row with nothing wrong in it -- the false clean #162 closed, one row down.

The parser half was checked once against PyYAML on 132 real workflow files (564
jobs, no disagreement on `runs-on:`, `strategy:` or `uses:`); the cases here pin
the shapes that check and the corpus measurement turned up.

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import contextlib
import io
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from typing import Any
from unittest import mock

sys.path.insert(
    0, str(pathlib.Path(__file__).resolve().parent.parent / "skills/dependabot-audit/scripts")
)

from runners import Unreadable, classify, jobs, load, main

# Recorded from fpga-board-sim #436 at 629ed25, `.github/workflows/ci.yml`, trimmed
# to two of its jobs: the literal one and the matrix one Row 3's grep printed as
# `ci.yml:96: runs-on: ${{ matrix.os }}`. Comments kept, because the file has them.
FBS_CI = """\
# covers push and pull_request on hosted runners.  The problem was never that it
# was off, it was that the key churned.

jobs:
  lint:
    name: Lint & type-check
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1  # v7.0.1
      - run: |
          uv sync --group dev
          echo "runs-on: self-hosted"
  test:
    name: Test (${{ matrix.os }}, Python ${{ matrix.python-version }})
    runs-on: ${{ matrix.os }}
    timeout-minutes: 15
    strategy:
      # fail-fast off: the macOS/arm entries are experimental — a red one must
      # not cancel the required ubuntu/windows checks mid-run.
      fail-fast: false
      matrix:
        # macos-latest + ubuntu-24.04-arm are arm64.  cocotb publishes no Linux
        # aarch64 wheel, so the arm job also proves the sdist (C++ GPI) build.
        os: [ubuntu-latest, windows-latest, macos-latest, ubuntu-24.04-arm]
        python-version: ["3.10", "3.11", "3.12", "3.13", "3.14"]
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1  # v7.0.1
"""

REPO = "Machai-Kydoimos/fpga-board-sim"


def job(text: str, name: str = "j") -> dict[str, Any]:
    found = dict(jobs(load(text)))
    return found[name]


def state(yaml_text: str, repo: str = REPO, name: str = "j") -> tuple[str, str]:
    return classify(job(yaml_text, name), repo)


class TestTheKnownAnswerFrom436(unittest.TestCase):
    """The PR the issue came from: all GitHub-hosted, the matrix one included."""

    def test_both_jobs_resolve_to_hosted_labels(self):
        found = dict(jobs(load(FBS_CI)))
        self.assertEqual(classify(found["lint"], REPO), ("hosted", "ubuntu-latest"))
        self.assertEqual(
            classify(found["test"], REPO),
            ("hosted", "macos-latest, ubuntu-24.04-arm, ubuntu-latest, windows-latest"),
        )

    def test_a_run_script_that_mentions_a_runner_is_not_one(self):
        """`echo "runs-on: self-hosted"` sits in a block scalar, which is opaque."""
        steps = dict(jobs(load(FBS_CI)))["lint"]["steps"]
        self.assertIn('echo "runs-on: self-hosted"', steps[1]["run"])


class TestAMatrixIsResolvedNotTrusted(unittest.TestCase):
    """#166's case: the self-hosted label is behind the expression."""

    MATRIX = """\
jobs:
  j:
    runs-on: ${{ matrix.runner }}
    strategy:
      matrix:
        runner:
          - ubuntu-latest
          - [self-hosted, gpu]
"""

    def test_a_self_hosted_entry_behind_an_expression_is_found(self):
        verdict, detail = state(self.MATRIX)
        self.assertEqual(verdict, "not hosted")
        self.assertIn("self-hosted", detail)

    def test_include_entries_are_labels_too(self):
        text = """\
jobs:
  j:
    runs-on: ${{ matrix.os }}
    strategy:
      matrix:
        os: [ubuntu-latest]
        include:
          - os: my-own-box
            python: "3.12"
"""
        verdict, detail = state(text)
        self.assertEqual(verdict, "not hosted")
        self.assertIn("my-own-box", detail)

    def test_a_nested_field_and_an_interpolation_resolve(self):
        nested = """\
jobs:
  j:
    runs-on: ${{ matrix.platform.runner }}
    strategy:
      matrix:
        platform:
          - {runner: ubuntu-24.04, target: x86_64}
          - {runner: windows-11-arm, target: aarch64}
"""
        self.assertEqual(state(nested), ("hosted", "ubuntu-24.04, windows-11-arm"))
        suffix = """\
jobs:
  j:
    runs-on: ${{ matrix.os }}-latest
    strategy:
      matrix:
        os: [ubuntu, macos]
"""
        self.assertEqual(state(suffix), ("hosted", "macos-latest, ubuntu-latest"))

    def test_a_matrix_built_by_an_expression_is_underivable(self):
        text = """\
jobs:
  j:
    runs-on: ${{ matrix.os }}
    strategy:
      matrix: ${{ fromJSON(needs.plan.outputs.matrix) }}
"""
        self.assertEqual(state(text)[0], "underivable")

    def test_a_key_the_matrix_lacks_is_underivable_not_empty(self):
        text = "jobs:\n  j:\n    runs-on: ${{ matrix.os }}\n    strategy:\n      matrix:\n        py: [a]\n"
        verdict, detail = state(text)
        self.assertEqual(verdict, "underivable")
        self.assertIn("matrix.os", detail)


class TestTheRepositorysOwnIdentityIsKnown(unittest.TestCase):
    """159 of the corpus's jobs choose a runner by the repository they run in."""

    TERNARY = """\
jobs:
  j:
    runs-on: ${{ github.repository_owner == 'astral-sh' && 'github-ubuntu-24.04-x86_64-4' || 'ubuntu-latest' }}
"""

    def test_in_the_owners_repository_it_is_the_owners_runner(self):
        verdict, detail = state(self.TERNARY, repo="astral-sh/uv")
        self.assertEqual(verdict, "not hosted")
        self.assertIn("not a standard GitHub-hosted label: github-ubuntu-24.04-x86_64-4", detail)

    def test_in_a_fork_it_is_the_hosted_fallback(self):
        self.assertEqual(state(self.TERNARY, repo="someone/uv"), ("hosted", "ubuntu-latest"))

    def test_the_comparison_ignores_case_as_github_does(self):
        self.assertEqual(state(self.TERNARY, repo="Astral-SH/uv")[0], "not hosted")


class TestWhatTheTreeCannotSay(unittest.TestCase):
    def test_an_input_or_a_variable_is_underivable(self):
        for value in ("${{ inputs.runner }}", "${{ vars.RUNNER }}", "${{ fromJSON(vars.R) }}"):
            with self.subTest(value=value):
                self.assertEqual(state(f"jobs:\n  j:\n    runs-on: {value}\n")[0], "underivable")

    def test_a_runner_group_is_underivable(self):
        text = "jobs:\n  j:\n    runs-on:\n      group: big-boxes\n      labels: [linux]\n"
        verdict, detail = state(text)
        self.assertEqual(verdict, "underivable")
        self.assertIn("big-boxes", detail)

    def test_a_workflow_in_another_repository_runs_where_it_says(self):
        text = "jobs:\n  j:\n    uses: org/shared/.github/workflows/x.yml@v1\n"
        self.assertEqual(state(text)[0], "underivable")
        local = "jobs:\n  j:\n    uses: ./.github/workflows/x.yml\n"
        self.assertEqual(state(local)[0], "hosted")


class TestOnlyGitHubsOwnLabelsAreHosted(unittest.TestCase):
    def test_the_published_forms(self):
        for label in (
            "ubuntu-latest",
            "ubuntu-24.04-arm",
            "ubuntu-slim",
            "windows-11-arm",
            "windows-2025",
            "macos-15-intel",
            "macos-latest-xlarge",
        ):
            with self.subTest(label=label):
                self.assertEqual(state(f"jobs:\n  j:\n    runs-on: {label}\n")[0], "hosted")

    def test_a_name_someone_chose_is_not(self):
        for label in (
            "self-hosted",
            "[self-hosted, linux]",
            "depot-ubuntu-24.04-4",
            "namespace-profile-macos-15",
            "github-ubuntu-24.04-x86_64-4",
            "ubuntu-latest-8-cores",
        ):
            with self.subTest(label=label):
                self.assertEqual(state(f"jobs:\n  j:\n    runs-on: {label}\n")[0], "not hosted")


class TestTheParserRefusesWhatItDoesNotRead(unittest.TestCase):
    def test_an_anchor_is_unreadable_not_guessed(self):
        with self.assertRaises(Unreadable):
            load("defaults: &d\n  runs-on: ubuntu-latest\njobs:\n  j:\n    <<: *d\n")

    def test_an_escaped_quote_does_not_end_a_string(self):
        """cli/cli's generated `*.lock.yml`: the first corpus run read this as a
        closing quote, and the ` #` after it as a comment."""
        # An odd number of escaped quotes before the ` #`: with an even number, a
        # parser that toggles on every quote lands back inside the string anyway.
        text = (
            "jobs:\n  j:\n    runs-on: ubuntu-latest\n    steps:\n"
            '      - run: "rm -f \\"$PKGS # not a comment"\n'
        )
        self.assertEqual(state(text), ("hosted", "ubuntu-latest"))
        step = job(text)["steps"][0]["run"]
        self.assertIn("# not a comment", step)

    def test_a_compact_sequence_at_the_keys_indent_is_read(self):
        text = "on:\n  push:\n    branches:\n    - main\njobs:\n  j:\n    runs-on: ubuntu-latest\n"
        self.assertEqual(state(text), ("hosted", "ubuntu-latest"))


class TestTheScriptAtARef(unittest.TestCase):
    """End to end, against a throwaway repository: the files come from the ref."""

    def repo(self, files: dict[str, str]) -> pathlib.Path:
        where = pathlib.Path(self.tmp.name)
        for path, text in files.items():
            target = where / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull}
        for argv in (
            ["git", "init", "-q", "-b", "main"],
            ["git", "add", "-A"],
            ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "x"],
            ["git", "branch", "pr-1"],
        ):
            subprocess.run(argv, cwd=where, check=True, env=env, capture_output=True)
        return where

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def run_main(self, where: pathlib.Path, ref: str = "pr-1") -> tuple[int, str, str]:
        argv = ["runners.py", "--ref", ref, "--repo", REPO]
        out, err = io.StringIO(), io.StringIO()
        cwd = os.getcwd()
        try:
            os.chdir(where)
            with (
                mock.patch.object(sys, "argv", argv),
                contextlib.redirect_stdout(out),
                contextlib.redirect_stderr(err),
            ):
                try:
                    code: int | str | None = main()
                except SystemExit as exc:
                    code = exc.code
        finally:
            os.chdir(cwd)
        return int(code or 0), out.getvalue(), err.getvalue()

    def test_all_hosted_exits_0(self):
        where = self.repo({".github/workflows/ci.yml": FBS_CI})
        code, out, _ = self.run_main(where)
        self.assertEqual(code, 0, out)
        self.assertIn("RESULT: HOSTED -- every one of 2 job(s)", out)

    def test_one_job_elsewhere_exits_1_and_says_what_it_costs(self):
        other = "jobs:\n  deploy:\n    runs-on: [self-hosted, prod]\n"
        where = self.repo({".github/workflows/ci.yml": FBS_CI, ".github/workflows/cd.yml": other})
        code, out, _ = self.run_main(where)
        self.assertEqual(code, 1, out)
        self.assertIn("NOT HOSTED   .github/workflows/cd.yml deploy", out)
        self.assertIn("silence is not `inert here` for these", " ".join(out.split()))

    def test_an_unreadable_file_is_underivable_not_skipped(self):
        where = self.repo({".github/workflows/x.yml": "a: &x 1\njobs:\n  j:\n    runs-on: *x\n"})
        code, out, _ = self.run_main(where)
        self.assertEqual(code, 1, out)
        self.assertIn("unreadable here", out)

    def test_a_ref_that_does_not_resolve_exits_2(self):
        where = self.repo({".github/workflows/ci.yml": FBS_CI})
        code, _, err = self.run_main(where, ref="pr-999")
        self.assertEqual(code, 2)
        self.assertIn("cannot list", err)

    def test_the_checkout_is_not_what_is_read(self):
        """The checkout is on `main`, whose committed workflow differs from the PR's.
        Index, working tree and HEAD all say self-hosted; the ref says hosted."""
        where = self.repo({".github/workflows/ci.yml": FBS_CI})
        (where / ".github/workflows/ci.yml").write_text(
            "jobs:\n  j:\n    runs-on: self-hosted\n", encoding="utf-8"
        )
        subprocess.run(
            ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qam", "main moves"],
            cwd=where,
            check=True,
            capture_output=True,
        )
        code, out, _ = self.run_main(where)
        self.assertEqual(code, 0, out)


if __name__ == "__main__":
    unittest.main()
