"""Regression tests for rulecheck.py -- Phase 2's rule check, run at the PR's ref (#181).

No network, and no `git`, `uv` or `uvx` either: `run()` is the seam every command
goes through, so the fake below plays them from a small model of a tool -- which
rules exist, which the tree's config runs, what the tree carries -- and records what
was asked of it. The live half, real ruff and rumdl over a planted repository, is
`integration/test_rulecheck_live.py`.

Every class is a claim the prose this replaced made, or a defect it had:

  - it ran in `$SCRATCH/pr-<N>`, which exists only where Phase 4 or 5 runs, so under
    `--no-execute` it never ran and the runs read the config instead;
  - a version read from the PR's lockfile would let the PR choose what runs;
  - a rumdl config can name a command, and `rumdl check` runs it;
  - a silent named run and a broken one print the same thing;
  - `grep -c UP052` counted a fixture by its filename: 45, where the rule fired 0 times.

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import contextlib
import io
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Mapping
from typing import Any
from unittest import mock

sys.path.insert(
    0, str(pathlib.Path(__file__).resolve().parent.parent / "skills/dependabot-audit/scripts")
)

import rulecheck
from rulecheck import INERT, LIVE, UNDERIVABLE, Count, main, reading

BASE = "b" * 40
PR = "pr-1"


def lock(**pins: str) -> str:
    body = 'version = 1\nrequires-python = ">=3.10"\n'
    for name, version in pins.items():
        body += (
            f'\n[[package]]\nname = "{name}"\nversion = "{version}"\n'
            'source = { registry = "https://pypi.org/simple" }\n'
        )
    return body


# The tree at the PR's ref: a config, two files the tool reads, a symlink and a
# submodule, and a file whose name carries a rule code (#197's `UP052.py`).
TREE = [
    ("100644", "blob", "1" * 40, "pyproject.toml"),
    ("100644", "blob", "2" * 40, "docs/guide.md"),
    ("100644", "blob", "3" * 40, "src/UP052.py"),
    ("120000", "blob", "4" * 40, "linked.md"),
    ("160000", "commit", "5" * 40, "vendored"),
]
BLOBS = {
    "1" * 40: b'[tool.rumdl]\ndisable = ["MD013"]\n',
    "2" * 40: b"# Guide\n\nA line.\n",
    "3" * 40: b"raise SystemExit('the PR's code ran')\n",
}


class Tool:
    """What the fake `ruff` or `rumdl` knows: its rules, this tree's config, the tree.

    A control fires for a rule when the file says `VIOLATES <code>`. `config_runs` is
    what the tree's config runs as it is; `tree` is where each rule fires over the
    tree when it runs at all.
    """

    def __init__(
        self,
        *,
        rules: dict[str, dict[str, Any]] | None = None,
        config_runs: set[str] | None = None,
        tree: dict[str, list[str]] | None = None,
        defaults: set[str] | None = None,
        files: int = 2,
        skips_code_block_tools: bool = True,
        config_preview: bool = True,
        pages: dict[str, str] | None = None,
    ) -> None:
        self.rules = rules if rules is not None else {"MD013": {}, "MD077": {}}
        self.config_runs = config_runs if config_runs is not None else {"MD077"}
        self.tree = tree if tree is not None else {}
        self.defaults = defaults if defaults is not None else set(self.rules)
        self.files = files
        self.skips_code_block_tools = skips_code_block_tools
        # `preview = true` in the tree's config, as `fpga-board-sim` sets it.
        self.config_preview = config_preview
        # Each rule's own page: ruff's comes in its `rule` JSON, rumdl's from GitHub.
        self.pages = pages or {}


class Fake:
    """`git` and `uvx`, answered from fixtures, every call recorded."""

    def __init__(
        self,
        *,
        name: str = "rumdl",
        tool: Tool | None = None,
        locks: dict[str, str] | None = None,
        tree: list[tuple[str, str, str, str]] | None = None,
    ) -> None:
        self.name = name
        self.tool = tool or Tool()
        self.locks = (
            {BASE: lock(rumdl="0.2.72", ruff="0.16.7"), PR: lock(rumdl="0.2.60", ruff="0.16.8")}
            if locks is None
            else locks
        )
        self.tree = TREE if tree is None else tree
        self.calls: list[dict[str, Any]] = []
        # For each run over the tree, the control files it could have scanned.
        self.tree_saw: list[list[str]] = []

    @staticmethod
    def done(argv: list[str], code: int, out: str | bytes = b"", err: str = "") -> Any:
        raw = out.encode() if isinstance(out, str) else out
        return subprocess.CompletedProcess(argv, code, raw, err.encode())

    def __call__(
        self,
        argv: list[str],
        *,
        cwd: pathlib.Path | None = None,
        env: dict[str, str] | None = None,
        stdin: bytes | None = None,
    ) -> Any:
        self.calls.append({"argv": list(argv), "cwd": cwd, "env": env})
        if argv[0] == "git":
            return self.git(argv, argv[1:], stdin)
        if argv[0] == "uvx":
            return self.uvx(argv)
        if argv[0] == "gh":
            return self.gh(argv)
        raise AssertionError(f"unexpected command {argv}")

    def gh(self, argv: list[str]) -> Any:
        """rumdl's pages, `docs/<rule>.md` at a tag; anything else is a 404."""
        assert argv[:2] == ["gh", "api"] and self.name == "rumdl", argv
        url = argv[-1]
        page, _, ref = url.removeprefix("repos/rvben/rumdl/contents/docs/").partition(".md?ref=")
        text = self.tool.pages.get(page.upper())
        if text is None or ref != "v0.2.72":
            return self.done(argv, 1, err="gh: Not Found (HTTP 404)\n")
        return self.done(argv, 0, text)

    def git(self, argv: list[str], rest: list[str], stdin: bytes | None) -> Any:
        if rest[0] == "show":
            ref, _, path = rest[1].partition(":")
            if ref in self.locks and path == "uv.lock":
                return self.done(argv, 0, self.locks[ref])
            return self.done(argv, 128, err=f"fatal: invalid object name '{ref}'.\n")
        if rest[0] == "ls-tree":
            if rest[-1] != PR:
                return self.done(argv, 128, err="fatal: not a tree object\n")
            listing = "".join(f"{m} {k} {s}\t{p}\0" for m, k, s, p in self.tree)
            return self.done(argv, 0, listing)
        if rest[:2] == ["cat-file", "--batch"]:
            out = b""
            for sha in (stdin or b"").decode().split():
                blob = BLOBS.get(sha)
                out += (
                    f"{sha} missing\n".encode()
                    if blob is None
                    else f"{sha} blob {len(blob)}\n".encode() + blob + b"\n"
                )
            return self.done(argv, 0, out)
        if rest[0] == "rev-parse":
            return self.done(argv, 0, "4f01a2ccb41d\n")
        raise AssertionError(f"unexpected git call {argv}")

    def uvx(self, argv: list[str]) -> Any:
        assert argv[1:3] == ["--quiet", "--no-config"], argv
        name, _, version = argv[3].partition("@")
        assert name == self.name, argv
        rest = argv[4:]
        if rest == ["--version"]:
            return self.done(argv, 0, f"{name} {version}\n")
        if rest[0] == "rule":
            return self.rule(argv, rest[1])
        if rest == ["check", "--help"]:
            flag = "--no-code-block-tools" if self.tool.skips_code_block_tools else ""
            return self.done(argv, 0, f"Options:\n  --no-config\n  {flag}\n")
        assert rest[0] == "check", argv
        return self.check(argv, rest[1:])

    def rule(self, argv: list[str], code: str) -> Any:
        if code not in self.tool.rules:
            return self.done(argv, 2, err=f"error: invalid value '{code}' for '[RULE]'\n")
        info = {"code": code, "name": code.lower(), **self.tool.rules[code]}
        if self.name == "ruff" and code in self.tool.pages:
            info["explanation"] = self.tool.pages[code]
        return self.done(argv, 0, json.dumps(info))

    def check(self, argv: list[str], args: list[str]) -> Any:
        valued = {"--output-format", "--select", "--enable", "--color"}
        plain = [
            a for i, a in enumerate(args) if not a.startswith("-") and args[i - 1] not in valued
        ]
        assert len(plain) == 1, argv
        path = pathlib.Path(plain[0])
        isolated = "--isolated" in args or "--no-config" in args
        flag = "--select" if self.name == "ruff" else "--enable"
        named = args[args.index(flag) + 1] if flag in args else None
        preview = "--preview" in args or (not isolated and self.tool.config_preview)
        out_json = "--output-format" in args
        key, file_key = rulecheck.FIELDS[self.name]
        if "--show-files" in args:
            return self.done(argv, 0, "".join(f"{path}/f{i}.py\n" for i in range(self.tool.files)))
        found: list[dict[str, str]] = []
        if path.is_dir():
            self.tree_saw.append(sorted(p.name for p in path.rglob("rulecheck-control-*")))
            running = {named} if named else self.tool.config_runs
            for code in sorted(running):
                found += [{key: code, file_key: f} for f in self.tool.tree.get(code, [])]
        else:
            text = path.read_text(encoding="utf-8")
            unnamed = self.tool.defaults if isolated else self.tool.config_runs
            for code in sorted({named} if named else unnamed):
                gated = self.tool.rules.get(code, {}).get("preview") and not preview
                if f"VIOLATES {code}" in text and not gated:
                    found.append({key: code, file_key: str(path)})
        err = ""
        if named and self.tool.rules.get(named, {}).get("preview") and not preview:
            err = f"warning: Selection `{named}` has no effect because preview is not enabled.\n"
        if not out_json:
            touched = len({f[file_key] for f in found})
            line = (
                f"Issues: Found {len(found)} issues in {touched}/{self.tool.files} files (3ms)"
                if found
                else f"Success: No issues found in {self.tool.files} files (3ms)"
            )
            if self.tool.files == 0:
                return self.done(argv, 0, "", "No markdown files found to check.\n")
            return self.done(argv, 1 if found else 0, line + "\n")
        return self.done(argv, 1 if found else 0, json.dumps(found), err)

    def checks(self) -> list[list[str]]:
        """The tool's own arguments for every `check`: after `uvx --quiet --no-config
        <tool>@<version>`, whose `--no-config` is uv's and would satisfy any test that
        looked for rumdl's."""
        return [
            c["argv"][4:] for c in self.calls if c["argv"][0] == "uvx" and c["argv"][4] == "check"
        ]


