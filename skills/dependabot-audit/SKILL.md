---
name: dependabot-audit
description: Audit an automated dependency-bump PR and produce an evidence-backed merge recommendation — verify lockfile artifact hashes against the registry, cross-check the true latest version, read changelogs for security and behavior changes, reproduce the repo's own checks in an isolated worktree, and report. Use when the user asks to review, audit, check, or decide on a Dependabot or Renovate PR, a dependency bump, a lockfile PR, or asks "is this safe to merge".
disallowed-tools: Edit, Write, NotebookEdit
---

# Dependabot Audit

**This skill reports; it does not decide.** It ends in a recommendation plus the
evidence behind it, and stops. Do not merge, approve, close, comment on, rebase,
or push to the PR. Print the merge command; do not run it.

`disallowed-tools` removes `Edit`, `Write`, and `NotebookEdit` from the pool while
this skill is active, but `Bash` remains and can reach `gh pr merge`. That
restraint is a **contract, not a sandbox** — honor it. There is no exception:
Phase 8 hands its memory entry back rather than writing it, precisely because
reaching for `Bash` to do what the withheld tools would have done makes the
withholding theatre.

## This audit executes the code it audits

The contract above governs what *this skill* writes. It says nothing about what
the audited code does, and two phases run it:

- **Phase 5** installs frozen and runs the PR's own test suite from the PR's tree.
  `npm ci` runs `preinstall`/`install`/`postinstall` scripts, `uv sync` builds any
  sdist in the resolution — which runs `setup.py` or the project's build backend —
  and `cargo build` runs every crate's `build.rs`.
- **Phase 4** runs the repo's gates at a version taken from the diff under audit.
- **`gate_diff.py`** passes its `--run` commands to a shell, and those commands are
  transcribed from the audited repo's CI config, which an actions bump legitimately
  modifies.

**The worktree isolates the user's working tree from the audit. It does not
isolate the machine from the PR.** Nothing here is a sandbox; if you need one, it
has to come from outside — a container, a throwaway VM, or a Landlock confinement
— and this skill cannot verify that you have one.

The ordering is the mitigation available inside the skill. Phases 1–3 are the
read-only checks that would catch a bad dependency, and **Phase 1 is a gate**: if
the diff reaches beyond the manifest and lockfile, or provenance fails, stop there.
Do not continue into the phases that execute. A procedure whose thesis is "verify
before you trust" must not run the artifact before it has finished deciding
whether to trust it.

**`--no-execute`** runs Phases 0–3 and 6–7 only. Every one of those is a network
read: provenance, currency, changelogs, OSV, CI state. That is most of this
procedure's value, and it is the right default for a PR you have no reason to
trust yet. Use it when the user asks, and when Phase 0 classifies the PR as one
the bots did not open. Say in the report which phases did not run.

## Why this procedure exists

The failure modes that bite are not "is this package malicious" — they are a
proposal that is already stale, a gap containing a fix no vulnerability database
knows about, and a bump that changes a *default* rather than a behavior. All
three are observed, not hypothetical. Phases 2 and 4 exist for them, and
`references/traps.md` has the cases.

## Phase 0 — Discover the repo (derive every run; never cache)

Never persist the answers to these. Required checks get added, CI jobs get
renamed, and a cached profile silently audits a repo that no longer exists.
Deriving costs one call each.

```bash
gh pr view <N> --json number,title,headRefOid,mergeStateStatus,files,author,createdAt,isCrossRepository

# derive the default branch — never assume "main"
DEFAULT=$(gh repo view --json defaultBranchRef --jq .defaultBranchRef.name)

cat .github/dependabot.yml 2>/dev/null || cat renovate.json 2>/dev/null

# pin the commit under audit, fetch it once, and build the tree every later
# phase works in
SCRATCH=${SCRATCH:-$(mktemp -d)}          # any directory OUTSIDE the repo
HEAD_SHA=$(gh pr view <N> --json headRefOid --jq .headRefOid)   # full 40 chars
git fetch origin "pull/<N>/head:pr-<N>"
BASE_SHA=$(git merge-base "$DEFAULT" "pr-<N>")
git worktree add "$SCRATCH/pr-<N>" "pr-<N>"

# save the required contexts; Phase 6 reads the file rather than a list retyped
# from memory, which verifies nothing while looking identical to a pass
gh api "repos/:owner/:repo/branches/$DEFAULT/protection" \
  --jq '.required_status_checks.contexts[]' > "$SCRATCH/required.txt"
```

