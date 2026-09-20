#!/usr/bin/env python3
"""Read the record of what this audit actually ran, and report where the run and
the procedure disagree.

Every other phase measures the *subject*. This one measures the *audit*, and it
exists because the report asserts "I followed this procedure" by silence. On
2026-08-19 that assertion was false with nothing anywhere to catch it, and on
2026-09-19 a replay wrote "No improvisation" in its report while its own
transcript showed a flag this procedure names as one not to add.

The record comes from a `PreToolUse` hook (see `hooks/hooks.json`), which appends
one JSON object per matched call to `${TMPDIR:-/tmp}/dbaudit-run-<session>.jsonl`.
The hook writes there and never into the audited repository: this plugin is
read-only by contract, and a log file is a write.

The record holds **Bash and Read calls** — that is the hook's matcher — so a
deviation carried out through any other tool never reaches it. Measured on round
twenty-nine, which returned no finding over 42 recorded calls while the audit
handed back five real deviations, one of them a plugin defect. `Read` was added
in 0.50.0 because reading a plugin file by hand, the one deviation this script
watches for directly, is far likelier to go through `Read` than through `cat`
(#150) — so the rule was nearly blind in its own direction.

What the record cannot hold is **output**: a `PreToolUse` hook fires before the
command runs. So every check here is about the *shape* of what was issued, never
about what it printed. Two rules were prototyped for 0.50.0 and dropped on that
boundary — one asking whether a named lint run had its default-state probe, one
asking whether it had a file count — because each fired on a round that had
established the same thing by a different legitimate route (rounds twenty-eight
and twenty-nine each used a different one). A check that cannot see the answer
cannot tell which question was asked.

Scope, stated because the honest boundary matters more than the count: this does
not diff the run against the procedure line by line. That was prototyped against
round twenty-six and scored 191 unattributed lines out of 323 — 59%, nearly all
of it `echo` separators, `head -30` and variable preamble. A check with that
noise floor gets tuned to silence, so what ships is four narrow rules with a
measured false-positive rate of zero on the two rounds behind them.

Exit codes follow the scans: 0 nothing to report, 1 findings, 128 underivable.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys
from dataclasses import dataclass, field
from typing import Any

SKILL = pathlib.Path(__file__).resolve().parent.parent


@dataclass
class Finding:
    rule: str
    detail: str
    commands: list[str] = field(default_factory=list)
    # A `note` is a limit on what the report may claim, not a defect in the run.
    # Keeping the two apart is what stops the exit code from going permanently
    # red and being read as background noise.
    kind: str = "finding"


@dataclass
class Rule:
    """One check, plus the sentence in the procedure it is derived from.

    `anchor` is not read at run time. It is what `tests/test_verify_run.py`
    asserts is still present, so that deleting the prose deletes the rule rather
    than leaving it asserting something this plugin no longer says.
    """

    id: str
    anchor_file: str
    anchor: str
    why: str


RULES = [
    Rule(
        id="hidden-sync",
        anchor_file="references/uv-lock.md",
        anchor="Add no flag to it: --frozen and --no-sync each hide the line",
        why="a gate run with --frozen or --no-sync cannot report that the environment moved",
    ),
    Rule(
        id="gate-tool-unchecked",
        anchor_file="references/uv-lock.md",
        anchor='test -x ".venv/bin/${g%% *}"',
        why="the project runner falls through to PATH, so a gate whose tool the environment\n"
        "lacks exits 0 against the wrong version",
    ),
    Rule(
        id="inert-needs-named-run",
        anchor_file="SKILL.md",
        anchor="with that rule selected by name",
        why="an allow-list config never enables the rule, so both runs go silent and\n"
        "`inert here` rests on nothing",
    ),
    Rule(
        id="plugin-file-read",
        anchor_file="SKILL.md",
        anchor="every plugin file read directly rather than invoked as written",
        why="reading the procedure by hand is the signature of it not having loaded",
    ),
]

# `uv run` in its project form. The --no-project form is a different command:
# Phase 4's reproducer uses it deliberately and neither flag applies there.
UV_RUN = re.compile(r"(?<![\w-])uv\s+run(?!\s+(?:-\S+\s+)*--no-project)\b")
HIDDEN = re.compile(r"(?<![\w-])--(?:frozen|no-sync)(?![\w-])")
NO_PROJECT = re.compile(r"(?<![\w-])--no-project(?![\w-])")
TOOL_CHECK = re.compile(r"test\s+-x\s+[\"']?\.venv/bin/")
READ_CMD = re.compile(r"(?<![\w-])(?:cat|less|more|head|tail|sed|awk|wc|bat)\b")
PLUGIN_DOC = re.compile(r"[\w/.-]*(?:SKILL\.md|references/[\w-]+\.md)")

# Per tool, because the answer is per tool. Round twenty-six ran
# `rumdl --enable MD065` and no `ruff --select` at all, then wrote "inert here by
# construction" about six *ruff* rule families — so a rule asking only whether
# *some* run named *some* rule reads that transcript as clean.
NAMED_RUN_BY_TOOL = {"ruff": "--select", "rumdl": "--enable"}


MAX_SHOWN = 8


def evidence(cmd: str, pattern: re.Pattern[str]) -> list[str]:
    """Every line that matched, not the first line of the block and not only the
    first match.

    These commands are multi-line, and their first line is almost always the
    `REPO=$(gh repo view ...)` preamble — quoting it names the wrong command and
    is precisely the paraphrase this repo keeps ruling out of evidence.

    Returning only the *first* match was the same mistake one level in. Round
    twenty-eight ran all five of #437's gates with `--frozen` inside a single
    Bash call, and this reported "1 command(s)" over one quoted line: true about
    the record, and an understatement of the run by a factor of five. The unit a
    reader needs is the offending invocation, and one Bash call holds as many of
    those as it likes.
    """
    hits = [window(line.strip(), pattern) for line in cmd.splitlines() if pattern.search(line)]
    return hits or [cmd.strip().splitlines()[0][:WIDTH]]


WIDTH = 120


def window(line: str, pattern: re.Pattern[str]) -> str:
    """Keep the match in view. A one-line `cd … && … && wc -l references/x.md`
    truncated from the left shows the `cd` and hides the thing it was flagged
    for, which reads as a false positive on a true one."""
    if len(line) <= WIDTH:
        return line
    m = pattern.search(line)
    if m is None:
        return line[:WIDTH]
    start = max(0, m.start() - WIDTH // 3)
    end = min(len(line), start + WIDTH)
    return ("…" if start else "") + line[start:end] + ("…" if end < len(line) else "")


def count(pattern: re.Pattern[str], cmds: list[str]) -> int:
    """Matching lines across all the commands — what a reader means by "how many"."""
    return sum(1 for c in cmds for line in c.splitlines() if pattern.search(line))


def entries(log: pathlib.Path) -> list[dict[str, Any]]:
    """Every record the log holds, in order.

    A partial final line is normal — the log is appended to while the audit runs
    — and is skipped rather than treated as corruption.
    """
    out = []
    for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(ev, dict):
            out.append(ev)
    return out


def commands(log: pathlib.Path) -> list[str]:
    """Every Bash command the record holds, in order."""
    out = []
    for ev in entries(log):
        if ev.get("tool_name") != "Bash":
            continue
        cmd = (ev.get("tool_input") or {}).get("command")
        if isinstance(cmd, str) and cmd.strip():
            out.append(cmd)
    return out


def reads(log: pathlib.Path) -> list[str]:
    """Every path the record shows opened with the Read tool.

    The matcher covers `Bash|Read` because reading a file by hand is far more
    likely to go through Read than through `cat` — so a rule that watches only
    Bash is blind in the direction it most needs to see (#150). A Read record
    carries `file_path` and nothing else, measured, so this costs the log almost
    nothing.
    """
    out = []
    for ev in entries(log):
        if ev.get("tool_name") != "Read":
            continue
        path = (ev.get("tool_input") or {}).get("file_path")
        if isinstance(path, str) and path.strip():
            out.append(path)
    return out


def under(parent: pathlib.Path, path: str) -> bool:
    """Is `path` inside `parent`? Both resolved, so a symlinked $TMPDIR or a
    worktree reached by two names still compares equal."""
    try:
        return pathlib.Path(os.path.realpath(path)).is_relative_to(
            pathlib.Path(os.path.realpath(parent))
        )
    except (OSError, ValueError):
        return False


def check(cmds: list[str], opened: list[str] | None = None) -> list[Finding]:
    findings = []
    opened = opened or []

    def lines_matching(pattern: re.Pattern[str], subset: list[str]) -> list[str]:
        """Every offending line across every offending command, capped for display.

        One Bash call can hold any number of invocations; counting calls is the
        record's unit, not the reader's.
        """
        out: list[str] = []
        for c in subset:
            out.extend(evidence(c, pattern))
        if len(out) > MAX_SHOWN:
            extra = len(out) - MAX_SHOWN
            out = [*out[:MAX_SHOWN], f"... and {extra} more"]
        return out

    hidden = [c for c in cmds if UV_RUN.search(c) and HIDDEN.search(c) and not NO_PROJECT.search(c)]
    if hidden:
        findings.append(
            Finding(
                "hidden-sync",
                f"{count(HIDDEN, hidden)} gate invocation(s), across {len(hidden)} "
                "command(s), ran in the project environment with --frozen or --no-sync. "
                "Phase 5 names both as flags not to add: each hides the line that would "
                "have said the environment moved.",
                lines_matching(HIDDEN, hidden),
            )
        )

    # Only meaningful once gates actually ran in the project environment.
    gated = [c for c in cmds if UV_RUN.search(c) and not NO_PROJECT.search(c)]
    if gated and not any(TOOL_CHECK.search(c) for c in cmds):
        findings.append(
            Finding(
                "gate-tool-unchecked",
                f"{count(UV_RUN, gated)} gate(s) ran in the project environment and nothing "
                "checked `.venv/bin/` first. "
                "`uv` puts .venv/bin first on PATH and falls through to the rest of it, so a gate "
                "whose tool the environment lacks runs the machine's copy and exits 0.",
                lines_matching(UV_RUN, gated),
            )
        )

    for tool, flag in sorted(NAMED_RUN_BY_TOOL.items()):
        # An *invocation*, not a mention. `echo "ruff or rumdl"` and
        # `changelog.py --package rumdl` both name the tool without running it,
        # and counting those is how this rule first scored two false positives.
        seen = re.compile(rf"(?<![\w-]){tool}\s+(?:check|format)(?![\w-])")
        named = re.compile(
            rf"(?<![\w-]){tool}\s+(?:check|format)[^;&|]*?{re.escape(flag)}(?![\w-])"
        )
        used = [c for c in cmds if seen.search(c)]
        if used and not any(named.search(c) for c in cmds):
            findings.append(
                Finding(
                    "inert-needs-named-run",
                    f"{count(seen, used)} invocation(s) of `{tool}` named no rule (`{flag}`). "
                    f"`inert here` is not derivable for {tool} from this record: where its "
                    "config is an "
                    "allow-list, the rule is enabled in neither run, both go silent, and "
                    "the silence "
                    "reads as proof. What this evidence supports is `underivable`.",
                    lines_matching(seen, used),
                    kind="note",
                )
            )

    read = [c for c in cmds if READ_CMD.search(c) and PLUGIN_DOC.search(c)]
    # Two narrowings, and round thirty measured the need for both.
    #
    # By location, not by filename: anything under this plugin's own skill
    # directory is this plugin's, and an audited repo carrying its own
    # `references/*.md` cannot be mistaken for it.
    #
    # And `SKILL.md` only, never a reference. SKILL.md is loaded *for* the audit,
    # so reading it by hand is the signature of it not having loaded (#52). A
    # reference is fetched *by* the audit — that is how a reference loads at all —
    # so matching one fires on every `uv.lock` audit ever run. Round thirty read
    # `references/uv-lock.md` twice through Read, correctly, and the first version
    # of this rule called it a finding.
    read_tool = [p for p in opened if pathlib.Path(p).name == "SKILL.md" and under(SKILL, p)]
    if read or read_tool:
        shown = [*lines_matching(PLUGIN_DOC, read), *(f"Read({p})" for p in read_tool)]
        if len(shown) > MAX_SHOWN:
            shown = [*shown[:MAX_SHOWN], f"... and {len(shown) - MAX_SHOWN} more"]
        findings.append(
            Finding(
                "plugin-file-read",
                f"{count(PLUGIN_DOC, read) + len(read_tool)} direct read(s) of a plugin document "
                f"— {len(read_tool)} of them through the Read tool — rather than invoking the "
                "procedure. That is the signature of the skill not having loaded — it is how the "
                "0.22.1 command shadowing survived to 0.23.0 — and is a Phase 8 hand-back "
                "even when "
                "the report it produced is correct.",
                shown,
            )
        )

    return findings


def default_log() -> pathlib.Path | None:
    session = os.environ.get("CLAUDE_CODE_SESSION_ID")
    if not session:
        return None
    # S108: this mirrors the hook's own `${TMPDIR:-/tmp}`. The two halves must agree
    # on the path or the record is written where nothing reads it.
    tmp = os.environ.get("TMPDIR", "/tmp")  # noqa: S108
    return pathlib.Path(tmp) / f"dbaudit-run-{session}.jsonl"


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--log", type=pathlib.Path, default=None, help="the record; default is this session's"
    )
    args = ap.parse_args(argv)

    log = args.log or default_log()
    if log is None:
        print(
            "underivable: no --log given and $CLAUDE_CODE_SESSION_ID is unset, so this session's "
            "record cannot be located.",
            file=sys.stderr,
        )
        return 128
    if not log.exists():
        print(
            f"underivable: no record at {log}. The PreToolUse hook did not run, so what this audit "
            "issued was never written down. This is not a clean result — do not read it as one.",
            file=sys.stderr,
        )
        return 128

    cmds = commands(log)
    opened = reads(log)
    if not cmds:
        print(f"underivable: {log} holds no Bash commands.", file=sys.stderr)
        return 128

    results = check(cmds, opened)
    defects = [f for f in results if f.kind == "finding"]
    notes = [f for f in results if f.kind == "note"]

    print(f"record: {log}")
    print(f"{len(cmds)} Bash command(s) and {len(opened)} Read call(s) recorded\n")
    for label, group in (("FINDING", defects), ("NOTE", notes)):
        for f in group:
            print(f"{label} [{f.rule}] {f.detail}")
            for c in f.commands:
                print(f"    {c}")
            print()

    if defects:
        print(f"RESULT: {len(defects)} finding(s) — report each, with its command, in Phase 7.")
    else:
        print(
            "RESULT: no finding. That is not the same as 'no improvisation': this checks "
            "four named "
            "rules, not every command against the procedure. And it reads what was issued, "
            "never what it printed — a named lint run recorded here is not thereby a named "
            "run that fired, so its control and its default-state run are still yours to "
            "read. See the docstring for the boundary."
        )
    if notes:
        print(f"         {len(notes)} note(s) above limit what the report may claim.")
    return 1 if defects else 0


def cli() -> int:
    try:
        return main(sys.argv[1:])
    except Exception as exc:
        print(f"verify_run.py failed: {exc.__class__.__name__}: {exc}", file=sys.stderr)
        return 128


if __name__ == "__main__":
    sys.exit(cli())