class Harness(unittest.TestCase):
    def go(
        self, fake: Fake, checks: Mapping[str, str | None] | None = None, tool: str | None = None
    ) -> tuple[int, str, str]:
        """(exit status, stdout, stderr), with the scratch directory kept for reading."""
        checks = checks if checks is not None else {"MD013": "VIOLATES MD013\n"}
        with tempfile.TemporaryDirectory() as where:
            self.scratch = pathlib.Path(where).resolve()
            inputs = self.scratch / "inputs"
            inputs.mkdir()
            suffix = ".py" if fake.name == "ruff" else ".md"
            argv = ["rulecheck.py", "--scratch", where, "--ref", PR, "--base", BASE]
            argv += ["--tool", tool or fake.name]
            for rule, body in checks.items():
                if body is None:
                    argv += ["--check", rule]
                    continue
                control = inputs / f"{rule.lower()}{suffix}"
                control.write_text(body, encoding="utf-8")
                argv += ["--check", f"{rule}={control}"]
            out, err = io.StringIO(), io.StringIO()
            with (
                mock.patch("rulecheck.run", fake),
                mock.patch.object(sys, "argv", argv),
                contextlib.redirect_stdout(out),
                contextlib.redirect_stderr(err),
            ):
                try:
                    code: int | str | None = main()
                except SystemExit as exc:
                    code = exc.code
            self.written = {
                str(p.relative_to(self.scratch)): p.read_bytes()
                for p in self.scratch.rglob("*")
                if p.is_file()
            }
        return int(code or 0), out.getvalue(), err.getvalue()


