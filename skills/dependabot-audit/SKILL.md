---
name: dependabot-audit
description: Audit an automated dependency-bump PR and produce an evidence-backed merge recommendation — verify lockfile artifact hashes against the registry, cross-check the true latest version, read changelogs for security and behavior changes, reproduce the repo's own checks in an isolated worktree, and report. Use when the user asks to review, audit, check, or decide on a Dependabot or Renovate PR, a dependency bump, a lockfile PR, or asks "is this safe to merge".
tools: Read, Grep, Glob, Bash
---

# Dependabot Audit

**This skill reports; it does not decide.** It ends in a recommendation plus the
evidence behind it, and stops. Do not merge, approve, close, comment on, rebase,
or push to the PR. Print the merge command; do not run it.

`tools:` withholds `Edit` and `Write`, but `Bash` can reach `gh pr merge`. That
restraint is a **contract, not a sandbox** — honor it. The one exception is the
learning loop in Phase 8, which appends to the user's project memory.

## Why this procedure exists

Dependency-bump PRs look trivial and usually are. The failure modes that actually
bite are not "is this package malicious". They are:

1. **The proposed version is not the current one.** Registries publish faster than
   the bot ingests. A bump can land already stale, and the gap can matter.
2. **The gap contains a fix no vulnerability database knows about.** A privately
   disclosed fix has no CVE or GHSA, so OSV, `pip-audit`, and `npm audit` all
   report clean while the changelog says "Security".
3. **The bump changes a *default*, not just behavior.** A linter that gains a rule
   or a formatter that widens its file scope can newly fail a *required* CI check
   that the repo's own pre-commit hooks are scoped too narrowly to catch.

All three are observed, not hypothetical. Phases 2 and 4 exist for them.

## Phase 0 — Discover the repo (derive every run; never cache)

Never persist the answers to these. Required checks get added, CI jobs get
renamed, and a cached profile silently audits a repo that no longer exists.
Deriving costs one call each.

```bash
gh pr view <N> --json number,title,headRefOid,mergeStateStatus,files,author,createdAt
gh api repos/:owner/:repo/branches/<default>/protection --jq '.required_status_checks.contexts[]'
cat .github/dependabot.yml 2>/dev/null || cat renovate.json 2>/dev/null
```

Then read the CI workflow and the pre-commit config to learn the repo's **own**
verification commands — do not assume `pytest`/`npm test`. Note where each tool
runs and **at what scope**: a hook scoped to `types_or: [python, pyi]` and a CI
step running the same tool over `.` are different gates, and Phase 4 turns on
that difference.

Recalled project memory may already name landmines for this repo (Phase 8 writes
them). Treat those as leads to check, not as facts — verify before repeating.

## Phase 1 — Scope and provenance

The diff should touch **only** the manifest and the lockfile (or a single
workflow file for an actions bump). Anything else is a finding — say so and stop.