If `git worktree add` refuses because the path already exists, a previous run
left it there. **Prove it still points at this PR's head before reusing it** — a
stale worktree silently audits the wrong commit and every result downstream is
wrong. Compare against the pinned SHA rather than eyeballing a log line:

```bash
test "$(git -C "$SCRATCH/pr-<N>" rev-parse HEAD)" = "$HEAD_SHA"
git -C "$SCRATCH/pr-<N>" status --porcelain                       # must be empty
```

If either check fails, `git worktree remove` it and re-add.

**Phase 0 outputs.** Every later phase consumes these and nothing else:

| | |
|---|---|
| `$DEFAULT` | the repo's default branch, derived |
| `$SCRATCH` | scratch directory, outside the repo |
| `$HEAD_SHA` | the full 40-character commit under audit |
| `$BASE_SHA` | merge base of `$DEFAULT` and `pr-<N>` |
| `pr-<N>` | the fetched branch, registered in the **user's** repo |
| `$SCRATCH/pr-<N>` | worktree at that branch — Phase 4 measures in it, Phase 5 reproduces in it |
| `$SCRATCH/required.txt` | the required contexts, one per line; **empty is a finding**, not a blank |

If a later phase needs something not on this list, it belongs here rather than
there. A phase that consumes what a later phase creates cannot be run in order,
and that has now shipped twice — `tests/test_skill_prose.py` is what stops the
third.

**Classify the PR before trusting it enough to run it.** Dependabot and Renovate
push their branches *into* the repository, so a dependency bump arriving from a
fork did not come from the bot:

| Observation | Meaning |
|---|---|
| `isCrossRepository: false`, author `dependabot[bot]` or `renovate[bot]` | the ordinary case |
| `isCrossRepository: true` | a fork PR — neither bot opens one |
| any other author | a human PR shaped like a bump, which it may well be, and may not |

Either of the last two is a **finding** in its own right, and it changes the
default: run `--no-execute`, report what the read-only phases found, and let the
user decide whether to authorise Phases 4 and 5. Say plainly that those phases
would run the PR's code.

**Pin the head SHA here and audit that one commit everywhere.** The lockfile
Phase 1 reads, the worktree Phase 5 reproduces in, and the CI run Phase 6 checks
must all describe the *same* commit, or the report's evidence table asserts a coherence it
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
with no protection both leave `$SCRATCH/required.txt` **empty**, so if stderr is
discarded or the file is skimmed only for its lines, they are indistinguishable —
and Phase 6 then verifies nothing while the report still says CI is green. Read
the status and message, which separate three genuinely different situations (all
verified):

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

*Requires from Phase 0: `$SCRATCH`, `$BASE_SHA`, `pr-<N>`.*

**This phase is a gate, not just a step.** The diff should touch **only** the
manifest and the lockfile (or a single workflow file for an actions bump).
Anything else is a finding: report it, and **stop before Phase 4**. The same
applies if provenance comes back with a discrepancy. Phases 4 and 5 execute the
PR's code, and the whole point of running the cheap read-only checks first is that
they can refuse to hand it a shell. Continuing anyway spends the ordering for
nothing.

Stopping here is not a failed audit. It is a complete one that reached a verdict
early — write the report with the phases that ran and say which did not.

Verify every artifact the lockfile pins for the changed packages against the live
registry: sha256/integrity, size, URL, and yanked status.

**Read both lockfiles out of git**, using the ref and merge base pinned in Phase
0 — never a bare `uv.lock` path, which resolves against the user's checkout:

```bash
S="${CLAUDE_PLUGIN_ROOT}/skills/dependabot-audit/scripts/audit.py"

git show "pr-<N>:uv.lock"    > "$SCRATCH/pr.uv.lock"
git show "$BASE_SHA:uv.lock" > "$SCRATCH/base.uv.lock"

python3 "$S" "$SCRATCH/pr.uv.lock" --changed-vs "$SCRATCH/base.uv.lock"
```

