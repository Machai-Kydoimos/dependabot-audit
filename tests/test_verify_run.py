"""Regression tests for verify_run.py, and for the prose each of its rules is
derived from.

No network. The record format is the one a `PreToolUse` hook writes, so the
fixtures here are JSON lines rather than shell.

**The command fixtures are not invented.** Every string in `R26` and `R27` was
issued by a real replay of `fpga-board-sim` #437 — round twenty-six under 0.48.0
before Phase 5 supplied its gate loop, and round twenty-seven after. That matters
because CONTRIBUTING's rule is that a fixture built from the rule can only ever
agree with it: these were recorded before the rules existed, which is the only
way this file can be evidence rather than an echo.

What the two rounds are known to contain, from their transcripts:

| | round 26 | round 27 |
|---|---|---|
| project-form run with `--frozen` | 3 | 0 |
| `test -x ".venv/bin/` | absent | present |
| `ruff check --select` | 0 | 3 |
| plugin document read by hand | 0 | 1 (`wc -l references/uv-lock.md`) |

Round twenty-six's report asserted *"No improvisation. Every command in this
audit came from SKILL.md or references/uv-lock.md as written"* and *"inert here
by construction"* for six ruff rule families. Both are false against the record,
and both are what this script exists to catch.

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import contextlib
import io
import json
import pathlib
import sys
import tempfile
import unittest
from collections.abc import Iterator
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
SKILL = ROOT / "skills/dependabot-audit"
SCRIPTS = SKILL / "scripts"
sys.path.insert(0, str(SCRIPTS))

from verify_run import (  # noqa: E402  # noqa: E402
    HIDDEN,
    PLUGIN_DOC,
    RULES,
    check,
    cli,
    commands,
    count,
    evidence,
    main,
    window,
)

# Verbatim from round twenty-six's transcript (2026-09-19, 0.48.0 pre-fix).
R26 = [
    # The preamble is kept verbatim because every block in a real record opens
    # with it: it is what makes "the first line" and "the line that matched"
    # different strings, and quoting the wrong one names the wrong command.
    "REPO=$(gh repo view --json nameWithOwner --jq .nameWithOwner)\n"
    '. "$SCRATCH/phase0.env" || exit 2\n'
    'for g in "ruff check ." "ruff format --check ." "mypy ." "rumdl check ." "actionlint"; do\n'
    "  out=$(uv run --frozen $g 2>&1); rc=$?\n"
    "done",
    'uv run --frozen pytest -m "not slow" -q 2>&1 | tail -15',
    "uv run --frozen pytest -q 2>&1 | tail -8",
    'uv run -q --no-project --with "rumdl==$v" rumdl check --fix --enable MD065 "at-$v.md"',
    'echo "--- closed bot PRs naming ruff or rumdl ---"',
    'python3 "${SCRIPTS:?}/changelog.py" --scratch "$SCRATCH" --package rumdl --from 0.2.67 --to 0.2.72',
]

# Verbatim from round twenty-seven (2026-09-20, 0.48.0 as released).
R27 = [
    'for g in "ruff check ." "ruff format --check ." "mypy ." "rumdl check ." "actionlint"; do\n'
    '  test -x ".venv/bin/${g%% *}"; echo "${g%% *} in the environment: $?"\n'
    '  uv run $g > "$SCRATCH/gate.out" 2> "$SCRATCH/gate.err"; echo "$g exit: $?"\n'
    "done",
    "uv run -q --no-project --with ruff==0.16.8 ruff check --isolated --select UP040 --statistics .",
    "uv run -q --no-project --with rumdl==0.2.67 rumdl check --fix .",
]


def record(commands_: list[str]) -> str:
    return "".join(
        json.dumps(
            {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": c}}
        )
        + "\n"
        for c in commands_
    )


@contextlib.contextmanager
def log_of(commands_: list[str] | str) -> Iterator[pathlib.Path]:
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "run.jsonl"
        p.write_text(
            record(commands_) if isinstance(commands_, list) else commands_, encoding="utf-8"
        )
        yield p


def run_cli(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue(), err.getvalue()


def fired(cmds: list[str]) -> set[str]:
    return {f.rule for f in check(cmds)}


class TestEveryRuleIsDerivedFromProseThatStillExists(unittest.TestCase):
    """The anti-drift guard, and the reason `Rule.anchor` exists at all.

    A rule whose sentence has been deleted is a rule asserting something this
    plugin no longer says, and it would keep passing in silence. `scope is stated
    in four places` is the recurring shape of that failure here; this is the one
    place it is mechanised.
    """

    def test_each_rules_anchor_is_present_in_the_file_it_names(self) -> None:
        for rule in RULES:
            with self.subTest(rule=rule.id):
                text = (SKILL / rule.anchor_file).read_text(encoding="utf-8")
                self.assertIn(
                    rule.anchor,
                    text,
                    f"verify_run.py's `{rule.id}` rule is derived from a sentence in "
                    f"{rule.anchor_file} that is no longer there. Either restore it or drop "
                    f"the rule — a check outliving its rationale is how prose and code split.",
                )

    def test_every_rule_id_can_actually_fire(self) -> None:
        """Anti-vacuity: a rule nobody can trigger satisfies the guard above in silence."""
        reachable = fired(R26) | fired(R27) | fired(["cat skills/dependabot-audit/SKILL.md"])
        declared = {r.id for r in RULES}
        self.assertEqual(
            declared - reachable,
            set(),
            "declared rules that no fixture in this file triggers",
        )


class TestTheTwoRoundsBehindTheRules(unittest.TestCase):
    def test_round_twenty_six_is_caught_on_both_counts(self) -> None:
        rules = fired(R26)
        self.assertIn("hidden-sync", rules)
        self.assertIn("gate-tool-unchecked", rules)
        self.assertIn("inert-needs-named-run", rules)

    def test_round_twenty_seven_clears_the_gate_rules(self) -> None:
        rules = fired(R27)
        self.assertNotIn("hidden-sync", rules)
        self.assertNotIn("gate-tool-unchecked", rules)

    def test_the_report_claim_round_twenty_six_made_is_the_one_contradicted(self) -> None:
        """`No improvisation` was written against a record holding three of them."""
        code, out, _ = run_cli_on(R26)
        self.assertEqual(code, 1)
        self.assertIn("uv run --frozen", out)


def run_cli_on(cmds: list[str]) -> tuple[int, str, str]:
    with log_of(cmds) as p:
        return run_cli("--log", str(p))


class TestHiddenSync(unittest.TestCase):
    def test_it_fires_on_the_project_form(self) -> None:
        self.assertIn("hidden-sync", fired(["uv run --frozen pytest -q"]))

    def test_it_does_not_fire_on_the_no_project_form(self) -> None:
        """Phase 4's reproducer uses --no-project deliberately; neither flag applies there."""
        self.assertNotIn(
            "hidden-sync",
            fired(['uv run -q --no-project --with "ruff==0.16.7" ruff check --fix .']),
        )

    def test_no_project_wins_even_when_a_hidden_flag_is_present(self) -> None:
        """The belt-and-braces case, and the only one that tests the braces.

        `UV_RUN`'s lookahead only scans an unbroken run of flags, so it gives up
        at the first non-flag token and matches anyway — here `ruff==0.16.7` sits
        before `--no-project`. What excludes this command is the separate
        `NO_PROJECT` test, and without it a legitimate Phase 4 reproducer is
        reported as a Phase 5 defect.
        """
        self.assertNotIn(
            "hidden-sync",
            fired(["uv run --frozen --with ruff==0.16.7 --no-project ruff check ."]),
        )

    def test_no_sync_counts_too(self) -> None:
        self.assertIn("hidden-sync", fired(["uv run --no-sync mypy ."]))

    def test_a_clean_gate_run_does_not_fire(self) -> None:
        self.assertNotIn("hidden-sync", fired(['test -x ".venv/bin/ruff"', "uv run ruff check ."]))


