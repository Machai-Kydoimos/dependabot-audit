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
6 has a row for it, `references/ecosystems.md` has a paragraph, and every replay
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
two, three or four files. `references/ecosystems.md` has the measurements, and
the rule for reading the versions out of that diff rather than off the title.
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
actions bump the script does not apply — there is no lockfile and no artifact
hash — so follow the recipe in `references/ecosystems.md` for this phase. The
later phases still apply: every one of them has an actions method, and its
section says so.

For any **other** ecosystem, say so and stop. Do not improvise a procedure from
the shape of the ones that are here: an unverified verifier reports green rather
than erroring, which is why npm, Cargo and Go were removed rather than left as
sketches. `references/ecosystems.md` has the case that settled it.

This phase leads with the script, so reaching for it on an unfamiliar repo is
the ordinary path rather than a careless one. It now refuses by name — `is a
Cargo.lock (Rust)`, `is a poetry.lock` — at exit 2. Report that as the boundary
it is, not as a failed audit: the ecosystem-independent phases still ran.

## Phase 2 — Currency

*Requires: the Phase 1 script output, and the PR's `createdAt` from Phase 0.*

**A bot's proposal is not evidence of "current".** Ask the registry what the
latest version actually is, and compare publish timestamps against the PR's
`createdAt`. What that comparison stopped settling on 2026-07-14 is *why*:
Dependabot now holds a version update until the release is **three days old**, by
default, with no `cooldown:` block required and nothing in the PR to show it. So
read the *age* of the gap and not only its existence — inside that window the bot
is waiting, outside it the bot is behind.

**For GitHub Actions, "current" is a question about the tag line, not the pin.**
A moving major tag picks up new releases on its own, so a newer patch is not a
gap. What matters is whether the *major* being adopted is still the newest one,
and whether the tag still points where the PR proposed — `references/ecosystems.md`
has the `compare` that separates *ahead* from **behind**.

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

**uv.lock.** Batch-query OSV across the whole locked set, then corroborate with
the ecosystem's own auditor. The OSV half is already done — the Phase 1 script
ran it — so read that result instead of issuing a second query, and what remains
here is the auditor. See `references/ecosystems.md` for its invocation and traps;
`pip-audit` in particular audits the wrong interpreter if invoked casually.

**GitHub Actions.** Actions *do* have an advisory database, and an audit that
skips this phase for them is skipping a real check:

```bash
gh api "/advisories?ecosystem=actions&affects=<owner>/<name>" \
  --jq '.[] | "\(.ghsa_id)\t\(.severity)\t\(.summary)"'
```

Also read the action repository's own status — `archived`, `disabled`, or a
transfer to a new owner are all supply-chain facts that no advisory records.

**Do not query OSV by version for this ecosystem.** OSV carries the same
advisories, but its GitHub Actions entries have no usable version ranges, so a
version-qualified query returns empty and reads as clean. Measured against
`tj-actions/changed-files`, the 2025 compromise:

| Query | Result |
|---|---|
| package only | **2 vulns** |
| `+ version 45.0.7` (the compromised release) | 0 |
| `+ version 0.0.0` | 0 — a range check would match everything |
| PyPI control: `requests` 2.19.0, version-qualified | 10, so the pattern itself is sound |

Copying the `uv.lock` shape here — batch by `(package, version)` — therefore
reports **clean on a known-compromised action**. Query by name, or use GHSA.

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

### uv.lock — measure it

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

### GitHub Actions — establish whether the change reaches this repo

You cannot run an action locally at two versions, so measurement is unavailable
and reading the release notes is the method rather than the shortcut. That makes
the second step load-bearing: **a change is only a finding here if this repo's
workflows are in its scope.**

Read the notes for every version in the gap, looking for changes to a *default*,
a *trigger*, an *input*, or a *runner requirement* — then find the line in this
repo's workflows that decides whether it applies:

| Change | What to grep for here |
|---|---|
| a trigger is newly restricted | `pull_request_target:`, `workflow_run:` in this repo's workflows |
| a default input flips | that input's name — an explicit setting pins the old behaviour |
| a minimum runner or Node version | `runs-on:` — GitHub-hosted is fine, a self-hosted label is not |
| credential or token handling | `permissions:`, `persist-credentials`, and what later steps do with the token |

**Report "inert here" as a result, not as silence.** Reaching it deliberately is
this phase working; reaching it by not looking is the failure. Observed:
`actions/checkout@v7` blocks fork-PR checkout under `pull_request_target` and
`workflow_run` — a security change shipped as a plain bullet with no heading and
no ⚠️ — and it was genuinely inert on a repo that uses neither trigger. The report
should say so and name the greps that settled it.