**Do not read the package names off the PR title** — a grouped bump names none
of them, and a bot may group everything (check the `groups:` key from Phase 0).
`--changed-vs` derives the set from the diff against the **merge base**;
`--changed pkg-a,pkg-b` is the fallback for a diff the script cannot read, not
the default.

**Exit 2 means it could not run; exit 1 means it ran and found something.** Never
read one as the other. Quote its `RESULT` counts — and whatever it names as
unreachable — in the report, rather than writing "verified" unqualified.

The script says why each package was selected. **`ARTIFACTS CHANGED at unchanged
version` is not a routine bump** — the lockfile re-points an artifact while the
version stands still. There are innocent explanations (a wheel added for a new
platform, a re-resolution against a different index); confirm which, rather than
assuming one.

For PyPI that one invocation covers this phase **plus the mechanical half of
Phases 2 and 3** — it also reports the registry's true latest version with
publish timestamps, PEP 740 build provenance where PyPI has it, and the OSV batch
across the whole lockfile. Read its output there rather than repeating those
queries by hand.

**`PUBLISHER CHANGED` outranks everything else in the output.** It means the
release being adopted was built somewhere the previous one was not. Absence of an
attestation is *not* a finding — it is normal for anything predating Trusted
Publishing — and the script distinguishes the two.

For npm, Cargo, Go, and GitHub Actions, follow the per-registry recipes in
`references/ecosystems.md` — they are short API comparisons you can run directly.

## Phase 2 — Currency

*Requires: the Phase 1 script output, and the PR's `createdAt` from Phase 0.*

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

*Requires: the Phase 1 script output.*

Batch-query OSV across the whole locked set, then corroborate with the
ecosystem's own auditor. Expect this to agree with Phase 2 only sometimes — that
divergence is the point, not a contradiction.

**For PyPI the OSV half is already done** — the Phase 1 script ran it. Read that
result instead of issuing a second query; what remains here is the ecosystem
auditor.

See `references/ecosystems.md` for the auditor invocations and their traps; the
Python one in particular audits the wrong interpreter if invoked casually.

## Phase 4 — Behavior change (the highest-yield phase)

*Requires from Phase 0: the `$SCRATCH/pr-<N>` worktree, and the repo's own gates.*
*Executes code from the PR. Skipped under `--no-execute`; skip it if Phase 1
found anything.*

Not "is it safe" but **"does it change what this repo's gates accept"**. Measure
that; do not predict it from the changelog.

```bash
G="${CLAUDE_PLUGIN_ROOT}/skills/dependabot-audit/scripts/gate_diff.py"

python3 "$G" --tree "$SCRATCH/pr-<N>" \
  --run locked   "uv run --no-project --with ruff==0.15.22 ruff format ." \
  --run proposed "uv run --no-project --with ruff==0.16.0  ruff format ." \
  --run latest   "uv run --no-project --with ruff==0.16.2  ruff format ."
```

**Give the tool's write mode, not `--check`** — the measurement is what each
version does to the files, and `--check` does nothing to them. Run it once per
gate from Phase 0, at *each* scope: a hook scoped to `types_or: [python, pyi]`
and a CI step running the same tool over `.` are different gates, and this is
the phase that turns on the difference. Add the `latest` run whenever Phase 2
found a newer version, because that is the one you would be recommending.

Read the result as three distinct findings:

| Result | Meaning |
|---|---|
| only in the newer run | widened scope, or a rule that now fires |
| only in the older run | narrowed scope |
| both, different result | the fix itself behaves differently |

The last is the one no security feed reports: a formatter that used to delete
something and no longer does, in a write mode many repos run on every commit.

**Do not read the exit codes as the answer** — see `references/traps.md`; both
versions can exit 0 while the scope moves underneath them.

`allow-list vs disable-list` is no longer something to work out in advance; the
run settles it. Keep it for the *report*, to explain why a difference fired:
under a config that disables specific rules a newly added rule is live the moment
it lands, and under one that enables specific rules it is inert.

A gate with no write mode — a type checker, a test suite — leaves the tree
untouched and `gate_diff` says so. Exit code and output are then the only
signals, which is the weaker measurement. Say so in the report rather than
implying the same confidence as a tree diff.

## Phase 5 — Independent reproduction

*Requires from Phase 0: the `$SCRATCH/pr-<N>` worktree, `$HEAD_SHA`, `pr-<N>`.*
*Executes code from the PR — the most of any phase. Skipped under
`--no-execute`; skip it if Phase 1 found anything.*