class TestTheToolIsTheBasesPinNeverThePrs(Harness):
    """The check stays inside `--no-execute` because the tool is the one this repo
    runs today. A pin read at the PR's ref would let the PR choose what runs here."""

    def test_every_run_is_the_version_the_base_pins(self) -> None:
        fake = Fake()
        code, out, _ = self.go(fake)
        self.assertEqual(code, 0, out)
        specs = {c["argv"][3] for c in fake.calls if c["argv"][0] == "uvx"}
        self.assertEqual(specs, {"rumdl@0.2.72"})
        self.assertIn("rumdl 0.2.72, the version uv.lock pins at the base", out)

    def test_the_prs_lockfile_is_never_read(self) -> None:
        fake = Fake()
        self.go(fake)
        shown = [c["argv"] for c in fake.calls if c["argv"][:2] == ["git", "show"]]
        self.assertEqual(shown, [["git", "show", f"{BASE}:uv.lock"]])

    def test_a_tool_the_base_does_not_pin_is_refused_before_anything_runs(self) -> None:
        fake = Fake(locks={BASE: lock(ruff="0.16.7"), PR: lock(rumdl="0.2.72")})
        code, _, err = self.go(fake)
        self.assertEqual(code, 2)
        self.assertIn("pins no rumdl from a registry", err)
        self.assertFalse([c for c in fake.calls if c["argv"][0] == "uvx"])

    def test_two_pins_under_markers_are_refused(self) -> None:
        twice = lock(rumdl="0.2.72") + lock(rumdl="0.2.60").split("\n", 2)[2]
        fake = Fake(locks={BASE: twice})
        code, _, err = self.go(fake)
        self.assertEqual(code, 2)
        self.assertIn("pins rumdl at 0.2.60, 0.2.72", err)