**Two signals that the notes alone will not give you.** Both were observed:

- **A coordinated release across every supported major is a security backport.**
  `actions/checkout` published v7.0.1, v6.1.0, v5.1.0, v4.4.0, v3.7.0 and v2.8.0
  within 35 minutes of each other; the backports carry `[BREAKING]` and a
  changelog link that the original major's notes do not. Check the sibling majors'
  release dates, not just the line you are on.
- **Version-coupled actions must move together.** `upload-artifact` and
  `download-artifact` ship majors in lockstep — the v7/v8 pair went out eight
  seconds apart. If the bump moves one half, check the sibling's pin in the same
  workflow and say whether the repo is now split across generations.

## Phase 5 — Independent reproduction

*Requires from Phase 0: the `$SCRATCH/pr-<N>` worktree, `$HEAD_SHA`, `pr-<N>`.*
*Executes code from the PR — the most of any phase. Skipped under
`--no-execute`; skip it if Phase 1 found anything.*

**The question: has this been shown to work, independently of the bot saying so?**
For `uv.lock` you answer it by building and running the thing. For GitHub Actions
no local reproduction exists at all, so the answer has to come from somewhere
else — and "no reproduction available" is a result to report, not a phase to skip.

### uv.lock — install frozen and run the suite

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

Then run the repo's own gates from Phase 0, and its full test suite.

**`--locked` checks the whole lockfile; the install materialises one resolution
out of it.** Those are different claims and the row must not merge them. A
`uv.lock` can carry several `[[package]]` blocks for the same name under
different `resolution-markers` — typically the last release supporting an older
Python alongside the current one. Phase 1 verifies **every** fork's artifacts
against the registry. `uv sync` then installs only the resolution matching the
interpreter and platform in front of it, which need not be the highest pin.

So a green row here on 3.14 says nothing about whether the 3.11 fork's artifacts
still fetch or its older release still installs. **Ask the environment which one
it built**, rather than the auditor's own `python3`, which may not be the
interpreter uv chose:

```bash
uv run python -V                  # inside the synced environment
uv pip list --format=freeze       # the versions actually materialised
```

The Phase 1 script prints the fork list — `forked packages: every pin verified,
one of them installed` — so the names and versions to reconcile against are
already in the output. Name the interpreter and the fork in the reproduction
row; do not report the install as though it covered every pin.

**When the bumped package is itself forked, a second sync is the thorough
version** and it is a deliberate escalation, not the default:

```bash
uv sync --locked --python <floor>   # the floor from requires-python
```

It costs an interpreter download and can fail for reasons that have nothing to
do with the bump. The cheap version — installing once and disclosing which fork
that was — is honest and is what this phase requires. The second sync is worth
it when the fork you did *not* install is one of the packages under audit, and
the report should say which of the two you did.

### GitHub Actions — substitute run history

There is nothing to install and no way to execute an action outside GitHub's
runners, so local reproduction is unavailable. The substitute is **evidence that
this pin has already run**: ask the workflow the bump changed.

```bash
gh run list --workflow <changed>.yml --limit 10 \
  --json conclusion,headBranch,createdAt,displayTitle \
  --jq '.[] | "\(.conclusion)\t\(.createdAt)\t\(.displayTitle)"'
```

Read it against the merge date, and be strict about what it proves. Runs *after*
the bump landed exercised the new pin; runs before it did not, and a green history
that predates the merge says nothing at all about the version being adopted.

| Situation | What you can honestly report |
|---|---|
| the workflow ran green on this pin since the bump landed | reproduced — the strongest evidence available for an actions bump |
| the workflow has not run since | **not reproduced.** State it; do not let Phase 6's green stand in for it |
| the workflow is not PR-triggered and the PR is open | reproduction is impossible before merge. That is a property of the change, and it belongs in the report |

Observed: a bump to `actions/upload-artifact` in a release-only workflow, merged
alongside a `download-artifact` pin two majors behind. Nothing in the PR could
show whether the pair still interoperated — seven green release runs over the
following month did.

**Three things qualify this phase's row, and "reproduced" alone asserts past all
of them.** Each has a green result that is true of *one* configuration and reads
as true of every one:

| Qualifier | Why the bare row overstates it |
|---|---|
| **which install** | the script-suppressing flags are the documented default and they weaken the proof: a package that genuinely needs its install script is not exercised. Re-running without them is a legitimate choice — say which produced the row |
| **which interpreter** | the install materialised one fork of a forked lockfile. `uv run python -V`, not the auditor's `python3` |
| **which forks were only verified** | Phase 1 checked all of them and Phase 5 installed one. Name the others rather than letting the install stand for them |

