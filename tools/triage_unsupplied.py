#!/usr/bin/env python3
"""Prose that asks for a measurement with no command near it. A LIST, NOT A GATE.

    python3 tools/triage_unsupplied.py

Run it once per sprint and **read every hit**, including the ones that look
obviously fine. It is not wired into CI and must not be: the class it looks for
-- #127's, a phase that says to establish some fact and supplies no way -- is not
mechanically separable from the far larger class of *"read the output the command
above just produced"*. Measured 2026-09-16 with a looser pattern: ~40 hits, ~35 of
them legitimate. A guard needing thirty-five exceptions gets tuned until it
discriminates nothing.

**Why it exists anyway, which is the whole point of this file.** 0.43.0 rejected
that prototype on the hit count and discarded its output. One replay later a new
instance of the class turned up by improvisation -- and the discarded output had
already flagged it, verbatim, inside a bucket labelled false positives that nobody
read through. *Cannot be a gate* and *cannot be useful* are different findings, and
conflating them cost an instance and a $6.73 replay (#130).

Its hit rate is partial and known: of #130's two instances it finds one. The other
-- `The remaining checks are structural: every `uses:` is SHA-pinned, ...` -- is
phrased as a description rather than an imperative, so no verb pattern reaches it.
Read the output as a prompt to look, never as a coverage claim.

The mechanical half that *can* gate is `TestAFlagNamedInProseIsAFlagThePhaseRuns`
in `tests/test_skill_prose.py`; the registry of known instances is
`TestAPhaseSuppliesTheMeasurementsItAsksFor` in the same file.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "skills/dependabot-audit"
FILES = [PLUGIN / "SKILL.md", *sorted((PLUGIN / "references").glob("*.md"))]

VERB = re.compile(
    r"(?:^|(?<=[.!?:]\s)|(?<=^[-*]\s)|(?<=\*\*))"
    r"(Read|Check|Ask|Grep|Compare|Confirm|Verify|Query|Count|Measure|Fetch|List|Diff)\b"
)
WINDOW = 6


def main() -> int:
    hits = 0
    for path in FILES:
        lines = path.read_text(encoding="utf-8").splitlines()
        fences = [i for i, line in enumerate(lines) if line.startswith("```")]
        phase = "(preamble)"
        for i, line in enumerate(lines):
            if line.startswith("## "):
                phase = line[3:].strip()
            stripped = line.strip()
            if stripped.startswith("```") or stripped.startswith("|"):
                continue
            if not VERB.search(line):
                continue
            if any(i < fence <= i + WINDOW for fence in fences):
                continue
            hits += 1
            print(f"{path.name}:{i + 1}  [{phase}]")
            print(f"    {stripped}")
            # follow the hard wrap forward one line -- the sentence rarely fits
            tail = lines[i + 1].strip() if i + 1 < len(lines) else ""
            if tail and not tail.startswith(("```", "|", "#")):
                print(f"    {tail}")
            print()
    print(f"=== {hits} hits. Read all of them. ===", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
