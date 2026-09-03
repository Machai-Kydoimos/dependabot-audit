"""Regression tests for precommit.py.

No network: `_gh` is the single seam every call goes through, so the fakes below
drive the real resolution, parsing and comparison and only the subprocess is
replaced.

Every case corresponds to a defect that shipped or to a failure the phase exists
to detect. The anchor case is this repo's own #99 — `ruff-pre-commit` v0.16.2 ->
v0.16.5, where `ruff-format`'s `types_or` gained `markdown` and the hook began
rewriting every Markdown file in the repository. `ruff` itself did not change in
any way a changelog reports, so **no per-package view of the tool could have
found it**: the defect lived entirely in the wrapper, which is the thing this
script reads and nothing else here did.

The fixtures are the real files, trimmed. Four live mirrors were parsed while
writing the parser and they disagree on every cosmetic detail — two-space and
four-space continuations, quoted and bare keys, and one folded scalar — so those
shapes are cases here rather than assumptions.

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

from precommit import Unparsed, cli, main, parse_hooks

OLD_SHA = "a" * 40
NEW_SHA = "b" * 40

# `ruff-pre-commit`, trimmed to the two hooks that matter. Two-space continuation.
RUFF_OLD = """\
- id: ruff-check
  name: ruff check
  entry: ruff check --force-exclude
  language: python
  types_or: [python, pyi, jupyter]
- id: ruff-format
  name: ruff format
  entry: ruff format --force-exclude
  language: python
  types_or: [python, pyi, jupyter]
"""
RUFF_NEW = RUFF_OLD.replace(
    "  types_or: [python, pyi, jupyter]\n", "  types_or: [python, pyi, jupyter, markdown]\n"
).replace(
    "[python, pyi, jupyter, markdown]\n- id: ruff-format",
    "[python, pyi, jupyter]\n- id: ruff-format",
)

# `mirrors-mypy`: four-space continuation, quoted keys, and the quoting of `name`
# is the only thing that moved between v1.18.2 and v2.3.1.
MYPY_OLD = """\
-   id: mypy
    name: mypy
    description: ''
    entry: mypy
    language: python
    'types_or': [python, pyi]
    args: ["--ignore-missing-imports"]
