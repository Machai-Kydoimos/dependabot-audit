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

| Phase | Result |
|---|---|
| **Scope** | Which files the diff touches. |
| **Provenance** | N/N artifacts verified: hash, size, URL, yanked status. |
| **Currency** | Registry latest vs. proposed, with publish time vs. PR open time. Yank and ignore-rule status. |
| **Security** | Changelog `Security` sections across the gap — including their absence. |
| **Vulnerabilities** | OSV result and the ecosystem auditor's, over N packages. |
| **Behavior change** | Added rules / changed defaults, whether config is opt-in or opt-out, and what running the tool actually showed. |
| **Local reproduction** | Frozen install, each repo gate with its exit code, test count. |
| **CI** | Run ID and conclusion against the full head SHA; required contexts; job count. |

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
