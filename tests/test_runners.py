"""Regression tests for runners.py -- which runner every job lands on (#166).

`actions.md` § Phase 4 reads an environment variable's silence as `inert here` only
if every runner is GitHub-hosted, and until 0.56.0 Row 3 was a grep whose line for
`runs-on: ${{ matrix.os }}` said nothing about where the job runs. A matrix that
carries a self-hosted label behind the expression read, from the grep alone, as a
row with nothing wrong in it -- the false clean #162 closed, one row down.

The parser half was checked against PyYAML on 132 real workflow files (564 jobs, no
disagreement on `runs-on:`, `strategy:` or `uses:`), and again for 0.57.0 on 337
files, whole documents (#175); the cases here pin the shapes those checks and the
corpus measurement turned up.

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

from runners import Unreadable, classify, describe, jobs, load, main, triggers

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

    def test_an_include_built_by_an_expression_says_so(self):
        """psf/black's mypyc job. The loop walked the expression's characters and
        reported `matrix.os` missing, which was underivable for the wrong reason."""
        text = (
            "jobs:\n  j:\n    runs-on: ${{ matrix.os }}\n    strategy:\n      matrix:\n"
            "        include: ${{ fromJson(needs.configure.outputs.include) }}\n"
        )
        verdict, detail = state(text)
        self.assertEqual(verdict, "underivable")
        self.assertIn("`matrix.include` is built by an expression", detail)


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


# Recorded from psf/black at 8d5a2d9, `.github/workflows/test.yml`, the `test` job
# trimmed to what reaches its runner. Its `if:` runs over two more lines, and its
# `python-version:` list starts on the line after the key. Until 0.57.0 the first
# of those made the file unreadable, and 9 of black's 13 workflows with it (#175).
BLACK_TEST = """\
jobs:
  test:
    # We want to run on external PRs, but not on our own internal PRs as they'll be run
    # by the push to the branch. Without this if check, checks are duplicated since
    # internal PRs match both the push and pull_request events.
    if:
      github.event_name == 'push' || github.event.pull_request.head.repo.full_name !=
      github.repository

    runs-on: ${{ matrix.os }}
    strategy:
      fail-fast: false
      matrix:
        python-version:
          ["3.10", "3.11", "3.12.10", "3.13", "3.14", "3.15", "pypy3.11-v7.3.22"]
        os: [ubuntu-latest, macOS-latest, windows-latest, windows-11-arm]