class TestGateToolUnchecked(unittest.TestCase):
    def test_it_fires_when_gates_ran_with_no_executable_check(self) -> None:
        self.assertIn("gate-tool-unchecked", fired(["uv run ruff check ."]))

    def test_the_supplied_loops_check_clears_it(self) -> None:
        self.assertNotIn(
            "gate-tool-unchecked",
            fired(['test -x ".venv/bin/${g%% *}"', "uv run ruff check ."]),
        )

    def test_it_stays_silent_when_no_gate_ran_in_the_project_environment(self) -> None:
        """Nothing to check where nothing used the environment."""
        self.assertNotIn(
            "gate-tool-unchecked",
            fired(["uv run -q --no-project --with ruff==0.16.7 ruff check ."]),
        )


class TestInertNeedsNamedRun(unittest.TestCase):
    def test_a_tool_that_ran_without_naming_a_rule_is_noted(self) -> None:
        self.assertIn("inert-needs-named-run", fired(["ruff check ."]))

    def test_both_named_tools_are_covered(self) -> None:
        """Dropping either entry from the table leaves the other's tests green."""
        self.assertIn("inert-needs-named-run", fired(["ruff check ."]))
        self.assertIn("inert-needs-named-run", fired(["rumdl check ."]))

    def test_naming_the_rule_clears_it(self) -> None:
        self.assertNotIn(
            "inert-needs-named-run",
            fired(["ruff check --isolated --select N802 ."]),
        )

    def test_the_answer_is_per_tool(self) -> None:
        """Round twenty-six's exact shape: rumdl named a rule, ruff did not, and the
        claim in the report was about ruff."""
        results = check(["rumdl check --enable MD065 .", "ruff check ."])
        detail = " ".join(f.detail for f in results if f.rule == "inert-needs-named-run")
        self.assertIn("ruff", detail)
        self.assertNotIn("rumdl", detail)

    def test_naming_a_tool_is_not_running_it(self) -> None:
        """`echo "ruff or rumdl"` and `--package rumdl` both name a tool without
        invoking it. Counting those scored this rule two false positives on r26/r27
        before it required a subcommand."""
        self.assertNotIn(
            "inert-needs-named-run",
            fired(
                [
                    'echo "--- closed bot PRs naming ruff or rumdl ---"',
                    "changelog.py --package rumdl",
                ]
            ),
        )

    def test_it_is_a_note_and_does_not_set_the_exit_code(self) -> None:
        code, out, _ = run_cli_on(["ruff check ."])
        self.assertIn("NOTE [inert-needs-named-run]", out)
        self.assertEqual(code, 0, "a limit on what may be claimed is not a defect in the run")


