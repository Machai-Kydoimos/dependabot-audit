---
description: Audit a Dependabot or Renovate PR and report an evidence-backed merge recommendation. Reports; never merges.
argument-hint: <PR number> [--comment]
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

- `--comment` means: produce the report, print it, and *offer* to post it.
  Posting is a separate action that the user asks for explicitly.
- Anything else is not a flag this command knows. Say so rather than inferring
  what it might have meant.