"""
MYPY_NEW = MYPY_OLD.replace("    name: mypy\n", "    name: 'mypy'\n")

PYPROJECT = '[project]\nname = "ruff-pre-commit"\ndependencies = [\n    "ruff==%s",\n]\n'
SETUP_PY = (
    "from setuptools import setup\nsetup(\n    name='pre_commit_placeholder_package',\n"
    "    version='0.0.0',\n    install_requires=['mypy==%s'],\n)\n"
)


class PreCommitHarness(unittest.TestCase):
    def _fake_gh(
        self,
        *,
        hooks: dict[str, str] | None = None,
        pyproject: dict[str, str] | None = None,
        setup: dict[str, str] | None = None,
        shas: dict[str, str] | None = None,
    ) -> Any:
        """A `_gh` dispatching on the call shape. Each mapping is rev -> content.

        A rev absent from a mapping is a **404**, which is the ordinary case for
        `pyproject.toml` on a mirror repo and for `setup.py` on a first-party
        one. Modelling it as an exit code rather than an empty string matters:
        `gh` writes the API error body to stdout, so a caller reading stdout
        alone gets a JSON error document that is not empty.
        """
        hooks, pyproject = hooks or {}, pyproject or {}
        setup = setup or {}
        shas = shas or {"v1": OLD_SHA, "v2": NEW_SHA}

        def fake(args: list[str]) -> tuple[int, str]:
            joined = " ".join(args)
            if "/commits/" in joined:
                rev = joined.split("/commits/")[1].split(" ")[0]
                return (0, shas[rev]) if rev in shas else (1, '{"message":"No commit found"}')
            path, _, rev = joined.split("/contents/")[1].partition("?ref=")
            rev = rev.split(" ")[0]
            table = {
                ".pre-commit-hooks.yaml": hooks,
                "pyproject.toml": pyproject,
                "setup.py": setup,
            }[path]
            return (0, table[rev]) if rev in table else (1, '{"message":"Not Found"}')

        return fake

    def _run(
        self, fake: Any, argv: list[str] | None = None, entry: Any = None
    ) -> tuple[Any, str, str]:
        full = [
            "precommit.py", "--repo", "o/r", "--from", "v1", "--to", "v2",
            *(argv or []),
        ]  # fmt: skip
        out, err = io.StringIO(), io.StringIO()
        with (
            mock.patch("precommit._gh", fake),
            mock.patch.object(sys, "argv", full),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            try:
                code: int | str | None = (entry or main)()
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue(), err.getvalue()

    def _json(self, fake: Any, argv: list[str] | None = None) -> dict[str, Any]:
        _, out, _ = self._run(fake, [*(argv or []), "--json"])
        loaded: dict[str, Any] = json.loads(out)
        return loaded


class TestTheWrapperIsWhereTheDefectWas(PreCommitHarness):
    """#99, the case this whole ecosystem was added for.

    `ruff-pre-commit` v0.16.2 -> v0.16.5 moved one word in one list, and the hook
    began reformatting Markdown. CI went red on this repository. The bump reads
    as a routine patch bump of a formatter, and every per-package check of `ruff`
    agrees with that reading, because `ruff` is not what changed.
    """

    def _ruff(self, new: str = RUFF_NEW) -> Any:
        return self._fake_gh(
            hooks={"v1": RUFF_OLD, "v2": new},
            pyproject={"v1": PYPROJECT % "0.16.2", "v2": PYPROJECT % "0.16.5"},
        )

    def test_a_widened_types_or_is_a_behavioural_finding(self):
        report = self._json(self._ruff())
        rows = [r for r in report["hooks"] if r["kind"] == "behavioural"]
        self.assertEqual(len(rows), 1, f"expected exactly the one field: {report['hooks']}")
        self.assertEqual((rows[0]["hook"], rows[0]["field"]), ("ruff-format", "types_or"))
        self.assertIn("markdown", rows[0]["after"])
        self.assertNotIn("markdown", rows[0]["before"])

    def test_it_exits_one_so_the_caller_cannot_miss_it(self):
        code, _, _ = self._run(self._ruff())
        self.assertEqual(code, 1)

    def test_the_sibling_hook_is_not_swept_in(self):
        """`ruff-check` did not change, and a diff that reported the whole file
        would say it did. The field-level comparison is what makes the row
        actionable rather than a pointer at a 30-line file."""
        report = self._json(self._ruff())
        self.assertEqual([r["hook"] for r in report["hooks"]], ["ruff-format"])

    def test_an_unchanged_bump_is_clean_and_exits_zero(self):
        """#98, the other live fixture. A gate that fires on every bump is one
        the reader stops reading, so the negative case is load-bearing."""
        code, out, _ = self._run(self._ruff(new=RUFF_OLD))
        self.assertEqual(code, 0)
        self.assertIn("CLEAN", out)

    def test_only_the_named_hook_is_compared_when_asked(self):
        report = self._json(self._ruff(), ["--hook", "ruff-check"])
        self.assertEqual(report["hooks"], [], "the repo does not use ruff-format here")


class TestTheRevIsNotTheDependency(PreCommitHarness):
    """A `rev:` is a git ref on someone else's repo, not a version.

    Phase 2 asking PyPI about `v2.3.1` because the tag says so is asking about
    the wrong artifact -- the version that gets installed is declared in the hook
    repo's packaging, and nothing enforces that the two agree.
    """

    def test_a_mirror_pins_the_tool_in_setup_py(self):
        report = self._json(
            self._fake_gh(
                hooks={"v1": MYPY_OLD, "v2": MYPY_NEW},
                setup={"v1": SETUP_PY % "2.3.0", "v2": SETUP_PY % "2.3.1"},
            )
        )
        got = report["requirement_to"]
        self.assertEqual(got["state"], "derived")
        self.assertEqual(got["source"], "setup.py")
        self.assertEqual([(s["name"], s["version"]) for s in got["specs"]], [("mypy", "2.3.1")])

    def test_pyproject_is_preferred_where_both_exist(self):
        report = self._json(
            self._fake_gh(
                hooks={"v1": RUFF_OLD, "v2": RUFF_OLD},
                pyproject={"v1": PYPROJECT % "0.16.2", "v2": PYPROJECT % "0.16.5"},
                setup={"v1": SETUP_PY % "9.9.9", "v2": SETUP_PY % "9.9.9"},
            )
        )
        self.assertEqual(report["requirement_to"]["source"], "pyproject.toml")
        self.assertEqual(report["requirement_to"]["specs"][0]["version"], "0.16.5")

    def test_a_tag_disagreeing_with_the_pin_is_a_finding(self):
        """Nothing enforces the convention. Where they differ, what `pre-commit`
        installs is not what the config appears to say, and a Phase 2 check run
        against the tag is about a version nobody will get."""
        code, out, _ = self._run(
            self._fake_gh(
                hooks={"v0.16.2": RUFF_OLD, "v0.16.4": RUFF_OLD},
                pyproject={"v0.16.2": PYPROJECT % "0.16.2", "v0.16.4": PYPROJECT % "0.16.5"},
                shas={"v0.16.2": OLD_SHA, "v0.16.4": NEW_SHA},
            ),
            ["--from", "v0.16.2", "--to", "v0.16.4"],
        )
        self.assertEqual(code, 1)
        self.assertIn("does not match what it installs", out)

    def test_a_rev_that_does_not_claim_a_version_is_not_compared(self):
        """The false-positive class, found by three tests failing at once. A
        branch pin, a SHA pin, or a mirror whose tags do not track the tool would
        disagree with the packaging on *every* bump -- so the comparison is made
        only where the tag is shaped like the version it appears to name.
        """
        code, out, _ = self._run(
            self._fake_gh(
                hooks={"main": RUFF_OLD, "next": RUFF_OLD},
                pyproject={"main": PYPROJECT % "0.16.2", "next": PYPROJECT % "0.16.5"},
                shas={"main": OLD_SHA, "next": NEW_SHA},
            ),
            ["--from", "main", "--to", "next"],
        )
        self.assertEqual(code, 0)
        self.assertNotIn("does not match", out)

    def test_a_computed_install_requires_is_underivable_not_empty(self):
        """`install_requires=['mypy==' + VERSION]` is a list whose entries are
        not constants. Reading the constants that *are* there would report a
        shorter requirement list as though it were the whole one -- the same
        shape as a capped file list read as complete. Nothing is executed to find
        out, so the honest answer is that it was not established."""
        # A *mixed* list, and that is what makes this discriminate. With every
        # entry computed the partial read is also empty, so both the right answer
        # and the wrong one arrive as `underivable` and the case proves nothing.
        # One constant beside one computed entry is where returning the constants
        # reports a shorter requirement list as though it were the whole one --
        # the same shape as a capped file list read as complete.
        computed = (
            "from setuptools import setup\nV = '2.3.1'\n"
            "setup(install_requires=['types-requests==1.0', 'mypy==' + V])\n"
        )
        report = self._json(
            self._fake_gh(
                hooks={"v1": MYPY_OLD, "v2": MYPY_OLD}, setup={"v1": computed, "v2": computed}
            )
        )
        self.assertEqual(report["requirement_to"]["state"], "underivable")

    def test_no_packaging_at_all_is_underivable_rather_than_absent(self):
        report = self._json(self._fake_gh(hooks={"v1": MYPY_OLD, "v2": MYPY_OLD}))
        self.assertEqual(report["requirement_to"]["state"], "underivable")

    def test_a_node_mirror_derives_its_pin_and_says_it_is_not_pypi(self):
        """`mirrors-prettier` carries no Python packaging and pins `prettier` in
        the hook's own `additional_dependencies`. Deriving it is right; calling
        it covered is not -- npm is the boundary again, one layer in."""
        node = (
            "-   id: prettier\n    entry: prettier --write\n    language: node\n"
            '    additional_dependencies: ["prettier@3.1.0"]\n'
        )
        code, out, _ = self._run(self._fake_gh(hooks={"v1": node, "v2": node}))
        self.assertEqual(code, 0)
        self.assertIn("NOT PyPI", out)
        report = self._json(self._fake_gh(hooks={"v1": node, "v2": node}))
        self.assertEqual(report["requirement_to"]["specs"][0]["name"], "prettier")
        self.assertEqual(report["language"], "node")


class TestTheParserRefusesRatherThanGuesses(PreCommitHarness):
    """A parser that skips what it does not recognise reports "no fields changed"
    about a file it did not read.

    That is this plugin's own thesis one level down -- the unverified verifier
    reporting green -- so the grammar is small and everything outside it raises.
    """

    def test_the_four_live_shapes_all_parse(self):
        """Two-space and four-space continuations, quoted keys, and a folded
        scalar. All four were read off real mirrors while writing this."""
        folded = (
            "- id: black\n  name: black\n  language: python\n"
            "- id: black-jupyter\n  description:\n"
            '    "Black: the formatter (with Jupyter support)"\n  language: python\n'
        )
        self.assertEqual(sorted(parse_hooks(RUFF_OLD)), ["ruff-check", "ruff-format"])
        self.assertEqual(parse_hooks(MYPY_OLD)["mypy"]["types_or"], "[python, pyi]")
        got = parse_hooks(folded)
        self.assertEqual(got["black-jupyter"]["description"],
                         "Black: the formatter (with Jupyter support)")  # fmt: skip

    def test_a_nested_block_raises_rather_than_being_skipped(self):
        with self.assertRaises(Unparsed):
            parse_hooks("- id: x\n  files:\n    - a\n    - b\n")

    def test_a_dedent_inside_an_item_raises(self):
        """A line less indented than the item it sits in is not a field of that
        item, and treating it as one silently attaches a value to the wrong hook.
        Found by a mutation run: replacing this raise with `continue` left every
        other case green."""
        with self.assertRaises(Unparsed):
            parse_hooks("- id: x\n  language: python\n language: node\n")

    def test_a_line_that_is_not_a_field_raises(self):
        """A block sequence takes a path worth knowing: `    - a` under `files:`
        begins with `- `, so it is read as a *new item* rather than as content,
        and is refused twice over -- once for having no colon and again for
        having no `id`. Mutating either raise alone leaves the file refused,
        which is the right outcome and the reason this case is here rather than
        a claim about which branch fires.
        """
        with self.assertRaises(Unparsed):
            parse_hooks("- id: x\n  files:\n    - a\n")

    def test_an_item_without_an_id_raises(self):
        """Every comparison is keyed on `id`. An item without one would compare
        against nothing and read as unchanged."""
        with self.assertRaises(Unparsed):
            parse_hooks("- name: no-id-here\n  language: python\n")

    def test_an_unparsable_file_is_underivable_and_keeps_the_raw_text(self):
        """Both halves matter. `underivable` stops it reading as "nothing
        changed"; the raw text stops the refusal from also withholding the
        evidence, which turns a degraded answer into no answer."""
        report = self._json(
            self._fake_gh(hooks={"v1": RUFF_OLD, "v2": "- id: x\n  files:\n    - a\n"})
        )
        self.assertEqual(report["hooks_state"], "underivable")
        self.assertIn("raw", report)
        self.assertIn("files:", report["raw"]["to"])

    def test_a_missing_hooks_file_is_underivable_not_clean(self):
        report = self._json(self._fake_gh(hooks={"v1": RUFF_OLD}))
        self.assertEqual(report["hooks_state"], "underivable")

    def test_quoting_alone_is_not_a_change(self):
        """`mirrors-mypy` restyled `name: mypy` to `name: 'mypy'` between
        v1.18.2 and v2.3.1. Reported as a field change it is noise on the row a
        reader uses to decide whether the wrapper moved."""
        report = self._json(
            self._fake_gh(
                hooks={"v1": MYPY_OLD, "v2": MYPY_NEW},
                setup={"v1": SETUP_PY % "2.3.0", "v2": SETUP_PY % "2.3.1"},
            )
        )
        self.assertEqual(report["hooks"], [])


class TestTheRankingErrsNoisy(PreCommitHarness):
    """Which fields make this exit 1, and which direction an unknown one goes."""

    def test_an_unknown_field_counts_as_behavioural(self):
        """`pre-commit` gains keys. A new one that selects files must not arrive
        as cosmetic because this list predates it, so the default is the noisy
        direction and not the quiet one."""
        report = self._json(
            self._fake_gh(
                hooks={
                    "v1": "- id: x\n  language: python\n",
                    "v2": "- id: x\n  language: python\n  some_future_selector: [md]\n",
                }
            )
        )
        self.assertEqual([r["kind"] for r in report["hooks"]], ["behavioural"])

    def test_a_cosmetic_field_is_reported_and_is_not_a_finding(self):
        code, out, _ = self._run(
            self._fake_gh(
                hooks={
                    "v1": "- id: x\n  language: python\n  description: old\n",
                    "v2": "- id: x\n  language: python\n  description: new\n",
                }
            )
        )
        self.assertEqual(code, 0, "a description is not a behaviour change")
        self.assertIn("cosmetic", out, "and it is still shown")

    def test_a_removed_hook_is_behavioural(self):
        code, out, _ = self._run(
            self._fake_gh(hooks={"v1": RUFF_OLD, "v2": "- id: ruff-check\n  language: python\n"})
        )
        self.assertEqual(code, 1)
        self.assertIn("THE HOOK WAS REMOVED", out)


class TestThePinIsAnImmutabilityClaimAndNotAnIntegrityOne(PreCommitHarness):
    """There is no artifact hash here, and the report must not imply one."""

    def test_a_tag_is_mutable_and_that_is_not_a_finding(self):
        """Every ordinary `pre-commit` config pins tags -- `autoupdate` writes
        them. A signal that fires on every run is one nobody reads, so it is
        reported and does not set the exit code. The actions reference reaches
        the same conclusion about `uses:` for the same reason."""
        code, out, _ = self._run(
            self._fake_gh(
                hooks={"v1": RUFF_OLD, "v2": RUFF_OLD},
                pyproject={"v1": PYPROJECT % "0.16.2", "v2": PYPROJECT % "0.16.5"},
            )
        )
        self.assertEqual(code, 0)
        self.assertIn("MUTABLE", out)
        self.assertIn("NOT a Hold", out)

    def test_a_sha_pin_is_recognised_as_immutable(self):
        fake = self._fake_gh(
            hooks={OLD_SHA: RUFF_OLD, NEW_SHA: RUFF_OLD}, shas={OLD_SHA: OLD_SHA, NEW_SHA: NEW_SHA}
        )
        report = self._json(fake, ["--from", OLD_SHA, "--to", NEW_SHA])
        self.assertEqual(report["from"]["pin"], "immutable")
        self.assertEqual(report["to"]["pin"], "immutable")


class TestFailureIsNotAFinding(PreCommitHarness):
    """Exit 1 means a field moved. Everything that could not run exits 2.

    The same guard the other scripts here carry, and for the same reason: this
    script's inputs are API responses and someone else's YAML, which is the shape
    least entitled to be assumed well-formed.
    """

    def test_an_unresolvable_rev_exits_two(self):
        code, _, err = self._run(self._fake_gh(hooks={"v1": RUFF_OLD}, shas={"v1": OLD_SHA}))
        self.assertEqual(code, 2, "a rev naming nothing is not a behaviour change")
        self.assertIn("did not resolve", err)

    def test_an_unexpected_exception_exits_two_not_one(self):
        def boom(_args: list[str]) -> tuple[int, str]:
            raise RuntimeError("network on fire")

        code, _, err = self._run(boom, entry=cli)
        self.assertEqual(code, 2)
        self.assertIn("RuntimeError", err)

    def test_a_malformed_repo_argument_exits_two(self):
        code, _, err = self._run(self._fake_gh(), ["--repo", "not-a-slug"])
        self.assertEqual(code, 2)
        self.assertIn("OWNER/NAME", err)