class TestPluginFileRead(unittest.TestCase):
    def test_reading_the_procedure_by_hand_is_a_finding(self) -> None:
        self.assertIn("plugin-file-read", fired(["cat skills/dependabot-audit/SKILL.md"]))

    def test_measuring_a_reference_counts(self) -> None:
        self.assertIn("plugin-file-read", fired(["wc -l references/uv-lock.md"]))

    def test_an_unrelated_markdown_read_does_not(self) -> None:
        self.assertNotIn("plugin-file-read", fired(["cat CHANGELOG.md", "head -5 docs/guide.md"]))


# Verbatim from round twenty-eight (2026-09-20), the first live run with the
# record switched on. All five of #437's gates carried --frozen inside ONE Bash
# call, which is what showed that counting calls understates the run.
R28_GATES = (
    'echo "=== ruff check . ==="        ; uv run --frozen ruff check .        ; echo "exit: $?"\n'
    'echo "=== ruff format --check . ===" ; uv run --frozen ruff format --check . ; echo "exit: $?"\n'
    'echo "=== mypy . ==="             ; uv run --frozen mypy .              ; echo "exit: $?"\n'
    'echo "=== rumdl check . ==="      ; uv run --frozen rumdl check .       ; echo "exit: $?"\n'
    'echo "=== actionlint ==="         ; uv run --frozen actionlint          ; echo "exit: $?"'
)