None of the three is a failure to disclose. "Frozen install passed under
`--no-build --no-install-project` on CPython 3.14; the 3.11 fork of `rpds-py` was
verified but not installed" is a stronger row than "frozen install passed",
because it is one a reader can falsify.

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

Use the full 40-character `$HEAD_SHA` pinned in Phase 0 — a short one matches
nothing and reads exactly like "CI never ran":

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
        contexts(first:100) { totalCount pageInfo { hasNextPage endCursor } nodes {
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

**`first:100` is a page, not the answer.** A repo with more contexts than that
returns the first hundred and says nothing about the rest, so a required check
sitting at position 101 is absent from the list — which is indistinguishable from
a check that passed, and is the same failure as the hand-written join one level
up. `totalCount` is what tells you, so read it before reading the nodes:

| `totalCount` | What the context list is |
|---|---|
| ≤ 100 | complete — every context reported is in the nodes |
| > 100 | **a page.** Follow `pageInfo.hasNextPage` / `endCursor` until it is exhausted |
| > 100, not paginated | **underivable**, per Phase 0. Say the required set could not be established; do not report the visible contexts as though they were all of them |

`mergeStateStatus` still covers you for the *merge* question — an unsatisfied
required check yields `BLOCKED` whether or not you paged to it. What truncation
costs is the ability to name *which* check, which is what the report's row asserts.

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

**A red check is not evidence that the bump caused it.** Phase 6 reports check
conclusions, and a failing required context is the row most likely to carry the
verdict — so it is the one that must not assert more than it established. "This
check is red" is established. "This bump broke it" is a *causal* claim, and
nothing above tests it.

Ask whether it was already red **on the commit the bot branched from** — which is
the parent of the bot's own commit, not the merge base:

```bash
PARENT=$(git rev-parse "pr-<N>^")
gh api "repos/$OWNER/$NAME/commits/$PARENT/check-runs" --paginate \
  --jq '.check_runs[] | "\(.name)\t\(.conclusion)"'
```

**`pr-<N>^` and `$BASE_SHA` are the same commit for a genuine one-commit bot PR**,
which is the ordinary case — so this costs nothing there and is the right answer
when they differ. When they differ, `$BASE_SHA` attributes to the bump
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
of them is the merge base. Read `$BASE_SHA` as a cross-check, not as the input:
if it disagrees with `pr-<N>^`, the branch has commits the bot did not write, and
that is worth reporting on its own.

**Use the check-runs endpoint, not `gh run list`.** `gh run list --json name`
returns the *workflow* name — one row reading `CI` — while the contexts in the
rollup above are **job** names like `test (ubuntu-latest)`. Matching a job name
against a workflow name yields nothing, for every matrix job, and an empty result
here is indistinguishable from no run at the base — which lands in the third
state below and marks every matrix failure underivable. Verified: on a repo whose
five contexts are `Test (Python 3.11)` … `Lint & type-check`, `gh run list --json
name` returns a single `CI`, and `check-runs` returns all five by their context
names. Add `commits/$PARENT/status` if the red context is a `StatusContext`
rather than a `CheckRun` — the two live in separate lists, and Phase 6's GraphQL
reads both.

Then label the row, in three states and never two:

| At `pr-<N>^` | Label | What it means for the verdict |
|---|---|---|
| the same check is green | **attributable** | the bump is implicated; this row can carry a Hold |
| the same check is red | **pre-existing** | the tree the bump landed on was already red. A real finding, a *different* one, and it must not produce a Hold on this bump |
| **no run at the base**, or no check by that name | **underivable**, per Phase 0 | say so rather than defaulting to attributable |

**When `pr-<N>^` has no runs at all, fall back to `$BASE_SHA` and weaken the
claim out loud.** An intermediate commit of a multi-commit branch is often never
built — CI ran on the head and nowhere else — so the parent has nothing to
compare against while the merge base, being on the default branch, does. That
fallback answers a *different* question and the label has to say so:

| Compared against | What a red result establishes |
|---|---|
| `pr-<N>^` | it was red **before this commit** — attribution to the bump |
| `$BASE_SHA` | it was red **before this branch** — everything below the bump is inside the claim |

Reaching for the second is legitimate and better than reporting nothing; passing
it off as the first is the failure. Observed on this plugin's own PR #26:
`pr-26^` is an intermediate commit of the branch and carries zero check runs.

**The third row has two more causes and they look identical.** The commit may
predate the workflow or its run may have aged out — or the check may simply be
*named* something else there. Names drift: `mdcat`'s `main` now reports `test`
and `test-windows` where the PR reports `test (ubuntu-latest)`, so a name match
against a distant commit finds nothing and reads as "never ran". Compare the
whole name list, not just the one you are chasing.

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
