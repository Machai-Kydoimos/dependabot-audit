"""Regression tests for exercised.py -- did a run on this commit exercise the change?

No network: `_gh` is the single seam every call goes through, so the fake below
answers by call shape and drives the real matching.

Phase 6 answered this by prediction until 0.57.0: an install-step grep and a
`sed` over each workflow's `on:` block, read for `pull_request`. It never read a
path filter, and 25 of 52 `pull_request` triggers across 12 repositories carry
one (#176). Each case below is a way the prediction and the runs disagree, most
of them recorded from a live PR.

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

from exercised import bumped_actions, changed_values, main, step_name

HEAD = "4f01a2ccb41d399b0c5cc4253debb9cbe66c9e48"

# fpga-board-sim at #438's head, trimmed: the lint job and the matrix test job,
# each syncing from uv.lock, and install-docs.yml, which filters its PR trigger
# by path and so did not run on a lockfile bump.
CI = """\
name: CI
on:
  push:
    branches: [main]
  pull_request:
jobs:
  lint:
    name: Lint & type-check
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1  # v7.0.1
      - uses: astral-sh/setup-uv@bec219d24cd3e171d82865faccec33120bb574f4  # v10.1.0
        with:
          cache-dependency-glob: uv.lock
      - run: uv sync --group dev
      - run: uv run ruff check .
  test:
    name: Test (${{ matrix.os }}, Python ${{ matrix.python-version }})
    runs-on: ${{ matrix.os }}
    strategy:
      matrix:
        os: [ubuntu-latest, windows-latest]
        python-version: ["3.12"]
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1  # v7.0.1
      - uses: astral-sh/setup-uv@bec219d24cd3e171d82865faccec33120bb574f4  # v10.1.0
      - run: uv sync --group dev
"""

INSTALL_DOCS = """\
name: Install docs
on:
  schedule:
    - cron: "17 6 * * 1"
  workflow_dispatch:
  pull_request:
    paths:
      - docs/install.md
      - README.md
      - .github/workflows/install-docs.yml
jobs:
  install:
    runs-on: ubuntu-latest
    steps:
      - run: uv sync