Phase 0 built the worktree and proved it points at `$HEAD_SHA`, so the user's
working tree is untouched throughout and this phase inherits a tree it can trust.
That isolation protects the *working tree*, not the machine — see the execution
section at the top, and `references/ecosystems.md` for the per-registry flags that
narrow what an install is allowed to run.

Install **frozen** — that proves the lockfile is self-consistent and resolves
nothing. For Python this is two commands, and they prove different things:

```bash
uv sync --locked --no-build --no-install-project   # every dep resolved to a wheel
uv sync --locked                                   # then add the project itself
```

`--no-build` alone **fails** on any project with a `[project]` table, because
installing itself editable is a build; `references/ecosystems.md` has the error
and the reasoning. For npm it is `npm ci --ignore-scripts`; for Cargo,
`cargo build --locked`, which runs every crate's `build.rs` and has no flag that
stops it.

Then run the repo's own gates from Phase 0, and its full test suite.

**Record which install you ran.** The script-suppressing flags are the documented
default and they weaken the proof: a package that genuinely needs its install
script is not exercised. Re-running without them is a legitimate choice, and the
report has to say which one produced the row rather than asserting "frozen install
passed" for either.

Gate on exit codes. `cmd | tail && next` gates on `tail`, so a failing suite sails
through; use `set -o pipefail` or separate calls.

**Close the loop.** The worktree *and* the `pr-<N>` branch Phase 0 created are
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

*Requires from Phase 0: `$HEAD_SHA`, `$SCRATCH/required.txt`.*

Confirm the green you are trusting belongs to **this** commit. Use the full
40-character `$HEAD_SHA` pinned in Phase 0 — a short one matches nothing and
reads exactly like "CI never ran":

```bash
gh run list --commit "$HEAD_SHA" --workflow <ci>.yml
```

Then check the required contexts **individually** — a repo can have far more jobs
than required checks, and only the required ones gate merge. The list comes from
the file Phase 0 derived; **never retype it**, because a list that names checks
this repo does not have matches nothing, prints nothing, and is indistinguishable
from a repo with no required checks:

```bash
gh pr view <N> --json statusCheckRollup --jq \
  '.statusCheckRollup[] | "\(.conclusion // .state)\t\(.name // .context)"' \
  > "$SCRATCH/rollup.tsv"

while IFS= read -r req; do
  awk -F'\t' -v r="$req" \
    '$2 == r { print $1 "  " $2; found = 1 }
     END { if (!found) print "NOT REPORTED  " r }' "$SCRATCH/rollup.tsv"
done < "$SCRATCH/required.txt"

# and the totals, to catch anything unsettled:
gh pr view <N> --json statusCheckRollup --jq \
  '[.statusCheckRollup[]|(.conclusion//.state)]|group_by(.)|map("\(.[0]): \(length)")|join("  ")'
```

Every required context produces exactly one row, so a context that never reported
says so rather than vanishing — which is the failure that made the previous recipe
unsafe. Matching is on the whole second field, never `awk '{print $1}'`, which
turns `Lint & type-check` into `Lint`.

What that looks like on a repo whose checks are named as awkwardly as this one's:

```text
success       Lint & type-check
success       Test (Python 3.11)
NOT REPORTED  Test (Python 3.14)
```

**An empty `$SCRATCH/required.txt` prints nothing at all**, and that is the
Phase 0 finding — no enforced required checks, for one of the two reasons its
table separates. Report it as a finding; do not report CI as verified.

`references/traps.md` has the reasoning, plus stale `CLEAN`, `UNSTABLE` being
mergeable, neutral CodeQL, and why a bot rebase does not re-trigger CI.

## Phase 7 — Report

*Requires from Phase 0: `$HEAD_SHA`.*

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
silently measures the wrong thing — **write it out and hand it over**: the
filename, the frontmatter, and the body of a `project` memory, with the evidence
and how it was caught. Do not create the file; the session that invoked this skill
can, and it is the one that owns the decision. If no memory directory exists,
offer the same text as an addition to the repo's `CONTRIBUTING.md` gotchas
section.

A generally portable trap belongs in `references/traps.md` in this plugin, not in
one project's memory. Say which you are proposing, and why.