class TestItReadsThePrAtItsRefNeverTheCheckout(Harness):
    """The prose ran in `$SCRATCH/pr-<N>`, which exists only where Phase 4 or 5 runs."""

    def test_every_regular_file_is_written_from_git_exactly(self) -> None:
        self.go(Fake())
        tree = {k.removeprefix("pr-1-rulecheck/"): v for k, v in self.written.items()}
        tree = {k: v for k, v in tree.items() if not k.startswith(("pr-1-", "inputs/"))}
        self.assertEqual(
            tree,
            {"pyproject.toml": BLOBS["1" * 40], "docs/guide.md": BLOBS["2" * 40],
             "src/UP052.py": BLOBS["3" * 40]},
        )  # fmt: skip

    def test_a_symlink_and_a_submodule_are_named_not_followed(self) -> None:
        _, out, _ = self.go(Fake())
        self.assertIn("not written: linked.md (mode 120000, not a regular file)", out)
        self.assertIn("not written: vendored (mode 160000, not a regular file)", out)

    def test_a_path_that_climbs_out_is_refused(self) -> None:
        tree = [*TREE, ("100644", "blob", "2" * 40, "../escape.md")]
        _, out, _ = self.go(Fake(tree=tree))
        self.assertIn("not written: ../escape.md (not a plain relative path)", out)
        self.assertFalse([k for k in self.written if "escape" in k])

    def test_every_git_call_names_a_ref_or_an_object(self) -> None:
        fake = Fake()
        self.go(fake)
        for call in fake.calls:
            argv = call["argv"]
            if argv[0] == "git" and argv[1:3] != ["cat-file", "--batch"]:
                self.assertTrue(any(n in " ".join(argv) for n in (PR, BASE)), argv)

    def test_every_tool_run_starts_in_the_scratch_directory(self) -> None:
        fake = Fake()
        self.go(fake)
        for call in fake.calls:
            if call["argv"][0] == "uvx":
                self.assertEqual(call["cwd"], self.scratch, call["argv"])

    def test_a_ref_that_does_not_resolve_is_underivable(self) -> None:
        fake = Fake(tree=TREE)
        fake.git = lambda argv, rest, stdin: (  # type: ignore[method-assign]
            Fake.done(argv, 0, lock(rumdl="0.2.72"))
            if rest[0] == "show"
            else Fake.done(argv, 128, err="fatal: not a tree object\n")
        )
        code, out, err = self.go(fake)
        self.assertEqual(code, 2)
        self.assertIn("cannot list the tree at pr-1", err)
        self.assertNotIn("RESULT", out)