"""

SYNCED = [
    {"name": "Set up job", "conclusion": "success"},
    {"name": "Run actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1", "conclusion": "success"},
    {"name": "Run astral-sh/setup-uv@bec219d24cd3e171d82865faccec33120bb574f4", "conclusion": "success"},
    {"name": "Run uv sync --group dev", "conclusion": "success"},
    {"name": "Post Run astral-sh/setup-uv@bec219d24cd3e171d82865faccec33120bb574f4", "conclusion": "success"},
    {"name": "Complete job", "conclusion": "success"},
]  # fmt: skip


def job(
    name: str, conclusion: str = "success", steps: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    return {"name": name, "status": "completed", "conclusion": conclusion, "steps": steps or []}


def run(
    run_id: int, path: str, status: str = "completed", conclusion: str = "success"
) -> dict[str, Any]:
    return {
        "id": run_id, "name": path, "path": path, "event": "pull_request",
        "status": status, "conclusion": conclusion,
    }  # fmt: skip


class Harness(unittest.TestCase):
    head = HEAD

    def fake(
        self,
        files: list[dict[str, Any]],
        workflows: dict[str, str],
        runs: list[dict[str, Any]],
        jobs: dict[int, list[dict[str, Any]]] | None = None,
        statuses: list[dict[str, Any]] | None = None,
    ) -> Any:
        jobs = jobs or {}
        self.calls: list[str] = []

        def gh(args: list[str]) -> str:
            joined = " ".join(args)
            self.calls.append(joined)
            if "graphql" in joined:
                entries = [
                    {"name": path.rsplit("/", 1)[1], "type": "blob",
                     "object": {"text": text, "isBinary": False}}
                    for path, text in workflows.items()
                ]  # fmt: skip
                return json.dumps({"data": {"repository": {"object": {"entries": entries}}}})
            if "/pulls/" in joined and "/files" in joined:
                return "\n".join(json.dumps(f) for f in files)
            if "/pulls/" in joined and ".head.sha" in joined:
                return self.head + "\n"
            if "actions/runs?head_sha=" in joined:
                return "\n".join(json.dumps(r) for r in runs)
            for run_id, listed in jobs.items():
                if f"actions/runs/{run_id}/jobs" in joined:
                    return "\n".join(json.dumps(j) for j in listed)
            if "/status" in joined:
                return "\n".join(json.dumps(s) for s in statuses or [])
            return ""

        return gh

    def main(self, gh: Any) -> tuple[int, str, str]:
        argv = ["exercised.py", "--owner", "o", "--name", "r", "--number", "1", "--head-sha", HEAD]
        out, err = io.StringIO(), io.StringIO()
        with (
            mock.patch("exercised._gh", gh),
            mock.patch.object(sys, "argv", argv),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            try:
                code: int | str | None = main()
            except SystemExit as exc:
                code = exc.code
        return int(code or 0), out.getvalue(), err.getvalue()


WORKFLOWS = {".github/workflows/ci.yml": CI, ".github/workflows/install-docs.yml": INSTALL_DOCS}
LOCKFILE = [{"filename": "uv.lock", "status": "modified", "patch": "@@ ruff 0.16.7 -> 0.16.8"}]


class TestTheKnownAnswerFrom438(Harness):
    """A lockfile bump: every job that ran synced from it; the one workflow that
    would have installed it filtered its PR trigger by path, and did not run."""

    def test_the_sync_steps_that_ran_exercised_it(self):
        gh = self.fake(
            LOCKFILE,
            WORKFLOWS,
            [run(7, ".github/workflows/ci.yml")],
            {
                7: [
                    job("Lint & type-check", steps=SYNCED),
                    job("Test (ubuntu-latest, Python 3.12)", steps=SYNCED),
                    job("Test (windows-latest, Python 3.12)", steps=SYNCED),
                ]
            },
        )
        code, out, _ = self.main(gh)
        self.assertEqual(code, 0, out)
        self.assertIn("uv.lock -- installed from by `uv sync` or `uv run`: EXERCISED", out)
        self.assertIn("Lint & type-check -- step `Run uv sync --group dev` success", out)
        self.assertIn("RESULT: EXERCISED -- every one of 1 changed file(s)", out)

    def test_the_path_filtered_workflow_says_why_it_did_not_run(self):
        gh = self.fake(
            LOCKFILE,
            WORKFLOWS,
            [run(7, ".github/workflows/ci.yml")],
            {7: [job("Lint & type-check", steps=SYNCED)]},
        )
        _, out, _ = self.main(gh)
        flat = " ".join(out.split())
        # The matrix job never appears among the runs' jobs, and that is listed.
        self.assertIn(
            "ci.yml Test (${{ matrix.os }}, Python ${{ matrix.python-version }}) -- no run of this job",
            flat,
        )
        self.assertIn(
            "NO RUN .github/workflows/install-docs.yml -- no run on this commit; it starts on: "
            "schedule, workflow_dispatch, pull_request [paths: docs/install.md, README.md",
            flat,
        )

    def test_only_the_workflows_that_matter_have_their_jobs_read(self):
        """One jobs call per run that holds a job syncing the lockfile, and none
        for a run of a workflow that does not."""
        other = "on: pull_request\njobs:\n  docs:\n    runs-on: ubuntu-latest\n    steps:\n      - run: make docs\n"
        gh = self.fake(
            LOCKFILE,
            {**WORKFLOWS, ".github/workflows/docs.yml": other},
            [run(7, ".github/workflows/ci.yml"), run(8, ".github/workflows/docs.yml")],
            {7: [job("Lint & type-check", steps=SYNCED)], 8: [job("docs")]},
        )
        self.main(gh)
        jobs_calls = [c for c in self.calls if "/jobs" in c]
        self.assertEqual(len(jobs_calls), 1, jobs_calls)
        self.assertIn("actions/runs/7/jobs", jobs_calls[0])


class TestAGreenJobCanSkipTheStepThatMatters(Harness):
    """A step-level `if:` skips one step and leaves its job `success` -- fpga-board-sim's
    own `Install GHDL :: skipped` in a green Windows job. The job's conclusion alone
    reads that as exercised."""

    def test_a_skipped_step_is_not_exercise(self):
        skipped = [dict(s) for s in SYNCED]
        skipped[3]["conclusion"] = "skipped"
        gh = self.fake(
            LOCKFILE,
            WORKFLOWS,
            [run(7, ".github/workflows/ci.yml")],
            {7: [job("Lint & type-check", steps=skipped)]},
        )
        code, out, _ = self.main(gh)
        self.assertEqual(code, 1, out)
        self.assertIn("job success, but step `Run uv sync --group dev` skipped", out)
        self.assertIn("NOT EXERCISED", out)

    def test_a_skipped_job_is_not_exercise(self):
        gh = self.fake(
            LOCKFILE,
            WORKFLOWS,
            [run(7, ".github/workflows/ci.yml")],
            {7: [job("Lint & type-check", conclusion="skipped")]},
        )
        code, out, _ = self.main(gh)
        self.assertEqual(code, 1, out)
        self.assertIn("Lint & type-check -- job skipped", out)

    def test_uv_run_alone_installs_from_the_lockfile(self):
        """`uv run` syncs the environment before it runs, so a job with no
        `uv sync` step still installs from `uv.lock`."""
        wf = "on: pull_request\njobs:\n  t:\n    runs-on: x\n    steps:\n      - run: uv run pytest\n"
        gh = self.fake(
            LOCKFILE,
            {".github/workflows/ci.yml": wf},
            [run(7, ".github/workflows/ci.yml")],
            {7: [job("t", steps=[{"name": "Run uv run pytest", "conclusion": "success"}])]},
        )
        code, out, _ = self.main(gh)
        self.assertEqual(code, 0, out)

    def test_a_failing_step_still_ran_it(self):
        """Red is not unexercised: the change ran, and failed."""
        failed = [dict(s) for s in SYNCED]
        failed[3]["conclusion"] = "failure"
        gh = self.fake(
            LOCKFILE,
            WORKFLOWS,
            [run(7, ".github/workflows/ci.yml", conclusion="failure")],
            {7: [job("Lint & type-check", conclusion="failure", steps=failed)]},
        )
        code, out, _ = self.main(gh)
        self.assertEqual(code, 0, out)
        self.assertIn("step `Run uv sync --group dev` failure", out)


class TestAnActionsBumpIsFollowedToItsStep(Harness):
    PATCH = (
        "@@ -10,7 +10,7 @@\n"
        "-      - uses: astral-sh/setup-uv@1111111111111111111111111111111111111111  # v10.0.1\n"
        "+      - uses: astral-sh/setup-uv@bec219d24cd3e171d82865faccec33120bb574f4  # v10.1.0\n"
    )

    def test_the_default_step_name_matches(self):
        """fpga-board-sim #436: GitHub names an unnamed `uses:` step `Run <uses>`."""
        gh = self.fake(
            [{"filename": ".github/workflows/ci.yml", "status": "modified", "patch": self.PATCH}],
            WORKFLOWS,
            [run(7, ".github/workflows/ci.yml")],
            {7: [job("Lint & type-check", steps=SYNCED)]},
        )
        code, out, _ = self.main(gh)
        self.assertEqual(code, 0, out)
        self.assertIn("changes astral-sh/setup-uv: EXERCISED", out)
        self.assertIn("step `Run astral-sh/setup-uv@bec219d", out)

    def test_a_workflow_that_only_runs_on_a_schedule_never_ran_it(self):
        """cli/cli #14486 bumped `codeql-action/upload-sarif` in govulncheck.yml, which
        starts on `schedule` and `workflow_dispatch`: nothing on the PR ran it."""
        scheduled = (
            "on:\n  schedule:\n    - cron: '0 1 * * *'\n  workflow_dispatch:\n"
            "jobs:\n  scan:\n    runs-on: ubuntu-latest\n    steps:\n"
            "      - uses: github/codeql-action/upload-sarif@abc\n"
        )
        patch = "+      - uses: github/codeql-action/upload-sarif@abc  # v4.1.0\n"
        gh = self.fake(
            [{"filename": ".github/workflows/govulncheck.yml", "patch": patch}],
            {".github/workflows/govulncheck.yml": scheduled},
            [],
        )
        code, out, _ = self.main(gh)
        self.assertEqual(code, 1, out)
        self.assertIn("it starts on: schedule, workflow_dispatch", out)
        self.assertIn("RESULT: NOT ALL EXERCISED -- 1 not exercised, of 1", out)

    def test_a_renovate_value_bump_is_matched_to_the_step_that_sets_it(self):
        """ruff#28880 changes `version:` under a setup-uv step and bumps no action.
        The job that ran it named that step `Install uv`."""
        wf = (
            "on: pull_request\njobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n"
            "      - run: echo hello\n"
            "      - name: Install uv\n        uses: astral-sh/setup-uv@c18668a\n"
            '        with:\n          version: "0.12.18"\n'
        )
        patch = '-          version: "0.12.11"\n+          version: "0.12.18"\n'
        steps = [
            {"name": "Run echo hello", "conclusion": "success"},
            {"name": "Install uv", "conclusion": "success"},
        ]
        gh = self.fake(
            [{"filename": ".github/workflows/ci.yaml", "patch": patch}],
            {".github/workflows/ci.yaml": wf},
            [run(3, ".github/workflows/ci.yaml")],
            {3: [job("test", steps=steps)]},
        )
        code, out, _ = self.main(gh)
        self.assertEqual(code, 0, out)
        self.assertIn("changes version: 0.12.18: EXERCISED", out)
        self.assertIn("step `Install uv` success", out)

    def test_a_change_no_step_sets_only_says_the_file_ran(self):
        """A job-level `env:` changed: the workflow ran, and that is all the runs say."""
        wf = (
            "on: pull_request\njobs:\n  test:\n    runs-on: ubuntu-latest\n"
            "    env:\n      LEVEL: 2\n    steps:\n      - run: make\n"
        )
        gh = self.fake(
            [{"filename": ".github/workflows/ci.yml", "patch": "+      LEVEL: 2\n"}],
            {".github/workflows/ci.yml": wf},
            [run(3, ".github/workflows/ci.yml")],
            {3: [job("test", steps=[{"name": "Run make", "conclusion": "success"}])]},
        )
        code, out, _ = self.main(gh)
        self.assertEqual(code, 1, out)
        self.assertIn("FILE RAN", out)
        self.assertIn("1 file ran", out)


