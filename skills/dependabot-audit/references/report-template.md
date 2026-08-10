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
| **Provenance** | N/N artifacts verified: hash, size, URL, yanked status. | this run |
| **Currency** | Registry latest vs. proposed, with publish time vs. PR open time. Yank and ignore-rule status. | this run |
| **Security** | Changelog `Security` sections across the gap — including their absence. | this run *or* reused — release notes immutable |
| **Vulnerabilities** | OSV result and the ecosystem auditor's, over N packages. | this run |
| **Behavior change** | Added rules / changed defaults, whether config is opt-in or opt-out, and what running the tool actually showed. | this run *or* reused — head `<sha>` unchanged |
| **Local reproduction** | Frozen install, each repo gate with its exit code, test count. | this run *or* reused — head `<sha>` unchanged, worktree verified |
| **CI** | Run ID and conclusion against the full head SHA; required contexts; job count. | this run |

An unmarked table asserts that everything in it was observed this run. If that is
not true, the table is lying — see the reuse rules in Phase 7.

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