Verify every artifact the lockfile pins for the changed packages against the live
registry: sha256/integrity, size, URL, and yanked status.

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/dependabot-audit/scripts/audit.py" uv.lock --changed <pkg>
```

That script covers PyPI/`uv.lock`. For npm, Cargo, Go, and GitHub Actions, follow
the per-registry recipes in `references/ecosystems.md` — they are short API
comparisons you can run directly.

## Phase 2 — Currency

**A bot's proposal is not evidence of "current".** Ask the registry what the
latest version actually is, and compare publish timestamps against the PR's
`createdAt`. If a newer version existed *before* the PR was opened, that is
ingestion lag, not a deliberate hold.

Rule out the innocent explanations before reporting a gap: a yanked release, or
an `ignore` rule in `dependabot.yml`/`renovate.json`.

Then **read the changelog for every version in the gap**, plus the versions being
adopted. Look for two things, in this order:

- **`Security` sections.** These outrank every vulnerability database. A privately
  disclosed fix ships with no CVE, and scanners will report clean.
- **Destructive-fix bugs.** Entries like "stop deleting…" or "no longer removes…"
  in a tool the repo runs in **write mode** (`--fix`, `--write`, `-i`) are
  data-loss bugs in a mode that runs automatically. They never appear in a
  security feed. Check whether the repo actually invokes that write mode.

## Phase 3 — Known vulnerabilities

Batch-query OSV across the whole locked set, then corroborate with the
ecosystem's own auditor. Expect this to agree with Phase 2 only sometimes — that
divergence is the point, not a contradiction.

See `references/ecosystems.md` for the auditor invocations and their traps; the
Python one in particular audits the wrong interpreter if invoked casually.

## Phase 4 — Behavior change (the highest-yield phase)

Not "is it safe" but **"does it change what this repo's gates accept"**.

1. From the changelog, list every **added rule, changed default, or widened file
   scope** — not the bug fixes.
2. Determine whether the repo's config is an **opt-in allow-list** or an
   **opt-out disable-list**. Under a disable-list, a newly added rule is **live
   immediately**. Under an allow-list it is inert. This single distinction decides
   whether a new rule can break the build.
3. Do not reason about it — **run the tool** at the CI's scope, and separately at
   the hook's scope. Green CI on the PR is good evidence, but it only covers the
   proposed version; if you are recommending a *newer* one, run that too.

## Phase 5 — Independent reproduction

In an isolated worktree, so the user's working tree is untouched:

```bash
git fetch origin pull/<N>/head:pr-<N>
git worktree add <scratch>/pr-<N> pr-<N>
```

If a worktree from an earlier run is already there, **prove it still points at
this PR's head before reusing it** — a stale worktree silently audits the wrong
commit and every result downstream is wrong:

```bash
git -C <scratch>/pr-<N> log --oneline -1     # must be the PR head
git -C <scratch>/pr-<N> status --porcelain   # must be empty
```

If either check fails, `git worktree remove` it and recreate.

Install **frozen** (`uv sync --locked`, `npm ci`, `cargo build --locked`) — that
proves the lockfile is self-consistent and resolves nothing. Then run the repo's
own gates from Phase 0, and its full test suite.

Gate on exit codes. `cmd | tail && next` gates on `tail`, so a failing suite sails
through; use `set -o pipefail` or separate calls.

## Phase 6 — CI verification

Confirm the green you are trusting belongs to **this** commit:

```bash
gh run list --commit <full-40-char-sha> --workflow <ci>.yml
```

A short SHA silently matches nothing and reads as "CI never ran".

Then check the Phase 0 required contexts **individually** — a repo can have far
more jobs than required checks, and only the required ones gate merge. Paste the
Phase 0 list into `$req`:

```bash
gh pr view <N> --json statusCheckRollup --jq '
  [.statusCheckRollup[] | {name:(.name//.context), state:(.conclusion//.state)}] as $all
  | ["Lint & type-check","Test (ubuntu-latest, Python 3.10)"] as $req
  | ($req | map(. as $r | ($all[] | select(.name==$r) | "\(.state)  \($r)")))[]'

# and the totals, to catch anything unsettled:
gh pr view <N> --json statusCheckRollup --jq \
  '[.statusCheckRollup[]|(.conclusion//.state)]|group_by(.)|map("\(.[0]): \(length)")|join("  ")'
```

Two ways to misread that output. **A required context that never reported
produces no line at all** — the `select` matches nothing — so count the lines
against `$req` and treat a missing one as "not reported", never as green. And
**do not post-process `gh pr checks` with whitespace-splitting tools**: real
check names contain spaces and ampersands, so `awk '{print $1}'` turns
`Lint & type-check` into `Lint` and quietly reports on a check that does not
exist.

`references/traps.md` covers the state-reporting gotchas: stale `CLEAN`,
`UNSTABLE` being mergeable, neutral CodeQL, and why a bot rebase does not
re-trigger CI.

## Phase 7 — Report

Use the exact shape in `references/report-template.md`: verdict, confidence,
evidence table, reasoning, what would change the verdict, and the **un-run** merge
command. Lead with evidence; the recommendation is a conclusion drawn from it,
not a headline it decorates.

Verdicts are one of:

- **Merge as-is** — clean and current.
- **Merge as-is, then follow up** — clean, but a newer version exists and the gap
  matters. Merge the bot's PR **exactly as written**, then take the newer version
  on a **separate branch**. Never push onto the bot's branch; that stops it
  managing the PR.
- **Hold** — a discrepancy, a regression, or a behavior change that breaks a gate.

If the user asked for `--comment`, print the report and offer to post it; posting
is a separate, explicitly requested action.

## Phase 8 — Learning loop (the only thing worth persisting)

Facts that were *derivable* were derived in Phase 0 and must not be saved. Facts
that were **learned the hard way cannot be re-derived — only re-suffered.** Those
are worth writing down.

If this audit surfaced a repo-specific landmine — a tool whose defaults collide
with this repo's config, a hook whose scope hides a CI failure, an invocation that
silently measures the wrong thing — append it to the user's **project memory** as
a `project` memory, with the evidence and how it was caught. If no memory
directory is available, propose the equivalent addition to the repo's
`CONTRIBUTING.md` gotchas section instead.

A generally portable trap belongs in `references/traps.md` in this plugin, not in
one project's memory. Say which you are proposing, and why.