class TestACalledWorkflowRunsUnderItsCaller(Harness):
    """A reusable workflow has no run of its own path: it runs as `caller / callee`
    inside its caller's run. ruff's release.yml calls five of them with `$/`."""

    CALLER = (
        "on: pull_request\njobs:\n  publish:\n    uses: {prefix}.github/workflows/publish.yml\n"
    )
    CALLEE = (
        "on: workflow_call\njobs:\n  upload:\n    name: Upload\n    runs-on: ubuntu-latest\n"
        "    steps:\n      - uses: pypa/gh-action-pypi-publish@abc\n"
    )
    PATCH = "+      - uses: pypa/gh-action-pypi-publish@abc  # v1.14.0\n"

    def test_both_spellings_reach_the_callee(self):
        for prefix in ("./", "$/"):
            with self.subTest(prefix=prefix):
                gh = self.fake(
                    [{"filename": ".github/workflows/publish.yml", "patch": self.PATCH}],
                    {
                        ".github/workflows/release.yml": self.CALLER.format(prefix=prefix),
                        ".github/workflows/publish.yml": self.CALLEE,
                    },
                    [run(9, ".github/workflows/release.yml")],
                    {
                        9: [
                            job(
                                "publish / Upload",
                                steps=[
                                    {
                                        "name": "Run pypa/gh-action-pypi-publish@abc",
                                        "conclusion": "success",
                                    }
                                ],
                            )
                        ]
                    },
                )
                code, out, _ = self.main(gh)
                self.assertEqual(code, 0, out)
                self.assertIn("release.yml  publish / Upload -- step", out)