class TestNothingTheTreeConfiguresRuns(Harness):
    """A rumdl config can name a command as a code-block tool, and `rumdl check` runs
    it, with `--enable` too. The tree's files are input, never instructions."""

    def test_every_rumdl_run_that_reads_the_config_skips_code_block_tools(self) -> None:
        fake = Fake()
        self.go(fake)
        reading_config = [a for a in fake.checks() if "--no-config" not in a and "--help" not in a]
        self.assertTrue(reading_config)
        for argv in reading_config:
            self.assertIn("--no-code-block-tools", argv)

    def test_a_rumdl_that_cannot_skip_them_never_reads_the_config(self) -> None:
        fake = Fake(tool=Tool(skips_code_block_tools=False))
        code, out, _ = self.go(fake)
        self.assertEqual(code, 2)
        runs = [a for a in fake.checks() if "--help" not in a]
        self.assertTrue(runs)
        for argv in runs:
            self.assertIn("--no-config", argv)
        self.assertIn("rumdl 0.2.72 has no --no-code-block-tools", out)
        self.assertIn("MD013: UNDERIVABLE", out)

    def test_no_run_fixes_and_every_run_skips_the_cache(self) -> None:
        for name in ("rumdl", "ruff"):
            with self.subTest(name):
                fake = Fake(name=name, tool=Tool(rules={"N802": {}}, config_runs=set()))
                checks = {"N802": "VIOLATES N802\n"} if name == "ruff" else None
                self.go(fake, checks)
                for argv in fake.checks():
                    if "--help" in argv:
                        continue
                    self.assertIn("--no-cache", argv)
                    for writes in ("--fix", "-f", "--diff", "--unsafe-fixes"):
                        self.assertNotIn(writes, argv)

    def test_a_user_level_config_cannot_stand_in_for_the_repos(self) -> None:
        fake = Fake()
        self.go(fake)
        homes = {c["env"].get("XDG_CONFIG_HOME") for c in fake.calls if c["argv"][0] == "uvx"}
        self.assertEqual(homes, {str(self.scratch / "pr-1-rulecheck-xdg")})


class TestTheControlMustFireFirst(Harness):
    """rumdl warns about a rule it does not know and exits 0; ruff exits 0 on a preview
    rule selected without `--preview`. Silence is evidence only after the control."""

    def test_a_silent_control_is_underivable_however_quiet_the_tree(self) -> None:
        fake = Fake(tool=Tool(config_runs=set()))
        code, out, _ = self.go(fake, {"MD013": "nothing to see\n"})
        self.assertEqual(code, 2)
        self.assertIn("MD013: UNDERIVABLE -- the control did not fire", out)
        self.assertIn("The fix's own test has one", out)
        self.assertNotIn("INERT", out)

    def test_a_rule_the_pinned_version_lacks_is_refused_by_name(self) -> None:
        fake = Fake(tool=Tool(rules={"MD013": {}}))
        code, out, _ = self.go(fake, {"MD090": "VIOLATES MD090\n"})
        self.assertEqual(code, 2)
        self.assertIn("rumdl 0.2.72, the version this repo runs today, has no rule MD090", out)
        self.assertIn("Phase 4's question", out)
        self.assertFalse([a for a in fake.checks() if "MD090" in a])

    def test_a_preview_rule_is_named_with_preview_and_nothing_else_is(self) -> None:
        tool = Tool(rules={"PLW1514": {"preview": True}}, config_runs={"PLW1514"})
        fake = Fake(name="ruff", tool=tool)
        code, out, _ = self.go(fake, {"PLW1514": "VIOLATES PLW1514\n"})
        self.assertEqual(code, 1, out)
        for argv in fake.checks():
            self.assertEqual("--preview" in argv, "--select" in argv, argv)

    def test_without_preview_the_same_rule_reads_as_a_silent_control(self) -> None:
        tool = Tool(rules={"PLW1514": {"preview": True}}, config_runs=set())
        fake = Fake(name="ruff", tool=tool)
        with mock.patch.object(
            rulecheck.Tool, "named", lambda self, code, preview: ["--select", code]
        ):
            code, out, _ = self.go(fake, {"PLW1514": "VIOLATES PLW1514\n"})
        self.assertEqual(code, 2)
        self.assertIn("ruff did not run PLW1514", out)


class TestTheCountIsTheJsonCode(Harness):
    """#197: `grep -c UP052` counted ruff's fixture `UP052.py` by its filename -- 45
    where the rule fired 0 times. The JSON's code field cannot be read that way."""

    def test_a_file_named_for_the_rule_does_not_count_for_it(self) -> None:
        tool = Tool(
            rules={"UP052": {}, "D100": {}},
            config_runs={"D100"},
            tree={"D100": ["src/UP052.py"] * 45},
        )
        fake = Fake(name="ruff", tool=tool)
        code, out, _ = self.go(fake, {"UP052": "VIOLATES UP052\n"})
        self.assertEqual(code, 0, out)
        self.assertIn("this config   this config", out)
        self.assertIn("0 in 2 file(s)", out)
        self.assertIn("UP052: INERT HERE", out)


