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

The ordering is the mitigation available inside the skill, and it is worth being
exact about what that buys. **Phase 1 is a gate**: if the diff reaches beyond the
manifest and lockfile, or provenance fails, stop there. Do not continue into the
phases that execute. A procedure whose thesis is "verify before you trust" must
not run the artifact before it has finished deciding whether to trust it.

**What the gate catches is a lockfile edited after it was written honestly** — a
hash, size, URL or yank status that disagrees with the registry — and a diff that
reaches into source. **It does not catch a malicious release.** Phase 1 compares
the lockfile against what the registry serves *today*, so when the attacker
published the artifact, the record and the lockfile agree — and agreement is the
entire test. A bump to a version whose maintainer account was compromised passes
Phase 1 clean and arrives at Phase 5's install with the gate's blessing.

The one signal that speaks to it is PEP 740 build provenance: `PUBLISHER CHANGED`
means the release being adopted was built somewhere the previous one was not.
Coverage is partial and version-dependent, so where there is no attestation there
is no signal. Read the ordering as what it is — it removes the cases it can see,
and `--no-execute` is the answer for the rest.

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
# Both SHAs out of ONE call. Fetched separately they can straddle a bot rebase,
# pinning a head and a base that never coexisted — and nothing downstream can
# tell, because each is individually a real commit.
PR_PIN=$(gh pr view <N> --json headRefOid,baseRefOid \
  --jq '"\(.headRefOid) \(.baseRefOid)"')