class TestOneCommandCanHoldManyInvocations(unittest.TestCase):
    """Round twenty-eight's finding, and a defect in this script folded back in.

    The first version reported "1 command(s)" here and quoted one line. True
    about the record, and an understatement of the run by a factor of five —
    the same class as quoting the preamble instead of the match, one level up.
    """

    def test_all_five_gates_are_counted(self) -> None:
        self.assertEqual(count(HIDDEN, [R28_GATES]), 5)

    def test_all_five_are_shown_as_evidence(self) -> None:
        code, out, _ = run_cli_on([R28_GATES])
        self.assertEqual(code, 1)
        self.assertIn("5 gate invocation(s), across 1 command(s)", out)
        for tool in ("ruff check", "ruff format", "mypy", "rumdl check", "actionlint"):
            self.assertIn(tool, out)

    def test_a_long_list_is_capped_and_says_so(self) -> None:
        many = "\n".join(f"uv run --frozen gate{i}" for i in range(20))
        _, out, _ = run_cli_on([many])
        self.assertIn("... and 12 more", out)


class TestEvidenceNamesTheCommandThatMatched(unittest.TestCase):
    def test_it_returns_the_matching_line_not_the_first(self) -> None:
        """Every one of these blocks opens with the same `REPO=$(gh repo view ...)`
        preamble, so quoting line one names the wrong command in every finding."""
        _, out, _ = run_cli_on(R26)
        self.assertIn("uv run --frozen pytest", out)
        self.assertNotIn("gh repo view", out)

    def test_it_returns_every_match_not_only_the_first(self) -> None:
        self.assertEqual(len(evidence(R28_GATES, HIDDEN)), 5)

    def test_a_long_one_liner_keeps_the_match_in_view(self) -> None:
        line = "cd " + "x" * 200 + " && wc -l references/uv-lock.md"
        shown = window(line, PLUGIN_DOC)
        self.assertIn("references/uv-lock.md", shown)
        self.assertTrue(shown.startswith("…"))

    def test_a_short_line_is_not_windowed(self) -> None:
        self.assertEqual(
            window("uv run --frozen pytest", __import__("verify_run").HIDDEN),
            "uv run --frozen pytest",
        )


class TestTheRecordIsReadTheWayTheHookWritesIt(unittest.TestCase):
    def test_non_bash_events_are_ignored(self) -> None:
        """Keyed on `tool_name`, not on the presence of a `command` key.

        Other tools take a `command` too — `BashOutput` among them — so filtering
        on the key alone would fold their inputs into the record and attribute
        them to this audit.
        """
        blob = json.dumps({"tool_name": "Read", "tool_input": {"file_path": "/x"}}) + "\n"
        blob += (
            json.dumps({"tool_name": "BashOutput", "tool_input": {"command": "uv run --frozen x"}})
            + "\n"
        )
        blob += json.dumps({"tool_name": "Bash", "tool_input": {"command": "echo hi"}}) + "\n"
        with log_of(blob) as p:
            self.assertEqual(commands(p), ["echo hi"])

    def test_a_truncated_final_line_is_skipped_not_fatal(self) -> None:
        """The log is appended to while the audit runs, so a partial last line is
        normal rather than corruption."""
        blob = json.dumps({"tool_name": "Bash", "tool_input": {"command": "echo hi"}}) + "\n"
        blob += '{"tool_name": "Bash", "tool_input": {"comm'
        with log_of(blob) as p:
            self.assertEqual(commands(p), ["echo hi"])


