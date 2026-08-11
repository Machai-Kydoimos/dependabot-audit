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

# derive the default branch — never assume "main"
DEFAULT=$(gh repo view --json defaultBranchRef --jq .defaultBranchRef.name)
gh api "repos/:owner/:repo/branches/$DEFAULT/protection" --jq '.required_status_checks.contexts[]'

cat .github/dependabot.yml 2>/dev/null || cat renovate.json 2>/dev/null

# pin the commit under audit and fetch it once, for every later phase
SCRATCH=${SCRATCH:-$(mktemp -d)}          # any directory OUTSIDE the repo
HEAD_SHA=$(gh pr view <N> --json headRefOid --jq .headRefOid)   # full 40 chars
git fetch origin "pull/<N>/head:pr-<N>"
BASE_SHA=$(git merge-base "$DEFAULT" "pr-<N>")
```

**Pin the head SHA here and audit that one commit everywhere.** The lockfile
Phase 1 reads, the worktree Phase 5 builds, and the CI run Phase 6 checks must all
describe the *same* commit, or the report's evidence table asserts a coherence it
does not have. Bots rebase, so this is not hypothetical: a rebase mid-audit leaves
Phases 1–5 describing a commit that no longer exists while Phase 6 reports on the
new one. Fetching once and working from `pr-<N>` makes them consistent by
construction, and Phase 7 re-checks the SHA before you write.

**Never audit the working tree.** Whatever branch the user happens to have checked
out is not the PR, and a lockfile read from it is indistinguishable from one read
from the PR — it just quietly reports no changes.

Use a harness-provided scratch directory for `SCRATCH` if you have one; otherwise
`mktemp -d`. **Never place it inside the repo under audit** — it pollutes
`git status`, and a gate that walks the tree (a linter, a formatter, a test
collector) will descend into a full second copy of the project and report on it.

**Derive the branch name; never type it.** A failed protection call and a repo
with no protection both yield *no contexts*, so if stderr is discarded or the
output is skimmed only for the list, they are indistinguishable — and Phase 6
then verifies nothing while the report still says CI is green. Read the status
and message, which separate three genuinely different situations (all verified):

| Response | Meaning |
|---|---|
| `404 Branch not found` | wrong branch name — your mistake, fix and re-run |
| `404 Branch not protected` | correct branch, no protection configured |
| `403 Upgrade to GitHub Pro…` | correct branch, but branch protection is unavailable on this plan (a private repo on a free plan) |

Only the first is an error on your part. The other two are **findings**: the repo
has no enforced required checks, which changes what a green CI run is worth. Say
so explicitly in the report rather than omitting the row.

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

**Read both lockfiles out of git**, using the ref and merge base pinned in Phase
0. A bare `uv.lock` path resolves against the user's checkout, which parses fine,
derives *no* changed packages, and produces a confident audit of nothing:

```bash
S="${CLAUDE_PLUGIN_ROOT}/skills/dependabot-audit/scripts/audit.py"

git show "pr-<N>:uv.lock"    > "$SCRATCH/pr.uv.lock"
git show "$BASE_SHA:uv.lock" > "$SCRATCH/base.uv.lock"

