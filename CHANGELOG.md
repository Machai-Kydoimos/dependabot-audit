# Changelog

Every release is tagged, and every tag is annotated. `git log` carries the full
reasoning behind each change; this file is the readable index of it.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). This
plugin's public surface is its **procedure**, not an API, so the versioning rule
is: a change to what a phase verifies, or to what the report asserts, is a minor
bump even when no code moved. A fix that only makes an existing claim true is a
patch.

## [Unreleased]

### Decided

- **The evidence split is dropped**, closing 0.15.0's *Not done, deliberately*.
  It was held pending something that could detect a narrative turning out to be
  discriminating rather than motivating. Measuring it first is what settled it
  instead.

  Every narrative block left in `SKILL.md` totals **2,260 tokens** — that is the
  figure for moving *all* of them, including the ones designated to stay. The
  defensible set is ~1,470. The ~4k projection was taken against the 970-line
  file, and 0.15.0 had already harvested most of it: `setup-nvc`, the seven green
  release runs and `cli/cli` #14124 left for `actions.md` with the ecosystem
  split. Of the four narratives listed to move, only the 3,682-deletion
  `Cargo.lock` story survives, at 78 tokens.

  **The classification had expired in both directions**, and that is the part
  worth keeping. *Does removing this example make the rule ambiguous?* is a
  question answered by someone who already knows the rule. The mechanical test is
  **who enforces this rule now**: `discover.py` decides `BRANCH_POINT`, so
  `cli/cli` #14049 no longer discriminates a choice the model makes, and
  `ci_state.py` picks the comparison point, so `mdcat` #6's four-point table does
  not either — both were listed as staying inline. Phase 4's ruff narrative *is*
  discriminating and was never a candidate, because `gate_diff.py` takes `--tree`
  from the caller.

  The destination was also wrong: `evidence.md` is `traps.md` renamed, and
  `SKILL.md` points at `traps.md` four times, so the move was into a file the
  per-run cost tables exclude and nothing had measured.

  Against that, ~1,470 tokens buys the property `CONTRIBUTING.md` argues for
  everywhere else — a rule sitting next to the measurement that produced it
  resists being reasoned away, which is the failure mode every entry in the
  replay table shares. Priced honestly that is ~$0.09 of a ~$1.6 run — about 5%,
  since documentation is re-read from cache on every turn, not once.

### Measured

Two cold runs against the **installed** 0.16.0 plugin, `--no-execute`, each in a
fresh session so nothing was already in context:

| | `cli/cli` #14091 | `cli/cli` #14049 |
|---|---|---|
| turns / cost | 30 / $1.61 | 27 / $1.63 |
| `actions.md` | fetched | fetched |
| `report-template.md` | fetched | fetched |
| **`traps.md`** | **never** | **never** |

Three `traps.md` pointers fire in each run — the preamble, Phase 0's
branch-protection line, and Phase 6's tail — and none was followed. Both runs
reached Phase 6 (`discover.py` twice, `ci_state.py` once each), so the phases
carrying those pointers did execute.

**Soft pointers are not followed; structural handoffs are.** *"`references/traps.md`
has the reasoning"* does not load a file. *"Method: `references/actions.md` §
Phase 1"*, in a table the phase must consult to proceed, does. That is why
0.15.0's split worked, and it is the load-bearing reason this one was dropped:
`evidence.md` would have been a document no run reaches.

Both audits were sound without it. #14049 — the two-parent maintainer merge —
came back `BRANCH_POINT=ok` with the base **not** substituted and a correct
2-file, 4-`uses:`-line scope, which is the narrative that stayed inline doing
exactly what keeping it inline was for.

### Known, not yet addressed

`traps.md` is ~4,891 tokens that no run reaches. `ci_state.py` mechanised most of
its CI-state traps in 0.14.0, so what is actually unreachable is narrow: stale
`CLEAN` right after a push, taking the *latest* run for a SHA, and a bot's own
rebase not re-triggering CI. Promoting those into Phase 6 versus retiring the
file changes what a phase verifies, so it belongs in its own release behind the
replay gate rather than in this entry.

## [0.19.0] — 2026-08-16