class TestTheReading(unittest.TestCase):
    """The verdict's question is whether this config runs the rule, not whether
    today's tree happens to carry it: a fix runs on the next file that does."""

    FIRED = Count(hits=1, files={"c"})

    def read(self, **runs: Any) -> tuple[str, str]:
        given: dict[str, Any] = {
            "control": self.FIRED,
            "state": Count(),
            "vouch": self.FIRED,
            "configured": Count(),
            "forced": Count(),
            "total": 41,
            "given": True,
            **runs,
        }
        return reading("MD013", **given, why_total="", hint="hint")

    def test_this_config_reporting_it_is_live_and_counted(self) -> None:
        verdict, sentence = self.read(configured=Count(hits=12, files={"a", "b"}), state=self.FIRED)
        self.assertEqual(verdict, LIVE)
        self.assertIn("it reports 12 in 2 of 41 file(s)", sentence)

    def test_this_config_running_it_over_a_clean_tree_is_live(self) -> None:
        verdict, sentence = self.read(state=self.FIRED)
        self.assertEqual(verdict, LIVE)
        self.assertIn("the tree carries none of it today", sentence)

    def test_this_config_not_running_it_is_inert_and_says_what_it_spares(self) -> None:
        verdict, sentence = self.read(forced=Count(hits=4949, files={str(i) for i in range(36)}))
        self.assertEqual(verdict, INERT)
        self.assertIn("this config does not run MD013", sentence)
        self.assertIn("Forced on, it reports 4949 in 36 of 41 file(s)", sentence)

    def test_inert_over_a_tree_that_carries_none_either(self) -> None:
        verdict, sentence = self.read()
        self.assertEqual(verdict, INERT)
        self.assertIn("the tree carries none of it either", sentence)

    def test_an_unread_state_falls_back_to_the_tree(self) -> None:
        verdict, _ = self.read(vouch=Count(), forced=Count(hits=3, files={"a"}))
        self.assertEqual(verdict, INERT)
        verdict, sentence = self.read(vouch=Count())
        self.assertEqual(verdict, UNDERIVABLE)
        self.assertIn("is not read", sentence)

    def test_no_file_read_is_not_clean(self) -> None:
        verdict, _ = self.read(total=0)
        self.assertEqual(verdict, UNDERIVABLE)

    def test_a_tree_run_that_failed_is_not_silence(self) -> None:
        verdict, sentence = self.read(configured=Count(why="exit 2"))
        self.assertEqual(verdict, UNDERIVABLE)
        self.assertIn("could not run: exit 2", sentence)

    def test_the_tree_outranks_an_input_that_did_not_fire(self) -> None:
        """Reversed in 0.61.0. The input only answers where the tree is silent: what
        this config reports over the tree is the rule running here, whatever an input
        does."""
        verdict, _ = self.read(control=Count(), configured=Count(hits=5, files={"a"}))
        self.assertEqual(verdict, LIVE)

    def test_a_silent_control_decides_when_the_tree_is_silent_too(self) -> None:
        verdict, sentence = self.read(control=Count())
        self.assertEqual(verdict, UNDERIVABLE)
        self.assertIn("the control did not fire", sentence)

    def test_no_input_over_a_silent_tree_names_the_one_it_needs(self) -> None:
        verdict, sentence = self.read(
            given=False, tried=", and none of the 9 example(s) in x fires"
        )
        self.assertEqual(verdict, UNDERIVABLE)
        self.assertIn("none of the 9 example(s) in x fires", sentence)
        self.assertIn("--check MD013=<file>", sentence)


class TestTheTreeIsTheControl(Harness):
    """The first replay pair on 0.61.0's first cut never called the script, and one
    run said why: every rule wanted an input from its fix's own test. Where the
    forced run fires on the tree, the rule fires here under this config, and no input
    is needed to say whether the config runs it."""

    def test_a_rule_the_config_leaves_off_is_inert_off_the_tree_alone(self) -> None:
        tool = Tool(config_runs=set(), tree={"MD013": ["README.md", "docs/guide.md"]})
        fake = Fake(tool=tool)
        code, out, _ = self.go(fake, {"MD013": None})
        self.assertEqual(code, 0, out)
        self.assertIn("MD013: INERT HERE -- this config does not run MD013. Forced on", out)
        self.assertFalse([c for c in fake.calls if c["argv"][0] == "gh"])
        self.assertNotIn("control", out)

    def test_a_rule_the_config_runs_is_live_off_the_tree_alone(self) -> None:
        tool = Tool(config_runs={"MD077"}, tree={"MD077": ["docs/guide.md"]})
        fake = Fake(tool=tool)
        code, out, _ = self.go(fake, {"MD077": None})
        self.assertEqual(code, 1, out)
        self.assertIn("MD077: LIVE HERE -- this config runs MD077: it reports 1 in 1 of", out)