python3 "$S" "$SCRATCH/pr.uv.lock" --changed-vs "$SCRATCH/base.uv.lock"
```

**Do not read the package names off the PR title.** A grouped bump is titled
"with 3 updates" and names none of them, and a bot may group everything (check
the `groups:` key you read in Phase 0). `--changed-vs` derives the set from the
diff against the **merge base** — not the current default branch, or unrelated
drift that landed after the PR branched gets attributed to this PR. Naming
packages by hand (`--changed pkg-a,pkg-b`) is the fallback for a diff the script
cannot read, not the default.

The script will not look successful while verifying less than it should. **Exit 2
means it could not run** — a name you asked for is not in the lockfile, the
selected set came out empty, the lockfile is unreadable, or a registry was
unreachable. **Exit 1 means it ran and found something.** Never read a 2 as a
finding, or a 1 as an outage. Its `RESULT` line carries the package and artifact
counts and names anything it could not reach (git, path, or a private-index
dependency); quote those counts in the report rather than writing "verified"
unqualified.

For PyPI that one invocation covers this phase **plus the mechanical half of
Phases 2 and 3** — it also reports the registry's true latest version with
publish timestamps, and runs the OSV batch across the whole lockfile. Read its
output there rather than repeating those queries by hand.

For npm, Cargo, Go, and GitHub Actions, follow the per-registry recipes in
`references/ecosystems.md` — they are short API comparisons you can run directly.

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

**For PyPI the OSV half is already done** — the Phase 1 script ran it. Read that
result instead of issuing a second query; what remains here is the ecosystem
auditor.

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

In an isolated worktree, so the user's working tree is untouched. `SCRATCH` and
the `pr-<N>` ref both come from Phase 0:

```bash
git worktree add "$SCRATCH/pr-<N>" "pr-<N>"
```

If a worktree from an earlier run is already there, **prove it still points at
this PR's head before reusing it** — a stale worktree silently audits the wrong
commit and every result downstream is wrong. Compare it against the pinned SHA
rather than eyeballing a log line:

```bash
test "$(git -C "$SCRATCH/pr-<N>" rev-parse HEAD)" = "$HEAD_SHA"   # from Phase 0
git -C "$SCRATCH/pr-<N>" status --porcelain                       # must be empty
```

If either check fails, `git worktree remove` it and recreate.

Install **frozen** (`uv sync --locked`, `npm ci`, `cargo build --locked`) — that
proves the lockfile is self-consistent and resolves nothing. Then run the repo's
own gates from Phase 0, and its full test suite.

Gate on exit codes. `cmd | tail && next` gates on `tail`, so a failing suite sails
through; use `set -o pipefail` or separate calls.

**Close the loop.** The worktree *and* the `pr-<N>` branch Phase 0 fetched are
both registered in the **user's** repo, so an audit that walks away leaves litter
behind — and it accumulates, one per PR audited. The branch outlives an audit that
stopped before Phase 5, too. Either remove them when finished, or keep them
deliberately and say so in the report so the user knows they are there:

```bash
git worktree remove "$SCRATCH/pr-<N>"
git branch -D "pr-<N>"
```

Keeping them is reasonable when a follow-up run is likely; silently keeping them
is not.

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

**Re-check the head SHA before you write anything.** Every row above describes the
commit pinned in Phase 0. If the bot rebased mid-audit, Phases 1–5 now describe a
commit that no longer exists while Phase 6 reports on the new one — and the table
silently asserts that they agree:

```bash
test "$(gh pr view <N> --json headRefOid --jq .headRefOid)" = "$HEAD_SHA"
```

If it moved, say so and re-run from Phase 1. Do not reconcile the two by hand.

Use the exact shape in `references/report-template.md`: verdict, confidence,
evidence table, reasoning, what would change the verdict, and the **un-run** merge
command. Lead with evidence; the recommendation is a conclusion drawn from it,
not a headline it decorates.

**Mark each row's provenance**, and reuse only where it is legitimate. What
invalidates a row is not how old it is but what it depends on:

- **Registry and CI rows — always fresh.** A release or an advisory can land
  mid-session, CI can re-run, a required context can be added. These are one
  call each, so reuse buys nothing and risks reporting a world that moved.
- **Changelog rows — reusable.** Published release notes are immutable.
- **Reproduction rows — reusable only against an unchanged head SHA**, with the
  Phase 5 worktree check passing. State that basis in the column.

The inversion is worth internalizing: the **cheap** evidence is what must be
fresh, and a full test suite is the only kind expensive enough to be worth
reusing at all. Re-running everything is nearly always the right default.

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
