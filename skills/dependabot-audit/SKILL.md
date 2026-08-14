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
  `uv sync` builds any sdist in the resolution, which runs `setup.py` or the
  project's build backend.
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
git worktree add --detach "$SCRATCH/base-<N>" "$BASE_SHA"   # Phase 4 measures here

# prove the merge base is where the bot branched: a genuine bot PR is one commit
# by the bot, so any other author above it means the base moved underneath
git log --format='%h %an' "$BASE_SHA..pr-<N>"
gh api "repos/:owner/:repo/issues/<N>/events" \
  --jq '.[] | select(.event=="base_ref_force_pushed") | "\(.actor.login) \(.created_at)"'

# owner and name, for the GraphQL query Phase 6 issues — `:owner/:repo` is a
# REST-path convenience and does not expand in a GraphQL variable
OWNER=$(gh repo view --json owner --jq .owner.login)
NAME=$(gh repo view --json name  --jq .name)

# what this account may do here: it decides the verdict's shape, and it is how
# a failed permission-gated call is told apart from a real absence
PERMS=$(gh api "repos/:owner/:repo" --jq '.permissions')
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
| `$BASE_SHA` | merge base of `$DEFAULT` and `pr-<N>` — **and whether it is the bot's branch point**, which is a separate answer |
| `pr-<N>` | the fetched branch, registered in the **user's** repo |
| `$SCRATCH/pr-<N>` | worktree at the PR's head — Phase 5 reproduces in it |
| `$SCRATCH/base-<N>` | worktree at the merge base — **Phase 4 measures in it**, and the reason is below |
| `$OWNER`, `$NAME` | the repo's owner and name, for Phase 6's GraphQL variables |
| `$PERMS` | this account's permissions on the repo — `admin`, `maintain`, `push`, `triage`, `pull` |

If a later phase needs something not on this list, it belongs here rather than
there. A phase that consumes what a later phase creates cannot be run in order,
and that has now shipped twice — `tests/test_skill_prose.py` is what stops the
third.

**An output that could not be derived is not an output.** Every row above has
*three* states, not two: derived; genuinely absent, which is often a finding in
its own right; and **underivable**, where the call failed or its precondition did
not hold. Record which one you got, and never let the third collapse into either
of the others.

That collapse is not hypothetical, and it is the shape both known defects take.
These two fail into a *plausible* value rather than an error:

| Output | How it fails quietly | What it then asserts |
|---|---|---|
| `$BASE_SHA` | the base branch was rewritten under the PR, so `git merge-base` walks back to a much older shared ancestor | a real commit, which is the wrong one — Phase 1 sees a diff full of files the bump never touched, and Phase 4 measures a tree the PR would never land on |
| `$SCRATCH/required.txt` | the protection call failed and wrote its error body to **stdout** | a well-formed file that reads as "no required checks", which is indistinguishable from a repo that has none |

Neither raises. Both travel downstream as fact, and the report says something
false with full confidence — which costs more than a crash, because the shape of
the report invites trust in every row.

So: a phase handed an underivable input says so in its evidence row instead of
proceeding on the value, and Phase 7 does not print a row whose input was never
established. "Could not check" is a legitimate thing for this procedure to
report. "Checked, found nothing" when you could not check is not.

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

**Prove the merge base is where the bot branched.** `git merge-base` always
returns *a* commit, and when the base branch has been rewritten under an open PR
it returns one that is far too old — silently, with every later phase consuming it
as fact. This is the `$BASE_SHA` row of the underivable table above, and either
signal in the block settles it:

| Signal | Meaning |
|---|---|
| any commit above `$BASE_SHA` whose author is not the bot | the base moved; `$BASE_SHA` is not the branch point |
| a `base_ref_force_pushed` event on the PR | the same fact, stated by GitHub, with actor and timestamp |

Observed: a two-file `Cargo.toml` / `Cargo.lock` bump whose merge-base diff was 14
files and 3,682 deletions, appearing to delete the repo's entire vendored
`supply-chain/` tree. The base branch had been force-pushed eleven minutes after
the PR opened, and `merge-base` fell back to an ancestor nineteen months earlier.

**Do not reach for `gh pr view --json files` as a cross-check.** GitHub computes
the PR's file list from the merge base too, and reported the same 14 files. It
agrees with the wrong answer rather than correcting it.

When `$BASE_SHA` is not the branch point, report that as the finding and
substitute:

```bash
git fetch origin "$DEFAULT"
git worktree add --detach "$SCRATCH/tip-<N>" "origin/$DEFAULT"
```

- **Phase 1** takes its scope diff from `pr-<N>^..pr-<N>` — the bot's own commit.
- **Phase 4** measures in `$SCRATCH/tip-<N>` rather than `$SCRATCH/base-<N>`,
  because the tree this PR would land on is the default branch's tip, and the
  merge base is no longer a tree that exists anywhere.