class TestTheToolsOwnPageIsTheInput(Harness):
    """Where the tree carries none of the rule -- every rule a gate that fixes on each
    commit runs -- the first example on the rule's own page that the tool fires on is
    the input. On #438's ten rumdl rules the pages answered nine."""

    PAGE = (
        "# MD077\n\n#### Correct\n\n```markdown\nfine\n```\n\n"
        "#### Incorrect\n\n````markdown\nVIOLATES MD077\n```\nfenced inside\n```\n````\n"
    )

    def test_a_fence_inside_an_example_stays_inside(self) -> None:
        """A page that shows a fence inside an example opens the example with a longer
        one, and CommonMark closes it only on a fence at least as long."""
        self.assertEqual(
            rulecheck.fences(self.PAGE, {"markdown"}),
            ["fine\n", "VIOLATES MD077\n```\nfenced inside\n```\n"],
        )
        self.assertEqual(rulecheck.fences("```toml\nx = 1\n```\n", {"markdown"}), [])

    def test_an_example_from_rumdls_page_answers_a_silent_tree(self) -> None:
        tool = Tool(config_runs={"MD077"}, pages={"MD077": self.PAGE})
        fake = Fake(tool=tool)
        code, out, _ = self.go(fake, {"MD077": None})
        self.assertEqual(code, 1, out)
        self.assertIn("the input: example 2 of 2 in rumdl's docs/md077.md at v0.2.72", out)
        self.assertIn("MD077: LIVE HERE -- this config runs MD077; the tree carries none", out)

    def test_the_page_is_read_at_the_version_the_base_pins(self) -> None:
        fake = Fake(tool=Tool(config_runs={"MD077"}, pages={"MD077": self.PAGE}))
        self.go(fake, {"MD077": None})
        urls = [c["argv"][-1] for c in fake.calls if c["argv"][0] == "gh"]
        self.assertEqual(urls, ["repos/rvben/rumdl/contents/docs/md077.md?ref=v0.2.72"])

    def test_an_example_the_config_does_not_run_is_inert(self) -> None:
        fake = Fake(tool=Tool(config_runs=set(), pages={"MD077": self.PAGE}))
        code, out, _ = self.go(fake, {"MD077": None})
        self.assertEqual(code, 0, out)
        self.assertIn("this config does not run MD077, and forced on the tree carries", out)

    def test_ruff_reads_its_page_from_its_own_binary(self) -> None:
        page = "## Example\n\n```python\nVIOLATES SIM117\n```\n"
        tool = Tool(rules={"SIM117": {}}, config_runs=set(), pages={"SIM117": page})
        fake = Fake(name="ruff", tool=tool)
        code, out, _ = self.go(fake, {"SIM117": None})
        self.assertEqual(code, 0, out)
        self.assertIn("the input: example 1 of 1 in ruff 0.16.7's own page for SIM117", out)
        self.assertFalse([c for c in fake.calls if c["argv"][0] == "gh"])

    def test_no_example_that_fires_asks_for_an_input_by_name(self) -> None:
        page = "#### Incorrect\n\n```markdown\nText\n---\nText\n```\n"
        fake = Fake(tool=Tool(config_runs={"MD065"}, rules={"MD065": {}}, pages={"MD065": page}))
        code, out, _ = self.go(fake, {"MD065": None})
        self.assertEqual(code, 2, out)
        self.assertIn("none of the 1 example(s) in rumdl's docs/md065.md at v0.2.72 fires", out)
        self.assertIn("--check MD065=<file>", out)

    def test_a_page_that_does_not_come_back_asks_for_an_input(self) -> None:
        fake = Fake(tool=Tool(config_runs={"MD077"}))
        code, out, _ = self.go(fake, {"MD077": None})
        self.assertEqual(code, 2, out)
        self.assertIn("which did not come back", out)

    def test_an_input_given_is_used_and_the_page_is_not_read(self) -> None:
        fake = Fake(tool=Tool(config_runs={"MD077"}, pages={"MD077": self.PAGE}))
        code, out, _ = self.go(fake, {"MD077": "VIOLATES MD077\n"})
        self.assertEqual(code, 1, out)
        self.assertIn("the input: the input given", out)
        self.assertFalse([c for c in fake.calls if c["argv"][0] == "gh"])