HEAD_SHA=${PR_PIN% *}                     # full 40 chars
BASE_REF=${PR_PIN#* }                     # GitHub's own base

# then what classifies the PR. Two fields are deliberately absent: `files`,
# because GitHub computes it from the merge base and so agrees with a rewritten
# one rather than correcting it, and `mergeStateStatus`, because GitHub computes
# it lazily and Phase 6 is where it is read fresh. Carrying either here invites
# a later phase to trust a value this one had no way to check.
gh pr view <N> --json number,title,author,createdAt,isCrossRepository

# derive the default branch — never assume "main"
DEFAULT=$(gh repo view --json defaultBranchRef --jq .defaultBranchRef.name)

cat .github/dependabot.yml 2>/dev/null || cat renovate.json 2>/dev/null

# pin the commit under audit, fetch it once, and build the tree every later
# phase works in
SCRATCH=${SCRATCH:-$(mktemp -d)}          # any directory OUTSIDE the repo
git fetch origin "pull/<N>/head:pr-<N>" "$DEFAULT"
BASE_SHA=$(git merge-base "$BASE_REF" "pr-<N>")
git worktree add "$SCRATCH/pr-<N>" "pr-<N>"
git worktree add --detach "$SCRATCH/base-<N>" "$BASE_SHA"   # Phase 4 measures here

# what sits above the base: a genuine bot PR is one commit by the bot. `%p` is
# there because two parents means a merge, which reads very differently
git log --format='%h [%p] %an %s' "$BASE_SHA..pr-<N>"
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
| `$BASE_SHA` | merge base of the PR's `baseRefOid` and `pr-<N>`, never of `$DEFAULT` — **and whether it is the bot's branch point**, which is a separate answer |
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
| `$BASE_SHA` | the PR has **landed**, so its head is an ancestor of the default branch and a merge base taken against `$DEFAULT` is the head itself | `$BASE_SHA == $HEAD_SHA` — Phase 1's scope diff is empty, Phase 4 measures the PR's own tree, and Phase 6 cross-checks the head against itself. All three report the reassuring answer |
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
| `isCrossRepository: false`, author `dependabot[bot]` or `renovate[bot]`, `push: true` | the ordinary case |
| `isCrossRepository: true` | a fork PR — neither bot opens one |
| any other author | a human PR shaped like a bump, which it may well be, and may not |
| **`$PERMS.push` false** | **not a repository you control.** You cannot merge this PR, so nothing is gained by letting it run on your machine |

Any of the last three is a **finding** in its own right, and each changes the
default: run `--no-execute`, report what the read-only phases found, and let the
user decide whether to authorise Phases 4 and 5. Say plainly that those phases
would run the PR's code.

The `push` row is the one easiest to argue away, so name the asymmetry it rests
on. A bot PR on a repo you control proposes code you were going to run anyway,
under gates you already trust — your own CI would run it too. A PR on a repo you
cannot merge into proposes code you had no plan to run, and the comparison to CI
stops holding: CI runs it in a fresh container with a scoped token, and this
procedure runs it on your workstation with your credentials in the environment.
`$PERMS` is already derived above, so this costs nothing to check.

**`$PERMS` is itself a row with three states, and this is where that matters.**
A failed `repos/:owner/:repo` call writes its error body to **stdout**, so the
capture succeeds and `$PERMS` holds a JSON error rather than a permissions
object — at which point `push` is not `true` and reads exactly like a pull-only
account. Measured on a name that does not resolve:

```
PERMS = {"message":"Not Found","documentation_url":"...","status":"404"}
```

The **exit code is 1**, which is what separates this from the branch-protection
trap below, where the same shape arrives at exit 0. So gate on the call, not on
the value:

```bash
PERMS=$(gh api "repos/:owner/:repo" --jq '.permissions') || PERMS=underivable
```

Failing this one closed is right — `--no-execute` is the safe direction, and
taking it costs the audit only the two phases it was least entitled to run. What
must not happen is the *report* saying "you lack `push` on this repo" when the
audit could not establish what you have. That is the underivable state
masquerading as a finding, which is the failure this whole section is built
against.

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

**`$DEFAULT` cannot be the left-hand side of the merge base.** Once a PR has
landed, its head *is* an ancestor of the default branch, so the merge base of the
two is the head — and auditing a merged PR is a supported thing to do here: Phase
6 has a row for it, `references/actions.md` has a paragraph, and every replay
this project's own gate asks for is one. `baseRefOid` is the base commit GitHub
diffs the PR against, and it is right in both states.

Measured on `cli/cli`, four merged bumps — #14147, #14091, #13981, #14049.
`git merge-base trunk pr-<N>` returns the PR's own head for all four, so the scope
diff is **0 files** where GitHub reports 4, 2, 3 and 2; via `baseRefOid` it is
those four numbers exactly. On open PR #14148 both forms return the same commit,
so this is a no-op wherever the old form worked.

It is not the rewritten-base case and does not stand in for its checks: there,
`baseRefOid` is the current tip of a branch that moved out from under the PR, and
`merge-base` still walks back too far.

**Prove the merge base is where the bot branched.** `git merge-base` always
returns *a* commit, and when the base branch has been rewritten under an open PR
it returns one that is far too old — silently, with every later phase consuming it
as fact. This is the first `$BASE_SHA` row of the underivable table above. The
block prints two signals for it, and they are not interchangeable:

| Signal | Meaning |
|---|---|
| a `base_ref_force_pushed` event on the PR | the base was rewritten. **This is the authority** — GitHub states it, with actor and timestamp |
| a non-bot commit above `$BASE_SHA` with **one** parent | a human commit on the bot's branch. Corroborates a rewritten base, and is what Phase 6 attributes against |
| a non-bot commit above `$BASE_SHA` with **two** | someone merged the base branch *into* the bot's branch. `$BASE_SHA` is still the branch point and the substitutions below must **not** fire |

The last row is why the author scan is corroboration rather than the test.
Measured on `cli/cli` #14049, whose head is exactly that merge — *"Merge branch
'trunk' into dependabot/…"* by a maintainer, above the bot's own commit: zero
`base_ref_force_pushed` events, and a correct two-file scope diff from
`$BASE_SHA`. Read as a moved base it would substitute the `pr-<N>^` diff — 20
files, 1,101 lines — and halt the audit on a bump that changes four workflow
lines.

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

- **Phase 1** takes its scope diff from `pr-<N>^..pr-<N>` — the bot's own commit,
  which assumes the head *is* that commit. A head with two parents is not: it is
  a merge someone made into the bot's branch, and `pr-<N>^` is then the branch
  tip rather than the branch point. Measured on `cli/cli` #14049, whose head is
  *"Merge branch 'trunk' into dependabot/…"*: that diff is 20 files and 1,101
  changed lines, none of them the bump.
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
manifest and the lockfile — or, for an actions bump, **only `uses:` lines**, in
however many workflow files pin that action. The count of files is not the
invariant and never was: an action is pinned in every workflow that uses it, and
a grouped bump moves several actions at once, so ordinary merged bumps touch
two, three or four files. `references/actions.md` has the measurements, and
the rule for reading the versions out of that diff rather than off the title.
Anything else is a finding: report it, and **stop before Phase 4**. The same
applies if provenance comes back with a discrepancy. Phases 4 and 5 execute the
PR's code, and the whole point of running the cheap read-only checks first is that
they can refuse to hand it a shell. Continuing anyway spends the ordering for
nothing.

Stopping here is not a failed audit. It is a complete one that reached a verdict
early — write the report with the phases that ran and say which did not.

**The method is per-ecosystem; the gate above is not.** Each reference is
sectioned by phase, so read the section for this one:

| Ecosystem | Method |
|---|---|
| `uv.lock` | `references/uv-lock.md` § Phase 1 — `scripts/audit.py` verifies every pinned artifact's hash, size, URL and yank status against the live registry, plus PEP 740 build provenance |
| GitHub Actions | `references/actions.md` § Phase 1 — no lockfile and no artifact hash, so the question becomes whether the pin is **immutable**: a 40-hex SHA, or a tag someone else can revoke. The scope gate keys on `uses:` lines, never on a count of files |

**This plugin covers `uv.lock` and GitHub Actions, and nothing else.** For any
other ecosystem, say so and stop. Do not improvise a procedure from the shape of
the ones that are here: an unverified verifier reports green rather than erroring,
which is why npm, Cargo and Go were removed rather than left as sketches.

That is not hypothetical. Followed faithfully against a real Cargo bump, an
improvised recipe returned matching checksums, a current latest version and a
clean OSV batch — on a PR that raised the project's minimum Rust version past its
own declared floor. Nothing in the output looked partial. A hand-run recipe also
lacks every guard the script has earned: batch limits, retries, version ordering,
and the refusal to report `CLEAN` on an empty selection.

`audit.py` enforces its half rather than leaving it to prose. Handed a
`Cargo.lock`, `poetry.lock`, `package-lock.json`, `Pipfile.lock`, `go.sum`,
`go.mod`, `yarn.lock`, `pnpm-lock.yaml` or a `pyproject.toml`, it exits **2**
naming the format. Report that as the boundary it is, not as a failed audit: the
ecosystem-independent phases still ran, so say what Phase 0's classification and
Phase 6's CI state established, and name plainly what was not checked.

## Phase 2 — Currency

*Requires: the Phase 1 script output, and the PR's `createdAt` from Phase 0.*

**A bot's proposal is not evidence of "current".** Ask the registry what the
latest version actually is, and compare publish timestamps against the PR's
`createdAt`. What that comparison stopped settling on 2026-07-14 is *why*:
Dependabot now holds a version update until the release is **three days old**, by
default, with no `cooldown:` block required and nothing in the PR to show it. So
read the *age* of the gap and not only its existence — inside that window the bot
is waiting, outside it the bot is behind.

**For GitHub Actions "current" is a question about the tag line, not the pin** —
a moving major tag picks up new releases on its own, so a newer patch is not a
gap. `references/actions.md` § Phase 2 has the `compare` that separates a tag
that merely moved *ahead* from one that rolled **behind**, which is the case a
bot cannot fix because it cannot propose a downgrade.

Rule out the innocent explanations before reporting a gap: a yanked release; a
**cooldown** (`cooldown:` in `dependabot.yml`, `minimumReleaseAge` in
`renovate.json`), which now applies even when the file says nothing; or an
`ignore` rule, which can name `"*"` and be scoped by `update-types`, so "no rule
names this dependency" is not "no rule covers it".

**A gap inside the cooldown window does not earn a follow-up branch.**
Recommending one hand-lands the release the bot is deliberately waiting on, which
inverts the control rather than clearing it. What outranks the hold is what this
phase reads for next: a `Security` entry or a destructive-fix bug in the gap. The
cooldown exempts Dependabot's *security updates* — the advisory-driven kind — and
not a version update whose changelog happens to carry a privately disclosed fix,
which is exactly the case below.

**A bot's ignore state is not always in a config file.** `@dependabot ignore this
major version` records the hold in the *PR*, not the repo, so a dependency can be
pinned indefinitely with nothing in `dependabot.yml` to show it. The evidence is a
closed bot PR carrying a comment like "OK, I won't notify you again about this
release". When a gap looks unexplained, list closed bot PRs for the same
dependency before reporting it as lag:

```bash
gh pr list --state closed --author "app/dependabot" --search "<dependency>" \
  --json number,title,closedAt
```

Then **read the changelog for every version in the gap**, plus the versions being
adopted. Look for two things, in this order:

- **`Security` sections.** These outrank every vulnerability database. A privately
  disclosed fix ships with no CVE, and scanners will report clean.
- **Destructive-fix bugs.** Entries like "stop deleting…" or "no longer removes…"
  in a tool the repo runs in **write mode** (`--fix`, `--write`, `-i`) are
  data-loss bugs in a mode that runs automatically. They never appear in a
  security feed. Check whether the repo actually invokes that write mode.

## Phase 3 — Known vulnerabilities

*Requires: the Phase 1 output for this ecosystem.*

**The question: what does the world already know is wrong with this?** Expect it
to agree with Phase 2 only sometimes — that divergence is the point, not a
contradiction. The method differs by ecosystem; the question does not.

| Ecosystem | Method |
|---|---|
| `uv.lock` | `references/uv-lock.md` § Phase 3 — the OSV batch is **already done** by the Phase 1 script, so read that result rather than re-querying; what remains is the ecosystem's own auditor, and `pip-audit` audits the wrong interpreter if invoked casually |
| GitHub Actions | `references/actions.md` § Phase 3 — GHSA carries an `actions` ecosystem, and the obvious port of the `uv.lock` query reports **clean on a known-compromised action** |

That second row is why this phase has a guard in the test suite. *"Not
applicable" is an assertion too*, and it shipped false: three places in this repo
once stated that GitHub Actions has no vulnerability database. A phase that
believed it skipped a real check — measured against `tj-actions/changed-files`,
where a package-only query returns two advisories and every version-qualified
form returns zero.

## Phase 4 — Behavior change (the highest-yield phase)

*Requires from Phase 0: the `$SCRATCH/base-<N>` worktree — or `$SCRATCH/tip-<N>`
if Phase 0 found the base rewritten — and the repo's own gates.*
*Executes code from the PR. Skipped under `--no-execute`; skip it if Phase 1
found anything.*

**The question: does this change what runs here, or what this repo's gates
accept?** Not "is it safe". For `uv.lock` you can measure it, and you must —
predicting it from the changelog is what this phase exists to replace. For
GitHub Actions you cannot run the thing at all, so the method is different and
the section for it is below.

| Ecosystem | Method |
|---|---|
| `uv.lock` | `references/uv-lock.md` § Phase 4 — **measure it.** `scripts/gate_diff.py` runs each gate at the locked, proposed and latest versions in `$SCRATCH/base-<N>` and compares what each run *did to the files* |
| GitHub Actions | `references/actions.md` § Phase 4 — an action cannot be run locally at two versions, so the method is reading the release notes **and then establishing whether this repo's workflows are in the change's scope at all** |

**Measure on the merge base, not on the PR's tree.** This is the difference
between finding the change and missing it, and the wrong choice fails silently: a
PR that already contains the fixup — someone reformatted to make CI pass — has a
tree the new version is already happy with, so measuring there reports no
difference. And that is exactly the case where the change was real enough that a
human had to deal with it. Observed on a real `ruff 0.15.22 -> 0.16.0` bump: six
Markdown files on the base, nothing on the PR's tree.

**Do not read the exit codes as the answer.** Both versions can exit 0 while the
scope moves underneath them — that is the founding observation of this phase, and
`references/traps.md` has it.

**"Inert here" is a result, not silence.** Reaching it deliberately is this phase
working; reaching it by not looking is the failure.

## Phase 5 — Independent reproduction

*Requires from Phase 0: the `$SCRATCH/pr-<N>` worktree, `$HEAD_SHA`, `pr-<N>`.*
*Executes code from the PR — the most of any phase. Skipped under
`--no-execute`; skip it if Phase 1 found anything.*

**The question: has this been shown to work, independently of the bot saying so?**
For `uv.lock` you answer it by building and running the thing. For GitHub Actions
no local reproduction exists at all, so the answer has to come from somewhere
else — and "no reproduction available" is a result to report, not a phase to skip.

| Ecosystem | Method |
|---|---|
| `uv.lock` | `references/uv-lock.md` § Phase 5 — install **frozen** and run the repo's own gates and suite in `$SCRATCH/pr-<N>` |
| GitHub Actions | `references/actions.md` § Phase 5 — nothing to install and no way to run an action off GitHub's runners, so the substitute is **evidence that this pin has already run**: the history of the workflow the bump changed |

Phase 0 built the worktree and proved it points at `$HEAD_SHA`, so the user's
working tree is untouched throughout. That isolation protects the *working tree*,
not the machine — see the execution section at the top.

**"No reproduction available" is a result to report, not a phase to skip.** For an
actions bump on an open PR whose workflow is not PR-triggered, reproduction is
impossible before merge; that is a property of the change and belongs in the
report.

**Say what the row actually covered.** A green reproduction is true of *one*
configuration and reads as true of every one, so name which install ran, which
interpreter produced it, and anything verified but not installed. "Frozen install
passed under `--no-build --no-install-project` on CPython 3.14; the 3.11 fork of
`rpds-py` was verified but not installed" is a stronger row than "frozen install
passed", because it is one a reader can falsify. `uv run python -V` from inside
the synced environment is where the interpreter comes from — not the auditor's
own `python3` — and `resolution-markers` is why the two can differ.

Gate on exit codes. `cmd | tail && next` gates on `tail`, so a failing suite sails
through; use `set -o pipefail` or separate calls.

The worktrees and the `pr-<N>` branch are cleaned up in Phase 7, not here — an
audit that stops at Phase 1's gate never reaches this phase and still has to
tidy up after itself.

## Phase 6 — CI verification

*Requires from Phase 0: `$HEAD_SHA`, `$BASE_SHA`, `$OWNER`, `$NAME`, `$PERMS`.*

Confirm the green you are trusting belongs to **this** commit, **and that it
exercised the change**. Those are two questions, and an actions bump routinely
passes the first while failing the second. A third follows whenever something is
red: whether the bump is why.

**Check that the changed file is reachable from a pull request.** A workflow
triggered only by `push: tags:` or `schedule:` never runs on a PR, so every check
on it comes from *other* workflows and none of them execute the changed line:

```bash
# for each workflow the diff touched, read its triggers
git show "pr-<N>:.github/workflows/<changed>.yml" | sed -n '/^on:/,/^[a-z]/p'
```

If the intersection of "workflows the diff touched" and "workflows a
`pull_request` can trigger" is **empty**, say so plainly: CI is green and it is
green for reasons unrelated to this diff. Then fall back to Phase 5's run-history
substitute. Observed: a PR changing only `release.yml`, which triggers on
`push: tags: [<prefix>-*]`, carried three green checks — all of them from the
repo's separate test workflow.

**Run the script; it is this phase's three questions in one call.** Every query
below used to be issued by hand, and three of the seven defects that have shipped
in this file were here — each of them a real endpoint asked the wrong question,
answering in a well-formed way. A hand-run query cannot be regression-tested.

```bash
C="${CLAUDE_PLUGIN_ROOT}/skills/dependabot-audit/scripts/ci_state.py"
PARENT=$(git rev-parse "pr-<N>^")

python3 "$C" --owner "$OWNER" --name "$NAME" --number <N> \
  --head-sha "$HEAD_SHA" --parent "$PARENT" --base-sha "$BASE_SHA"
```

**Exit 2 means it could not run; exit 1 means it ran and found something.** Never
read one as the other.

It asks GitHub **which checks are required** rather than deriving a list and
joining it by hand — `isRequired` is a field on the rollup contexts, evaluated for
this PR against whatever enforces it (classic protection, a ruleset, a path-scoped
rule) and readable at `pull`. The join happens server-side, so there is no list to
retype and no `awk` matching to get wrong. It pages `contexts` to exhaustion, reads
`mergeStateStatus` alongside the rollup, compares against `pr-<N>^`, and labels
every red context. What it will not do is decide the verdict; that is Phase 7's
table, and putting it in both places is how the two drift.

Read **three** fields out of its output, and never substitute one for another:

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
before it settles. `UNKNOWN` is underivable, not "nothing blocks" — the script
says so rather than leaving it to be remembered.

**Zero required contexts is a finding only when `mergeStateStatus` agrees**, and
the script reads them together:

| Required contexts | `mergeStateStatus` | Reading |
|---|---|---|
| none | not `BLOCKED` | the repo enforces nothing — real, and it changes what a green run is worth |
| none | `BLOCKED` | something gates this PR that you cannot see. **Underivable**, per Phase 0 — do not report it as "no enforced checks" |
| some | `BLOCKED` | read `reviewDecision` and the unsettled contexts; the checks alone do not explain it |

**The context list can be truncated, and the script refuses to hide it.**
`contexts(first:100)` is a page: a repo reporting more returns the first hundred
and says nothing about the rest, so a required check at position 101 is absent —
indistinguishable from one that passed, and the same failure as the hand-written
join one level up. It pages on `pageInfo`, and where it cannot it reports the
required set as **underivable** rather than complete:

| `totalCount` vs. what was held | What the required set is |
|---|---|
| equal | complete |
| greater, paged to exhaustion | complete |
| greater, could not be paged | **underivable**, per Phase 0 — not "these are all of them" |

**A red check is not evidence that the bump caused it.** Phase 6 reports check
conclusions, and a failing required context is the row most likely to carry the
verdict — so it is the one that must not assert more than it established. "This
check is red" is established. "This bump broke it" is a *causal* claim, and
nothing above tests it.

The script asks whether it was already red **on the commit the bot branched
from** — the parent of the bot's own commit, not the merge base — and labels the
row in three states, never two:

| At `pr-<N>^` | Label | What it means for the verdict |
|---|---|---|
| the same check is green | **attributable** | the bump is implicated; this row can carry a Hold |
| the same check is red | **pre-existing** | the tree the bump landed on was already red. A real finding, a *different* one, and it must not produce a Hold on this bump |
| **no run at the base**, or no check by that name | **underivable**, per Phase 0 | say so rather than defaulting to attributable |

**`pr-<N>^` and `$BASE_SHA` are the same commit for a genuine one-commit bot PR**,
which is the ordinary case — so preferring the parent costs nothing there and is
right when they differ. When they differ, `$BASE_SHA` attributes to the bump
everything that happened on the branch beneath it, which is the same mistake as
diffing scope against a rewritten base and gets the same substitution (#19).

Measured on `BIRSAx2/mdcat` #6, the PR this section comes from. Its branch
carries a *human* commit under the bot's, so the four candidate comparison
points disagree:

| Commit | `test (ubuntu-latest)` |
|---|---|
| the bot's commit — the PR head | `failure` |
| `pr-<N>^`, the human commit below it | `failure` — **pre-existing**, and the answer |
| `git merge-base` | the check does not exist there at all |
| the base branch's tip | `success` — which would have said **attributable** |

Two of those four produce the false Hold this section exists to prevent, and one
of them is the merge base.

**When `pr-<N>^` has no runs at all the script falls back to `$BASE_SHA` and marks
the claim weaker.** An intermediate commit of a multi-commit branch is often never
built — CI ran on the head and nowhere else — so the parent has nothing to compare
against while the merge base, being on the default branch, does. That fallback
answers a *different* question:

| Compared against | What a red result establishes |
|---|---|
| `pr-<N>^` | it was red **before this commit** — attribution to the bump |
| `$BASE_SHA` | it was red **before this branch** — everything below the bump is inside the claim |

Reaching for the second is legitimate and better than reporting nothing; passing
it off as the first is the failure, so carry the weakened wording into the report
rather than dropping it. Observed on this plugin's own PR #26: `pr-26^` is an
intermediate commit of the branch and carries zero check runs.

**The underivable row has two more causes and they look identical.** The commit
may predate the workflow or its run may have aged out — or the check may simply be
*named* something else there. Names drift: `mdcat`'s `main` now reports `test` and
`test-windows` where the PR reports `test (ubuntu-latest)`, so a name match
against a distant commit finds nothing and reads as "never ran". The script prints
the whole name list at the comparison point for exactly this reason; read it
rather than the one name you are chasing.

**A red check on a workflow the diff never touched is a strong prior for
pre-existing**, and Phase 6 already derives which workflows the diff touched for
the PR-reachability check above. Share that input rather than deriving it twice.

Matching on name and conclusion establishes that the check was *already
failing*, not that it is failing for the same reason. Where the distinction
decides the verdict, read the failing step's log at both commits.

Observed on `BIRSAx2/mdcat` #6: `test (ubuntu-latest)` red beside two green
siblings, which reads exactly like a dependency bump breaking one platform. The
failure was `unresolved link to pulldown-cmark-mdcat` — a rustdoc intra-doc-link
error under `#[deny(warnings)]`, failing identically on the base commit and
having nothing to do with the dependency. A Hold driven by that row would have
been **correct by accident and unfalsifiable in the report**: every cell in it
true, the causal claim never established. That is the same family as the
rewritten base and the hand-joined required list — rows that are individually
accurate and collectively misleading.

It is also the direction that costs least to be wrong in, and therefore gets
least scrutiny: a false Hold looks conservative, so nobody goes back to check
whether the bump was the cause.

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

### Which evidence produces which verdict

Every row above is a finding; the verdict is a function of them, and leaving that
function implicit is how two audits with the same evidence reach different
recommendations. Read the table top-down and take the **first** row that matches:

| Evidence | Verdict |
|---|---|
| Phase 1's gate fired — scope, a provenance discrepancy, or `PUBLISHER CHANGED` | **Hold** |
| OSV or GHSA reports a vulnerability in a version being **adopted** | **Hold** |
| A `Security` entry in the gap, and the gap is outside the cooldown | **Hold** — or merge-then-follow-up when the fix is already in the adopted version |
| Actions: the tag rolled **behind** the proposed SHA | **Hold.** Close the bot's PR and replace it by hand; a bot cannot express a downgrade |
| Phase 4: base differs, PR differs — the change is real and unabsorbed | **Hold** |
| Phase 5: the frozen install failed, or a repo gate failed | **Hold** |
| A red required check labelled **attributable** | **Hold** |
| A red required check labelled **pre-existing** | **Not a Hold on this bump.** Report it as its own finding, take the verdict from the remaining evidence, and say the PR is unmergeable until someone fixes it |
| Phase 4: base differs, PR agrees — real and already absorbed | **Merge as-is**, naming what the PR absorbed and how |
| `mergeStateStatus: BLOCKED` with every check green | **Merge as-is** on the bump's merits; name what blocks it, usually `reviewDecision` |
| Actions: the workflow file is generated (`DO NOT EDIT`) | **Merge as-is, then follow up** on the generator — this bump is transient without it |
| A gap exists, outside the cooldown, nothing security-shaped in it | **Merge as-is, then follow up** |
| A gap exists **inside** the cooldown window | **Merge as-is.** Do *not* offer a follow-up: it hand-lands the release the control exists to delay |
| Everything derived, nothing above matched | **Merge as-is** |

**When phases disagree, this is the precedence** — and they are *expected* to
disagree, which is why more than one of them exists:

1. Phase 1's gate
2. Changelog `Security` entries across the gap
3. OSV / GHSA
4. Phase 4's measured difference
5. Phase 5's reproduction
6. Phase 6's CI state

A changelog `Security` entry outranking a clean OSV batch is not a contradiction
to explain away — a privately disclosed fix ships with no CVE, so *clean scanner,
dirty changelog* is the expected reading and the whole reason Phase 2 reads
changelogs at all.

### Confidence

Not a feel. It is a function of how much of the evidence was actually derived,
which the three-state rule has already recorded per row:

| Condition | Confidence |
|---|---|
| Every verdict-bearing input derived, and the executing phases ran | **high** |
| One or more verdict-bearing inputs **underivable**, none of them decisive | **medium** |
| `--no-execute`, with a Phase 4-shaped question still open | **medium** — say what running Phase 4 would add |
| A **decisive** input underivable — one whose value would change the verdict | **low**, and name which one |

"Verdict-bearing" is the test, not "present in the table": an underivable row that
no verdict rule reads does not lower confidence, and saying it does trains the
reader to discount the field. Conversely a single underivable input that would
flip the recommendation caps it at **low** however green everything else is.

If the user asked for `--comment`, print the report and offer to post it; posting
is a separate, explicitly requested action.

**Close the loop, whatever phase the audit reached.** The two worktrees *and* the
`pr-<N>` branch Phase 0 created are registered in the **user's** repo, and they
accumulate one set per PR audited. This step lives here rather than in Phase 5
because Phase 5 is skippable and this is not: `--no-execute` skips it, and Phase
1's gate stops before it — which is the path where the audit was *most* right to
stop, and the one that used to litter every time. Phase 7 is the only phase every
audit reaches, including the one that ends at the gate.

```bash
git worktree remove "$SCRATCH/pr-<N>"
git worktree remove "$SCRATCH/base-<N>"
git branch -D "pr-<N>"
```

Add `$SCRATCH/tip-<N>` if Phase 0 found the base rewritten and created it.

Keeping them is reasonable when a follow-up run is likely — say so in the report,
with the commands above, so the user knows what is there. Silently keeping them
is what this step exists to prevent.

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