Say both substitutions in the report. "The base branch was rewritten under this
PR" is a true and useful finding; "this bump reaches beyond the manifest and
lockfile" is not, and they are easy to confuse because they produce the same diff.

**`$PERMS` decides two separate things, and conflating them gets both wrong.**
The tier that can read branch protection is `admin`. The tier that can *merge* is
`push`. They are different, and the common case — a maintainer with `push` but not
`admin` — sits between them:

| `$PERMS` | Consequence |
|---|---|
| `admin: true` | branch protection is readable, if the plan offers it at all |
| `push: true`, `admin: false` | can merge; **cannot** read protection. The ordinary case for a maintainer in an org |
| `pull` only | cannot merge — so Phase 7's verdict is a recommendation, and the un-run merge command is a command this reader cannot run. Offer `--comment` text instead |

**Do not call `branches/<b>/protection` to find the required checks.** It needs
`admin`, and GitHub answers a bare `404 Not Found` without it rather than a 403,
so on any repo you do not administer the call fails in a way that is
indistinguishable from an unprotected branch — and `gh` writes that error body to
**stdout**, so redirecting it to a file produces a well-formed artifact that reads
as "no required checks". Verified: a repo whose `main` carries three required
checks returns exactly that 404 to a `pull`-only account, while
`branches/<b>` reports `"protected": true`.

Phase 6 asks a different question that is readable at `pull` and answers this one
directly. Two states remain worth naming when protection *is* readable, and both
are findings rather than errors on your part: `404 Branch not protected` (no
protection configured) and `403 Upgrade to GitHub Pro…` (a private repo on a free
plan, where protection is unavailable). A `404 Branch not found` is the one that
is your mistake — the branch name was wrong, so fix it and re-run.

Also do not substitute `repos/:owner/:repo/rules/branches/<b>`. It is readable
without `admin`, which makes it tempting, and it reports **only rulesets** —
classic branch protection is invisible to it. The same repo above returns `[]`
from both it and `/rulesets` while enforcing three required checks, so an empty
result there would manufacture the exact false finding this section exists to
prevent.

Then read the CI workflow and the pre-commit config to learn the repo's **own**
verification commands — do not assume `pytest`; it may be `uv run pytest`, `tox`,
`nox`, or a `make` target, and the workflow is what says so. Note where each tool
runs and **at what scope**: a hook scoped to `types_or: [python, pyi]` and a CI
step running the same tool over `.` are different gates, and Phase 4 turns on
that difference.

Recalled project memory may already name landmines for this repo (Phase 8 writes
them). Treat those as leads to check, not as facts — verify before repeating.

## Phase 1 — Scope and provenance

*Requires from Phase 0: `$SCRATCH`, `$BASE_SHA`, `pr-<N>`.*

**Check the branch point before you read the diff.** If Phase 0 found the base
rewritten, a merge-base diff shows the whole divergence and the gate below will
fire on files the bump never touched — so take the diff from `pr-<N>^..pr-<N>`
and report the rewritten base as its own finding. Firing the gate on a stale
base is not a safe default: it stops the audit for a reason that is not true, and
it reads in the report exactly like a bump that reaches into source.

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

**This plugin covers `uv.lock` and GitHub Actions, and nothing else.** For an
actions bump the script does not apply at all — no lockfile, no artifact hash, no
vulnerability database — so follow the recipe in `references/ecosystems.md`, which
is that ecosystem's whole mechanical half.

For any **other** ecosystem, say so and stop. Do not improvise a procedure from
the shape of the ones that are here: an unverified verifier reports green rather
than erroring, which is why npm, Cargo and Go were removed rather than left as
sketches. `references/ecosystems.md` has the case that settled it.

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

*Requires from Phase 0: the `$SCRATCH/base-<N>` worktree — or `$SCRATCH/tip-<N>`
if Phase 0 found the base rewritten — and the repo's own gates.*
*Executes code from the PR. Skipped under `--no-execute`; skip it if Phase 1
found anything.*

Not "is it safe" but **"does it change what this repo's gates accept"**. Measure
that; do not predict it from the changelog.

**Measure on the merge base, not on the PR's tree.** This is the difference
between finding the change and missing it, and the wrong choice fails silently:

```bash
G="${CLAUDE_PLUGIN_ROOT}/skills/dependabot-audit/scripts/gate_diff.py"

python3 "$G" --tree "$SCRATCH/base-<N>" \
  --run locked   "uv run --no-project --with ruff==<locked> ruff format ." \
  --run proposed "uv run --no-project --with ruff==<proposed> ruff format ." \
  --run latest   "uv run --no-project --with ruff==<latest> ruff format ."
```

