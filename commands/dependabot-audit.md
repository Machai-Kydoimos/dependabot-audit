---
description: Audit a Dependabot or Renovate PR and report an evidence-backed merge recommendation. Covers uv.lock and GitHub Actions. Reports; never merges.
argument-hint: <PR number> [--no-execute] [--comment]
---

Audit pull request `$1` in this repository.

Use the **`dependabot-audit` skill** to do it. Invoke the skill rather than
working from this file: the skill carries the phases, the scripts, the
per-registry recipes, and the `disallowed-tools` frontmatter that withholds
`Edit`, `Write`, and `NotebookEdit` for the duration of the audit. Restating the
procedure here would quietly drop that withholding, which is the whole point of
the read-only contract.

If no PR number was given above, ask which PR before starting — do not guess from
the most recent one, and do not audit whatever branch happens to be checked out.

Arguments as given: `$ARGUMENTS`

- `--no-execute` means: run Phases 0–3 and 6–7 only, which are all network reads.
  Phases 4 and 5 execute code from the PR — a frozen install runs lifecycle
  scripts and build backends, and the test suite is the PR's own — so this is the
  right mode for a PR you have no reason to trust yet. The report must name the
  phases that did not run.
- `--comment` means: produce the report, print it, and *offer* to post it.
  Posting is a separate action that the user asks for explicitly.
- Anything else is not a flag this command knows. Say so rather than inferring
  what it might have meant.

Even without `--no-execute`, Phase 0 classifies the PR and Phase 1 gates on the
diff. A cross-repository or non-bot-authored bump, or a diff reaching past the
manifest and lockfile, stops the audit before anything executes — report that and
let the user decide, rather than proceeding.