Phase 1's scope gate fired on a bump that never left the manifest and the
lockfile (#43). Same family as #19 and the same shape — the gate stopping the
audit for a reason that is not true, in language that reads exactly like a bump
reaching into source. #19 was a rewritten base; this is a **human commit on the
bot's own branch**.

### The replay

`fpga-board-sim` #334, `ruff` 0.15.22 → 0.16.0. Three commits above the base, and
Phase 0 already printed `HUMAN` against two of them:

| | Files gated on |
|---|---|
| 0.18.0 — `git diff $BASE_SHA..pr-334` | **8**: 6 docs, `pyproject.toml`, `uv.lock` → gate fires → Hold |
| 0.19.0 — the bot's own commits | **2**: `pyproject.toml`, `uv.lock` → gate does not fire |
| reported separately — the human commits | the 6 docs, as their own finding |

The six files are `8a5f2e130`, *"style: reformat docs for ruff 0.16's markdown
code-fence formatting"* — a maintainer landing the reformat the bump requires, on
the bot's branch, so a required check passes again. Merging it is correct.

**The signal was already derived and then thrown away.** Phase 0 reads the
authorship of every commit above the base; Phase 1 consumed none of it and gated
on the union. That is this file's forward-reference defect inverted — an output
derived early and dropped.

**It cost more than the verdict.** The gate stops the audit *before Phase 4*, and
Phase 4 was the phase that would have measured this bump: ruff 0.16.0 "can now
format Python code blocks in Markdown files and will do this by default" is this
plugin's founding Phase 4 observation occurring for real. Phase 4 measures on the
merge base precisely because a PR carrying the fixup reports no difference on its
own tree. The base worktree was built, the measurement was available, and the
gate stopped one phase short of it. Of the five PRs in that batch it is the only
one where Phase 4 had something to find, and the only one where it did not run.

**A no-op wherever the old form worked.** Replayed against #359, #355 and #332 —
ordinary one-commit bot PRs — the bot's-commits gate returns exactly the files
the merge-base diff returned, and `$HUMAN_COMMITS` is correctly unset.

### Added

- **`$BOT_COMMITS` and `$HUMAN_COMMITS`**, Phase 0 outputs. Full 40-character
  SHAs; the report still abbreviates to nine for reading.

- **A merge commit is in the human half**, not dropped. The branch-point scan
  drops two-parent commits deliberately — `cli/cli` #14049 — and reusing that
  filter here would be the obvious move and wrong: `git show` on a clean merge
  prints nothing, and on an **evil** merge prints what the merge itself changed.

### Changed

- **An empty gate list is never emitted.** `for c in $BOT_COMMITS` over an empty
  string iterates zero times, so the gate would pass *trivially* — clean rather
  than erroring, on the one phase whose whole job is to refuse. Underivable is
  emitted commented-out, and Phase 1 falls back to the whole-diff gate saying so.

### Tests

`TestTheScopeDiffIsSplitByAuthorship` (5) and
`TestTheScopeGateIsAboutTheBumpNotTheBranch` (3). Mutation-checked: emitting
unconditionally, reusing `parents == 1` for the split, keeping the `[:9]`
truncation, and the 0.18.0 Phase 1 prose — each caught by 2–3 cases.

## [0.18.0] — 2026-08-16

Phase 0 read the repo's own gate list out of the **working tree** (#44). Every
other Phase 0 output is pinned to the PR, and the lockfile reads in
`uv-lock.md` do it properly — `git show "pr-<N>:uv.lock"`. The gate read was the
one that did not.

### The replay

`fpga-board-sim` #359, merged. `ci.yml` read three ways:

| Read from | Gates |
|---|---|
| the checkout — `main` at `bddcc47` | 6, including `uv run actionlint` |
| `git show "pr-359:.github/workflows/ci.yml"` | **5** — no `actionlint` |
| the PR's merge base, `fff3eaf12` | 5 |

`actionlint` arrived in that repo's #362, three PRs *after* the one under audit.
Run in the PR's worktree it exits **2** — `Failed to spawn: actionlint` — which
reads downstream as a Phase 5 gate failure on a gate the PR never had. Exit 2 is
the status this procedure is most careful about everywhere else: "could not run",
never "ran and found something". Here the procedure manufactured one.

**Not only a replay problem.** Auditing merged PRs is supported and makes the
divergence certain — the checkout is ahead of every merged PR by construction,
and every replay CONTRIBUTING's gate asks for is one. The same gap opens on an
**open** PR whenever another branch is checked out, or the default branch has
moved since the bot branched.

### Changed

- **Phase 0 reads the gates, and the bot config, at a ref.** The bot config went
  with them for the same reason: whether a currency gap is lag or a deliberate
  hold is decided by the config in force on the PR, not by the copy in whatever
  is checked out.

- **Each phase's gate list comes from the tree that phase runs it in.** Phase 5
  reproduces in `$SCRATCH/pr-<N>`, Phase 4 measures in `$SCRATCH/base-<N>`, and
  one list served both. On #359 the two lists agree, which is the honest thing to
  report and not a reason to read only one.

- **A gate on only one side of the bump is now a finding.** Quiet in both
  directions: a gate since *removed* runs against a tree that never had it, and a
  gate the PR *adds* never runs at all. The second is the one that matters — an
  actions or tooling bump can legitimately add its own.

- **An actions bump creates no worktrees.** `actions.md` reads the diff with
  `git show` throughout, Phase 4 reads release notes, and Phase 5's substitute is
  `gh run list`, so Phase 0 was adding and Phase 7 removing two worktrees that no
  phase consumed. The fetch stays: `git show` needs the ref.

### Tests

`TestTheRepoConfigIsReadAtARef`, three guards. Mutation-checked against the
0.17.0 prose: the positive guard found no `git show` of a gate list in Phase 0 at
all, and the per-line negative guard fired on `cat .github/dependabot.yml`.

## [0.17.0] — 2026-08-15

`references/traps.md` is retired (#35). It was never fetched — measured, not
suspected — and by the time that was measured, almost nothing in it was still
load-bearing.

### The audit that decided it

Every trap in the file, checked against everything else that ships:

| Section | Where its content already lived |
|---|---|
| Installing is executing | `SKILL.md`'s execution preamble and `SECURITY.md`; the npm/Cargo/Go rows address a human with an out-of-scope repo, not a run |
| Currency and changelogs | `SKILL.md` Phase 2, in full; the `cli/cli` #13996 measurement in a test docstring |
| Behavior change | allow-list vs disable-list, hook-scope ≠ CI-scope and formatter-vs-linter in `uv-lock.md`, reachable through Phase 4's handoff |
| Lockfile shape | `audit.py` — `(name, version)` keying, artifact-moved-at-unchanged-version, forked packages |
| Registry and pinning | `audit.py` for PEP 440 epochs and provenance; `actions.md` for tag dereferencing and the two-way `compare` |
| CI state | `ci_state.py`, except three |
| Verification hygiene | `SKILL.md` Phase 5 and `uv-lock.md` |

**The file was a fossil of the pre-script era.** 0.14.0's `ci_state.py`, 0.16.0's
`discover.py`, `audit.py`'s accumulated guards and 0.15.0's ecosystem split each
absorbed a section, and nobody went back to see what was left. Exactly one rule
was unique to it and unmechanised — *"nor can you diff the two versions'
output"* — and it warns against an approach `gate_diff.py` makes impossible.

### Added

- **Three CI-state traps promoted into `SKILL.md` Phase 6**, the only run-relevant
  content at stake. Each is about whether an answer is *current* rather than how
  to read it, which is why no script covers them: a merge state reading `CLEAN`
  on stale checks, taking the **latest** run for a SHA where `cancelled` is not
  `failure`, and a bot's own rebase not re-triggering CI — so a green may belong
  to the commit before the rebase.

- **The founding Phase 4 measurement is inline** rather than pointed at: ruff
  0.15.22 → 0.16.0, identical exit 0, **33 more files** formatted. With the reason
  diffing the two versions' *output* does not rescue it.

- **Phase 0 states both endpoint failures instead of deferring them** — the bare
  `404` needing `admin`, and `rules/branches/<b>` reporting rulesets only.

### Changed

- **The cross-ecosystem execution table moved to `SECURITY.md`**, which already
  had that audience and that section. It is a warning for a human arriving with
  an out-of-scope repository, not a rule on any run's path.
- **The four-state branch-protection table moved to `CONTRIBUTING.md`**, beside
  the ruleset-vs-classic mirror measured in 0.16.2.
- **Five soft pointers removed** — four in `SKILL.md`, one in `uv-lock.md`.

### Measured

**This costs tokens; it does not save them**, and saying otherwise would invert
the finding:

| | 0.16.2 | 0.17.0 |
|---|---|---|
| `SKILL.md` | ~11,660 tok | **~12,104** |
| a `uv.lock` run | ~16,743 | **~17,191** |
| an actions run | ~16,000 | **~16,444** |

A retired file that was never loaded frees nothing at run time. What changes is
that three traps which *could not fire* now always do, at +448 tokens, and 4,891
tokens of prose stop being maintained, tested and shipped for no reader.

**No rule was lost, verified mechanically rather than asserted.** All 40
rule-bearing spans extracted from `traps.md` before the change were located in
the shipped corpus after it — `SKILL.md`, the two ecosystem references,
`SECURITY.md`, `CONTRIBUTING.md`, `README.md`, or a script that enforces them.

### Tests

201, unchanged. The prose suite needed no new guard: `TestEverythingTheProseNamesExists`
already fails on a named path that does not exist, which is what would catch a
missed pointer.

## [0.16.2] — 2026-08-15

The repository went public. Four statements that were accurate about a private
free-org repo stopped being accurate the moment it flipped, and one question the
plugin has argued about for six releases became measurable for the first time.

### Changed

- **CI is the enforcing gate; the hooks are the fast local pre-check.** A ruleset
  on `main` requires `Lint & type-check` and all four `Test (Python 3.x)` legs.
  The README said the reverse, correctly — rulesets and branch protection are
  unavailable on a private free-org repo (`403 Upgrade to GitHub Pro or make this
  repository public`), so nothing could be marked required and the hooks carried
  the whole load.

- **`SECURITY.md` points at GitHub private vulnerability reporting**, now enabled.
  It previously said an issue *was* a private report, which was true while only
  org members could see the repo and became false on the flip.

- **The install paragraph no longer asks for credentials**, verified rather than
  assumed with a credential-free anonymous clone — `GIT_TERMINAL_PROMPT=0`, no
  credential helper, global and system git config discarded. Exit 0.

### Measured

**A required check that never reports blocks the merge and is invisible to the
auditor — both, at the same time.** Measured on PR #37 by requiring a context
(`Test (Python 3.99)`) that can never report:

| | Baseline | With the unsatisfiable requirement |
|---|---|---|
| contexts in the rollup | 5 | **5** — the missing one produces no row |
| every listed `isRequired` | true | true |
| `statusCheckRollup.state` | SUCCESS | **SUCCESS** |
| `mergeable` | MERGEABLE | MERGEABLE |
| `mergeStateStatus` | CLEAN | **BLOCKED** |

The repository's view is loud; the auditor's view is silent. A procedure reading
`isRequired` and the rollup sees five of five required checks green and reports
all-clear on a PR GitHub will refuse. That is exactly why Phase 6 reads
`mergeStateStatus` alongside the rollup, and it is the first time the claim has
been tested on a repository where the right answer was known in advance rather
than inferred from someone else's PR.

The genuinely silent variant is the inverse and nothing here catches it: rename a
job *and* update the ruleset but miss a leg, and that leg still runs, still
reports, is no longer required, and nothing says so. Recorded in
`CONTRIBUTING.md` as a hand-check.

- **`references/traps.md` gains the mirror of its own endpoint case.** It already
  recorded classic protection making `rules/branches/<b>` return `[]` on `mdcat`.
  Measured here at `admin`, ruleset active, no classic protection:
  `rules/branches/main` reports the three rules, and `branches/main/protection`
  returns `404 Branch not protected` about a branch requiring five checks. That
  404 is the *distinguishable* one, not the bare `404 Not Found` that means you
  lack `admin`, so permissions do not explain it. Neither endpoint answers "what
  gates this branch"; each answers for its own mechanism and returns a confident
  nothing about the other.

### Note on the version

A **patch**, by this file's own rule. Nothing changed about what a phase verifies
or what the report asserts — the procedure is untouched. Going public is not a
procedural event, which is the same reasoning recorded on #17 for why the flip
itself earns no bump.

## [0.16.1] — 2026-08-15

### Fixed

- **Every description now names the covered ecosystems.** `plugin.json`,
  `marketplace.json`, `SKILL.md`'s frontmatter and the command's all promised to
  *"audit an automated dependency-bump PR"* and named no ecosystem, while the
  plugin verifies exactly two. `README.md` has always been scrupulous about this
  — a table naming both, then a section on why npm, Cargo and Go are out of scope
  rather than unimplemented — but the descriptions are what a marketplace listing
  shows, so the first place a user learned otherwise was `audit.py` exiting 2 on
  their lockfile.

  The added clause is *"verifies uv.lock and GitHub Actions end to end; any other
  ecosystem gets the ecosystem-independent phases and a stated boundary"*. The
  second half is deliberate: an out-of-scope PR is meant to load the skill and
  receive Phase 0's classification, Phase 6's CI state and a named boundary. A
  refusal with reasons is the product. So the trigger list in `SKILL.md`'s
  frontmatter is left broad rather than narrowed to the two ecosystems — stating
  coverage should not suppress the match.

  A patch by this file's own rule: nothing changed about what a phase verifies,
  only about whether an existing claim was true.

  Considered and rejected: **renaming the repo.** The name is not where the
  over-promise lives. It under-claims on the bot axis if anything — Renovate is
  covered too — and encoding coverage in the identifier puts the most volatile
  fact in the least changeable place, given `CONTRIBUTING.md`'s ecosystem rule is
  conditional on having a repository to test against rather than a permanent
  closure. Domain in the name, coverage in the description, evidence in the
  README.

## [0.16.0] — 2026-08-15

Phase 0 was the last large block of prose asking a reader to hold a three-state
discipline in their head, and both defects that have ever shipped in it were in
**one output**: `$BASE_SHA`. A rewritten base sent `git merge-base` nineteen
months too far and presented a two-file bump as fourteen files and 3,682
deletions; a merged PR collapsed the base onto the head, so Phase 1's diff came
back empty, Phase 4 measured the PR against itself, and Phase 6 cross-checked the
head against itself. Neither raises.

### Added

- **`scripts/discover.py`.** Derives every Phase 0 output and tags each
  **derived / absent / underivable**, proves whether the merge base is the branch
  point rather than assuming it, and decides whether Phases 4 and 5 are
  authorised.

  **Read-only** — no fetch, no worktree, no local `git` at all. The merge base
  comes from GitHub's `compare` endpoint, which is right whether or not the PR
  has landed, so no phase runs a local merge base any more. The two things Phase
  0 changes in the user's repository stay visible in `SKILL.md`, where a plugin
  whose contract is "reports, never merges" should keep them.

- **`--shell`, so the outputs are sourced rather than transcribed.** Four of them
  are 40-character SHAs and a wrong one is not detectable downstream: a truncated
  `$HEAD_SHA` matches no CI run and reads exactly like *CI never ran*. An
  **underivable output is emitted commented-out** so the variable stays unset —
  a later phase then fails loudly on an empty value instead of quietly on a
  plausible one, which is the distinction the whole phase exists to preserve.

### Fixed

Two defects in the new script, both found by replaying rather than reasoning,
and the second created by the fix for the first:

- **The corroboration scan fired on every human PR.** "A non-bot commit above the
  base" is the signal that a bot PR has been tampered with — and on a *human* PR
  it is the definition of the PR. Replaying this plugin's own #26: five human
  commits, no force-push, reported `SUSPECT` on a branch nobody had touched.
  Applied to human PRs it manufactures a finding on every one, which is the
  fastest way to train a reader to skip the row that matters.

- **Suppressing it left the explanation false.** The `ok` verdict then fell into
  a branch reading *"every commit above the base is the bot's"* — on a PR with no
  bot commits at all. A correct verdict carried by a false sentence is the same
  family as a red check reported without its attribution: every cell true except
  the one doing the work.

### Changed

- **Phase 0 lost the prose the script now enforces**, 305 → 238 lines. Three of
  the four cuts were material that had already stopped being true:

  | Cut | Why it was stale |
  |---|---|
  | the `git merge-base` collapse worked example | no phase runs a local merge base any more |
  | `gh pr view --json files` as a cross-check | `files` stopped being fetched in 0.12.0 |
  | branch protection, ~33 lines | the required checks moved to Phase 6 in 0.14.0; `traps.md` still carries the four states those endpoints return |

- **Two Phase 0 guards re-pointed at `reachable(0)`** and tightened. One asserted
  on `merge_base_commit|baseRefOid`, which kept passing when the compare call was
  removed — because `baseRefOid` survived as a *display label* in the script's
  own output. The label was also simply wrong: the script reads REST `base.sha`,
  not the GraphQL field. Renamed, and the guard now asserts on the field alone.

### Measured

| | v0.11.0 | v0.15.0 | 0.16.0 |
|---|---|---|---|
| a `uv.lock` run | ~18,600 tok | ~17,500 | **~16,700** |
| an actions run | ~18,600 tok | ~17,500 | **~16,000** |
| `SKILL.md` alone | ~13,100 tok | ~12,500 | **~11,700** |

−10% and −14% against where this round started, which is less than the file
shrank: `SKILL.md` went 970 → 828 lines while *gaining* the verdict table, the
confidence rule, the truncation guidance and two script handoffs. The
documentation got smaller and said more.

### Note on the mutation harness

Three mutation runs in this release reported "not caught" against a defect the
tests do catch. The cause was stale `__pycache__` — the mutated module was
edited, and the test imported the previously compiled one. A verification method
that silently checks the wrong artifact is the same failure the suite exists to
find, one level up. Clear `__pycache__` between mutations, or run with `-B`.

### Tests

182 → 201.

## [0.15.0] — 2026-08-15

Every run paid for both ecosystems. A `uv.lock` bump loaded the whole GitHub
Actions recipe and an actions bump loaded the whole PyPI one, and roughly a third
of the documentation a run carried was guaranteed irrelevant before it started.

**No rule was removed. Only its location changed.** Verified mechanically rather
than asserted: extracting every non-comment command line from `SKILL.md` and
`ecosystems.md` at v0.14.0 and from all four documents now gives **0 commands
gained and 0 genuinely lost** — the two the diff flags are the same `uv sync`
pair, which `ecosystems.md` and `SKILL.md` each carried with different trailing
comments.

### Changed

- **`references/ecosystems.md` is retired, split into `references/uv-lock.md`
  and `references/actions.md`**, each sectioned by phase. `SKILL.md`'s Phases 1
  through 5 now carry the phase's *question* and its gate, and hand off to the
  section for the ecosystem in front of them.

  Sectioning by phase is a constraint, not tidiness. The prose suite attributes a
  command to the phase whose heading it sits under, and that attribution is the
  check which has caught three shipped forward-reference defects. A section
  retitled out of that shape takes its guard with it.

- **What stays in `SKILL.md` is anything that must fire without a reference being
  fetched**: the read-only contract, the execution warning, Phase 1's gate, the
  Phase 0 outputs table and the three-state rule, and Phase 7's verdict
  derivation. A rule in `SKILL.md` is *guaranteed* loaded; a rule in a reference
  loads only if the pointer is followed. That is acceptable for a recipe and not
  for a gate.

- **The cross-ecosystem "installing is executing" table moved to `traps.md`**,
  where it belongs: it is a warning for a reader who arrived with an out-of-scope
  repository, not a rule on this plugin's own path.

### Added

- **A guard that every handoff lands.** Two halves, because they fail
  independently: each split phase must name both ecosystem references, and each
  named reference must actually have the `## Phase N` section it is pointed at.

  This is the risk the split creates and the reason it is worth gating. Moving a
  method converts it from *text the model already has* into *text the model must
  go and fetch*, and a pointer into a section that does not exist leaves a
  question, a promise, and nothing to answer it with — where the likeliest
  recovery is improvising a method, which is exactly what the ecosystem boundary
  exists to prevent.

- **`material(n)` beside `reachable(n)` in the prose suite.** Guards about what a
  phase *says* read the first; guards about what it *calls* read the second.
  Keeping them apart is what stops a negative assertion firing on a paragraph
  that warns against the very thing it forbids — which is how the first version
  of `reachable` failed the `/protection` guard on the prose explaining why never
  to call it.

### Measured

Per-run documentation cost, `SKILL.md` + one ecosystem + the report template:

| | v0.14.0 | 0.15.0 |
|---|---|---|
| a `uv.lock` bump | ~20,700 tok | **~17,500 tok** (−15%) |
| an actions bump | ~20,700 tok | **~16,800 tok** (−19%) |

Less than the ~25% projected, and the reason is worth recording rather than
rounding away: the two largest blocks left in `SKILL.md` are Phase 0 (284 lines)
and Phase 6 (179), both **ecosystem-independent**, so an ecosystem split cannot
reach either. The remaining saving is in relocating motivating narrative and in
mechanising Phase 0 — both still ahead, and both carrying more risk than this one
did.

### Not done, deliberately

The **evidence split** — moving motivating case narratives out of `SKILL.md` —
is held. The ecosystem split extends a pattern with a track record: the plugin
has depended on the model following pointers into `ecosystems.md` for many
releases. Moving *justification* away from *rules* is a different bet, and a
narrative that turns out to be discriminating rather than merely motivating goes
missing silently. With `claude plugin eval` still unavailable (#32) there is
nothing that would detect it.

### Tests

179 → 182.

## [0.14.0] — 2026-08-15

Phase 6 was 188 lines of prose carrying **three of the seven** defects that have
shipped in `SKILL.md`, and all three were the same mistake: a real endpoint asked
the wrong question, answering in a well-formed way. A hand-run query cannot be
regression-tested. This file's own rule — *"a trap a script refuses cannot be
skipped, one in prose is silently skipped"* — has been applied to forked-package
disclosure since 0.9.0 and nowhere else.

### Added

- **`scripts/ci_state.py`**, and Phase 6 now invokes it. It pages the rollup to
  exhaustion, reads `isRequired` / `mergeStateStatus` / `reviewDecision`, merges
  the check-run and status lists at the comparison commit — they are separate,
  and reading one answers correctly about half the possible reds — and labels
  every red context **attributable | pre-existing | underivable**.

  It stops short of a verdict deliberately. That mapping is Phase 7's table, and
  putting it in two places is how the two drift.

  Exit codes match the other scripts: `0` clean, `1` found something, `2` could
  not run, with the `cli()` backstop so an unhandled exception cannot exit 1 and
  read as a red required check.

- **23 → 24 cases in `tests/test_ci_state.py`**, every one mutation-checked
  against a broken implementation. The first round caught 8 of 9 mutations; the
  miss is recorded below because it is more interesting than the hits.

### Fixed

- **The script collapsed three states into two on its own first live run.** With
  zero required contexts it read `blocked` as a boolean, which is False both when
  the merge state is genuinely clear *and* when it was never established. So it
  printed these four lines apart, on this plugin's own #26:

      !! mergeStateStatus is UNKNOWN ... *not established*, not 'nothing blocks'
      -- zero required contexts, and nothing blocks: this repo enforces nothing

  The second asserts exactly what the first says was never established — the
  collapse the whole discipline exists to prevent, reproduced inside the script
  written to enforce it. "This repo enforces nothing" is a strong claim about a
  repository, and it needs the merge state to have been *read*, not merely to be
  un-blocking.

  Found by replaying, not by reasoning. The unit suite was green.

### Changed

- **`tests/test_skill_prose.py` follows a phase into its script.** Six guards
  asserted on `self.shell[6]`; moving the query out would have left every one of
  them green against an empty string. `reachable(n)` now returns the phase's
  shell **plus the code of every script it names**, so the property survives
  relocation while staying the same property.

  Two corrections to that, both caught by mutation-checking rather than review:

  1. The first version concatenated the phase *body* and immediately failed the
     `/protection` guard — on Phase 0's paragraph explaining why never to call
     that endpoint. A negative assertion over prose cannot tell a warning from an
     instruction, so it fires on the document that gets it right. Executable
     material only.
  2. Scripts contribute their **code**, never docstrings or comments.
     `ci_state.py`'s module docstring names `isRequired`, `totalCount` and
     `check-runs` while explaining them, so deleting all three from the actual
     query left every guard green. A rule must not be satisfiable by a comment
     claiming it.

- **The parent-attribution guard asserts what Phase 6 *hands* the comparison**,
  not that `pr-<N>^` appears somewhere reachable — `ci_state.py` spells it in the
  basis text it prints, so a phase that derived the parent wrongly and described
  it correctly passed the looser form. It now also refuses `--parent "$BASE_SHA"`,
  which is defect #25 exactly.

### Verified

Replayed against both PRs the method comes from, since CONTRIBUTING records that
one cannot reach what the other does:

    BIRSAx2/mdcat #6          rollup FAILURE, mergeStateStatus DIRTY
      test (ubuntu-latest)    FAILURE
        -> PRE-EXISTING — red at b1b0dd4c1 (pr-<N>^) too
      exit 1

    dependabot-audit #26      rollup SUCCESS, mergeStateStatus UNKNOWN
      5 of 5 contexts, 0 required
        -> UNDERIVABLE, not "nothing enforced"
      exit 0

The first is the false-Hold case the section exists for, answered correctly. The
second is what surfaced the three-state defect above.

`SKILL.md` is 1083 lines — Phase 6 fell 188 → 179, less than the mechanism
removed, because what stays is the *reading* guidance the script cannot carry.
The restructure that shortens the file is separate and still ahead.

## [0.13.0] — 2026-08-15

Every phase was rigorous about establishing evidence, and then the step that
turns evidence into a recommendation was left entirely implicit. Two audits with
identical findings could reach different verdicts and neither report would show
where they diverged.

### Added

- **Phase 7 derives the verdict from a table rather than from judgment.** The
  three verdicts had one-line definitions — "Hold — a discrepancy, a regression,
  or a behavior change that breaks a gate" — which do not decide the cases the
  procedure works hardest to establish:

  | Case the old wording did not cover | What it now produces |
  |---|---|
  | a red required check labelled **pre-existing** | not a Hold *on this bump*; a separate finding, and the PR is unmergeable until someone fixes it |
  | Phase 4: base differs, PR agrees — real and absorbed | **Merge as-is**, naming what the PR absorbed |
  | `mergeStateStatus: BLOCKED` with everything green | **Merge as-is** on the bump's merits; name what blocks |
  | a gap **inside** the cooldown window | **Merge as-is**, and explicitly *no* follow-up |
  | an actions tag rolled **behind** | **Hold**, close the bot's PR, replace by hand |

  The pre-existing row is the one that mattered most. Phase 6 has said since
  0.10.0 that such a check "must not produce a Hold on this bump", and nothing
  downstream consumed the label — so the rule existed in the phase that derives
  it and not in the phase that acts on it.

  Replayed against `BIRSAx2/mdcat` #6, the PR the rule comes from:

      head   65bfd8e  failure test (ubuntu-latest)  success lint  success test (windows-latest)
      parent b1b0dd4  failure test (ubuntu-latest)  success lint  success test (windows-latest)

  Red at both points, so **pre-existing**, so not a Hold on the bump — while the
  report still has to say the PR cannot merge. Two things to carry at once, which
  the report template now spells out, because reporting only the first reads as
  "merge this" on a PR that will not merge and reporting only the second blames
  the bump.

- **A precedence order for when phases disagree.** Phase 1's gate, then changelog
  `Security`, then OSV/GHSA, then Phase 4's measurement, then Phase 5, then Phase
  6. Disagreement is the designed case rather than a problem: a privately
  disclosed fix ships with no CVE, so *clean scanner, dirty changelog* is the
  expected reading and the reason Phase 2 reads changelogs at all. That was
  stated in one place about one pair and never generalised.

- **Confidence is now a function of what could not be derived.**
  `report-template.md` had asked for `high | medium | low` since the beginning and
  nothing anywhere defined it — the report's most visible field was its least
  falsifiable. It now reads off the three-state discipline the rows already
  carry: **high** when every verdict-bearing input was derived and the executing
  phases ran, **medium** when something underivable sits outside the verdict's
  path or `--no-execute` left a Phase 4-shaped question open, **low** when an
  input that would *change* the verdict could not be established — and then it
  has to name which.

  "Verdict-bearing" rather than "present in the table" is load-bearing: an
  underivable row no verdict rule reads must not lower confidence, or the field
  becomes noise and the reader learns to discount it.

### Tests

149 → 154. Five guards, mutation-checked against the pre-change prose: that the
precedence is stated, that a pre-existing red does not carry the verdict, that
the cooldown distinction reaches the verdict table, that confidence is defined in
terms of underivable inputs, and that the report template carries the same rule
rather than letting it drift from `SKILL.md`.

## [0.12.0] — 2026-08-15

A review pass over the whole plugin rather than a round of replays, so the
findings are structural: two of them are places where a rule was written into a
phase that the paths most needing it never reach, and one is a claim the preamble
made that the mechanism underneath it cannot support.

This release **adds** to `SKILL.md` (970 → 1053 lines). That is the wrong
direction and is deliberate for now: correctness first, relocation second. The
restructure that halves it is scheduled and gated on the eval suite existing, so
that "did a rule stop being followed when it moved" is a measurement rather than
a hope.

### Fixed

- **The cleanup ran only on the path that needed it least.** Phase 0 registers
  two worktrees and a `pr-<N>` branch in the **user's** repo, and the block that
  removed them lived in Phase 5. `--no-execute` skips Phase 5, and Phase 1's gate
  stops before it — so an audit that correctly refused to run an unexpected diff
  left litter behind, one set per PR audited, while an audit that ran to
  completion cleaned up after itself. Exactly backwards.

  The prose already said so, two lines above the block that never ran: *"The
  branch outlives an audit that stopped before Phase 5, too."* It was recorded and
  not acted on.

  Cleanup now lives in Phase 7, which is the only phase every audit reaches — a
  Phase 1 stop still writes a report, because stopping there "is not a failed
  audit… it reached a verdict early".

- **`contexts(first:100)` was read as the answer rather than as a page.** A repo
  reporting more than a hundred contexts returns the first hundred and says
  nothing about the rest, so a required check at position 101 is absent from the
  list — indistinguishable from one that passed. That is the same failure as the
  hand-written required-list join that `isRequired` was introduced to replace,
  reproduced one level up.

  The query now selects `totalCount` and `pageInfo`, and the prose gives an
  unpaged `totalCount > 100` Phase 0's third state: **underivable**, not
  complete. Verified live against `cli/cli` #14148 — `totalCount=25`,
  `hasNextPage=false`, 25 returned, 3 required — which confirms the fields exist
  and are accepted, on a PR small enough that nothing was being truncated.

- **`gate_diff.py` invented a file path and dropped a real one.** `git status
  --porcelain -z` emits a staged rename as *two* NUL-delimited fields,
  `R  <new>\0<orig>\0`, and only the first carries the `XY ` status prefix.
  Slicing three characters off every field turned `tracked.txt` into `cked.txt`:

      field='R  renamed.txt'  -> line[3:]='renamed.txt'
      field='tracked.txt'     -> line[3:]='cked.txt'

  So a run reported `cked.txt` as deleted — a path that never existed — while the
  real deletion of the source went unreported. Both halves fail in the reporting
  direction this repo cares about: a change invented, and a change dropped.

  The old comment claimed "a rename shows as delete + add", which is true of the
  **unstaged** case only (` D a` + `?? b`, two entries). Git detects renames in
  the index, and `restore()`'s own docstring already names `pre-commit` as a gate
  that stages directly. Measured against git 2.55.0; both shapes are now in the
  docstring.

### Changed

- **Phase 0 switches to `--no-execute` when `$PERMS.push` is false.** The
  classification already refused to execute a cross-repository or non-bot PR;
  it never asked whether this was a repository you control. A PR you cannot merge
  is one whose code you had no plan to run, and the usual defence — "CI would run
  it anyway" — stops holding there: CI runs it in a fresh container with a scoped
  token, and this procedure runs it on a workstation with the auditor's
  credentials in the environment.

  Replayed: `cli/cli` and `BIRSAx2/mdcat` are both `pull`-only for this account,
  `dependabot-audit` is `push: true`. Which has a consequence for this repo's own
  process, now recorded in CONTRIBUTING — the documented replay targets no longer
  execute by default, so a Phase 4 or Phase 5 method change replayed against them
  exercises everything except the phase being changed.

- **`$PERMS` gets the three-state treatment, found while replaying the above.** A
  failed `repos/:owner/:repo` call writes its error body to stdout, so the capture
  succeeds and `$PERMS` holds `{"message":"Not Found",…}` — at which point `push`
  is not `true` and reads exactly like a pull-only account. The **exit code is 1**,
  which is what separates this from the branch-protection trap where the same
  shape arrives at exit 0, so the derivation now gates on the call rather than the
  value. Failing closed is right; reporting "you lack `push` here" when the audit
  could not tell is not.

- **The preamble no longer claims the ordering catches a bad dependency.** Phase 1
  compares the lockfile against what the registry serves *today*, so a maliciously
  published release passes it clean: the record and the lockfile agree, and
  agreement is the entire test. `traps.md` has said this for several releases
  while the preamble asserted otherwise two screens above it. The gate catches a
  lockfile edited after it was written honestly, and a diff reaching into source.
  PEP 740 `PUBLISHER CHANGED` is the only signal that speaks to the other case,
  and its coverage is partial.

- **Phase 0 derives both SHAs from one call.** `headRefOid` and `baseRefOid` were
  two separate `gh pr view` invocations, which can straddle a bot rebase and pin a
  head and a base that never coexisted — with nothing downstream able to tell,
  because each is individually a real commit. Also dropped `files` (GitHub
  computes it from the merge base, so it agrees with a rewritten one rather than
  correcting it, and `SKILL.md` already warned against using it) and
  `mergeStateStatus` (computed lazily; Phase 6 reads it fresh).

- **`claude plugin eval` is still unavailable, and the three places that say so
  now say how.** The subcommand exists in the CLI and prints a complete `--help`
  — graders, ablation arms, cost ceilings, thresholds — which reads exactly like
  a usable feature. Invoking it, on both `init` and the run path, prints
  ``plugin eval` is currently in early access` and does nothing, **at exit 0**.

  So a CI step added on the strength of the help text goes green while running
  nothing: the silent-failure shape this whole repo is organised around, arriving
  in the tool meant to close its largest gap. The first draft of this release
  asserted the opposite, from reading `--help` rather than running it — which is
  the same error one level up, and is why CONTRIBUTING now says to verify by
  invoking.

### Tests

139 → 149. Seven new prose guards (cleanup placement, context truncation, the
permission switch, and the scoped execution warning) and three for the staged
rename. All ten mutation-checked against the pre-fix artifact.

One of the seven did not discriminate on the first attempt and the mutation check
is what caught it: a guard asserting `underivable` appears in Phase 6 passed
against the defective prose, because that word was already there for
`mergeStateStatus: UNKNOWN`. It now asserts on the table row that reads
`totalCount`. This is the second time that exact trap has been recorded — the
first is in `test_a_base_with_no_run_is_underivable_rather_than_attributable` —
which is an argument for the mutation check rather than for reading more
carefully.

## [0.11.0] — 2026-08-15

Findings from running the procedure against `cli/cli`. Go is out of scope and
stayed out — the Go PRs were used to test the boundary, not to audit them — but
`cli/cli`'s Dependabot queue is half GitHub Actions, which **is** in scope, and
its bot config turned out to be running against a rule this plugin had not
noticed changing.

Most of them are the same shape as the `mdcat` round: a rule that was true when
it was written and returns a confident wrong answer now. The first was found by
replaying the *fixed* Phase 1 gate across eleven bumps, which is the gate in
CONTRIBUTING doing exactly what it is for — the finding is not in the phase the
replay was checking.

### Fixed

- **`$BASE_SHA` collapsed onto `$HEAD_SHA` on any PR that had already landed.**
  Phase 0 derived it as `git merge-base "$DEFAULT" pr-<N>`, and a merged PR's head
  *is* an ancestor of the default branch — so the merge base of the two is the
  head itself. Nothing raises, and three phases downstream quietly answer a
  different question than the one they were asked:

  | Phase | What it did with `$BASE_SHA == $HEAD_SHA` |
  |---|---|
  | 1 | scope diff against the head: **empty**, so the gate passes on a diff it never saw |
  | 4 | `base-<N>` checked out at the head, so the differential measures the PR's tree against itself — reinstating the defect 0.6.0 exists to prevent |
  | 6 | the cross-check compares the head with itself, so every red check reads **pre-existing** |

  Phase 0's own branch-point proof does not catch it either: `git log
  "$BASE_SHA..pr-<N>"` over an empty range finds no non-bot commit above the base,
  so the check passes.

  Measured on `cli/cli`'s merged bumps #14147, #14091, #13981 and #14049:
  `git merge-base trunk pr-<N>` returns the head for all four, and the scope diff
  is **0 files** where GitHub reports 4, 2, 3 and 2. Taken from `baseRefOid` — the
  base commit GitHub itself diffs against — it is those four numbers exactly, and
  on open PR #14148 both forms return the same commit, so the correction is a
  no-op wherever the old form already worked.

  This is not an edge case dressed up as one. Auditing a merged PR is supported —
  Phase 6 has a row for `mergeStateStatus: UNKNOWN`, `ecosystems.md` has a
  paragraph on comparing against the repo's current pin — and CONTRIBUTING's
  replay gate asks for a merged PR before every method change, so the defect sat
  directly on the path this project uses to verify itself.

  It is a separate failure from the rewritten base (#19) and does not replace its
  checks: there, `baseRefOid` is the current tip of a branch that moved out from
  under the PR, and `merge-base` still walks back too far.

  Found while replaying the corrected Phase 1 gate across eleven `cli/cli` bumps:
  ten showed a clean `uses:`-only diff and #14049 showed 20 files and 1,101
  changed lines, because its head is a human merge commit — *"Merge branch 'trunk'
  into dependabot/…"* — so the `pr-<N>^` fallback spans everything trunk brought
  in. Chasing that one row is what exposed the merge base underneath it.

- **A merge of the base branch into the bot's branch read as a rewritten base.**
  Fixing the merge base made this reachable, so it ships in the same release: with
  `$BASE_SHA` correct, Phase 0's author scan now *sees* the commits above it, and
  its rule was that any non-bot commit there means the base moved (#19). On
  `cli/cli` #14049 that commit is a maintainer's merge **of** trunk **into** the
  bot's branch — the branch point has not moved at all. Zero
  `base_ref_force_pushed` events; a correct two-file scope diff from `$BASE_SHA`;
  and the substitution the rule would trigger produces the 20-file diff above and
  halts the audit on a bump that changes four workflow lines.

  The force-push event is now the authority and the author scan is corroboration,
  split by parent count: one parent is a human commit on the branch, two is a
  merge and the substitutions must not fire. `git log` in Phase 0 prints `%p` so
  the distinction is visible rather than inferred. Trading a silent false negative
  for a loud false positive would not have been a fix.

- **Phase 2 read a configured hold as ingestion lag.** Its test was the publish
  timestamp — *if a newer version existed before the PR was opened, that is
  ingestion lag, not a deliberate hold* — and on **2026-07-14** that inference
  became wrong by default. Dependabot now withholds a version update until the
  release is **three days old**, across every ecosystem it supports, with no
  `cooldown:` block required in `dependabot.yml` and nothing in the PR body to
  say so.

  Measured on `cli/cli` #13996, opened 2026-07-28T14:06Z proposing
  `github/gh-aw-actions/setup-cli` 0.83.2 → 0.83.3 under an explicit
  `cooldown: default-days: 3`: upstream 0.83.4 had been published
  2026-07-27T09:07Z, 29 hours earlier. The bot proposed 0.83.4 itself two days
  later, in #14018. Four PR bodies were grepped for the word; it appears in none
  of them.

  The consequence was not a cosmetic mislabel. A gap read as lag earns **Merge
  as-is, then follow up** — take the newer version on a separate branch — so the
  procedure would have recommended hand-landing the release the cooldown exists
  to delay, at the exact moment the delay is doing its work. An audit whose
  thesis is *verify before you trust* proposing the bypass of a supply-chain
  control, on the strength of a rule that predates it.

  Phase 2 now reads the **age** of the gap rather than only its existence, and
  ranks a cooldown beside the yanked release and the `ignore` rule. Two clauses
  came with it. The `ignore` rule can be written `dependency-name: "*"` and
  scoped by `update-types` — `cli/cli`'s gomod block holds every major of every
  package that way — so a search for the dependency's own name finds nothing
  while a rule covers it. And the cooldown's exemption is for Dependabot's
  *security updates*, the advisory-driven kind: a version update whose changelog
  carries a **privately disclosed** fix is held like any other, which puts this
  procedure's highest-value finding squarely inside the three-day window where
  no timestamp will point at it.

- **Phase 1's scope gate refused the ordinary actions bump.** It read *only the
  manifest and the lockfile (or a single workflow file for an actions bump)*, and
  an action is pinned in every workflow that uses it. Measured on `cli/cli`, all
  three merged and all three ordinary: #14091 two files, #13981 three, #14147
  four — every changed line across them a `uses:` line or its trailing version
  comment.

  The gate is a **stop**, so the failure is not a warning: it halts the audit
  before Phase 4 and reports a bump reaching past the manifest, which is the same
  false finding the rewritten-base case (#19) produced and reads identically in
  the report. The invariant is the kind of line the diff touches, which holds at
  any file count, so that is what the gate now says.

- **`audit.py` blamed a valid `go.mod` for a syntax error.** 0.10.0 gave the
  ecosystem boundary a message that names the file; `go.sum` was on the list and
  `go.mod` was not, though it is the other half of every Go bump's two-file diff
  and the half whose name reads like the file Phase 1 asks for. Handed `cli/cli`'s,
  the TOML parser reached line 1 column 8 of `module github.com/cli/cli/v2` and
  reported `Expected '=' after a key in a key/value pair`. Exit 2 was already
  right; the diagnosis pointed at the reader's file instead of at the tool's
  edge. The signature requires **both** a `module` line and a `go` line, because
  it runs before the parse and one line of prose inside a TOML string is far
  likelier than two.

### Added

- **The actions recipe takes the versions under audit from the diff.** Phase 1's
  rule against reading package *names* off the PR title extends to versions here,
  where no script derives them and the prose is the whole method. `cli/cli`
  #13981 — titled *and* summarised "bump actions/checkout from 6 to 7" — moves one
  bare `@v6` pin to `@v7` **and** nine SHA pins from `v7.0.0` to `v7.0.1`. Two
  transitions; one of them described. Its embedded release notes stop at v7.0.0
  and are marked `(truncated)`, and `7.0.1` appears once in 10 KB of body, as a
  commit subject inside a collapsed `Commits` list. An auditor who takes the range
  from the PR reads the wrong release notes for nine of the eleven pins — and
  reads them for the *earlier* version, which is the direction that misses things.

- **A workflow file can be generated, and then the bot's edit does not stick.**
  Compilers that emit workflows own the `uses:` pins they write, and Dependabot
  edits the emitted file because that is where the pin lives. Merging is not
  wrong; it is transient.

  Observed on `cli/cli`, whose `*.lock.yml` workflows are generated by `gh-aw`
  from a `.md` source and carry a `DO NOT EDIT` header: #14124 merged
  `github/gh-aw-actions/setup` to v0.86.1 (`8914f47b`) on 2026-08-10, and the
  regeneration commit `ed5a99f` three days later rewrote it to `2709137e`,
  v0.85.4. `compare` reports `behind ahead=0 behind=2` — the same shape as the
  rolled-back tag already in `ecosystems.md`, arrived at from the other
  direction. The bot's own next PR, #14147, then reads the current pin as
  **0.85.4**: the version its previous merged PR had already moved past, which is
  the revert stating itself in the bot's own words.

  Detection is one `grep` for the generator's header over the files the diff
  touches, and the report should say that the durable fix is a bump of the
  generator.

Also validated, and unchanged: the default branch is `trunk`, so Phase 0's refusal
to assume `main` earns itself; `isRequired` returns 3 required contexts out of 41
at `pull`-only permission; `mergeStateStatus` is `UNKNOWN` on every merged PR, as
Phase 6 says; the attribution query reads 43 check runs by context name at the
bot's parent; and all four workflows in #14147 are `schedule`/`issues`/
`workflow_dispatch`-triggered, so Phase 6's empty-intersection case — CI green for
reasons unrelated to the diff — is the common case on an actions bump rather than
the exotic one.

## [0.10.1] — 2026-08-15

One clause, on one row. A patch by this file's own rule: it makes an existing
claim true rather than changing what a phase verifies or what the report asserts.

The rest of this window was #27's contributor-process work — the live replay is
now a gate with a checkbox in `.github/PULL_REQUEST_TEMPLATE.md` rather than a
habit. It changes nothing anyone installs, so it has no entry below; it is in
`CONTRIBUTING.md` and in the log.

### Fixed

- **`traps.md`'s `403 Upgrade to GitHub Pro…` row read as a permission symptom.**
  It sits directly beneath *"you lack `admin`"*, in a table about a
  permission-gated read, and said only "a private repo on a free plan" — so the
  natural response to it is to go chasing access that would not help. Measured on
  this repo, private on a free org, holding
  `{"admin":true,"maintain":true,"pull":true,"push":true,"triage":true}`:
  `branches/<b>/protection`, `rules/branches/<b>` and `rulesets` all return the
  same 403. The row now carries the one clause neither it nor Phase 0 had — the
  plan gate survives the top permission tier, and only the plan or the repo's
  visibility changes it.

## [0.10.0] — 2026-08-15

The last three findings from the `BIRSAx2/mdcat` run. All three are the same
shape: a row that is accurate and asserts more than it established.

### Fixed

- **`audit.py` blamed itself for a bug when handed a lockfile it does not
  support.** Pointed at a `Cargo.lock` it printed `unexpected AttributeError:
  'str' object has no attribute 'get'` followed by `This is a bug, not a
  finding` — everything right except the diagnosis. Exit 2 was correct and no
  false `CLEAN` was printed; the failure-versus-finding contract held against an
  input it was never designed to see. But Cargo writes `source` as a *string*
  where uv writes a table, so the run reached `_is_pypi` and died, and the
  message sent the reader hunting for a defect that does not exist.

  It got more important with 0.8.0, not less: `uv.lock` and GitHub Actions are
  now the entire supported surface, so this message is the **boundary of the
  tool** and the first thing anyone arriving with a different lockfile sees.
  Phase 1 leads with the script, so arriving there innocently is the ordinary
  path.

  The script now sniffs before parsing and names what it found — `Cargo.lock`,
  `poetry.lock`, `package-lock.json`, `Pipfile.lock`, `go.sum`, `yarn.lock`,
  `pnpm-lock.yaml`, `pyproject.toml` — and refuses anything else whose
  `[[package]]` blocks are not uv-shaped without guessing a name. Exit stays 2;
  `This is a bug` is reserved for exceptions that are one, which is what makes
  the phrase worth anything when it appears.

  **`poetry.lock` was worse than the Rust case**, and is why the list is longer
  than the three formats the issue named. It parses, yields zero PyPI-sourced
  packages, and exits 2 saying *"either this lockfile did not change, or it is
  being compared against itself"* — a confident false diagnosis, in this
  plugin's own vocabulary, on Python's other lockfile.

  Two ordering traps found while building it, both now the reason the sniffer is
  shaped as it is. Identifying uv *first* and diagnosing second — the apparently
  safe order — lets a real `poetry.lock` through, because poetry writes a
  `[package.source]` table too; the invariant that works is that every foreign
  signature must be one a `uv.lock` **cannot** produce, checked against a real
  uv.lock's keys. And sniffing only after a `TOMLDecodeError` never sees a yarn
  v1 lockfile, whose two-line comment header is valid TOML.

### Added

- **Phase 5 says which interpreter and which fork produced its row.**
  `uv sync --locked` asserts the whole lockfile is consistent with the manifest,
  across every `resolution-markers` fork, and Phase 1 verifies **every** fork's
  artifacts against the registry. The install then materialises only the
  resolution matching the interpreter present — which need not be the highest
  pin. A green row on 3.14 therefore said nothing about whether the 3.11 fork's
  artifacts fetch or its older release installs, and nothing distinguished the
  two.

  Phase 5 already required the row to name *which install* ran. The same rule now
  applies to the interpreter, where it matters more, and `audit.py` prints the
  fork list for any selected package pinned more than once — mechanised rather
  than left to prose, per CONTRIBUTING's rule that a disclosure the report is
  merely asked to remember is one it can omit. A second `uv sync --locked
  --python <floor>` is documented as the thorough answer and as a deliberate
  escalation, worth its interpreter download only when the un-installed fork
  belongs to a package under audit.

  The Cargo instance is what made it visible — `cargo build --locked` passing on
  1.97.1 against a repo declaring `rust-version = "1.83"` — and the uv analogue
  is narrower, because uv honours `requires-python` and forks rather than
  breaking the floor. What survives the scope cut is the reproduction claim, not
  a resolution failure.

- **Phase 6 establishes whether a red required check is the bump's fault.** It
  reported conclusions and never asked. Observed on `mdcat` #6:
  `test (ubuntu-latest)` red beside two green siblings, which reads exactly like
  a dependency bump breaking one platform. It was `unresolved link to
  pulldown-cmark-mdcat` — a rustdoc intra-doc-link error under
  `#[deny(warnings)]`, failing identically on the base commit, with nothing to do
  with the dependency.

  A Hold driven by that row would have been **correct by accident and
  unfalsifiable in the report**: every cell true, the causal claim never
  established. Same family as the rewritten base (#19) and the hand-joined
  required list (#20). It is also the direction that costs least to be wrong in
  and so draws the least scrutiny — a false Hold looks conservative, so nobody
  goes back to check whether the bump was the cause.

  A red check is now labelled **attributable**, **pre-existing**, or
  **underivable** against the commit the bot branched from. A pre-existing
  failure stays a finding — the tree the bump landed on was already red — but a
  different one, and it must not produce a Hold on a bump. A red check on a
  workflow the diff never touched is a strong prior for pre-existing, and shares
  its input with the PR-reachability check added in 0.9.0.

  **The comparison point is `pr-<N>^`, not `$BASE_SHA`,** and replaying the
  original PR is what settled it. `mdcat` #6 carries a human commit under the
  bot's — a #19 case — so its four candidate comparison points disagree:

  | Commit | `test (ubuntu-latest)` |
  |---|---|
  | the bot's commit, the PR head | `failure` |
  | `pr-6^`, the human commit below it | `failure` — pre-existing, and the answer |
  | `git merge-base main pr-6` | the check does not exist there at all |
  | the base branch's tip | `success` — which would say **attributable** |

  Two of those four produce the false Hold this change exists to prevent, and
  one of them is the merge base. `pr-<N>^` *is* `$BASE_SHA` for a genuine
  one-commit bot PR, so it costs nothing in the ordinary case and is right in
  the case that is not — the same substitution #19 established for Phase 1's
  scope diff.

  Two more causes of the underivable state came out of the same replay and out
  of dogfooding the query on this repo's own PR #26. Check names drift —
  `mdcat`'s `main` now reports `test` and `test-windows` where the PR reports
  `test (ubuntu-latest)`, so a name match against a distant commit finds nothing
  and reads as "never ran". And an intermediate commit of a multi-commit branch
  is often never built at all: `pr-26^` carries zero check runs, because CI ran
  on the head and nowhere else. Phase 6 falls back to `$BASE_SHA` there and says
  which question it answered — red *before this branch* is a weaker claim than
  red *before this commit*, and passing one off as the other is the failure.

  **The obvious query for this is wrong in the same direction as the defect.**
  `gh run list --commit <sha> --json name` returns the *workflow* name, so a
  per-check match against it is empty for every matrix job — and empty reads as
  "no run at the base", marking every matrix failure underivable. Measured on a
  repo whose five contexts are `Test (Python 3.11)` through `Lint & type-check`:
  `gh run list --json name` returns a single `CI`, while
  `commits/<sha>/check-runs` returns all five by context name. Phase 6 uses the
  latter, and `commits/<sha>/status` for a `StatusContext` rather than a
  `CheckRun`, since the two live in separate lists.

## [0.9.0] — 2026-08-14

Two releases' worth of findings from the `BIRSAx2/mdcat` run, and a correction.

0.8.0 narrowed the supported surface to `uv.lock` and GitHub Actions. That made
a structural problem impossible to keep deferring: the eight phases were designed
around an immutable artifact, identified by a version, resolved through a
lockfile, installed locally and exercised by tests. Actions has a **mutable ref**,
no lockfile, no local execution and no visibility into its own transitive `uses:`.
Treating it as a thin registry was a category error — for a library the dependency
is data your code consumes, for an action it is code that runs your pipeline with
your token.

Each phase now states an ecosystem-neutral *question* and gives a method per
ecosystem, rather than applying to one and being marked N/A for the other.

### Fixed

- **GitHub Actions has an advisory database, and three places here said it did
  not.** `references/ecosystems.md` in two spots and `SKILL.md`'s Phase 1 — one
  predating 0.8.0 and two introduced by it. GHSA carries an `actions` ecosystem;
  `/advisories?ecosystem=actions&affects=<owner>/<name>` returns real advisories,
  including both against `tj-actions/changed-files`. A Phase 3 that believed the
  claim skipped a real check on every actions bump this plugin has ever audited.

  The trap underneath it is worse than the omission. OSV carries the same
  advisories but its GitHub Actions entries have no usable version ranges, so the
  obvious port of the `uv.lock` query — batch by `(package, version)` — returns
  empty. Measured: package-only returns 2 vulns, `45.0.7` (the compromised
  release) returns 0, and `0.0.0` returns 0, which a working range check would
  match. A PyPI control confirms the pattern itself is sound. Copying the house
  style here reports **clean on a known-compromised action**, which is this
  plugin's signature failure mode generated by its own idiom.

### Added

- **Phase 3 gets an actions method** — the GHSA query, the action repository's
  `archived` / `disabled` / transferred status, and the OSV version trap with the
  measurements behind it.
- **Phase 4 gets an actions method.** An action cannot be run locally at two
  versions, so reading the release notes is the method rather than the shortcut —
  which makes the second step load-bearing: a change is a finding only if this
  repo's workflows are in its scope. The table pairs each kind of change with the
  line to grep for. **"Inert here" is a result, not silence**: `actions/checkout@v7`
  blocks fork-PR checkout under `pull_request_target` and `workflow_run`, shipped
  as a plain bullet with no heading, and was genuinely inert on a repo using
  neither trigger.

  Two signals the notes alone will not give you, both observed: a coordinated
  release across every supported major is a security backport (`checkout` shipped
  v7.0.1, v6.1.0, v5.1.0, v4.4.0, v3.7.0 and v2.8.0 within 35 minutes, and only
  the backports carry `[BREAKING]`), and version-coupled actions must move
  together (`upload-artifact` v7 and `download-artifact` v8 went out eight seconds
  apart).
- **Phase 5 gets an actions method.** No local reproduction exists, so the
  substitute is run history for the workflow the bump changed, read strictly
  against the merge date — a green history predating the merge says nothing about
  the version being adopted. Three outcomes, including "reproduction is impossible
  before merge", which is a property of the change and belongs in the report.
- **Phase 6 checks whether the changed file is reachable from a pull request.**
  A workflow triggered only by `push: tags:` never runs on a PR, so its checks
  come from other workflows and none execute the changed line. Observed: a PR
  changing only `release.yml` carried three green checks, all from the repo's
  separate test workflow.
- **Phase 2 gets an actions method** — "current" is a question about the tag line,
  not the pin, because a moving major picks up releases on its own.
- **A bot's ignore state is not always in a config file.** `@dependabot ignore this
  major version` records the hold on the PR, so a dependency can be pinned
  indefinitely with nothing in `dependabot.yml` to show it. Phase 2 now lists
  closed bot PRs before reporting an unexplained gap.

### Changed

- `references/ecosystems.md` states Phase 1's real question for actions: **is the
  pin immutable?** A SHA is content-addressed and what you audit is what will run;
  a tag, branch or bare `docker://` is a promise someone else can revoke, and what
  you audit is what runs *today*. A repo that pins nothing by SHA does not have a
  stale pin — its pins are not evidence.

### Tests

118, up from 115. A new **ecosystem coverage** group asserts that no phase from 1
to 6 is written for only one ecosystem, that Phase 3 names an advisory source for
actions, and that it keeps the measured case behind the OSV trap. All
mutation-checked.

The group exists because of the correction above: **"not applicable" is an
assertion too**, and this one shipped false in three places. Marking a phase
inapplicable is the mechanism that produced the defect, which is the argument for
restating the question per ecosystem rather than gating it.

## [0.8.0] — 2026-08-14

Found by auditing `BIRSAx2/mdcat` PRs #15, #14 and #6 — the first run against a
repository this account does not administer, and the first against a `Cargo.lock`.
Both were new ground; the Cargo half is what this release acts on.

The deciding observation is that the Cargo recipe, followed faithfully, returned
matching checksums, a current `max_stable_version` and a clean OSV batch on a bump
that raised the project's minimum Rust version from 1.83 to 1.85. Nothing in that
output looked partial. `references/ecosystems.md` already warned that an
unverified verifier is worse than none, because it emits confident green output
nobody checks — this is that sentence describing the file it appears in.

Documentation only; no code moved. Under this file's versioning rule that is still
a minor bump, because it changes what the procedure claims to verify.

### Removed

- **The npm, Cargo and Go recipes.** Out of scope now, not deferred. Removing them
  rather than completing them is the point: adding the missing MSRV check would
  have corrected one bump and left the class untouched. A prose recipe is a
  verifier too, and it inherits none of the guards `scripts/audit.py` has earned —
  the Cargo OSV query written while investigating that bump had no batch cap and
  no 429 retry, which is 0.3.1 and 0.4.0 re-derived from scratch and got wrong.
  The audited lockfile's 286 crates fit under the cap; a larger one would have
  failed exactly as 0.3.1 describes.

### Changed

- **The supported surface is `uv.lock` and GitHub Actions, and it is now stated
  rather than implied.** Together they are what a Python project's Dependabot queue
  actually holds — on this plugin's own test repo the bot PRs split `uv: 11` /
  `github_actions: 10`. Actions is not a partial ecosystem here: it has no
  lockfile, no artifact hash and no vulnerability database, so its recipe is the
  whole mechanical half rather than a stopgap for absent script support.
- **Phase 1 now says what to do with an ecosystem that is not covered — say so and
  stop.** Deleting the recipes without this would have left silence for a model to
  fill, and that improvisation is the exact failure the deletion exists to prevent.
  It also fails in the dangerous direction: it returns a green result rather than
  an error.
- **The `Installing is executing` table keeps its npm, Cargo and Go rows.** The cut
  runs between the half that *warns* and the half that *verifies* — a warning that
  is ignored costs nothing, where a verification that is wrong reports green.
  `cargo build --locked` running every crate's `build.rs` with no flag to stop it
  stays true whatever this plugin reads; 58 of them fired on the audited repo.
- Phase 5's install forms, the "name the form" rule in
  `references/report-template.md`, and two lines in `references/traps.md` are now
  `uv`-only. Two other cross-ecosystem mentions in `traps.md` stay deliberately:
  they illustrate general lockfile and provenance principles, and the examples are
  what show a reader those are not uv quirks.
- **Other Python lockfiles are named as out of scope too.** The script reads
  `uv.lock` specifically, so "Python" was a wider promise than it could keep —
  Poetry, pip-tools and PDM are not covered.

### Verified, unchanged

- Phase 0's pin-and-worktree discipline held across all three PRs, including one
  whose head branch had been deleted from the remote: `refs/pull/<N>/head` still
  fetches.
- Phase 2's timestamp comparison correctly *suppressed* a false currency finding.
  A release newer than the lockfile's, published after the PR was opened, is
  elapsed time rather than ingestion lag, and the rule already said so.
- `scripts/audit.py` exits 2, never 1, when handed a `Cargo.lock`, and never prints
  a false `CLEAN`. The failure-versus-finding contract held against an input it was
  never designed to see. Its *message* is wrong — it blames itself for a bug rather
  than naming an unsupported format — which the scope statement now makes worth
  fixing rather than moot.

### Not addressed here

The same run found two defects in the ecosystem-**independent** phases, which this
release does not touch and which affect `uv.lock` audits identically: Phase 1's
scope gate false-fires when the base branch has been rewritten (#19), and Phase 0's
branch-protection call requires admin, with its failure indistinguishable from an
unprotected branch (#20). #20 bears on the public flip tracked in #14 — after the
flip, most runs will be against repositories the user does not administer, which
is precisely when it misfires.

## [0.7.0] — 2026-08-14

Found by auditing `Machai-Kydoimos/fpga-board-sim` PR #99, a SHA-to-SHA GitHub
Actions bump — the first run against an ecosystem where `scripts/audit.py` does
not apply at all and the per-registry recipe is the entire mechanical half. It
held up, and it was thin in three places.

### Changed

- **The GitHub Actions recipe now prescribes what to do when the tag does not
  point at the proposed SHA.** It previously said to confirm the pin "really is
  the commit the claimed tag points at" and stopped there, so a mismatch had no
  defined next step — and the two mismatches mean opposite things.

  A two-way `compare` separates them: *ahead* means the tag moved on after the PR
  was opened, which is ordinary lag; **behind** means the tag was rolled backward
  and merging pins a commit the tag no longer covers. Only the second is a
  finding, and a bare equality check reports both identically.

  The `behind` case is the one a bot cannot fix, because retargeting would be a
  downgrade — `@dependabot recreate` does not help either. Close the PR and
  replace it by hand.

  Observed end to end: a `nickg/setup-nvc` bump proposed the branch tip
  `8bdacf7f`; upstream then moved `v1` back two commits to `48f966df`, dropping
  "Bump ESLint version" and "Bump Actions SDK". `compare` reports the proposal two
  commits *ahead* of the tag. The audit reached that conclusion from the API
  before reading the maintainer's own explanation, which says the same thing.
- **The tag is documented as a claim in a comment, not part of the pin.** The
  convention is `uses: owner/action@<40-hex>  # v1`, where only the SHA is
  load-bearing and `# v1` is unverified metadata. A bump that leaves the comment
  unchanged on both sides is tracking a *moving* tag, which is what makes the
  question time-dependent.
- Auditing an old or merged actions PR now compares against **the repo's current
  pin** as well as the PR's proposal, because the mismatch may already have been
  fixed on the default branch.

### Verified, unchanged

- The annotated-tag dereference fired for real: `nickg/setup-nvc@v1` is annotated,
  and the undereferenced ref SHA matches nothing. The recipe's mandatory
  dereference step is doing exactly the job it was written for.
- Phase 1's scope rule handled a diff of one workflow file correctly, and routed
  to the recipe rather than to `audit.py`, with no lockfile to read.
- All seven required checks were green on this PR, and the correct verdict is
  still *do not merge*. Recorded in the recipe: green says the pin resolves, not
  that upstream still stands behind it.

## [0.6.0] — 2026-08-14

Found by auditing `Machai-Kydoimos/fpga-board-sim` PR #334 — the exact
`ruff 0.15.22 -> 0.16.0` bump this plugin's founding observation came from, which
made it the one PR where the right answer was already known.

### Changed

- **Phase 4 measures on the merge base, not on the PR's tree.** This is the
  difference between finding a behaviour change and missing it, and the wrong
  choice fails silently.

  A PR that already contains the fixup — someone reformatted, or re-ran the tool,
  to make CI pass — has a tree the new version is already satisfied by. Measuring
  there reports **no difference**. And a PR carrying a fixup is precisely one
  whose behaviour change was real enough that a human had to deal with it, so the
  phase returned a confident "no change" in exactly the case it exists for.

  Measured on #334, both ways, against ground truth: on the merge base, six
  Markdown files; on the PR's tree, nothing. The six were exactly the files the
  maintainer had hand-reformatted onto the bot's branch in a separate commit — so
  the run predicted the work before it existed, and the old invocation would have
  called the same bump inert.

  Phase 0 now builds `$SCRATCH/base-<N>` alongside `$SCRATCH/pr-<N>`, and Phase 4
  documents reading both: base-differs-and-PR-agrees means the change is real
  *and* handled, which is the answer you actually want and neither tree gives
  alone.

  This is the fifth defect to ship in the prose and the worst of them — the others
  stalled a run or made noise. `tests/test_skill_prose.py` gates it.

### Fixed

Both of these came from the same exercise, against PRs #359 and #355.

- **`uv sync --locked --no-build` does not work**, and 0.3.0 documented it as the
  default. `--no-build` refuses *every* source build including the project's own,
  and a project with a `[project]` table installs itself editable — which is a
  build. It fails outright:

  ```
  error: Distribution `fpga-simulator==0.20.0 @ editable+.` can't be installed
         because it is marked as `--no-build` but has no binary distribution
  ```

  uv has `--no-build-package` but no inverse, so there is no single flag for
  "build my project, nothing else". Phase 5 for Python is now two commands:
  `uv sync --locked --no-build --no-install-project` proves every dependency
  resolved to a wheel and ran no third-party build code, then `uv sync --locked`
  adds the project so its suite can run. The two-step is the better shape anyway,
  because the steps prove different things.
- **`gate_diff`'s "no run changed any file" note asserted a cause it could not
  know.** It told the operator they had "measured the wrong thing" and should
  re-run with the write mode — advice that is wrong when the write mode *was*
  given and the tree is simply already compliant with every version under test.
  That was the outcome on all three real runs. The note now names all three
  causes and hands the choice over. The report key is renamed `nothing_touched`
  from `no_write_mode` for the same reason: it now says what was observed rather
  than what it implies.

## [0.5.0] — 2026-08-14

### Added

- **PEP 740 build provenance.** Comparing a lockfile's hash against what the
  registry serves today catches a lockfile edited after it was written honestly.
  It cannot catch a bad artifact PyPI itself is serving, because then the record
  and the lockfile agree and agreement is the whole test. An attestation names the
  repository and workflow that built the file — *this wheel was built by the
  project's own CI*, not merely *this wheel is what PyPI is serving*.

  Reported as three states, never two: attested; **no attestation, which is not a
  finding** (Trusted Publishing postdates most of PyPI, and collapsing absence
  into a warning would make the row noise on most lockfiles); and a publisher that
  moved, which is a loud one.

  In `--changed-vs` mode the publisher is compared against the release being
  replaced — both versions are in the same Simple API response, so it costs one
  request and needs no external source of truth. "The previous release was built
  by the project's CI and this one was not" is the signal worth having.

  Scope: this reads PyPI's *summary* of the bundle. It does not verify the
  Sigstore signature, which would mean a dependency, and stdlib-only is
  load-bearing. The report says so — stronger than a hash echo, not independent.
- **A live-checks suite and its own CI job**, scheduled weekly and never required.
  It holds the two things the hermetic suite cannot reach, both of which have
  wanted a home:
  - the ruff `0.15.22` → `0.16.0` replay, now against a checked-in fixture — six
    Markdown files reformatted by the newer version, both exiting 0. The README
    asserted this while nothing re-ran it; it had been verified by hand, once,
    against a tree in another repository.
  - a cross-check of the computed "latest" against what the legacy endpoint still
    declares, across fourteen real projects, plus assertions that the Simple API
    still has the shape `audit.py` reads.

### Changed

- **Migrated to the Simple API** (PEP 691/700/714) from the legacy
  `/pypi/<name>/json`, whose `releases` key is its undocumented, long-discouraged
  half. It is the specified interface, the one with a stability commitment, and
  the only one exposing `provenance`. One request either way.

  Two consequences worth stating plainly. `check_provenance` gets simpler — the
  flat `files` list is already keyed on filename, which is what it matched on
  anyway. And `latest` is now **computed** rather than declared, because the
  Simple API has no `info.version`; that is what the previous release's PEP 440
  comparator was for, and what the live cross-check now guards.
- Files are attributed to releases by filename, since the Simple API carries no
  per-file version. Measured across 24,512 real files from 12 projects: 2
  unattributable, both old setuptools sdists whose filename version predates
  normalisation. An unattributed file costs a **timestamp**, never a gap entry —
  which versions exist comes from `versions`, and that is complete.
- `latest` excludes pre-releases and fully-yanked releases, and deliberately
  *includes* a release whose files could not be attributed: naming an empty
  release as latest is a visible, recoverable wrong answer, while silently
  omitting a real one is how the epoch defect hid.

## [0.4.0] — 2026-08-14

### Changed

- **A PEP 440 version key replaces the best-effort one.** The old comparator split
  on `.` and mapped any non-numeric segment to `-1`, which is correct for ordinary
  versions and put an epoch (`2!1.0`) *below* unversioned releases. The gap is
  bounded by `locked < v <= latest`, so an epoch release dropped out of it
  entirely — and the gap is what Phase 2 reads changelogs across. A version that
  vanishes from the gap is one whose `Security` section never gets read. Epochs
  exist precisely because a project changed versioning scheme, which is when its
  changelog matters most.

  The new key covers epochs, numeric release segments, the full
  `dev → a → b → rc → final → post` cycle, `1.0 == 1.0.0`, and the spelling
  variants (`alpha`/`a`, `c`/`rc`, `1.0-1`/`1.0.post1`). `_is_prerelease` is now
  parsed rather than pattern-matched, so a dev release is excluded from the gap
  for the same reason an rc is.

  This is a minor bump rather than a patch because it changes which versions the
  currency phase reports.
- **A version the script cannot order now exits 2** rather than sorting to the
  bottom. A version whose place it cannot judge is one whose currency it cannot
  judge, and sorting it low quietly is exactly how the epoch defect hid.

### Fixed

- **429 is retried.** `_get_json` retried only `>= 500`, and the reasoning behind
  that — "a 4xx is an answer, not a hiccup" — is right for every 4xx except this
  one: `429 Too Many Requests` explicitly means try again, and usually says when.
  Both registries this script talks to rate-limit, and an audit issues one PyPI
  call per changed package plus the OSV batch, which is the burst shape that trips
  a limiter. `Retry-After` is honoured and **capped at 30s** — a registry may ask
  for ten minutes; an audit is not entitled to stall that long in silence — and an
  HTTP-date falls back to the ordinary backoff rather than crashing.

## [0.3.1] — 2026-08-14

Four defects in `audit.py`, all of which fail safe — toward exit 2, or toward
noise — and all of which cost an audit something anyway.

### Fixed

- **`--changed-vs` missed an artifact swap at an unchanged version.** The changed
  set was keyed on `(name, version)`, so a PR that rewrites a wheel's `url` and
  `hash` and leaves the version alone selected *no packages at all* — the single
  lockfile change most worth catching, on the path the skill documents as the
  default. The empty-selection guard stopped it reporting `CLEAN`, so it failed
  safe, but its message offered two benign explanations and neither was what
  happened: an operator who believed it dismissed a correctly-refused audit. The
  comparison now includes the artifact hashes, and the diagnostic distinguishes
  `added` / `version` / `ARTIFACTS CHANGED`, which is loud in both the stderr
  diagnostic and the report.
- **A lockfile entry without `size` reported a false size `MISMATCH`.** `size` is
  optional in a `uv.lock` artifact table — uv omits it when the index does not
  report one — and it was compared unconditionally, so an artifact matching PyPI
  byte-for-byte came back `BAD`. That is the report row a reader is least able to
  dismiss: "the hash matches but the size does not" reads like tampering. Absent
  is now a third state, `not recorded`, and `null` in `--json`.
- **An unhandled exception exited 1**, the status the contract reserves for "ran
  and found something". Every *foreseeable* failure already routed through
  `fail()`; there was no backstop for the rest, so a `KeyError` on a lockfile
  written by the PR under audit read as a discrepancy. Both scripts now dispatch
  through a `cli()` that re-raises `SystemExit` first — or `fail()`'s exit 2 and
  `main()`'s legitimate 0 and 1 all get rewritten — and route anything else to
  exit 2. `DEPENDABOT_AUDIT_DEBUG=1` keeps the traceback.
- **The OSV batch was unchunked**, and `querybatch` rejects more than 1000 queries
  with a 400 (measured at the boundary: 1000 returns 1000 results, 1001 returns
  HTTP 400). A lockfile large enough to trip it lost the whole vulnerability phase
  at the last step, after every provenance and currency call had been paid for,
  with a message pointing at OSV rather than at the lockfile size. A
  1000-package lockfile is ordinary for a monorepo.

### Added

- OSV `next_page_token` is followed rather than dropped. A `querybatch` result
  carries one page; unread, the remaining ids simply vanish from the report. If
  the page cap is reached the row says so instead of quietly truncating.
- `report["selection"]` in `--json`, so a consumer can read *why* each package was
  selected rather than inferring it.

## [0.3.0] — 2026-08-14

The audit executes code from the PR it audits, and said so nowhere. On a repo
whose dependencies you already run that is a non-issue; pointed at an arbitrary
repository's fork PR it is the largest thing this tool does that it cannot undo.
This release states it, orders the phases so the read-only checks can refuse, and
gives the read-only subset a name.

### Added

- **A `--no-execute` mode** — Phases 0–3 and 6–7 only. Every one is a network
  read: provenance, currency, changelogs, OSV, CI state. That is most of the
  procedure's value and the right default for a PR there is no reason to trust.
  The report names the phases that did not run.
- **Phase 0 classifies the PR.** Dependabot and Renovate push their branches
  *into* the repository, so a bump arriving from a fork did not come from the bot.
  `isCrossRepository`, or an author that is neither, is a finding in its own right
  and switches the run to `--no-execute` unless the user authorises otherwise.
- **A "What it executes" section** in the README, an execution section at the top
  of `SKILL.md` beside the read-only contract, and an *Installing is executing*
  table in `references/ecosystems.md`. The phases that run PR code are labelled in
  their own headers, and a test asserts they stay labelled.
- `SECURITY.md` and `CONTRIBUTING.md`. The skill's Phase 8 offers to write into a
  repo's `CONTRIBUTING.md` gotchas section, so the plugin had been recommending a
  file it did not have.

### Changed

- **Phase 1 is a gate, not a step.** A diff reaching past the manifest and
  lockfile, or a provenance discrepancy, stops the audit *before* Phase 4. Running
  the cheap read-only checks first is only worth something if they are allowed to
  refuse; a procedure whose thesis is "verify before you trust" must not run the
  artifact before it has finished deciding whether to trust it. Stopping there is
  a complete audit that reached a verdict early, and
  `references/report-template.md` now carries the row shapes for saying so.
- **Narrowed frozen installs are the documented default** — `npm ci
  --ignore-scripts`, `uv sync --locked --no-build`. They cost something real: a
  package that genuinely needs its install script is not exercised, so the report
  must name which form ran. `cargo build --locked` runs every crate's `build.rs`
  and has no equivalent flag, which the reference states rather than implying
  parity.
- The README distinguishes two claims that had been running together: what the
  *plugin* writes (the `disallowed-tools` contract) and what the *audited code*
  does. The worktree isolates the user's working tree from the audit; it does not
  isolate the machine from the PR, and nothing here is a sandbox.

## [0.2.1] — 2026-08-14

### Fixed

- **Phase 4 ran against a worktree Phase 5 created.** Read in order — which is how
  the skill is meant to be read — Phase 4 could not run at all. `git worktree add`
  moves to Phase 0 beside the fetch, so every later phase is consistent by
  construction, which was already Phase 0's stated principle. Phase 5 keeps the
  reproduction and the cleanup; the staleness check moves to where the worktree is
  now made.
- **`gate_diff` reported a false `GATES AGREE` when a gate staged its changes.**
  The restore was `git checkout -- .`, which restores the worktree *from the
  index*, so anything staged survived it and `clean -fd` will not remove a tracked
  file. Run two inherited run one's edits and was credited with them — the wrong
  direction to fail in for a tool whose job is reporting that two versions differ,
  and not exotic: `pre-commit` stages directly. Now `git reset --hard`.
- **Phase 6 hardcoded one repo's required check names** in the only snippet in
  `SKILL.md` that filled a placeholder in rather than showing one. Reused literally
  against a repo whose checks are named anything else, it matched nothing, printed
  nothing, and was indistinguishable from "no required checks configured" — Phase 6
  then verified nothing while the report asserted CI was checked. Phase 0 now
  derives the list into `$SCRATCH/required.txt` and Phase 6 reads the file, with
  every context producing a row so one that never reported says `NOT REPORTED`
  instead of vanishing.

### Added

- **`tests/test_skill_prose.py`** — `SKILL.md` checked against itself, offline, in
  the existing suite. Four defects have now shipped in the prose and nowhere else,
  two of them the same forward-reference shape, so fixing them one instance at a
  time was demonstrably not working. It asserts that no phase
  consumes what a later phase creates, that the required-context list is read from
  a Phase 0 artifact rather than typed, that every script and reference path the
  prose names exists, and that the frontmatter key withholding tools is the one
  that works rather than the inert key 0.1.9 shipped.

  It does not check whether the model *follows* the phases. That is behavioral,
  belongs in `claude plugin eval`, and remains unavailable — the README says so
  and continues to.
- **`commands/dependabot-audit.md`.** The README documented `/dependabot-audit`
  and the plugin shipped no `commands/` directory, so the first command a new user
  tried rested on bare-name resolution the plugin does not control. The command
  invokes the skill rather than restating the procedure, because `disallowed-tools`
  applies only while the skill is active and an inlined copy would silently drop
  the read-only contract.
- A `Requires:` line on every phase, and a **Phase 0 outputs** table naming
  everything later phases consume. The forward-reference test reads it.
- `CHANGELOG.md`, and annotated tags for every release back to `v0.1.0` — a
  dependency-provenance tool that ships untagged gives a user pinning to a version
  nothing to pin to. Each tag points at the last commit *declaring* that version in
  `plugin.json`, derived from the file rather than from commit subjects, because
  those differ.
- Issue templates, shaped like this repo's own bug reports: the exact command, the
  output verbatim, the exit status, and a lockfile excerpt with private index URLs
  and tokens stripped.

### Changed

- The README states both entry points as equals rather than presenting one as
  shorthand, and its usage example no longer cites a PR number from a different
  repository.
- The README's Tests section no longer presents the end-to-end ruff replay as
  something the suite does. It was verified by hand, once, against a tree that is
  not in this repository, and nothing re-runs it — it is the observation
  `gate_diff.py` was built from, and `references/traps.md` is where it lives.
- `.gitignore` now covers `.claude/settings.local.json`, which was untracked only
  because of one machine's *global* ignore file. A contributor without that rule
  would see it as untracked and could commit absolute scratch paths and session
  identifiers — the one thing the pre-release cleanliness sweep concluded the tree
  was free of.

## [0.2.0] — 2026-08-11

### Added

- `scripts/gate_diff.py`, and with it Phase 4 stops predicting behaviour change
  and starts measuring it. It runs the same gate once per version in a disposable
  worktree and compares what each run *did to the files*.

  Three findings from replaying a real bump — Dependabot's ruff `0.15.22` →
  `0.16.0` PR — drove the design. Exit codes miss it: both versions exit 0 on a
  compliant repo while the newer formats 33 more files. Output is not comparable
  across versions: `0.15` prints `Would reformat: x.py`, `0.16` prints an
  annotated diff, so a text comparison marks everything different. What the tool
  *touches* is stable and comparable, and that is what gets diffed.

  Being ecosystem-independent falls out of parsing nothing: the operator supplies
  the invocations and the tool's own output is never read.

### Changed

- `SKILL.md` deduplicated against the scripts. The rule adopted: a trap a script
  now *refuses* keeps its imperative inline and moves its explanation to
  `references/traps.md`; a trap still resting on the reader stays where it will be
  read. Branch protection's 404-vs-403 table, `pipefail`, worktree isolation, and
  never pushing to the bot's branch all stay — nothing enforces them.

### Fixed

- A `# noqa` only suppresses diagnostics reported on its own line, and
  `ruff check --fix` deletes any code that is not. `S603` belongs on the call and
  `S607` on the argv one line below; grouping them reads better and silently loses
  one.

## [0.1.10] — 2026-08-11

### Added

- The gates this plugin demands of every repo it audits: `ruff`, `mypy --strict`,
  and the suite as pre-commit hooks, plus CI across Python 3.11–3.14 — `audit.py`
  runs under whatever bare `python3` the audited repo has, and `tomllib` sets the
  floor.
- `dependabot.yml` watching the two dependency surfaces this repo has, which also
  gives the plugin the only end-to-end exercise available without
  `claude plugin eval`: a real bump PR to run itself against.

### Changed

- CI invokes `pre-commit run --all-files` rather than installing its own `ruff`
  and `mypy`, so tool versions have exactly one pin instead of two that drift.

### Fixed

- Selecting ruff's `S` rules made the pre-existing `# noqa: S310` mean something
  for the first time, and surfaced that it fires on `Request()` as well as
  `urlopen()`. Rather than silence it twice, `_get_json` now refuses a non-`https`
  URL outright, and the package name interpolated into the PyPI URL is
  percent-encoded — that name comes out of the lockfile written by the PR under
  audit, which is precisely the input not to trust.

## [0.1.9] — 2026-08-11

### Fixed

- **The read-only claim was decorative.** The skill declared
  `tools: Read, Grep, Glob, Bash` and the README said that withheld `Edit` and
  `Write`. It withheld nothing: `tools` is not a skill frontmatter field, and
  unknown keys load without complaint — the worst shape for a safety property,
  visible and documented and absent. The field that removes tools from the pool is
  `disallowed-tools`, which is what the skill now uses. (`allowed-tools` is the
  near neighbour that does the opposite: it grants use without prompting and
  restricts nothing.)
- Phase 8 stops describing a memory write as "the one exception" to the no-write
  rule. Once `Edit` and `Write` genuinely go, honouring that promise would mean
  shelling out — the skill routing around its own restriction. It now hands the
  memory entry back for the invoking session to save. There is no exception left.

## [0.1.8] — 2026-08-11

### Fixed

- **A failed audit no longer exits as though it found something.** An unhandled
  exception exits 1, the status reserved for "found a discrepancy, a stale
  version, or a vulnerability". Four failures took that route — a missing
  lockfile, malformed TOML, a lockfile with no `[[package]]` entries, and an
  unreachable OSV. The last is the one that mattered: with OSV down, anything
  gating on the status read an outage as a vulnerability. Everything foreseeable
  now routes through `fail()` and exits 2.
- The network handler widened to `OSError`, which covers `URLError` and
  `TimeoutError` both — a read that times out mid-body raises the latter, so the
  old `URLError`-only clause let the script's own timeout escape.
- `--json` is parseable again: the `derived N changed package(s)` diagnostic went
  to stdout in front of the JSON, breaking the documented machine-readable mode in
  `--changed-vs`, the mode the skill recommends. Diagnostics belong on stderr.

### Added

- One retry with a 2s backoff for 5xx and transport errors, plus a User-Agent. An
  audit makes a call per changed package plus the OSV batch, and losing a dozen
  good calls to one transient 502 is worse than waiting. A 4xx is not retried:
  "no such package" is the answer, and retrying only delays it.

## [0.1.7] — 2026-08-11

### Fixed

- **`resolution-markers` no longer excuse the pin that matters.** uv stamps them
  on *every* block of a forked package, so treating their presence as an exemption
  exempted the highest pin too — the version actually installed on a current
  interpreter, and the only one a follow-up bump could move. A live pin years
  behind the registry reported as "expected to trail". Exemption now depends on
  the entry's place among that package's pins: lower forks are held back, the
  highest is live and still checked.
- Publish times come from a version's *earliest* artifact, not its first-listed
  one. The currency question is whether a version existed before the PR was
  opened, and a wheel built by CI can land hours after its sdist.
- Pre-releases are filtered out of the gap — a bot never proposes an rc, so
  reporting `1.3.0rc1` sent the reader to a changelog that could not be the
  answer. Tail-anchored, so post-releases, which a bot *does* propose, survive.
- The gap is ordered by publish time rather than by version, because "compare the
  earliest of these against the PR's `createdAt`" is a time comparison, and a
  patch on an older line can be published after a higher version.

## [0.1.6] — 2026-08-11

### Fixed

- **Phase 1 audited the working tree rather than the PR.** It used the `pr-<N>`
  ref that Phase 5 created, and passed a bare `uv.lock` — a cwd-relative path
  resolving against whatever branch the user had checked out. Followed literally,
  the audited lockfile was the base branch's.
- The script made that failure look like success: with both sides on the base
  branch every `(name, version)` pair matches, nothing is selected, and `main()`
  printed `RESULT: CLEAN` and exited 0 — a complete audit of nothing wearing the
  output of a clean one. An empty selection now exits 2.
- Phase 0 pins the head SHA and fetches the ref once; Phase 1 reads both lockfiles
  out of git at that ref; Phase 7 re-checks the SHA before writing, because a bot
  rebase mid-audit otherwise leaves Phases 1–5 describing a commit that no longer
  exists while Phase 6 reports on the new one.

### Changed

- The `RESULT` line carries package and artifact counts, so a reader who skims to
  the verdict sees the size of the evidence behind it.
- Packages the script cannot reach — resolved from git, a path, or a private index
  — are named rather than dropped. A bumped git dependency absent from the output
  is an under-audit indistinguishable from a clean one.

## [0.1.5] — 2026-08-10

### Added

- The regression suite: 18 stdlib-only cases, offline and free. Every one
  corresponds to a defect that shipped or to a failure the audit exists to detect.
  Each was mutation-checked against the original buggy implementation rather than
  merely passing against the fixed one — a suite that only ever passes proves
  nothing.

## [0.1.4] — 2026-08-10

### Fixed

- **Three defects of one shape: a name treated as a unique key.** `--changed`
  ignored names it could not match, so asking for two packages and getting one
  produced output indistinguishable from success; it now exits 2 and names them.
  Matching is PEP 503 normalized, so `Pillow`/`pillow` and `foo_bar`/`foo-bar` no
  longer miss.
- Target selection returns *every* matching entry. A lockfile can pin one package
  at several versions under different `resolution-markers`; selecting through a
  name→entry dict kept the last and dropped the rest. Observed on `rpds-py` pinned
  at `0.30.0` and `2026.6.3`: 116 of 231 artifacts verified, while the output
  reported completeness.
- A marker-constrained pin trails the registry by design, so reporting it as stale
  invited a follow-up bump that can never be made. Now labelled and excluded from
  the clean determination.

### Added

- `--changed-vs`, deriving the changed set from a merge-base lockfile, because a
  grouped PR title names none of its packages.

## [0.1.3] — 2026-08-10

### Added

- Evidence provenance in the report. An unmarked table asserts everything in it
  was just observed, and silently lies when that is false. Rows now carry an
  `Observed` column, with reuse rules keyed to what invalidates each row rather
  than to its age: registry and CI rows must be fresh, changelog rows are reusable
  because published release notes are immutable, and reproduction rows are
  reusable only against an unchanged head SHA.

  The useful inversion: the *cheap* evidence is what must be fresh, and a full
  test suite is the only kind expensive enough to be worth reusing at all.

## [0.1.2] — 2026-08-10

### Fixed

- Phase 0 said `branches/<default>/protection` without saying how to obtain
  `<default>`. Guessing wrong returns an empty context list — identical to a repo
  with no protection — so Phase 6 would verify nothing while the report claimed CI
  was green. The name is now derived, and the status read to separate three
  verified cases: `404 Branch not found`, `404 Branch not protected`, and
  `403 Upgrade to GitHub Pro`. The latter two are findings, not omissions.
- Phase 5 created a worktree no phase removed, so audits left one registered in
  the user's repo per PR reviewed. Now either cleaned up or declared, with its
  command, in the report.
- Phase 5 left the scratch directory undefined. It is now named explicitly as a
  directory *outside* the repo — the obvious wrong choice pollutes `git status`
  and feeds a second copy of the project to any gate that walks the tree.
- Phase 3 read as asking for an OSV query Phase 1 had already run.

## [0.1.1] — 2026-08-10

### Fixed

- Phase 6 told the model to check required contexts "individually" but supplied no
  command. The obvious approach — post-processing `gh pr checks` with `awk` —
  mangles every check name containing spaces or an ampersand, turning
  `Lint & type-check` into `Lint`, and the audit then confidently reports on a
  check that does not exist. Ships the `--json statusCheckRollup` recipe instead.
- That recipe has its own trap, verified: a required context that never reported
  yields no row, indistinguishable from a passing one that was not printed. Rows
  must be counted against the required list.
- Phase 5 said nothing about a worktree left over from a prior run. A stale one
  silently audits the wrong commit, so head and cleanliness must now be proven
  before reuse.

## [0.1.0] — 2026-08-10

### Added

- First release. A Claude Code plugin that audits an automated dependency-bump PR
  and reports a merge recommendation with the evidence behind it. It never merges;
  it prints the merge command un-run.
- The procedure targets the three failure modes that actually bite: a proposed
  version lagging the registry, a fix in the gap that no vulnerability database
  knows about, and a bump that changes a default and breaks a required check the
  local hooks are scoped too narrowly to see. Each has been observed in practice.
- `scripts/audit.py` implements PyPI / `uv.lock` end-to-end and is tested against
  it; other registries are documented as procedures rather than shipped as
  untested code.
- Repo specifics are derived every run and never cached; only non-derivable
  landmines are persisted, via the Phase 8 learning loop.

[Unreleased]: https://github.com/Machai-Kydoimos/dependabot-audit/compare/v0.19.0...HEAD
[0.19.0]: https://github.com/Machai-Kydoimos/dependabot-audit/compare/v0.18.0...v0.19.0
[0.18.0]: https://github.com/Machai-Kydoimos/dependabot-audit/compare/v0.17.0...v0.18.0
[0.17.0]: https://github.com/Machai-Kydoimos/dependabot-audit/compare/v0.16.2...v0.17.0
[0.16.2]: https://github.com/Machai-Kydoimos/dependabot-audit/compare/v0.16.1...v0.16.2
[0.16.1]: https://github.com/Machai-Kydoimos/dependabot-audit/compare/v0.16.0...v0.16.1
[0.16.0]: https://github.com/Machai-Kydoimos/dependabot-audit/compare/v0.15.0...v0.16.0
[0.15.0]: https://github.com/Machai-Kydoimos/dependabot-audit/compare/v0.14.0...v0.15.0
[0.14.0]: https://github.com/Machai-Kydoimos/dependabot-audit/compare/v0.13.0...v0.14.0
[0.13.0]: https://github.com/Machai-Kydoimos/dependabot-audit/compare/v0.12.0...v0.13.0
[0.12.0]: https://github.com/Machai-Kydoimos/dependabot-audit/compare/v0.11.0...v0.12.0
[0.11.0]: https://github.com/Machai-Kydoimos/dependabot-audit/compare/v0.10.1...v0.11.0
[0.10.1]: https://github.com/Machai-Kydoimos/dependabot-audit/compare/v0.10.0...v0.10.1
[0.10.0]: https://github.com/Machai-Kydoimos/dependabot-audit/compare/v0.9.0...v0.10.0
[0.9.0]: https://github.com/Machai-Kydoimos/dependabot-audit/compare/v0.8.0...v0.9.0
[0.8.0]: https://github.com/Machai-Kydoimos/dependabot-audit/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/Machai-Kydoimos/dependabot-audit/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/Machai-Kydoimos/dependabot-audit/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/Machai-Kydoimos/dependabot-audit/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/Machai-Kydoimos/dependabot-audit/compare/v0.3.1...v0.4.0
[0.3.1]: https://github.com/Machai-Kydoimos/dependabot-audit/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/Machai-Kydoimos/dependabot-audit/compare/v0.2.1...v0.3.0
[0.2.1]: https://github.com/Machai-Kydoimos/dependabot-audit/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/Machai-Kydoimos/dependabot-audit/compare/v0.1.10...v0.2.0
[0.1.10]: https://github.com/Machai-Kydoimos/dependabot-audit/compare/v0.1.9...v0.1.10
[0.1.9]: https://github.com/Machai-Kydoimos/dependabot-audit/compare/v0.1.8...v0.1.9
[0.1.8]: https://github.com/Machai-Kydoimos/dependabot-audit/compare/v0.1.7...v0.1.8
[0.1.7]: https://github.com/Machai-Kydoimos/dependabot-audit/compare/v0.1.6...v0.1.7
[0.1.6]: https://github.com/Machai-Kydoimos/dependabot-audit/compare/v0.1.5...v0.1.6
[0.1.5]: https://github.com/Machai-Kydoimos/dependabot-audit/compare/v0.1.4...v0.1.5
[0.1.4]: https://github.com/Machai-Kydoimos/dependabot-audit/compare/v0.1.3...v0.1.4
[0.1.3]: https://github.com/Machai-Kydoimos/dependabot-audit/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/Machai-Kydoimos/dependabot-audit/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/Machai-Kydoimos/dependabot-audit/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/Machai-Kydoimos/dependabot-audit/releases/tag/v0.1.0