"""


class TestAValueThatStartsOnTheNextLineIsRead(unittest.TestCase):
    """#175. A scalar on the line after its key, or over several lines, is YAML.

    Checked against PyYAML's `BaseLoader` on 304 workflow files from 22 repositories
    and 33 `action.yml` files: every document agrees, block scalars aside, which
    stay raw by design. The expected values below are PyYAML's on the same text.
    """

    def test_blacks_test_job_resolves(self):
        found = job(BLACK_TEST, "test")
        self.assertEqual(
            found["if"],
            "github.event_name == 'push' || "
            "github.event.pull_request.head.repo.full_name != github.repository",
        )
        self.assertEqual(found["strategy"]["matrix"]["python-version"][0], "3.10")
        self.assertEqual(
            classify(found, "psf/black"),
            ("hosted", "macOS-latest, ubuntu-latest, windows-11-arm, windows-latest"),
        )

    def test_a_quoted_value_on_the_next_line(self):
        """astral-sh/setup-uv's `action.yml` opens this way, at every tag read."""
        text = (
            'name: "astral-sh/setup-uv"\ndescription:\n'
            '  "Set up your GitHub Actions workflow with a specific version of uv."\n'
            'author: "astral-sh"\n'
        )
        self.assertEqual(
            load(text)["description"],
            "Set up your GitHub Actions workflow with a specific version of uv.",
        )

    def test_a_quoted_value_over_several_lines(self):
        """actions/download-artifact at 484a0b5: a single-quoted description over
        three lines, and the `default:` after it that a script would compare."""
        text = (
            "inputs:\n"
            "  merge-multiple:\n"
            "    description: 'When multiple artifacts are matched, this changes the"
            " behavior of the destination directories.\n"
            "      If true, the downloaded artifacts will be in the same directory"
            " specified by path.\n"
            "      If false, the downloaded artifacts will be extracted into individual"
            " named directories within the specified path.'\n"
            "    required: false\n"
            "    default: 'false'\n"
        )
        entry = load(text)["inputs"]["merge-multiple"]
        self.assertEqual(entry["default"], "false")
        self.assertIn("directories. If true, the downloaded", entry["description"])
        self.assertTrue(entry["description"].endswith("within the specified path."))

    def test_lines_fold_as_yaml_folds_them(self):
        cases = {
            "a:\n  one\n  two\n\n  three\n": "one two\nthree",
            'a: "one \\\n    two"\n': "one two",
            'a: "one\n\n  two"\n': "one\ntwo",
            'a: "one\n  # not a comment"\n': "one # not a comment",
            "a: 'it''s\n  here'\n": "it's here",
            'a: "tab\\tnl\\n"\n': "tab\tnl\n",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(load(text)["a"], expected)

    def test_a_dash_then_a_scalar_below_it(self):
        self.assertEqual(
            load("a:\n  -\n    one\n    two\n  - three\n"), {"a": ["one two", "three"]}
        )

    def test_what_is_not_yaml_stays_unreadable(self):
        """PyYAML refuses the first three. It reads the fourth as `one b: x`, but
        a line at the key's own indent reads as the next key, so that is refused
        here rather than guessed at."""
        for text in (
            "a:\n  one\n  # note\n  two\nb: x\n",
            'a: "one\nb: x\n',
            'a: "one\n  two" tail\n',
            'a: "one\nb: x"\n',
        ):
            with self.subTest(text=text), self.assertRaises(Unreadable):
                load(text)


# Recorded 2026-09-26: the `on:` of Homebrew/brew's `tests.yml` (ce46735), which
# runs in a merge queue, and of pydantic/pydantic's `ci.yml` (bb6da4c), whose
# `tags:` sits three lines below `push:`, past the old grep's two.
BREW_ON = """\
on:
  push:
    branches:
      - main
      - master
  pull_request:
  merge_group:
"""

PYDANTIC_ON = """\
on:
  push:
    branches:
      - main
    tags:
      - '**'
  pull_request: {}
"""


def listed(text: str) -> str:
    return ", ".join(describe(event, spec) for event, spec in triggers(load(text)).items())


class TestRow1ListsEveryTrigger(unittest.TestCase):
    """#172. Row 1 was an alternation of three event names and a `tags:` grep two
    lines deep. The list cannot leave an event out, and says which refs a push takes."""

    def test_a_merge_queue_is_listed(self):
        self.assertEqual(
            listed(BREW_ON),
            "push [branches: main, master; no tag pushes], pull_request, merge_group",
        )

    def test_a_tag_filter_below_a_branch_list_is_found(self):
        self.assertEqual(listed(PYDANTIC_ON), "push [branches: main; tags: **], pull_request")

    def test_a_push_with_no_ref_filter_runs_on_every_tag(self):
        """17 of the corpus's 36 tag-push workflows looked like this or like
        pydantic's, and the old grep missed every one."""
        for text in (
            "on: push\n",
            "on: [push, pull_request]\n",
            "on:\n  push:\n    paths:\n      - src/**\n",
        ):
            with self.subTest(text=text):
                self.assertIn("push [every branch and tag", listed(text))

    def test_the_filters_are_shown_as_written(self):
        text = (
            "on:\n  release:\n    types: [published]\n"
            "  workflow_run:\n    workflows: [CI]\n    types: [completed]\n"
            "  push:\n    tags-ignore: ['**']\n  schedule:\n    - cron: '0 3 * * 1'\n"
        )
        self.assertEqual(
            listed(text),
            "release [types: published], workflow_run [types: completed; workflows: CI], "
            "push [tags-ignore: **], schedule",
        )

    def test_no_on_key_starts_nothing(self):
        self.assertEqual(triggers(load("jobs:\n  j:\n    runs-on: ubuntu-latest\n")), {})


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

    def test_row_1_lists_every_workflows_triggers_and_indexes_them(self):
        where = self.repo(
            {
                ".github/workflows/ci.yml": BREW_ON + FBS_CI,
                ".github/workflows/docs.yml": "on: [push, pull_request]\n",
            }
        )
        code, out, _ = self.run_main(where)
        self.assertEqual(code, 0, out)
        self.assertIn(
            ".github/workflows/ci.yml: push [branches: main, master; no tag pushes], "
            "pull_request, merge_group",
            out,
        )
        self.assertIn(".github/workflows/docs.yml: push [every branch and tag], pull_request", out)
        self.assertIn("events: merge_group (1), pull_request (2), push (2)", out)

    def test_an_unreadable_file_has_unknown_triggers_not_none(self):
        where = self.repo(
            {
                ".github/workflows/ci.yml": BREW_ON + FBS_CI,
                ".github/workflows/x.yml": "on: &x push\njobs:\n  j:\n    runs-on: ubuntu-latest\n",
            }
        )
        code, out, _ = self.run_main(where)
        self.assertEqual(code, 1, out)
        flat = " ".join(out.split())
        self.assertIn("x.yml: unreadable here, so its triggers are unknown -- not none", flat)
        self.assertIn("1 file(s) unreadable, so an event missing from this index", flat)

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