class TestTheFileCountIsTheToolsOwn(Harness):
    """rumdl over a tree with no Markdown says so on stderr and exits 0."""

    def test_no_markdown_is_underivable_not_clean(self) -> None:
        fake = Fake(tool=Tool(files=0, config_runs=set()))
        code, out, _ = self.go(fake)
        self.assertEqual(code, 2)
        self.assertIn("this config's run read no files", out)

    def test_ruff_counts_with_show_files(self) -> None:
        tool = Tool(rules={"N802": {}}, config_runs=set(), files=215)
        fake = Fake(name="ruff", tool=tool)
        _, out, _ = self.go(fake, {"N802": "VIOLATES N802\n"})
        self.assertIn("this config reads 215 file(s) over the tree", out)
        self.assertTrue([a for a in fake.checks() if "--show-files" in a])

    def test_the_count_and_the_tree_runs_read_the_same_config(self) -> None:
        """The prose's count had to carry the exposure run's isolation, 3 files
        against 4 under one `exclude`. The tree runs now keep the config, so the
        count does too: neither may drop it."""
        for name, rule in (("ruff", "N802"), ("rumdl", "MD013")):
            with self.subTest(name):
                fake = Fake(name=name, tool=Tool(rules={rule: {}}, config_runs=set()))
                self.go(fake, {rule: f"VIOLATES {rule}\n"})
                tree = str(self.scratch / "pr-1-rulecheck")
                over_tree = [a for a in fake.checks() if a[-1] == tree or tree in a]
                self.assertGreaterEqual(len(over_tree), 3, fake.checks())
                for argv in over_tree:
                    self.assertNotIn("--isolated", argv)
                    self.assertNotIn("--no-config", argv)


class TestSeveralRules(Harness):
    def test_live_outranks_underivable_outranks_inert(self) -> None:
        tool = Tool(rules={"MD013": {}, "MD077": {}}, config_runs={"MD077"})
        fake = Fake(tool=tool)
        checks = {"MD013": "VIOLATES MD013\n", "MD077": "VIOLATES MD077\n", "MD090": "x\n"}
        code, out, _ = self.go(fake, checks)
        self.assertEqual(code, 1)
        self.assertIn("RESULT: LIVE HERE -- MD077", out)
        self.assertIn("RESULT: UNDERIVABLE -- MD090", out)
        self.assertIn("RESULT: INERT HERE -- MD013", out)

    def test_the_tree_runs_never_see_the_control(self) -> None:
        """A control in the tree would read as exposure: its violation is planted.
        It is copied outside the tree, and placed at the root only for the config
        state, after the tree runs."""
        tool = Tool(rules={"MD013": {}, "MD077": {}}, config_runs={"MD077"})
        fake = Fake(tool=tool)
        self.go(fake, {"MD013": "VIOLATES MD013\n", "MD077": "VIOLATES MD077\n"})
        self.assertTrue(fake.tree_saw)
        self.assertEqual([seen for seen in fake.tree_saw if seen], [])

    def test_the_control_at_the_root_is_gone_before_the_next_rule(self) -> None:
        tool = Tool(rules={"MD013": {}, "MD077": {}}, config_runs={"MD077"})
        fake = Fake(tool=tool)
        self.go(fake, {"MD013": "VIOLATES MD013\n", "MD077": "VIOLATES MD077\n"})
        self.assertFalse([k for k in self.written if "rulecheck-control-" in k])

    def test_a_malformed_check_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as where:
            argv = ["rulecheck.py", "--scratch", where, "--ref", PR, "--base", BASE]
            argv += ["--tool", "rumdl", "--check", "MD013="]
            with (
                mock.patch.object(sys, "argv", argv),
                contextlib.redirect_stderr(io.StringIO()) as err2,
                self.assertRaises(SystemExit) as raised,
            ):
                main()
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("--check takes <RULE> or <RULE>=<file>", err2.getvalue())


if __name__ == "__main__":
    unittest.main()
