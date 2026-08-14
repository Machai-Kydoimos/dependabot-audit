# Report shape

Evidence first, recommendation as its conclusion. Every row is something you
*ran*, not something you assumed — if a phase was skipped, say so in the row
rather than omitting it.

---

# Dependabot Audit — PR #\<N>

**`<PR title>`** · head `<short sha>` · \<runtime | dev-only> dependency

## Recommendation: \<merge as-is | merge as-is, then follow up to X | hold>

**Confidence: \<high | medium | low>.** One or two sentences on what the verdict
turns on.

## Evidence

| Phase | Result | Observed |
|---|---|---|
| **Scope** | Which files the diff touches. | this run |
| **Provenance** | N package(s), M artifact(s): hash, size, URL, yanked status — quote the script's own counts, plus anything it reported as unreachable. | this run |
| **Currency** | Registry latest vs. proposed, with publish time vs. PR open time. Yank and ignore-rule status. | this run |
| **Security** | Changelog `Security` sections across the gap — including their absence. | this run *or* reused — release notes immutable |
| **Vulnerabilities** | OSV result and the ecosystem auditor's, over N packages. | this run |
| **Behavior change** | Added rules / changed defaults, whether config is opt-in or opt-out, and what running the tool actually showed. | this run *or* reused — head `<sha>` unchanged |
| **Local reproduction** | Frozen install *in the form that ran*, the interpreter it ran under, any fork verified but not installed, each repo gate with its exit code, test count. | this run *or* reused — head `<sha>` unchanged, worktree verified |
| **CI** | Run ID and conclusion against the full head SHA; required contexts; job count. Every red check labelled **attributable**, **pre-existing**, or **underivable** against the base. | this run |

An unmarked table asserts that everything in it was observed this run. If that is
not true, the table is lying — see the reuse rules in Phase 7.

**A phase that did not run gets a row saying so, never a missing row.** The three
reasons, each of which the reader needs and none of which is a failure:

| Phase | Result | Observed |
|---|---|---|
| **Behavior change** | *Not run* — `--no-execute`. Phase 4 executes the PR's code. | — |
| **Local reproduction** | *Not run* — Phase 1 found a file outside the manifest and lockfile, and the audit stopped before any phase that executes. | — |
| **Local reproduction** | *Not run* — PR is cross-repository, so the bots did not open it; execution not authorised. | — |

An audit that stops at Phase 1 is complete, not failed: it reached a verdict
early, on the evidence that mattered. Say what the verdict rests on, and say what
running the remaining phases would add — that is the difference between a report
and an apology.

Where an install ran, name the form: `uv sync --locked --no-build
--no-install-project` and a plain `uv sync --locked` prove different things, and
"frozen install passed" is not the same claim in both. Name the **interpreter**
too, and any fork the install did not materialise — `--locked` checks the whole
lockfile, the install covers one resolution out of it, and the bare row asserts
both.

**A red check needs its attribution in the cell, not just its conclusion.**
"Required check `test (ubuntu-latest)` FAILURE" is true and does not say whether
this bump caused it; "FAILURE — **pre-existing**, red on the base commit too" is
a different finding with a different verdict. A Hold that rests on an
unattributed red row is correct only by accident, and the report gives the reader
no way to tell which.

## Reasoning

Why the verdict follows from the evidence. This is where a finding that no
scanner reports gets explained — name the mechanism, and show the repo config
line that makes it apply here.

## What would change this

The specific observations that would flip the verdict. If nothing would, say the
verdict is robust and why.

---

**Not merged.** When you want it: `gh pr merge <N> --squash`

For a follow-up: a separate branch, never a push onto the bot's branch.

If the reproduction worktree was kept rather than removed, name it here with its
cleanup command — never leave one registered in the user's repo unannounced.