class TestWhatCannotBeSettledIsNotSettled(Harness):
    def test_a_run_still_going_is_pending(self):
        gh = self.fake(
            LOCKFILE, WORKFLOWS, [run(7, ".github/workflows/ci.yml", status="in_progress")]
        )
        code, out, _ = self.main(gh)
        self.assertEqual(code, 1, out)
        self.assertIn("PENDING", out)
        self.assertNotIn("no run of this job", out)

    def test_an_unreadable_workflow_is_underivable(self):
        gh = self.fake(
            LOCKFILE,
            {".github/workflows/ci.yml": "a: &x 1\njobs: *x\n"},
            [run(7, ".github/workflows/ci.yml")],
        )
        code, out, _ = self.main(gh)
        self.assertEqual(code, 1, out)
        self.assertIn("UNREAD", out)
        self.assertIn("UNDERIVABLE", out)

    def test_a_file_with_no_rule_says_so(self):
        gh = self.fake([{"filename": "package-lock.json", "patch": "+x"}], WORKFLOWS, [])
        code, out, _ = self.main(gh)
        self.assertEqual(code, 1, out)
        self.assertIn("package-lock.json -- no rule for this file here", out)

    def test_two_jobs_named_only_by_expressions_are_ambiguous(self):
        wf = (
            "on: pull_request\njobs:\n"
            "  a:\n    name: ${{ matrix.n }}\n    runs-on: x\n    steps:\n      - run: uv sync\n"
            "  b:\n    name: ${{ matrix.m }}\n    runs-on: x\n    steps:\n      - run: uv sync\n"
        )
        gh = self.fake(
            LOCKFILE,
            {".github/workflows/ci.yml": wf},
            [run(7, ".github/workflows/ci.yml")],
            {7: [job("anything", steps=[{"name": "Run uv sync", "conclusion": "success"}])]},
        )
        code, out, _ = self.main(gh)
        self.assertEqual(code, 1, out)
        self.assertIn("anything -- fits two jobs", out)

    def test_the_more_literal_name_wins(self):
        """`Test (${{ matrix.os }})` and `${{ matrix.name }}` both fit `Test (ubuntu)`,
        and only the first is the job that ran it."""
        # The less literal job first, so taking the first fit gets it wrong.
        wf = (
            "on: pull_request\njobs:\n"
            "  u:\n    name: ${{ matrix.name }}\n    runs-on: x\n    steps:\n      - run: make\n"
            "  t:\n    name: Test (${{ matrix.os }})\n    runs-on: x\n    steps:\n      - run: uv sync\n"
        )
        gh = self.fake(
            LOCKFILE,
            {".github/workflows/ci.yml": wf},
            [run(7, ".github/workflows/ci.yml")],
            {7: [job("Test (ubuntu)", steps=[{"name": "Run uv sync", "conclusion": "success"}])]},
        )
        code, out, _ = self.main(gh)
        self.assertEqual(code, 0, out)

    def test_pre_commit_ci_counts_for_the_hook_config(self):
        """pytest#15027: pre-commit runs outside Actions, as a status."""
        gh = self.fake(
            [{"filename": ".pre-commit-config.yaml", "patch": "+  rev: v1.2.3\n"}],
            WORKFLOWS,
            [],
            statuses=[{"context": "pre-commit.ci - pr", "state": "success"}],
        )
        code, out, _ = self.main(gh)
        self.assertEqual(code, 0, out)
        self.assertIn("pre-commit.ci - pr -- status success, outside Actions", out)

    def test_the_pre_commit_action_counts_for_the_hook_config(self):
        """A workflow can run the hooks through `pre-commit/action` with no
        `pre-commit run` line anywhere in it."""
        wf = (
            "on: pull_request\njobs:\n  lint:\n    runs-on: x\n    steps:\n"
            "      - uses: pre-commit/action@2c7b3805fd2a0fd8c1884dcaebf91fc102a13ecd\n"
        )
        steps = [
            {
                "name": "Run pre-commit/action@2c7b3805fd2a0fd8c1884dcaebf91fc102a13ecd",
                "conclusion": "success",
            }
        ]
        gh = self.fake(
            [{"filename": ".pre-commit-config.yaml", "patch": "+  rev: v1.2.3\n"}],
            {".github/workflows/lint.yml": wf},
            [run(5, ".github/workflows/lint.yml")],
            {5: [job("lint", steps=steps)]},
        )
        code, out, _ = self.main(gh)
        self.assertEqual(code, 0, out)
        self.assertIn("lint -- step `Run pre-commit/action@2c7b380", out)

    def test_a_sha_that_is_not_the_prs_head_is_said_so(self):
        """A mistyped SHA has no runs and no tree, which quietly reads as nothing
        having run. Caught writing this suite's live test: a SHA typed from memory."""
        self.head = "0" * 40
        gh = self.fake(LOCKFILE, {}, [])
        code, out, _ = self.main(gh)
        self.assertEqual(code, 1, out)
        self.assertIn("IS NOT THIS PR'S HEAD", out)
        self.assertIn("no .github/workflows/ at this commit", out)
        self.assertIn("RESULT: UNDERIVABLE -- --head-sha is not the head of #1", out)

    def test_a_failed_read_is_exit_2(self):
        def gh(args: list[str]) -> str:
            from exercised import fail

            fail("`gh api` failed: HTTP 404")

        code, _, err = self.main(gh)
        self.assertEqual(code, 2)
        self.assertIn("HTTP 404", err)