class TestTheExitCodesMatchTheScans(unittest.TestCase):
    def test_a_clean_record_is_zero(self) -> None:
        code, out, _ = run_cli_on(['test -x ".venv/bin/ruff"', "uv run ruff check --select E501 ."])
        self.assertEqual(code, 0)
        self.assertIn("no finding", out)

    def test_findings_are_one(self) -> None:
        code, _, _ = run_cli_on(R26)
        self.assertEqual(code, 1)

    def test_a_missing_record_is_underivable_not_clean(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            code, _, err = run_cli("--log", str(pathlib.Path(d) / "absent.jsonl"))
        self.assertEqual(code, 128)
        self.assertIn("not a clean result", err)

    def test_no_session_id_is_underivable(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True):
            code, _, err = run_cli()
        self.assertEqual(code, 128)
        self.assertIn("CLAUDE_CODE_SESSION_ID", err)

    def test_an_empty_record_is_underivable(self) -> None:
        with log_of([]) as p:
            code, _, err = run_cli("--log", str(p))
        self.assertEqual(code, 128)
        self.assertIn("no Bash commands", err)

    def test_the_default_path_is_the_sessions_own(self) -> None:
        tmp = "/tmp"  # noqa: S108 - the documented default the hook also uses
        with mock.patch.dict("os.environ", {"CLAUDE_CODE_SESSION_ID": "abc-123", "TMPDIR": tmp}):
            code, _, err = run_cli()
        self.assertEqual(code, 128)
        self.assertIn("dbaudit-run-abc-123.jsonl", err)

    def test_an_unexpected_exception_is_a_bug_not_a_finding(self) -> None:
        with mock.patch("verify_run.main", side_effect=RuntimeError("boom")):
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertEqual(cli(), 128)
            self.assertIn("RuntimeError", err.getvalue())


class TestTheHookThatWritesTheRecord(unittest.TestCase):
    # The plugin root, next to `.claude-plugin/` — NOT under the skill directory
    # where `scripts/` and `references/` live. Round twenty-eight was launched with
    # it one level too deep: the audit issued five Bash calls and the record was
    # never created, which is the failure this path assertion now prevents.
    HOOKS = ROOT / "hooks/hooks.json"

    def test_it_sits_at_the_plugin_root(self) -> None:
        """Claude Code reads a plugin's hooks from `<plugin root>/hooks/hooks.json`.
        Anywhere else and it is inert — silently, since a hook that never runs and
        a hook with nothing to say look identical from inside the audit."""
        self.assertTrue(self.HOOKS.is_file(), f"no hooks.json at {self.HOOKS}")
        self.assertTrue((ROOT / ".claude-plugin/plugin.json").is_file())
        self.assertFalse(
            (SKILL / "hooks/hooks.json").exists(),
            "hooks.json under the skill directory is never loaded",
        )

    def test_it_is_valid_json_with_a_bash_matcher(self) -> None:
        cfg = json.loads(self.HOOKS.read_text(encoding="utf-8"))
        entries = cfg["hooks"]["PreToolUse"]
        self.assertTrue(any(e.get("matcher") == "Bash" for e in entries))

    def test_it_writes_outside_the_audited_repository(self) -> None:
        """Read-only by contract, and a log file is a write. Measured on Claude Code
        2.1.278: `CLAUDE_PROJECT_DIR` is the subject's checkout, so writing the record
        relative to it would put a file in the repo under audit."""
        cmd = json.loads(self.HOOKS.read_text(encoding="utf-8"))["hooks"]["PreToolUse"][0]["hooks"][
            0
        ]["command"]
        self.assertIn("TMPDIR", cmd)
        self.assertNotIn("CLAUDE_PROJECT_DIR", cmd)

    def test_it_keys_on_the_session_the_script_reads(self) -> None:
        """Both halves must agree on the path, and they are written in two files."""
        cmd = json.loads(self.HOOKS.read_text(encoding="utf-8"))["hooks"]["PreToolUse"][0]["hooks"][
            0
        ]["command"]
        self.assertIn("dbaudit-run-", cmd)
        self.assertIn("CLAUDE_CODE_SESSION_ID", cmd)
        self.assertIn(
            "CLAUDE_CODE_SESSION_ID", (SCRIPTS / "verify_run.py").read_text(encoding="utf-8")
        )

    def test_it_cannot_fail_the_tool_call(self) -> None:
        cmd = json.loads(self.HOOKS.read_text(encoding="utf-8"))["hooks"]["PreToolUse"][0]["hooks"][
            0
        ]["command"]
        self.assertIn("|| true", cmd)


if __name__ == "__main__":
    unittest.main()
