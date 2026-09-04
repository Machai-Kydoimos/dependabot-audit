"""The live half of `precommit.py`, against the two bumps it was written for.

The hermetic suite in `tests/test_precommit.py` drives the real parsing and
comparison against **trimmed** fixtures, which is what makes it cheap and
trustworthy on every commit. What it cannot establish is that the fixtures still
resemble the files: `.pre-commit-hooks.yaml` is written by four different
projects that agree on nothing cosmetic, and the grammar this parser accepts was
derived by reading theirs.

So this is the same two cases against the real repositories:

    ruff-pre-commit  v0.16.2 -> v0.16.5   ruff-format.types_or gained `markdown`
    mirrors-mypy     v2.3.0  -> v2.3.1    nothing moved

Both are historical tags on public repositories, so this does not drift -- and
both are this repository's own #99 and #98, the PRs #109 was filed from. The
first is a real defect the verifier must catch and the second a clean bump it
must not flag; a check that only ever fires proves as little as one that never
does.

    RUN_NETWORK_TESTS=1 python3 -m unittest discover -s integration -v
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import pathlib
import subprocess
import sys
import unittest
from typing import Any
from unittest import mock

sys.path.insert(
    0, str(pathlib.Path(__file__).resolve().parent.parent / "skills/dependabot-audit/scripts")
)

from precommit import main

NETWORK = os.environ.get("RUN_NETWORK_TESTS") == "1"


@unittest.skipUnless(NETWORK, "set RUN_NETWORK_TESTS=1 to run (needs `gh` and the network)")
class TestTheRealBumpsStillReadTheSameWay(unittest.TestCase):
    def _audit(self, repo: str, old: str, new: str) -> tuple[int, dict[str, Any]]:
        argv = [
            "precommit.py", "--repo", repo, "--from", old, "--to", new, "--json",
        ]  # fmt: skip
        out = io.StringIO()
        with mock.patch.object(sys, "argv", argv), contextlib.redirect_stdout(out):
            try:
                code: int | str | None = main()
            except SystemExit as exc:
                code = exc.code
        return int(code or 0), json.loads(out.getvalue())

    def test_the_ruff_wrapper_change_is_found_in_the_real_file(self):
        """#99. One word, in one list, in a file nobody diffs.

        Asserted on the real `.pre-commit-hooks.yaml` rather than the trimmed
        fixture, because the fixture is only evidence that the parser reads what
        was copied out of the file on the day it was written.
        """
        code, report = self._audit("astral-sh/ruff-pre-commit", "v0.16.2", "v0.16.5")
        self.assertEqual(code, 1)
        rows = [r for r in report["hooks"] if r["kind"] == "behavioural"]
        self.assertEqual(
            [(r["hook"], r["field"]) for r in rows],
            [("ruff-format", "types_or")],
            f"the live comparison no longer isolates the field: {report['hooks']}",
        )
        self.assertIn("markdown", rows[0]["after"])
        self.assertNotIn("markdown", rows[0]["before"])

    def test_the_tool_version_comes_from_the_hook_repos_packaging(self):
        """`ruff-pre-commit` declares `ruff==0.16.5` in `pyproject.toml`. Phase 2
        queries that, never the tag -- `v0.16.5` is not a PyPI version of
        anything, and a query built from it comes back empty."""
        _, report = self._audit("astral-sh/ruff-pre-commit", "v0.16.2", "v0.16.5")
        got = report["requirement_to"]
        self.assertEqual(got["source"], "pyproject.toml")
        self.assertEqual([(s["name"], s["version"]) for s in got["specs"]], [("ruff", "0.16.5")])
        self.assertEqual(report["language"], "python", "which is what makes it covered")

    def test_the_mypy_mirror_bump_is_clean(self):
        """#98, and the half that keeps the signal readable. This is the bump
        whose report said "a Hold I would not defend on the merits, only on the
        procedure" -- the procedure now has something to say about it."""
        code, report = self._audit("pre-commit/mirrors-mypy", "v2.3.0", "v2.3.1")
        self.assertEqual(code, 0, f"a clean bump was flagged: {report['hooks']}")
        self.assertEqual(report["hooks"], [])
        self.assertEqual(report["requirement_to"]["source"], "setup.py")
        self.assertEqual(report["requirement_to"]["specs"][0]["version"], "2.3.1")

    def test_the_quoting_only_release_is_not_a_change(self):
        """`mirrors-mypy` restyled `name: mypy` to `name: 'mypy'` between v1.18.2
        and v2.3.1. Read as a field change it is noise on the row a reader uses
        to decide whether the wrapper moved at all."""
        code, report = self._audit("pre-commit/mirrors-mypy", "v1.18.2", "v2.3.1")
        self.assertEqual(code, 0, f"quoting was reported as a change: {report['hooks']}")

    def test_the_parser_accepts_all_four_live_shapes(self):
        """Two-space and four-space continuations, quoted keys, and a folded
        scalar -- one from each of four mirrors that agree on nothing cosmetic.
        `Unparsed` here means the grammar has fallen behind a real file, which is
        the failure this test exists to notice."""
        from precommit import parse_hooks, read_at

        for repo, rev, expected in (
            ("astral-sh/ruff-pre-commit", "v0.16.5", "ruff-format"),
            ("pre-commit/mirrors-mypy", "v2.3.1", "mypy"),
            ("pre-commit/mirrors-prettier", "v4.0.0-alpha.8", "prettier"),
            ("psf/black-pre-commit-mirror", "25.1.0", "black-jupyter"),
        ):
            with self.subTest(repo=repo):
                text = read_at(repo, ".pre-commit-hooks.yaml", rev)
                self.assertIsNotNone(text, f"{repo}@{rev} has no hooks file")
                self.assertIn(expected, parse_hooks(text or ""))


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(NETWORK, "set RUN_NETWORK_TESTS=1 to run (needs `gh` and the network)")
class TestTheDocumentedCommandsActuallyRun(unittest.TestCase):
    """The reference's one-liners, extracted from the file and executed.

    Prose guards assert a command is *present*. That is not the same as it
    running, and the difference is not hypothetical: the first draft of the OSV
    query in `references/pre-commit.md` had a mismatched bracket
    (`{"queries":[... for v in ...}`) and was caught only by running it back out
    of the file. Every reading of it looked right.

    This is the same lever `test_ruff_replay.py` uses and the same one
    `test_audit.py` uses for the `--no-build` salvage: when the recipe is
    executable, write the guard to run it.
    """

    REF = pathlib.Path(__file__).resolve().parent.parent / (
        "skills/dependabot-audit/references/pre-commit.md"
    )

    def _line_after(self, marker: str, substitute: tuple[str, str]) -> str:
        text = self.REF.read_text(encoding="utf-8")
        self.assertIn(marker, text, f"the reference no longer contains `{marker[:40]}…`")
        start = text.index(marker)
        return text[start : text.index("\n", start)].replace(*substitute)

    def _run(self, line: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S602
            line, shell=True, capture_output=True, text=True, timeout=120, check=False
        )

    def test_the_pypi_currency_one_liner_runs(self):
        """Phase 2's, and the cooldown subtraction depends on its timestamp."""
        got = self._run(
            self._line_after(
                "python3 -c 'import json,sys,urllib.request as u; d=json.load", ("<pkg>", "ruff")
            )
        )
        self.assertEqual(got.returncode, 0, got.stderr[:400])
        self.assertRegex(got.stdout, r"latest=\d+\.\d+", got.stdout)
        self.assertIn("published=", got.stdout)
        self.assertIn("yanked=", got.stdout)

    def test_the_osv_batch_one_liner_runs_over_a_gap(self):
        got = self._run(
            self._line_after(
                "python3 -c 'import json,sys,urllib.request as u; vs=sys.argv",
                ("<pkg> <every version in the gap>", "ruff 0.16.2 0.16.5"),
            )
        )
        self.assertEqual(got.returncode, 0, got.stderr[:400])
        self.assertEqual(len(got.stdout.strip().splitlines()), 2, "one row per version in the gap")
        self.assertIn("advisory(ies)", got.stdout)

    def test_the_ghsa_query_runs_and_discriminates(self):
        """Run twice, and the second run is the point. A query that has only ever
        returned empty proves nothing about whether it can return anything --
        CONTRIBUTING's rule about a test that has only ever passed, one level out.
        """
        text = self.REF.read_text(encoding="utf-8")
        start = text.index("gh api graphql -f query='{securityVulnerabilities")
        end = text.index("```", start)
        recipe = text[start:end].strip().rstrip("\\").strip()

        clean = self._run(recipe.replace("<pkg>", "ruff"))
        self.assertEqual(clean.returncode, 0, clean.stderr[:400])
        self.assertEqual(clean.stdout.strip(), "", "ruff is expected to have none")

        control = self._run(recipe.replace("<pkg>", "requests"))
        self.assertEqual(control.returncode, 0, control.stderr[:400])
        self.assertRegex(
            control.stdout,
            r"GHSA-\w+",
            "the same query returns nothing for a package known to have "
            "advisories, so a clean result from it establishes nothing",
        )