class TestThePatchIsReadForWhatChanged(unittest.TestCase):
    def test_added_uses_lines_name_the_actions(self):
        patch = (
            "-  - uses: github/codeql-action/init@aaa  # v4.0.0\n"
            "+  - uses: github/codeql-action/init@bbb  # v4.1.0\n"
            "+  - uses: ./.github/actions/local\n"
            '+        uses: "actions/checkout@ccc"\n'
            "+  - uses: docker://alpine@sha256:ddd\n"
        )
        self.assertEqual(
            bumped_actions(patch),
            ["github/codeql-action/init", "actions/checkout", "docker://alpine"],
        )

    def test_values_skip_uses_and_comments(self):
        patch = '+  uses: a/b@c\n+  # renovate: datasource=x\n+  version: "0.12.18"  # pinned\n'
        self.assertEqual(changed_values(patch), [("version", "0.12.18")])

    def test_step_names_as_github_shows_them(self):
        for step, shown in (
            ({"uses": "a/b@c"}, "Run a/b@c"),
            ({"run": "\n  uv sync --locked\n  x\n"}, "Run uv sync --locked"),
            ({"name": "Test ${{ matrix.os }}", "run": "x"}, "Test ubuntu-latest"),
        ):
            with self.subTest(step=step):
                pattern = step_name(step)
                assert pattern is not None
                self.assertTrue(pattern.match(shown))
        self.assertIsNone(step_name({}))


if __name__ == "__main__":
    unittest.main()