The question is what the new version does to *the code you have*, which is the
base. A PR that already contains the fixup — because someone reformatted to make
CI pass — has a tree the new version is already happy with, so measuring there
reports no difference. And that is precisely the case where the behaviour change
was real enough that a human had to deal with it. Observed: on a real
`ruff 0.15.22 -> 0.16.0` bump, the base tree reports six Markdown files and the
PR's tree reports nothing.

**Then optionally re-run on `$SCRATCH/pr-<N>`.** The two trees answer different
questions, and together they say something neither says alone:

| base | PR | Reading |
|---|---|---|
| differs | agrees | a real behaviour change, and this PR already absorbs it — check *how* |
| differs | differs | a real behaviour change the PR does **not** handle — it will land on you |
| agrees | agrees | no behaviour change on this repo's code |

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
untouched and `gate_diff` says so. But **"no run changed any file" has three
causes**, and the tool deliberately does not choose between them: you gave a
read-only invocation, or the tree already satisfies every version, or the gate has
nothing to write. Only the first is a mistake; the second is a real agreement.
Decide which, and say so — do not report the weaker reading by default.

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
and the reasoning.

An actions bump installs nothing, so this phase has no frozen install to run. What
there is to reproduce is the pin itself, and that is Phase 1's tag comparison in
`references/ecosystems.md` rather than anything here. Say the phase did not apply;
do not report it as passed.

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
git worktree remove "$SCRATCH/base-<N>"
git branch -D "pr-<N>"
```

Keeping them is reasonable when a follow-up run is likely; silently keeping them
is not.

## Phase 6 — CI verification

*Requires from Phase 0: `$HEAD_SHA`, `$OWNER`, `$NAME`, `$PERMS`.*

Confirm the green you are trusting belongs to **this** commit. Use the full
40-character `$HEAD_SHA` pinned in Phase 0 — a short one matches nothing and
reads exactly like "CI never ran":

```bash
gh run list --commit "$HEAD_SHA" --workflow <ci>.yml
```

Then ask GitHub **which checks are required**, rather than deriving a list and
joining it by hand. `isRequired` is a field on the rollup contexts, it is
evaluated for this PR against whatever enforces it — classic protection, a
ruleset, a path-scoped rule — and it is readable at `pull`:

```bash
gh api graphql -f query='
query($owner:String!, $name:String!, $number:Int!) {
  repository(owner:$owner, name:$name) {
    pullRequest(number:$number) {
      mergeable mergeStateStatus reviewDecision
      commits(last:1) { nodes { commit { statusCheckRollup { state
        contexts(first:100) { nodes {
          ... on CheckRun      { name    conclusion isRequired(pullRequestNumber:$number) }
          ... on StatusContext { context state      isRequired(pullRequestNumber:$number) }
        } } } } } }
    }
  }
}' -F owner="$OWNER" -F name="$NAME" -F number=<N> > "$SCRATCH/checks.json"
```

The join happens server-side, so there is no list to retype and no `awk` matching
to get wrong. Read **three** fields, and never substitute one for another:

| Field | What it settles |
|---|---|
| `isRequired`, per context | which checks gate merge — a repo can report far more than it requires |
| `statusCheckRollup.state` | whether the checks that *reported* passed |
| `mergeStateStatus` | whether anything still blocks, **including what never reported** |

**A green rollup is not a mergeable PR.** Verified on a real PR: 39 contexts, 3
required, every one `SUCCESS`, rollup `SUCCESS` — and `mergeStateStatus: BLOCKED`
with `reviewDecision: REVIEW_REQUIRED`. A procedure that stops at the required
contexts reports all-green and recommends a merge GitHub will refuse. The old
recipe could not see this at any permission tier.

**`isRequired` only sees contexts that reported.** A required check that never ran
is absent from the list entirely — the failure the hand-written join existed to
catch. `mergeStateStatus` covers it, because an unsatisfied required check yields
`BLOCKED` and never `CLEAN`. Two traps travel with it: it is `UNKNOWN` on a merged
PR, and GitHub computes it lazily, so an open PR may need the query re-issued
before it settles. `UNKNOWN` is underivable, not "nothing blocks".

**Zero required contexts is a finding only when `mergeStateStatus` agrees.** Read
them together:

| Required contexts | `mergeStateStatus` | Reading |
|---|---|---|
| none | not `BLOCKED` | the repo enforces nothing — real, and it changes what a green run is worth |
| none | `BLOCKED` | something gates this PR that you cannot see. **Underivable**, per Phase 0 — do not report it as "no enforced checks" |
| some | `BLOCKED` | read `reviewDecision` and the unsettled contexts; the checks alone do not explain it |

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
