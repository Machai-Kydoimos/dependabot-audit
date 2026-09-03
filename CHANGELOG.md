# Changelog

Every release is tagged, and every tag is annotated. `git log` carries the full
reasoning behind each change; this file is the readable index of it.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). This
plugin's public surface is its **procedure**, not an API, so the versioning rule
is: a change to what a phase verifies, or to what the report asserts, is a minor
bump even when no code moved. A fix that only makes an existing claim true is a
patch.

## [Unreleased]

## [0.38.0] — 2026-09-03

Closes [#109](https://github.com/Machai-Kydoimos/dependabot-audit/issues/109),
[#110](https://github.com/Machai-Kydoimos/dependabot-audit/issues/110) and
[#111](https://github.com/Machai-Kydoimos/dependabot-audit/issues/111), all three
raised by live runs against this repository's own bump PRs.

**Minor, not patch.** A third ecosystem is covered end to end, Phase 6 gains a
question it did not ask, Phase 7 gains a fourth verdict, and Phase 0's worktree
rule changes on which runs it fires.

### Added — `pre-commit` is a covered ecosystem (#109)

**Half of this plugin's only live exercise landed on the refusal path.**
`.github/dependabot.yml` configures `github-actions` and `pre-commit` and says
these PRs are "the only end-to-end exercise this plugin gets". One of the two was
verified end to end; the other reached Phase 1's boundary every month and stopped.
#98's report put it plainly — *"a Hold I would not defend on the merits, only on
the procedure"* — and a gate that always says Hold is one the reader learns to
discount.

**What made it worth covering rather than documenting is that a `rev:` bump has
real checkable content.** `scripts/precommit.py` derives three things no other
phase could:

- **the pin**, resolved to a commit and classified `immutable` / `mutable` /
  `underivable`. A `rev:` is a git ref on someone else's repository — no artifact
  hash, so this reports *immutability*, never integrity, exactly as
  `references/actions.md` does for `uses:`;
- **the requirement**, read from the hook repository's own packaging.
  `mirrors-mypy` pins `mypy==2.3.1` in `setup.py`; `ruff-pre-commit` pins
  `ruff==0.16.5` in `pyproject.toml`. *That* is the dependency, and pointing
  Phases 2 and 3 at it puts them back on covered ground;
- **the hook definition**, diffed field by field between the two revs.

The third is the one that earns the script. Measured on `ruff-pre-commit`
v0.16.2 → v0.16.5, which is this repo's #99:

```
!! ruff-format.types_or  [behavioural]
     before: [python, pyi, jupyter]
     after:  [python, pyi, jupyter, markdown]
```

One word in one list, and the hook began rewriting every Markdown file in the
repository. **`ruff` itself did not change in any way its changelog reports**, so
a per-package view of `ruff` returns *current* and *clean* — correctly, about the
wrong artifact. Finding that by hand took a day.

**Not a lockfile procedure with the names changed**, which is the objection the
removal of npm, Cargo and Go was about. Where the claim cannot be made, it is
refused rather than approximated:

- a hook whose `language` is not `python` has its requirement derived and its
  registry named as **not covered** — npm is the boundary again, one layer in;
- `.pre-commit-hooks.yaml` is parsed by a deliberately small grammar that
  **raises** on anything outside it, and the raw text is still handed to the
  reader. A parser that skips what it does not recognise reports "no fields
  changed" about a file it did not read — this plugin's own thesis one level
  down. Verified against four live mirrors that disagree on every cosmetic
  detail: two-space and four-space continuations, quoted keys, and a folded
  scalar;
- `install_requires` is read by **AST**, not regex, and a computed entry makes the
  answer `underivable` rather than a partial list read as whole;
- an unrecognised hook field counts as **behavioural**, deliberately: `pre-commit`
  gains keys, and a new selector must not arrive as cosmetic because a list
  predates it.

`discover.py` classifies the ecosystem and gates its scope on `rev:` lines, the
way it already does on `uses:`. The two line-based gates now share one
implementation rather than a copy each. A diff also moving `args:`,
`additional_dependencies:` or `repo:` is `beyond` — those change what the hook
does, and `repo:` is not a bump at all.

Both live PRs are the fixtures and they are complementary: #99 is a defect the
verifier must catch, #98 a clean bump it must not flag. `integration/` replays
both against the real repositories, because the hermetic fixtures are only
evidence that the parser reads what was copied out of the files the day it was
written.

### Added — Phase 6 asks whether the base moved (#110)

- **Phase 6 now asks whether the base moved under the check results**, and
  `ci_state.py` answers it. CI runs on `refs/pull/<N>/merge` — the base merged
  with the head — so a fix landing on the default branch invalidates every result
  on the PR, with **no event on the PR to re-trigger them**. Attribution cannot
  see this: it compares the head against `pr-<N>^` and *both of those commits are
  unchanged*, so the row reads `attributable`, correctly, about a merge that no
  longer exists.

  Observed on this repo's #99. The fix for what the red check caught merged to
  `main` as `09911c1` at 2026-09-03T17:43:31Z; the red `Lint & type-check` had
  started 2026-09-01T01:14:41Z, **2d 16h earlier**, so it could not have contained
  its own fix. A report that stopped at Phase 6 carried a substantive Hold on a PR
  whose only blocker had already been repaired.

  **The comparison is a timestamp, not a ref, and that is the finding inside the
  finding.** The obvious signal is the merge ref — compare `refs/pull/<N>/merge`'s
  base parent against the branch tip, which is what the issue proposed. Measured,
  it does not work: `refs/pull/<N>/merge` and `potentialMergeCommit` are recomputed
  *lazily*, and querying `mergeable` is what pokes them, so by the time either is
  read it names the current base while the check results still do not. Commit
  dates are not recomputed by being read. `baseRef`'s tip committed after a
  settled check started means that check could not have contained it — no local
  `git`, which is what let this go into `ci_state.py` rather than staying prose.

  Three states, and the third is load-bearing as usual: a settled context with no
  start time, or an unreadable base tip, is **underivable** rather than "current".
  A *queued* context has no start time either and never will until it starts, so
  it is excluded — reading it as underivable would fire on every PR with a pending
  job and retire the signal.

  Verified in both directions live: silent on #99 as it stands (base tip 18:39:15Z,
  checks 18:43:34Z), and firing on `cli/cli` #14196, whose seven contexts predate
  the current `trunk` by 15d 20h.

- **A fourth verdict, `Hold, pending a re-run`** (#110). "Red, and the base still
  explains it" and "red, but the base has moved" are different recommendations:
  the first owes the reader a cause, the second owes a commit and an action.
  Phase 7's table gains two rows for the distinction, above the `attributable`
  row so the first-match read reaches them. Collapsing the two is what produced
  the Hold above.

### Changed

- **Phase 0 creates the worktrees on a condition, not on an ecosystem** (#111).
  The old rule said *an actions bump consumes neither worktree*. That is true and
  it is not the reason: what makes them pointless is that **Phases 4 and 5 will
  not run**, which is equally true of an uncovered ecosystem, a `pull` tier, a
  non-bot author, `--no-execute`, and Phase 1 finding anything. Every input is on
  disk before the decision — `discover.py` writes `$ECOSYSTEM`, `$SCOPE_GATE` and
  `$MAY_EXECUTE` one command earlier.

  Two live runs deviated from the old rule independently, each reasoning out the
  uncovered-ecosystem case and each writing it up rather than acting on it. The
  Phase 0 outputs table carried the same rule in its own words and is updated with
  it, because a reader following the table alone got whichever statement was not
  changed.

  **`--no-execute` is not `$MAY_EXECUTE`**, and #111's own text conflated them.
  `discover.py` derives `MAY_EXECUTE` from the author, the cross-repository check
  and `push`; it never sees the flag. The rule now names both inputs.

- **Phase 6 separates reading the simulated merge from re-gating it.** #110 held
  that `git merge-tree --write-tree` "works under `--no-execute` for any gate that
  is itself read-only". Half right: reading a file out of the written tree is
  read-only and always available, but *running* the repo's gate against it
  executes the bumped tool, which is the whole of `$MAY_EXECUTE`. Recorded as
  written, that would have put a Phase 5 action inside the flag that exists to
  forbid it.

### Fixed

- **A prose guard that stopped discriminating when a row moved.** Phase 7's
  attributable-row guard took the first row mentioning `attributable` and
  returned, so the new stale-base rows — inserted above it, and about *not*
  reporting a red as attributable — satisfied it on behalf of the row it was
  written for. It now selects on the verdict cell and checks every match.
  Mutation-checked afterwards against the defect it was written for.

- **Two new guards that went green under mutation, caught before they shipped.**
  One asserted a phrase over the whole of `render`'s output and was satisfied by a
  different `print` higher up; it now slices at the row. The other asserted the
  rollup query's field names, which occur more than once in the same reachable
  code, so deleting any one from the query passed — removed rather than kept as
  coverage, with the negative result recorded in the test. `SkillHarness` gains
  `flat()`, because `SKILL.md` is hard-wrapped and three guards matching more than
  a few words failed on their own fixed prose.

## [0.37.0] — 2026-09-03

Closes [#104](https://github.com/Machai-Kydoimos/dependabot-audit/issues/104),
[#105](https://github.com/Machai-Kydoimos/dependabot-audit/issues/105) and
[#106](https://github.com/Machai-Kydoimos/dependabot-audit/issues/106) — three
defects found by running this plugin against its own repository's open Dependabot
PRs, #98 and #99. Both are `pre-commit` bumps, which is the ecosystem this plugin
does not cover, so all three sit on the boundary path rather than in the verified
one.

**Minor, not patch.** Phase 1's boundary report gains two phases it never named,
Phase 4 gains a step it did not require, and the report shape gains a rule about
what a row may assert.

### Changed

- **Phase 1 now says which phases survive the boundary, and why** (#106). The
  passage told an uncovered ecosystem to "say so and stop … do not improvise",
  then fifteen lines later to report "what Phase 0's classification and Phase 6's
  CI state established". That enumeration omitted Phases 2 and 3, which are
  equally ecosystem-independent — so the two readings disagreed and **a run
  taking either was equally compliant**.

  Measured on #98: the run performed the currency read and the vulnerability
  queries, established that v2.3.1 was the true latest of both the mirror and
  `python/mypy` and that OSV and GHSA held nothing for `mypy` at any version, and
  then spent a deviation row defending them as improvisation. On a PR with six
  green required contexts that evidence is most of what a reader weighing a
  boundary Hold has to go on.

  The line is now stated as a property rather than a list: Phases 2 and 3 ask a
  registry and an advisory database questions that are **falsifiable against the
  same public source the reader can open**, and neither certifies that an
  artifact is what the registry says it is. That certification is Phase 1's
  alone, and it is what the boundary withholds. The Cargo failure the warning
  comes from was an improvised *verifier* reporting green about artifact
  integrity — not a currency read.

- **Phase 4 must reconcile an exclusion the bump defeated** (#105). Where the
  repo configures an exclusion covering files the bump newly reaches, and they
  are rewritten anyway, the audit now establishes *why* before reporting the
  cause. Two check-only runs settle it: once as the gate invokes the tool, once
  forced to the repo-root manifest.

  Measured on #99. `pyproject.toml` carried `extend-exclude = ["integration/fixtures"]`
  with a comment saying it existed to stop `ruff format` tidying six Markdown
  fixtures — "the evidence erased by the tool it is evidence about" — and it had
  never done that job. `integration/fixtures/ruff-md-fences/pyproject.toml` is
  the nearest config for files beneath it and shadows the root's. Forced to the
  root, ruff reports `warning: No Python files found under the given path(s)`;
  left to resolve normally, `1 file would be reformatted`. The old report named
  the cause correctly and never reconciled it, **which sends the reader to the
  wrong file**: the pattern is right, and the remedy is at the hook layer.

  **The rule explicitly survives Phase 4 being skipped**, because the bump that
  produced it was an ecosystem that skips Phase 4. The contradiction arrived in a
  CI log instead. A Phase-4-only rule would not have fired on its own case.

### Fixed

- **A report could re-label a tool's count as a file class nobody measured**
  (#104). `references/report-template.md` opened with "every row is something you
  *ran*", which governs whether a row ran and not arithmetic performed inside
  one — so it passed against this.

  Measured on #99: `6 files reformatted, 23 files left unchanged` was reported as
  "the other 23 markdown files it also newly scanned", on a tree holding 14 `.py`
  and 15 `.md`. The unchanged 23 were 14 Python and **9** Markdown. The
  conclusion it supported — that the repo's own documentation is newly in scope
  and currently passes — was correct, and the evidence offered for it was 2.5x
  larger than anything measured, in the one row a reader uses to size blast
  radius. Either derive the split, or quote the tool's number unsplit; rows that
  quote a tool verbatim are the ones worth keeping, so the rule is narrow on
  purpose.

### The replay

`/dependabot-audit 99` under `claude -p --plugin-dir`, in a fresh context against
the unreleased tree — verified from the transcript, whose first call resolves
`discover.py` under the working tree rather than the installed 0.36.0 cache. 42
tool calls, 2 denials, both multi-line commands re-issued as singles.

| Changed here | What the run did with it |
|---|---|
| § Phase 1, the boundary enumeration | the deviation row for running Phases 2 and 3 is now classed **`correct`**, citing the new text. On #98 the identical work was a `prose gap` row |
| § Phase 4, the reconciliation | performed, check-only, and reported in the Behavior-change row: run 1 `6 files would be reformatted`, run 2 forced to the root manifest `warning: No Python files found under the given path(s)`. The report's own words: *"the reconciliation SKILL.md owes there **was** performed"* |
| § Phase 4, surviving a skipped Phase 4 | the row that carried it reads *"Phase 4 not run — uncovered ecosystem. The Phase 4-shaped observation arrived in the CI log instead"* |
| § report shape, the count rule | *"The CI run's own summary line, quoted unsplit: `6 files reformatted, 23 files left unchanged`"* — and, separately derived, the nine newly-scoped Markdown files, `9 files already formatted` |

**And the verdict changed, correctly.** #108 had landed the exclusion an hour
earlier, so the red check was stale with respect to `main`. The run found that
itself, simulated the merge with `git merge-tree --write-tree` rather than
merging anything, measured the merged tree green, and returned *"hold —
procedurally, until CI re-runs"* at **medium** confidence instead of the
substantive Hold the same evidence produced before. Dependabot then rebased #99
onto `main` while the follow-up issues were being written, and the PR went
`CLEAN` — confirming the prediction the simulation had made.

Two findings came out of the replay that are not fixed here, filed as
[#110](https://github.com/Machai-Kydoimos/dependabot-audit/issues/110) — a red
check can be stale because the base moved rather than the PR, which Phase 6's
head-versus-parent comparison structurally cannot see — and
[#111](https://github.com/Machai-Kydoimos/dependabot-audit/issues/111), Phase 0's
worktree carve-out naming the ecosystem instead of the condition, now raised by
two independent runs.


## [0.36.0] — 2026-09-01

Closes [#94](https://github.com/Machai-Kydoimos/dependabot-audit/issues/94) and
[#101](https://github.com/Machai-Kydoimos/dependabot-audit/issues/101). Phase 2's
changelog ladder stopped at the first rung that answered, and every one of its
exit conditions was an *absence* — so a rung returning real, well-formed content
ended the read while five fix commits never entered the audit.

**Minor, not patch.** Phase 2 gains a source it always reads and a row it can
report; Phase 0 and Phase 6 gain a derivation they only implied.

### Added

- **`scripts/changelog.py`** (#94) reads all three sources and reconciles them.
  The rungs were never a fallback chain: **prose is what the project chose to
  say, and the commit range is what actually landed.** Measured on `rumdl`
  v0.2.60…v0.2.62, the bump this was found in — rung 1 answers for both versions,
  rung 2 answers for both, each says one `### Added` bullet, and the range holds
  18 commits, five of them `fix(…)`. Two are `stop rewriting Rust source when
  formatting doc comments`, which wrote `# [derive(Debug)]` to disk, and `stop
  reading a lazy continuation as a setext underline`, in a tool the audited repo
  runs as `rumdl check --fix` on every Markdown commit. A run honoring the ladder
  as written reported "two additive releases" and was wrong about the only
  interesting thing in the bump.

  **The obvious heuristic does not save it.** *"Does this project document its
  fixes at all?"* returns a confident yes: 0.2.56, 0.2.57, 0.2.59 and 0.2.60 all
  carry a `### Fixed` section. Only the versions under audit had none, because
  the release automation lists `feat` and drops `fix`. This is 0.33.0's failure
  class with the absence moved out of the tooling and into the upstream project's
  notes, where no exit status can reach it — **nothing fails.**

  Exit `0` the prose names every fix, `1` it does not and they are listed, `2`
  could not run. The evidence — both halves — goes to
  `$SCRATCH/changelog-<repo>-<from>-<to>.md`, because no count substitutes for
  reading a `Security` heading.

- **`--write-mode` escalates a finding and never gates the search.** #94 proposed
  making the range mandatory only where the repo runs the tool in write mode; the
  script fetches it always. Gating the *call* on that judgement asks the auditor
  to be right about write mode before it has the evidence, and one wrong guess
  restores the silence the script exists to remove. One `compare` call is what
  rung 3 already cost when it ran.

- **A third row in Phase 2's scope test** (#94), where the entry names a **file
  type** or a **document shape** rather than a setting. No config key exists to
  grep, so "no config line matches" reads as `inert here`. Grep the tree instead
  — and **do not pipe it into `wc`**: `git grep` exits `1` on no match and `128`
  when it could not run, both printing nothing, so `| wc -l` reports `0` at exit 0
  either way and turns `underivable` into `inert here`. Measured on git 2.55.0.
  The counted sentence moved from "two cases" to "three", which CONTRIBUTING
  already records the cost of forgetting.

- **Phase 0 derives the workflow list instead of naming `<ci>`** (#101), at both
  refs, so "diff the two lists" has two lists to diff. `ls-tree` on a missing
  directory exits **128** and says so rather than printing nothing at exit 0, so a
  repo with no workflows stays distinguishable from a read that failed. Phase 6
  and `actions.md` § Phase 5 asked the same question under a second spelling
  (`<changed>`); all three are now `<workflow>`, so the second and third are
  visibly the first.

### Changed

- **`reachable()` now follows scripts named in an ecosystem reference.** It
  followed only those named in `SKILL.md`, while `audit.py`, `gate_diff.py` and
  `changelog.py` are all invoked from `references/uv-lock.md` — so their code was
  in no phase's `reachable()`, and a guard about any of them was reading the
  invocation line alone. That is the silent retirement the function's own
  docstring warns about, sitting inside the function that warns about it.

  It also needed `_code_only(printed=False)` for negative assertions only: what a
  script *prints* is output, not a call, and `audit.py`'s advice to run
  `uv run python -V` made the `--no-execute` guard fire on Phase 1 — the guard
  that catches a phase executing the audited project, defeated by a phase
  mentioning it. Opt-in, because Phase 6 asserts on printed text on purpose.

- **Two ref-pinning guards widened to the property rather than the command.**
  `git ls-tree "<ref>:<path>"` and `git diff "<ref>...<ref>" -- <path>` are pinned
  exactly as `git show "<ref>:<path>"` is; a `show`-only pattern called both
  working-tree reads.

### Security

- **The GitHub repository was resolved with an unanchored substring test**, in
  the prose since 0.33.0 and in this script's first cut. `project_urls` is
  written by the package author — the party this plugin exists to *not* trust —
  so `if "github.com/" in url` hands the audit whatever repository that author
  names. Measured:

  | `project_urls` entry | Repo the audit would have read |
  |---|---|
  | `https://evil.example.invalid/github.com/attacker/lookalike` | `attacker/lookalike` |
  | `https://example.invalid/?q=github.com/attacker/repo` | `attacker/repo` |
  | `https://github.com/../../users/octocat` | `../..`, walking out of `repos/` |

  The first two point Phase 2's entire changelog read at a repository the package
  controls: tidy release notes, no unreconciled fixes, a clean currency row. The
  reconciliation added above is a verdict input Phase 7 reads, so this was worse
  after #94 than before it — the feature made the target worth attacking.

  Now the host is **compared**, never searched for, and both path segments are
  validated (which is what rejects `..`, since the slug is interpolated into a
  `gh api repos/<slug>/…` path). `--repo-slug` goes through the same check: it
  reaches the same API path, and a typo that silently answers about something
  else is the failure this phase is about.

  Caught by CodeQL as `py/incomplete-url-substring-sanitization`, high severity,
  on the PR that mechanised the ladder. **The prose copy was found only because
  the script copy was flagged** — and that copy is now deleted rather than fixed,
  because a second implementation of what `changelog.py` already does is how one
  of them keeps the bug.

### Fixed

- **An empty release window was reported as "this project publishes no releases
  for these versions"** whatever caused it. Three causes reach it — the target
  has no release, the *start* has no release, or the two are not in the order the
  caller believes (a downgrade, or a backported patch line) — and only the first
  makes that sentence true. A backport got told the project publishes nothing, by
  the tool written to stop absence of evidence reading as evidence of absence.
  `gap()`'s own comment claimed it said which; nothing did, and the branch had no
  test. It now returns the reason with the window, and the reason is printed.

- **Every commit body was fetched and discarded.** `commits()` asks for whole
  messages because *"the body is where a fix says what it corrupted"* — rumdl's
  Rust-source fix names `# [derive(Debug)]` only there — and the terminal then
  told the reader to go and fetch the range again. The evidence file now carries
  the full message for each **destructive-shaped** row, which are the ones
  Phase 7 takes the verdict from; the rest stay subjects, because a body for all
  266 of ruff's would be the wall that file exists to replace.

- **`changelog.py`'s own first version reported `python/mypy` v2.3.0…v2.3.1 as
  carrying no fixes.** It carries four. Filtering on `fix(` is only honest where
  the project did the labelling, and mypy labels nothing — so the script now says
  **which classifier ran**, and filters nothing when the project did not. Caught
  by running it against the range the reference already cites, not by reading it.

- **`section_for` found no changelog section in a file that has one per release.**
  A generated heading links to a compare range carrying the *previous* version, so
  the raw line matches the wrong one. Caught by replaying the script live; the
  offline suite now pins it in both directions.

- **`--jq '.commits[].commit.message'` runs eighteen messages together** with no
  record boundary, landing one commit's subject inside the last one's body.
  `@json` escapes the newlines so one line is one message. The same fix was needed
  in `integration/`, where `--jq .body` returned unparseable raw text.

## [0.35.0] — 2026-09-01

Closes [#97](https://github.com/Machai-Kydoimos/dependabot-audit/issues/97) and
[#95](https://github.com/Machai-Kydoimos/dependabot-audit/issues/95). Phase 7's
tidy-up becomes a script, because the three commands it replaces said nothing
about what does or does not dirty a worktree — and two separate audits therefore
reasoned it out and reached the same wrong answer.

**Minor, not patch.** Phase 7 changes what it does and gains a row it can report;
Phase 5 gains a stated expectation it did not have.

### Added

- **`scripts/cleanup.py`** (#97) removes exactly what Phase 0 created — `pr-<N>`,
  `base-<N>`, `tip-<N>` and the `pr-<N>` branch — discovering which are actually
  present, so a rewritten base needs no extra line and an actions bump, which
  creates no worktree at all, needs none removed. The prose used to ask the reader
  to adjust the command list for both cases, which is the kind of instruction that
  gets skipped.

  **Why a script for three commands.** Prose is the weakest of the three levers,
  and this trap kept recurring through it: on 2026-08-30 and again on 2026-09-01,
  two different audits handed back the claim that Phase 5's `uv sync` leaves a
  `.venv/` which makes `git worktree remove` refuse. It does not — `remove` gates
  on `git status --porcelain`, which omits *ignored* files, and uv, pytest, ruff
  and mypy each write a `.gitignore` containing `*` inside their own directory.
  Measured on git 2.55.0 and uv 0.12.8 in a worktree of a repo with **no
  `.gitignore` at all**, the plain form exits 0. A run that types the command
  re-derives that question every time; a run that calls the script never asks it.

- **The residue is written out before the tree goes.** `$SCRATCH/residue-<tree>.diff`
  carries the porcelain output and `git diff HEAD`; untracked paths are listed and
  not dumped. `$SCRATCH` outlives the worktrees, so the evidence outlives the tree
  that held it — which is what makes the removal safe. Forcing without writing
  would discard something the audit produced and had not yet reported, the loss
  `gate_diff.py`'s `restore()` declines `-x` to avoid.

  Per-tree rather than one file: `pr-<N>` dirty is Phase 5's gates, `base-<N>`
  dirty means `gate_diff.py`'s restore did not hold, and those are different
  findings that must not land in one buffer under one heading.

- **Exit `1` means residue was found and the worktree was still removed** — a
  finding for the report, not a cleanup failure. `0` is clean and `2` is
  could-not-run, the same contract `gate_diff.py` uses. Phase 7 says to read the
  code and not to chain on it: `cleanup.py … && next` swallows the finding, the
  same shape as Phase 5's `cmd | tail && next` trap one phase over.

### Fixed

- **Phase 5's residue has somewhere to go** (#95). Phase 4 mutates `base-<N>` and
  `gate_diff.py` restores it after every run; Phase 5 mutates `pr-<N>` and nothing
  restored it, so a fix-mode gate — `rumdl check --fix`, `ruff format`, a
  `pre-commit` run that stages what it fixes — left tracked files modified or
  staged and Phase 7 exited 128 on them. Phase 5 now says the dirt is **expected
  and is a result**, and that tidying it destroys the finding: *"the repo's own
  gates rewrote N tracked files at the proposed version"* is Phase 4's question
  reached from the other direction.

- **Phase 0's reuse remedy no longer refuses on the state it was written for.**
  *"If either check fails, `git worktree remove` it and re-add"* followed a check
  that fails precisely when the tree is dirty — which is what a plain `remove`
  refuses on. It now calls `cleanup.py`, so a previous run's residue is saved
  rather than discarded unread.

### Tests

- **`tests/test_cleanup.py`**, 14 cases on real git worktrees: clean removal, a
  missing path (exit 0, per #90's measurement), the actions-bump shape, a
  rewritten base's `tip-<N>`, a self-ignoring `.venv`, un-ignored `__pycache__`,
  a modified tracked file, a staged one, a dirty `base-<N>` getting its own file,
  and the three exit codes including a crash reporting 2 rather than 1.

- **One case asks `uv` rather than restating it.** CONTRIBUTING's rule is that a
  fixture built from the rule can only ever agree with it, and the rule here is a
  claim about what uv writes — so `test_a_real_uv_venv_does_not_count_either`
  builds a real venv and skips where `uv` is absent. The hand-built equivalent
  stays, because it is what CI on four interpreters actually runs.

- **A guard retired itself, exactly as predicted, and the suite caught it.**
  `reachable()`'s docstring warns that a guard scanning only a phase's own bash
  goes green the moment its mechanism moves into a script. Mechanising Phase 7 did
  that to `TestCleanupRunsOnEveryPath`, on the same commit — the pattern now
  matches `cleanup.py` too, and the artifact list is read through `reachable()`,
  where `_code_only` strips docstrings so the script's own prose about `WORKTREES`
  cannot satisfy the assertion that the tuple must.

- **Quote-agnostic argv matching.** `_code_only` normalises source through
  `ast.unparse`, so a double-quoted argv in a script arrives single-quoted and a
  literal match fails silently. Noted in the guard that hit it.


## [0.34.0] — 2026-09-01

Closes [#93](https://github.com/Machai-Kydoimos/dependabot-audit/issues/93) and
[#96](https://github.com/Machai-Kydoimos/dependabot-audit/issues/96), both raised
from a Phase 8 hand-back of a `uv.lock` audit of PR #384. They are the same shape
from two directions: a claim this procedure makes **about itself** that its own
commands falsified, and nothing anywhere to catch it.

**Minor, not patch.** Phase 3 changes what it audits and what it runs to do it,
and Phase 8 gains a class the hand-back did not have.

### Fixed

- **Phase 3 audits the lockfile, and no longer executes the PR** (#93). The only
  recipe was `uv run --with pip-audit pip-audit --skip-editable`, and `uv run`
  syncs the project first — installing it editable and building any sdist in the
  resolution. Measured on uv 0.12.8 against a project whose `setup.py` writes a
  file when it runs: the file appears, and `pip-audit`'s own output names the
  project as `distribution marked as editable`.

  `--no-execute` runs Phases 0–3 and 6–7 and says *"every one of those is a
  network read"*. So the mode that exists for **a PR you have no reason to trust
  yet** ran that PR's build code — with no `MAY_EXECUTE` gate and no banner, both
  of which Phases 4 and 5 carry.

  The phase now exports and audits the lockfile: `uv export --frozen` into
  `$SCRATCH`, then `uvx pip-audit -r … --no-deps --disable-pip`. No resolution, no
  `pip`, no environment. **Verified in both directions**, because an auditor that
  cannot report dirty is worse than none: a clean export exits 0, and
  `jinja2==2.11.3` through the same command exits 1 with four advisories.

  Two further gaps closed with it. The recipe never said **which tree** — run in
  the user's checkout it audits the currently installed set and reports clean
  about a dependency set that is not the one under audit, the same silent-failure
  shape as the interpreter trap already documented beside it. And it audited **one
  resolution**; the export emits every fork with its marker, which is the scope
  `uv sync --locked` asserts and the install does not.

- **Phase 2's config differential stopped syncing the project too** (#93). Found
  by the new guard rather than by reading — `uv run <tool> check` is the same
  defect in the same `--no-execute` set, and it had been there since 0.32.0. It
  now reads `uv run --no-project --with <tool>==<locked>`, the spelling Phase 4
  already used. Measured on uv 0.12.8: plain `uv run` produces the build trace and
  a `.venv`; `--no-project` produces neither.

- **The `--no-execute` claim now says what it depends on.** *"Every one of those
  is a network read"* is a property of each phase's commands, not of its number,
  and a phase in that set which gains a command has to be checked against it. The
  paragraph says so, and the suite now enforces it.

### Added

- **A hand-back row carries its evidence, or it is `unproven`** (#96). Phase 8's
  deviation list classified as **plugin defect**, **prose gap** or **correct**,
  with no requirement to say how the class was reached — so a row's classification
  carried the authority of a measurement while resting on an inference. It is the
  only output in this plugin that classifies without citing; Phase 7's report has
  a verdict table and a confidence rule that is *"derived, not felt"*.

  A `plugin defect` row now names the command that failed and the exit status it
  returned. Where the run went straight to a workaround and never issued the form
  the procedure specifies, the class is the new **`unproven`** — the deviation is
  still real and still handed back, but its cause was inferred and the row says so.

- **"Did this happen?" before "is this mechanism right?"** The cheapest check is
  searching the session's own history for the command the row says failed. Where
  it was never issued, that settles the row in seconds, before any measurement.

- **An `unproven` row is a question, not a ticket.** The reader-facing half, and
  the one that actually broke: the same claim — that Phase 7's `git worktree
  remove` fails because Phase 5's `uv sync` leaves a `.venv/` — arrived on
  2026-08-30 and again on 2026-09-01 from two different audits, each with a stated
  mechanism, neither having run the plain command. Measured on git 2.55.0 and uv
  0.12.8 in a worktree of a repo with **no `.gitignore` at all**: `remove` exits 0.
  It gates on `git status --porcelain`, which omits *ignored* files, and uv writes
  a `.gitignore` containing `*` inside `.venv/`. The second row labelled itself
  unproven and became an issue anyway.

### Tests

- **`TestNoExecutePhaseBuildsTheAuditedProject`** holds every phase in the
  `--no-execute` set to running nothing that builds or installs the audited
  project, reading `reachable()` so it follows a phase into its ecosystem
  reference and into any script the phase is mechanised into later. It found the
  Phase 2 instance above on its first run, which is the argument for it.

- **The anti-vacuity check is pinned to literals, not to the document.** A pattern
  that matches nothing satisfies a negative guard in silence, so the discrimination
  is asserted against the two commands that shipped and the three that replaced
  them. A live check that the pattern still fires on Phase 5 sits beside it.

- **The guard scans code, not comments.** Its first version fired on a comment in
  `uv-lock.md` § Phase 2 warning against the very command it forbids — the failure
  `reachable()`'s own docstring names, reproduced by the guard written to use it.
  Whole-line comments are dropped before matching, and the explanation moved out of
  the fence into the prose where it reads better anyway.

- **Mutation checking caught a dead guard before it shipped.** The
  "did this happen?" assertion was written as an alternation and stayed green with
  half its rule deleted. It now binds both halves — the question and the place to
  look — and each half fails the guard on its own.


## [0.33.0] — 2026-08-30

Closes [#90](https://github.com/Machai-Kydoimos/dependabot-audit/issues/90) and
[#91](https://github.com/Machai-Kydoimos/dependabot-audit/issues/91), handed back
by the round-12 and round-13 replays of `fpga-board-sim` #365. Both are the same
shape: a phase names a step it never says how to take, so the run improvises one
— and an improvisation that comes up empty is indistinguishable from a real
absence.

**Minor, not patch.** Phase 0 gains a command in its state-changing block, and
Phase 2 gains a documented method where it had none. Both change what the phase
does and what its row can assert.

### Fixed

- **Phase 0 prunes stale worktree registrations before it fetches** (#90).
  `$SCRATCH` lives under `$TMPDIR`, so a reboot or a tmp sweep between two audits
  of the same PR deletes the worktrees and leaves their registrations behind. Git
  then refuses **the fetch** — one command before either `worktree add` — with
  `refusing to fetch into branch 'refs/heads/pr-<N>' checked out at '<a path that
  no longer exists>'`. The stale-worktree paragraph that exists for exactly this
  is keyed to `git worktree add` refusing, so Phase 0 died two commands before
  reaching its own remedy. Measured on git 2.55.0: stale registration → fetch
  exits 128; `prune` first → fetch and both adds exit 0; `prune` on clean state
  is a no-op.

  **The hand-back was wrong about both remedies**, and the issue records the
  measurement rather than the row: `git worktree remove` does *not* fail on a
  missing path (exit 0, and git's own message for the `add` case names it), and
  `git branch -D` was never needed once the registration was gone. `prune` is
  preferred for taking no path argument and clearing `pr-<N>` and `base-<N>`
  together.

### Added

- **Phase 2 has a way to reach a changelog** (#91). *"Read the changelog for
  every version in the gap"* had no method anywhere in the plugin — not how to
  find the project's repository, not how to name the release tag, not what to do
  when there is no release. `references/uv-lock.md` § Phase 2 now carries the
  repo lookup and a three-rung ladder: release notes, then a changelog section
  for that exact version, then the tag-to-tag commit range. **Say which rung
  produced the row**, for the same reason Phase 5 says which install ran.

- **The tag is matched against the release list, never constructed.** Measured
  2026-08-30 across one bump's three tools: `ruff` releases as `0.16.4` (and
  `gh release view v0.16.4` is *release not found*, while its **older** tags do
  carry `v`), `rumdl` as `v0.2.58`, and `python/mypy` publishes **zero** releases
  while tagging `v2.3.1`. A guessed prefix returns "release not found", which
  reads exactly like "this version has no notes" — so the audit reports an
  absence of evidence as evidence of absence for a version whose notes are
  sitting there.

  mypy is the worked example because it needs all three rungs: no releases, a
  `CHANGELOG.md` written per *minor* release so there is no `2.3.1` section, and
  then six commits in the range. It is also in the `dev` group of most repos this
  plugin audits, so the row that used to go quiet went quiet often.

### Tests

- **The repo's own pipe guard caught the first version of the ladder.**
  `gh api … | grep | head` reports `head`'s status, so a failed lookup yields an
  empty tag at exit 0 — the exact failure the new prose warns about, reintroduced
  by the code teaching it. The block now captures the release list, checks the
  status, and filters the variable.

- **`integration/test_live_changelog_sources.py`** holds the two claims about
  other people's repositories that the prose rests on: that mypy still publishes
  no releases, and that ruff and rumdl still disagree about the `v` prefix. They
  belong in the network suite precisely because they can change under us — if
  mypy starts cutting releases, the example should be revisited rather than left
  quietly asserting something that stopped being true.

- **A guard anchored to the whole of `material(2)` passed trivially** and was
  narrowed. `actions.md` § Phase 2 carries its own `compare` call, so a check for
  the commit-range rung was satisfied by a different reference's prose — the
  round-8 trap, recurring. Six mutants confirm the guards, including that one.

## [0.32.0] — 2026-08-30

Closes [#87](https://github.com/Machai-Kydoimos/dependabot-audit/issues/87) and
[#88](https://github.com/Machai-Kydoimos/dependabot-audit/issues/88), the two
deviations the round-11 replay of `fpga-board-sim` #365 handed back. Both are
Phase 5, and both are the same failure in different clothing: the phase asks for
a disclosure the run cannot actually make, so the auditor improvises one — and
the improvisation is not the same twice.

**Minor, not patch.** The fork list now covers the whole lockfile instead of the
changed set, and Phase 5 gained a documented salvage where it previously
recorded an accepted loss. Both change what the phase verifies and what its row
asserts.

### Fixed

- **`audit.py`'s fork list covers the lockfile, not the bump** (#88). It was
  built from `report["currency"]`, which only ever holds the changed set, so a
  package that is forked and *unchanged* produced no fork list at all — while
  `pins`, four lines above it, was already lockfile-wide and said so in its own
  comment. On #365 that left the auditor deriving the list by hand and the report
  asserting *"`rpds-py 0.30.0` … was verified by Phase 1 but not installed"*,
  false in its first half: `rpds-py` was never in the audited set.

- **The widened list keeps the two claims apart**, which is the half that makes
  widening safe rather than worse. Forks print under one of two headings —
  `artifacts verified against the registry above` for packages this run audited,
  `NOT audited by this run` for the rest. Artifact provenance really is
  changed-only; conflating it with pin structure is how #88 started.

- **Three sentences in `references/uv-lock.md` that were wrong about their own
  script.** The scope table promised *"**every** fork's artifacts"*, the
  qualifier row claimed *"Phase 1 checked all of them"*, and the worked example
  modelled *"the 3.11 fork of `rpds-py` was verified but not installed"* — the
  exact wrong sentence a real report then reproduced.

- **An unparseable version in an unaudited fork no longer aborts the run.**
  `_version_key` refuses what it cannot order, and for a package under audit that
  refusal is the contract. The fork list is wider than the audited set now, so
  the same raise would let one odd version in a package nobody asked about kill a
  complete audit. Disclosure is not judgment: `_ordered_pins` falls back to
  lockfile order.

### Added

- **A documented salvage for a refused `--no-build`** (#87). The refusal names
  the package it rejected; when that is a *dependency* rather than the project,
  the loss can be narrowed instead of accepted. The lockfile already identifies
  the offenders — a package with no `wheels` array is one uv must build — so
  excluding every package that *has* one leaves the offenders as the only source
  builds, and yields a falsifiable row: *"4 of 6 third-party packages resolved to
  wheels; `actionlint-py` was the only sdist built"*.

  This existed as a gap because the same refusal on the same PR produced two
  different rows in two consecutive replays: round 10 fell back to a plain
  `uv sync --locked` and dropped the wheels claim, round 11 improvised the
  enumeration for 36 of 38 packages. Phase 5's own text says *"'Frozen install
  passed' is not the same claim in the two cases"*, so a non-deterministic row
  here is a defect rather than a preference.

  Two boundaries ship with it, both measured on uv 0.12.7: the narrowing **proves
  less than a true `--no-build`**, because the conceded package's build code did
  run; and uv **fails fast, naming one package per run**, so the lockfile read is
  a predictor and the sync remains the proof.

- **The recipe counts packages, not lockfile blocks** — a correction the replay
  made to the first version of it, which had shipped in this same branch. Run
  against #365 it printed *"held 37 of 38 third-party packages to wheels"*, and
  the lockfile has 39 blocks, 38 remote, but **37 remote packages**: `rpds-py` is
  forked across two. The block-wise count inflated both halves of the row and
  passed the duplicate to `--no-build-package` twice. Corrected, the same lockfile
  yields *"36 of 37"*, with no duplicate. A forked package is now held to a wheel
  only if **every** one of its forks has one, since uv must build the fork that
  does not. Getting this wrong is the sharpest possible version of the phase's own
  failure mode: a row whose entire value is that a reader can check the numbers.

- **The local-versus-remote distinction the recipe turns on.** Filtering on
  `registry` looks equivalent and is not — measured, uv also emits `url` and
  `git` (remote, and a `url` wheel *does* carry a `wheels` array) alongside
  `editable`, `virtual` and `directory` (local). Registry-only filtering lets a
  `url` or `git` dependency escape both the exclusion and the denominator, so a
  package built from source goes unnamed in a row claiming to name them all.
  This was caught by the guard below, on the first version of the recipe.

### Tests

- **The salvage guard runs the documented snippet**, against a fixture carrying
  every `source` kind uv was observed to emit, rather than matching it for
  substrings. Inverting the condition to `not p.get("wheels")` — the mistake that
  makes the recipe a no-op — leaves every substring in place, so a
  pattern-matching guard cannot tell the recipe from the one that does nothing.
  Four mutants confirm it: the inversion, the dropped local filter, the
  registry-only filter, and a `LOCAL` set missing `directory`.

- **The cross-artifact quotation guard reads its quote out of the prose** instead
  of holding a third copy of the literal. It was passing by agreeing with itself;
  derived, a reworded script fails until the reference is brought along.

## [0.31.0] — 2026-08-30

Closes [#85](https://github.com/Machai-Kydoimos/dependabot-audit/issues/85), which
the round-10 replay of `fpga-board-sim` #365 found while validating 0.30.1 — and
which shows why that validation is a separate gate from the test suite.

**0.30.1 shipped green, mutation-checked, and inert.** Its neutralisation is a
`--run` fragment, and the replay never built a `--run` to put it in: Phase 4 took
ruff and rumdl through `gate_diff.py` and compared **mypy** — the
project-importing gate the whole `--no-project` exception exists for — with a
bare `cd` into the worktree and two `uv run` calls. The residue fix reached
nothing, and three older guarantees went with it: `require_clean_worktree`, the
`reset --hard` between runs, and the tree snapshot the phase exists to take. The
run then reported *"no improvisation affecting the evidence — every phase ran
through the plugin's own scripts (`gate_diff.py`) as written"*, which is the
Phase 8 line a reader trusts to decide whether to re-check.

Two candidate causes, and the fix closes both rather than settling which:

- **the example was a fragment.** Every other block in Phase 4 is a whole
  `python3 "$G" --tree …` invocation; the project-importing one was a lone
  `--run` line, reading as advice about a flag rather than a call to make.
- **the prose arguably licensed it.** *"A read-only gate has no tree diff to fall
  back on, so this capture is the measurement"* is a defensible warrant for
  capturing the output by hand.

**Minor, not patch.** Phase 4 now takes a tree measurement for a class of gate it
was only comparing by output, and its report says so — a change to what the phase
verifies.

### Fixed

- **The project-importing example is a whole invocation**, preamble and all,
  matching every other block in the phase. So is the residue example from 0.30.1,
  which had the same fragment shape and the same consequence.

- **A new rule, "through the script, not beside it — including the gates with no
  write mode."** It names the #365 bypass, what it costs, and the sharp half: for
  a read-only gate, a tree delta is not a lesser signal but the **only** thing it
  can contain — a gate that writes nothing, measured as having written something,
  has measured its own cache. That makes 0.30.1's neutralisation matter most
  exactly where it looked least relevant.

- **The warm-up line no longer reads as a licence.** "Warming beforehand is a
  `--run` you discard, not a licence to drive the tool by hand."

- **`.pre-commit-config.yaml`'s header stopped arguing a policy that reversed.**
  It claimed branch protection and rulesets were unavailable "on this repo's plan
  (private repo, free org)", so CI "can never be made required" and the hooks
  "are what can actually stop a bad commit". The repo went public 2026-08-15 and a
  ruleset on `main` requires six contexts, so a red check blocks the merge. The
  stale text argued the opposite. The version-pinning paragraph below it was
  correct and is untouched.

### Tests

Two guards in `tests/test_skill_prose.py`, **structural rather than textual on
purpose**: every Phase 4 block that pins a version under test must contain an
actual invocation, and the neutralisation must sit inside one. A guard asserting
the phase *says* to use the script would have passed against the text that did
not elicit it — 298 tests did exactly that, which is the whole lesson of #85.
Both mutation-checked by restoring each example to its fragment form.

### Replay

`fpga-board-sim` #365, headless against the working tree, **before** this PR
rather than after — 40 turns, 8m12s, $4.16.

`gate_diff.py` calls went 3 → 5, and mypy is now one of them:

```
--run warm-locked "PYTHONDONTWRITEBYTECODE=1 uv run --group dev --with mypy==2.3.0 mypy ."
--run locked      "PYTHONDONTWRITEBYTECODE=1 uv run --group dev --with mypy==2.3.0 mypy ."
--run proposed    "PYTHONDONTWRITEBYTECODE=1 uv run --group dev --with mypy==2.3.1 mypy ."
```

The discarded warm-up run is the new warm-up line being followed. The report's
disclosure is now accurate — it names its one real improvisation instead of
claiming none — and the run's own hand-back reads: *"That guidance worked — I
took mypy through the script with a discarded warm-up run, and the uv-provisioning
artifact it warns about did not appear."* Verdict unchanged and correct: merge
as-is, then follow up.

## [0.30.1] — 2026-08-30

[#83](https://github.com/Machai-Kydoimos/dependabot-audit/issues/83), handed back
by the `fpga-board-sim` #365 replay against the released 0.30.0 and classified a
prose gap. The hand-back was right that 0.30.0 opened a hole, and wrong about
where it is. Measuring moved the fix.

0.30.0 tells Phase 4 to drop `--no-project` for a gate that imports the project.
Such a gate writes its own bookkeeping into the tree `gate_diff.py` is measuring,
and the issue read the cache directories as the hazard — to be closed by checking
they are gitignored. Measured with `gate_diff.py` itself, that is the one shape
that never was:

| What the gate leaves behind | Reaches `snapshot_changes` |
|---|---|
| `.mypy_cache/`, `.ruff_cache/`, `.pytest_cache/` | **never** — each tool writes a `.gitignore` of `*` into its own cache; measured on mypy 1.18.2, ruff 0.14.2 and pytest 8.4.2 against a repo with no `.gitignore` at all |
| another untracked directory, `__pycache__/` above all | **not by default** — `git status --porcelain` collapses it to `dir/`, and `_content_key` returns `None` for anything that is not a file, so the entry is dropped before it can be compared |
| a file at a non-ignored path — `.coverage`, `coverage.xml`, `junit.xml` | **always** |

So the proposed fix would have sent auditors to gitignore directories that were
already invisible, while the shape that does fire went unnamed. Both false
findings reproduced end to end, and neither run touched a line of the repo's code:

- a real `coverage 7.6.1 -> 7.13.0` bump, `.coverage` untracked and unignored, in
  the **default** git configuration, reports `~ .coverage  both act, different
  result — the fix itself changed`. That is the destructive-fix vocabulary — the
  finding this phase exists for — spent on coverage's own data file.
- with `status.showUntrackedFiles=all` in the repo under test, which un-collapses
  the directory row, a real `pytest 8.4.2 -> 9.1.1` bump reports a `+`/`-` pair on
  `tests/__pycache__/test_ok.cpython-313-pytest-9.1.1.pyc` — *widened scope* and
  *narrowed scope* — because pytest stamps its own version into the name of every
  file it rewrites.

**Patch rather than minor.** Phase 4 verifies what it always did, on the tree it
always did. What changes is that the measurement stops carrying the tool's own
bookkeeping into the answer, which is this file's definition of a patch: a fix
that only makes an existing claim true.

### Fixed

- **`references/uv-lock.md` Phase 4 names the residue that actually reaches the
  comparison**, as a table rather than a caution. The useful half is which rows to
  stop worrying about — a reference that says "check your caches are ignored"
  spends the auditor's attention on the two rows that were never visible.

- **The neutralisation goes in the command, not in the repo's `.gitignore`.**
  `PYTHONDONTWRITEBYTECODE=1 COVERAGE_FILE=../cov-<label>`, prepended identically
  to every `--run`: the runs have to be treated alike for the comparison to mean
  anything, and only the command reaches both. `gate_diff.py` runs each command
  with the tree as its working directory, so the relative `../` lands the data
  file beside the worktree and needs no handoff to resolve. That spelling is the
  suite's doing — the first draft wrote `$SCRATCH/cov-proposed` and the guard
  against a block reading the handoff without sourcing it failed on it — the
  guard 0.28.0 added, doing the job it was added for.

### Tests

Three guards in `tests/test_gate_diff.py` pin the mechanics the prose describes: a
collapsed directory is dropped, a file at the root is compared, and
`status.showUntrackedFiles=all` is the repo setting that turns the first into the
second. Two in `tests/test_skill_prose.py` hold Phase 4 to naming both the
neutralisation and the residue.

All five mutation-checked. Returning `_content_key`'s `None` to a sentinel fails
the directory guard and nothing else; deleting either token from the reference
fails one prose guard each.

## [0.30.0] — 2026-08-24

The `uv.lock` path had never been replayed in a fresh context. `fpga-board-sim`
#363 is a `setup-uv` bump, and every round of the replay gate since 0.24.0 rode
on it — so fifteen versions of the Python method shipped without a run against
it. The first one handed back five things, and one of them was a line that could
never have worked. Minor: it changes what Phase 2 verifies, what Phase 5's row
asserts, and what Phase 0 hands over.

### `references/uv-lock.md` named its scripts with a token nothing expands

Two measurements this repo already had, never put together:

- **0.24.0**: `${CLAUDE_PLUGIN_ROOT}` is expanded *textually at skill load*, into
  `SKILL.md`'s injected text. That is why Phase 0's `D=` line resolves.
- **#52**: `$CLAUDE_PLUGIN_ROOT` is *empty* in the Bash tool's environment —
  `ROOT=[]`, marketplace install and `--plugin-dir` alike.

A reference file is never injected. The model reads it off disk, so the token
reaches the shell intact and the path collapses. Measured, both spellings, with a
real handoff:

| `S=` as the file wrote it | resolves to |
|---|---|
| `${CLAUDE_PLUGIN_ROOT}/skills/dependabot-audit/scripts/audit.py` | `/skills/dependabot-audit/scripts/audit.py` — **exit 2** |
| `${SCRIPTS:?not in the handoff — re-run Phase 0}/audit.py` | the real file |

`references/actions.md` contains no occurrence, which is the whole reason three
replay rounds on an actions bump never ran one.

**#52 named this and closed with it still in.** Its premise was that *every*
handoff fails and every audit silently substitutes an absolute path; 0.24.0
corrected that for `SKILL.md` and nobody re-checked the files the harness never
touches. Its option 1 is what ships here:

> Drop the variable and have Phase 0 derive the plugin root once […] into a Phase
> 0 output the later phases consume.

`discover.py --shell` now emits `SCRIPTS=`, from
`os.path.dirname(os.path.realpath(__file__))`. Derived rather than named, which
also closes the hazard 0.23.0 flagged from the other side: an invented
`export CLAUDE_PLUGIN_ROOT=…/0.22.1` pins a release into a cache that keeps every
older copy, and carried forward it audits with a stale plugin silently and
successfully. **A path taken from the file that just ran cannot name a different
copy than the one running.** Phase 6's `ci_state.py` moved to the same spelling,
so exactly one line in the plugin depends on the substitution — Phase 0's
bootstrap — and a guard holds it there.

### Phase 5 could report a green install of an environment without the package

And the hand-back's reason for it was wrong, which is worth more than the fix.

It proposed `uv sync --locked --group dev`, because *"for a dev-dependency bump
the plain form installs none of the packages under audit"*. Measured on uv 0.12.5
against a project with two groups:

| Command | a `dev` package | a package in any other group |
|---|---|---|
| `uv sync --locked` | **installed** | absent |
| `uv sync --locked --group lint` | installed | **installed** |

`default-groups` defaults to `["dev"]`. `fpga-board-sim` declares its group as
`dev` and has no `[tool.uv]` table at all, so the flag was a no-op and Phase 5
did exercise the bump. **The row underneath is the defect**: a bump into `lint`,
`test`, `docs` — or anything once `default-groups` is narrowed — is not
installed, nothing fails, and the phase reports a reproduction for an environment
that never held the package the PR is about.

So the fix is not the flag. It is the fourth qualifier in the table Phase 5
already keeps — *which install*, *which interpreter*, *which forks*, and now
**which groups** — plus the reconciliation that closes it: Phase 1 derived which
packages moved, and `uv pip list` will say which ones are there. A memorised
`--group dev` is a no-op where it was proposed and still wrong where it matters.

### Phase 4 isolated gates that cannot answer without the project

`--no-project` was written for ruff and given for every gate. A type checker
denied the project's dependencies degrades every expression from them to `Any`
under `ignore_missing_imports`, and `warn_return_any` — implied by
`strict = true` — fires on code that is fine. `gate_diff.py` reports the
difference faithfully; the difference is the environment.

The audited repo documents the trap in its own `.pre-commit-config.yaml`, as the
reason its mypy hook is local rather than `mirrors-mypy` — and **so does this
repo's**, one directory over, as the reason `mirrors-mypy` is safe here. Both
were written before Phase 4 walked into it.

**Measured while fixing it, and not in the hand-back: `--no-project` is not
isolation.** uv 0.12.5, one command, one difference:

| The working directory | a project dependency, under `--no-project --with …` |
|---|---|
| has a `.venv` beside it | **importable**, and the project's own `src/` is on `sys.path` |
| has none | absent |

The overlay layers on a `.venv` when it finds one. Phase 4's worktree is fresh,
so isolation is what it gets — the half that is wrong for a type checker. Worth
knowing anyway: a re-run after anything has synced there is a different
measurement wearing an identical command, and the phase compares three of them.
`--with` still pins the version under test without the flag, measured against a
project locking `iniconfig 2.1.0` where `--with iniconfig==2.0.0` resolves to
2.0.0.

### Phase 2 called its scope test "one `grep`", and two ordinary cases fall outside it

Both return a confident `inert here` nobody established. `references/uv-lock.md`
gains a `## Phase 2` section for them, and Phase 2 joins the split phases.

**A changelog entry naming a dependency.** `rumdl` 0.2.60 ships one line —
`deps: update h2 to 0.4.16`, under **Fixed**, no `Security` heading — and that is
RUSTSEC-2026-0258 / GHSA-q83h-524g-xf6h, *h2 unbounded empty DATA frames*. The
crate is Rust inside a Python wheel, so `pip-audit` is clean under both
`-s pypi` and `-s osv`, correctly, and this repo's config has never heard of
`h2`. The wheel answers it — PEP 770 puts an SBOM in `.dist-info/sboms/`:

```
rumdl-0.2.60-py3-none-manylinux_2_28_x86_64.whl   6,657,559 bytes
  dist-info/sboms/rumdl.cyclonedx.json   CycloneDX 1.5, 178 components
  h2, reqwest, hyper, jsonschema         absent
  tokio                                  present — so the document is the shipped set
```

**`Cargo.lock` says the opposite, and that is the trap.** It records
`[dev-dependencies]`, and `rumdl`'s `Cargo.toml` at `v0.2.60` has
`jsonschema = "0.46"` under exactly that heading — which is what pulls `reqwest`
→ `h2`. Reaching for the lockfile finds the crate and calls it exposure.

A wheel with no SBOM is **`underivable`, never clean**. PEP 770 coverage is
partial, so absence of the file says nothing about absence of the crate, and the
recipe exits non-zero saying so rather than printing an empty component list.

**A rule the repo disables.** `rumdl` 0.2.59 fixed a *destructive* autofix — "stop
reflow from joining a setext heading into its underline" — in a repo running
`rumdl check --fix` on every Markdown commit. `disable = ["MD013", "MD036"]` is a
claim about a file; the verdict is about the tool:

```
$ uv run rumdl check README.md                 Success: No issues found
$ uv run rumdl check --no-config README.md     Issues: Found 32 issues
```

That is Phase 6's rule one phase over. A red check does not carry a verdict until
it is attributed; a config line does not carry `inert` until the tool has been run
both ways. The hand-back classified this row **correct** — ordinary judgement, not
a gap — and it is promoted because the judgement was right and the procedure did
not ask for it.

### Tests

Fifteen guards in `tests/test_skill_prose.py`, four classes, every one
mutation-checked against the prose or the code it was written for, with the
mutation asserted to have landed before the result was read.

**Two did not discriminate as first written, and both are the house failure.**
One asserted `"group"` appeared in Phase 5 and passed against `grouped`,
`--group` and `default-groups` alike; it now reads the qualifier *table*. The
other asserted `uv pip list` and passed against prose that has printed the
installed versions since 0.20.0 to answer a different question — the same shape
as the 0.24.0 guard satisfied by a narrative quoting the string it looked for. It
now asserts the filter is fed from the set Phase 1 derived.

`reachable()` had to learn the new spelling in the same commit, and the reason is
its own docstring: converting Phase 6's `C=` line silently emptied
`reachable(6)`, and four guards asserting on `ci_state.py`'s query went to
matching nothing. They failed loudly, which is the only reason this is a footnote
rather than an entry of its own.

### The replay

`fpga-board-sim` #365 — a grouped `python-deps` bump, mypy 2.3.0 → 2.3.1, ruff
0.16.3 → 0.16.4, rumdl 0.2.55 → 0.2.58 — audited end to end in a fresh context
via `claude -p --plugin-dir`, execution authorised deliberately because Phase 4
and Phase 5 both changed and `fpga-board-sim` is a repo this account can push to.
Checked against the session transcript's actual `Bash` calls, not the report's
recollection.

**It ran out of budget at Phase 7 and never printed a report, so there is no
verdict and no Phase 8 hand-back from it.** What it did reach is every surface
this release changes, and the transcript is the record:

| Changed here | What the run did with it |
|---|---|
| `SCRIPTS=` in the handoff | emitted, sourced, and `S="${SCRIPTS:?…}/audit.py"` resolved — the Phase 1 block ran **as written** and verified all three packages against PyPI |
| § Phase 2, the SBOM recipe | run against `rumdl` 0.2.58: `h2 -> no`, `hyper -> no`, `reqwest -> no`, `jsonschema -> no`, `tokio -> YES` |
| § Phase 2, the config differential | `rumdl check README.md` clean against `--no-config` finding 32, reproduced — then it went further and grepped the repo for setext headings, which is the question behind the question |
| § Phase 4, no `--no-project` for a type checker | `uv run --group dev --with mypy==2.3.0/2.3.1 mypy .`, both `Success: no issues found in 157 source files`. Under the old recipe those 157 files would have been resolved against nothing |
| § Phase 5, reconcile against Phase 1's set | `uv pip list --format=freeze \| grep -iE '^(mypy\|ruff\|rumdl)='` → `mypy==2.3.1 ruff==0.16.4 rumdl==0.2.58`, and it read `[tool.uv]` for `default-groups` unprompted |

**And it found a defect in the Phase 4 prose written one hour earlier.** Dropping
`--no-project` means the diagnostics comparison is a capture of `uv run`, and the
first invocation provisions the environment while the second does not. Both mypy
runs printed `Success: no issues found in 157 source files`; the diff came back
eight lines long, every one of them uv's own `Creating virtual environment` /
`Installed 35 packages` / hardlink warning. A read-only gate has no tree diff to
fall back on, so that capture *is* the measurement — a false difference of
exactly the kind this phase exists to catch, introduced by the fix for the last
one. Folded in rather than filed.

Two deviations visible in the transcript, both minor: a repeat `gate_diff.py`
call abbreviated `G="${SCRIPTS:?…}"` to `G="$SCRIPTS"`, dropping the fail-loudly
guard the file writes; and `gate_diff.py --help` was invoked by absolute path for
reconnaissance before first use. Neither changed a result.

`uv sync --locked --no-build --no-install-project` failed on `actionlint-py`,
exit 2, and step 2 succeeded — the known sdist-only case, reproduced, not a
finding against this bump.


## [0.29.0] — 2026-08-22

Phase 1's scope gate — the branch that decides whether Phases 4 and 5 get a
shell — was the reader's. `discover.py` derives it now, and the ecosystem with
it. Minor: it changes what Phase 1 verifies and what Phase 0 hands over.

### The gate's rule was already deterministic; only its execution was not

`SKILL.md` stated both halves exactly. For a lockfile bump, *"the diff should
touch **only** the manifest and the lockfile"*. For an actions bump,
`actions.md`'s *"every changed line across them is a `uses:` line or its trailing
version comment. That is the invariant."* A file-set test and a line-kind test —
and the block under them printed a sorted file list and left the decision to
whoever read it.

Three properties made it worth a script rather than a paragraph:

- **It is the highest-stakes branch in the procedure.** A gate that answers
  *clean* is what lets the audit go on to install the PR's dependencies and run
  its test suite. Every way it goes wrong goes wrong in that direction.
- **Its failure modes are all silent.** An unset `$BOT_COMMITS` iterates zero
  times; an empty file list has nothing to object to; the API caps a commit's
  `files` at 300 and says nothing about the rest; a withheld `patch` reads as no
  lines beyond the pin. Each arrives as *no objection*.
- **The wrong heuristic is the reachable one.** `SKILL.md` warns that *"the count
  of files is not the invariant and never was"*, which is a warning precisely
  because a four-file diff invites the count.

### Measured on the PRs the rule was written from

`fpga-board-sim` #334 is the case 0.28.0 named and could not close. Its branch
carries the bot's bump, a maintainer's `style: reformat docs for ruff 0.16`
fixup, and a merge of `main`; gated on the union it reported eight files as
*"this bump reaches beyond the manifest and lockfile"* and stopped **before Phase
4** — the phase that would have measured `ruff` 0.15.22 → 0.16.0, this plugin's
founding Phase 4 observation, occurring for real. 0.28.0 emitted the authorship
split; the loop that consumed it stayed in the shell and the judgement stayed
with the reader.

Now, unchanged inputs:

```
=== scope: CLEAN  [uv.lock]
    every changed file is the manifest or the lockfile
    read from the bot's own commits
      pyproject.toml
      uv.lock
```

Every documented case, live:

| PR | Result |
|---|---|
| `fpga-board-sim` #363 — `setup-uv` 9.0.0 → 10.0.1 | `CLEAN [github-actions]` |
| `fpga-board-sim` #364 — grouped `python-deps`, 3 updates | `CLEAN [uv.lock]` |
| `fpga-board-sim` #334 — bot + human fixup + merge | `CLEAN [uv.lock]` — the false Hold, closed |
| `cli/cli` #14091 / #13981 / #14147 — two, three and four files | `CLEAN [github-actions]`, all three |
| `cli/cli` #14049 — two-parent head | `CLEAN [github-actions]`, base not substituted |
| `dependabot-audit` #60 — human PR, no bot commit | `BEYOND [unknown]`, the four files 0.28.0 named |

### The first implementation failed two of the three PRs the rule cites

Written from `actions.md`'s wording, the line test accepted a `uses:` line and
its **trailing** comment. Replayed against `cli/cli` it fired on #13981 and
#14147:

```
-#   - actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0 # v7.0.0
+#   - actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
```

A compiler that emits workflows records the pins it wrote in a header block, so a
correct bump changes the `uses:` line **and** the comment naming the same pin. The
trailing version comment is not always trailing. Read as a line beyond the pin,
the gate produced the false Hold the file-count rule exists to prevent, on two of
the three PRs `actions.md` offers as its own measurement — and the synthetic
patches in the first test pass could not see it.

`actions.md` said *"a `uses:` line or its **trailing** version comment"*, which is
the wording the first implementation was written from — so the reference was
inaccurate against the very PRs it cites, and is corrected here too. It now names
the comment half properly and points at `$SCOPE_GATE` rather than restating a rule
the script owns.

**Fixed in the same change**, not deferred: a YAML comment cannot execute, so it
is inside the pin for a gate that asks what the diff makes *run*. Comment lines
are counted and reported rather than ignored, because a pin manifest is how a
generated workflow announces itself — which is a Phase 7 row of its own.

### `$BOT_COMMITS` stops crossing the shell boundary

It was emitted for Phase 1 to iterate, and `$SCOPE_GATE` is the answer that loop
was computing. Emitting both would leave the shell holding everything it needs to
roll a second gate by hand, which is how a prose copy and a script copy drift. It
stays in the report output and in `--json`, where it is evidence rather than an
input. `$HUMAN_COMMITS` has no script consumer and still crosses.

Two new outputs replace it — `$ECOSYSTEM` and `$SCOPE_GATE` — and Phase 1's block
is now the branch it always described:

```bash
[ "$SCOPE_GATE" = clean ] \
  || { echo "scope $SCOPE_GATE — report it, and STOP before Phase 4" >&2; exit 1; }
```

Unset compares equal to nothing, so a missing handoff stops here rather than
reading as clean.

### The tip worktree was gated in prose only

Removing Phase 1's `[ "$BRANCH_POINT" = rewritten ]` branch left `$BRANCH_POINT`
with no shell consumer at all, and the completeness guard added in 0.26.1 said
so. Following it found the block that creates `$SCRATCH/tip-<N>` running
unconditionally under a paragraph that says to run it only when the base was
rewritten — 0.28.0's own lesson, one phase along. It now tests the variable.

Phase 0's substitution list also claimed *"Phase 1 takes its scope diff from
`pr-<N>^..pr-<N>`"*, in `SKILL.md` and in `discover.py`'s report. Phase 1 needs no
substitution now: it reads the bot's own commits, and a commit carries its own
diff with no range to be wrong about. Where the split is *also* underivable the
gate answers `underivable` rather than falling back to a range that, under a
rewritten base, is the entire divergence.

### Two more defects, found by review and by replay

- **A patch withheld in one of the bot's commits was covered by another's.** The
  union kept the half that *was* readable, so it read as "these are the lines
  that changed" while the lines the API never sent were the ones the gate would
  have objected to. Withheld on either side is now withheld for the union.
- **The unrecognised-manifest branch could answer `beyond` while naming nothing.**
  A `pyproject.toml`-only bump — pip with nothing locked — is all manifest, so the
  beyond list filtered to empty and the verdict fired anyway. It reaches a report
  as *"this bump reaches past the manifest"* over a blank list, which is the one
  verdict a reader cannot act on. There is no scope rule for that ecosystem, and
  `underivable` says so.

### Replayed end to end

`fpga-board-sim` #334 under `claude -p --plugin-dir`, in a fresh context against
the unreleased plugin. Phase 0's report returned:

```
=== scope: CLEAN  [uv.lock]
    every changed file is the manifest or the lockfile
    read from the bot's own commits
      pyproject.toml
      uv.lock

RESULT: ORDINARY — 0 finding(s)
```

Phase 1's block then ran as written, `[ "$SCOPE_GATE" = clean ]` passed, and the
audit **continued into Phase 2** — reading `ruff` 0.16.0's release notes, which is
the phase the gate used to stop one short of. The run ended on a spend limit
rather than a verdict; what it establishes is the gate, and the gate is what
changed.

### Measured

| | 0.28.0 | 0.29.0 |
|---|---|---|
| `SKILL.md` | 74,778 B / ~18,694 tok | **73,786 B / ~18,446 tok** |
| `discover.py` output, `fpga-board-sim` #363 | 1,026 B | **1,201 B** |
| `discover.py` output, `cli/cli` #14147 | 1,354 B | **1,889 B** |

`SKILL.md` is re-read from cache every turn and the script's output only from the
turn it lands on, so on the 52-turn shape measured in 0.16.0 the net is about
**−11,000 token-turns for a lockfile bump and −7,000 for the worst actions case**
— roughly half a cent a run. The saving is real and it is not the reason: a rule
in prose costs tokens every turn *and* can be misapplied, and this one was
misapplied on the PR that mattered most.

### Tests

Thirteen cases in `tests/test_discover.py`, each a failure the prose already
names: the four-file trap, a `with:` edit past the pin, the generated pin
manifest, the bot-versus-branch split, a withheld patch, an empty file list, the
API's cap, an unsupported lockfile, a rewritten base with no split. Five
load-bearing behaviours were mutation-checked and each was caught by exactly the
intended case.

Five prose guards moved with the rule rather than being deleted:
`test_phase_1_gates_on_the_bots_own_commits` now reads Phase 1's prose (the claim
the report makes) and requires the shell to consult `$SCOPE_GATE`;
`test_every_capture_of_git_output_is_checked` covers the one remaining half of
the split; the unset-fallback canary asserts the gate moved rather than vanished.

## [0.28.0] — 2026-08-22

One sprint, one release: a producer/consumer sweep of the plugin — for every
value a phase produces, who consumes it; for every value a phase consumes, who
reliably produces it. Six defects, each with its own commit below. Minor: several
of them change what a phase verifies or what the report asserts.

### Phase 0's handoff was sourced once, in Phase 0

`discover.py --shell` writes `$SCRATCH/phase0.env` and Phase 0 sources it. That
was the only `. "$SCRATCH/phase0.env"` in the plugin, and eleven blocks across
`SKILL.md` and both references read what it carries.

`SKILL.md` has stated the mechanism since 0.26.0, in its own Phase 0:

> Measured against this harness, two separate calls: an `export` in the first is
> **unset** in the second, shell functions likewise, and each call is a new shell
> process.

Re-measured across two Bash calls in one session: a variable exported in the
first reads `<UNSET>` in the second, a function defined in the first is `not
found`, the pids differ. 0.26.0 fixed `$SCRATCH`'s **derivation** so the next
call could name the directory. **Recomputable is not recomputed** — nothing told
the next call to go and find it.

Each consuming block, run alone with nothing sourced:

| Block | Result |
|---|---|
| Phase 1, `uv.lock` | `/base.uv.lock: Permission denied` — loud |
| Phase 4, `gate_diff.py` | `error: /base-1 is not a git worktree`, exit 2 — loud |
| Phase 6, `ci_state.py` | `Could not resolve to a Repository with the name '/'`, exit 2 — loud |
| Phase 7, cleanup | `fatal: '/pr-1' is not a working tree`, exit 128 — loud, worktrees left registered |
| Phase 0, the gate read | **exit 0, 17,623 bytes from the INDEX** |
| **Phase 1, the authorship gate** | **silent** |

Two of those are silent, and both are the failure this procedure is most explicit
about. `for c in $BOT_COMMITS` over an unset variable iterates zero times, so the
gate's file list is empty and it passes — *"the one outcome worse than a false
Hold"*, in `SKILL.md`'s own words. And `git show ":path"` with an empty rev is
the **index**, so Phase 0's gate list came from the user's working tree — the
exact failure its *"read every one of them at a ref"* rule exists to prevent,
reached through an empty variable rather than a forgotten ref.

**Fixed.** Every block that consumes a Phase 0 output opens with three lines that
re-derive `$SCRATCH` and re-source the handoff, `Phase 0's own later blocks
included` — the exemption is the block that *writes* the handoff, keyed on
`--shell >`, not the phase it sits in. Repeated in all eleven rather than stated
once, because a step merely implied is one that gets skipped. The `||` is the
load-bearing half: a `.` on a missing file returns 1 and keeps going.

Phase 1's underivable fallback moved out of the prose and into the shell too:
Phase 0 already emits `# BOT_COMMITS is absent` commented out so the variable
stays unset, and the prose already said to gate on the whole diff instead. The
block did not implement it.

`git` state is the exception and now says so. The `pr-<N>` ref lives in the
repository, so `git show "pr-<N>:…"` works from any call; only the shell handoff
is lost, and conflating the two is what made the reload look unnecessary.

**Replayed** two deliberately separate calls against two PRs, for the two
branches of the gate. `fpga-board-sim` #363 (bot PR, `BOT_COMMITS` set): call 2
opened with `SCRATCH` and `BASE_SHA` unset, re-derived, re-sourced, gated
correctly on `.github/workflows/ci.yml`. `dependabot-audit` #60 (human PR, split
absent) is the discriminating one — same handoff, both blocks:

```
OLD:  (empty)  0 files. The gate passes.
NEW:  BOT_COMMITS underivable — gating on the whole $BASE_SHA..pr-60 diff
      CHANGELOG.md
      .claude-plugin/plugin.json
      skills/dependabot-audit/references/actions.md
      tests/test_skill_prose.py
```

Four files, one inside the plugin's own `references/`, reported as a clean scope
by the shipped gate.

**Tests.** Three guards, each mutation-checked: every block reading a handoff
value sources it; the `$SCRATCH` derivation exists in exactly one form; a value
the prose promises to handle when unset is tested rather than iterated. The
second mutation was run twice — the first attempt used a malformed `sed` that
never landed, and the guard passed. A mutation that does not mutate reads exactly
like a guard that discriminates.

### `ci_state.py` made five API calls on a green PR and read one

Measured with a logging passthrough around `gh`, on `fpga-board-sim` #363:

```
CALL: api graphql <rollup>                                  <- the answer
CALL: api repos/…/commits/bddcc474b7/check-runs --paginate  <- never read
CALL: api repos/…/commits/bddcc474b7/status                 <- never read
CALL: api repos/…/commits/bddcc474b7/check-runs --paginate  <- never read
CALL: api repos/…/commits/bddcc474b7/status                 <- never read
```

Note the SHA. `pr-<N>^` and `$BASE_SHA` are the same commit on a genuine
one-commit bot PR — `SKILL.md` says so and calls it the ordinary case — so the
ordinary case fetched **the same two endpoints twice** and consulted neither.

Every consumer is gated on red: `attribute()` runs once per red context, and
`parent_names` renders only under `if report["red"] and …`. `conclusions_at()`
ran before `analyse()` had computed `report["red"]`, so it could not know. The
script already applied this reasoning one rung lower — *"Only worth two calls
when there is a red row for them to qualify"*, on `committed_at` — and had never
applied it to the pair above it, which is twice the calls and paginates.

**Fixed.** `analyse()` hoisted above the comparison reads, both gated on
`report["red"]`, and the merge base fetched only when the parent has no runs —
the single branch `attribute()` reads it in. `committed_at` follows the dict it
qualifies.

**Replayed** live through the same tap, on the two PRs this phase's prose cites:
`fpga-board-sim` #363 (green) **5 → 1** calls, verdict unchanged; `BIRSAx2/mdcat`
#6 (red) **7 → 4**, verdict unchanged at `PRE-EXISTING — red at b1b0dd4c1
(pr-<N>^) too`. The second is the one that had to be checked: it is the case the
attribution comparison exists for, and the saving must not reach it. It does not.

**Tests.** Three guards on the *shape of the work* rather than its output, each
mutation-checked. The third — *a red PR with no runs at the parent still falls
back to the base* — was written first, as the control: a saving that also removes
a fallback is not a saving.

`--json` now reports `parent_names: []` on a PR with nothing red. Nothing reads
it there and no phase consumes `--json`, but it is visible.

### `MAY_EXECUTE` was emitted, documented as load-bearing, and read by nothing

`discover.py` derives the classification — a fork PR, a non-bot author, an
account without `push` — and reduces it to one bit. Phase 0's prose said why:

> Reducing it to the one bit later phases actually branch on is what
> `MAY_EXECUTE` is.

No phase branched on it. Every occurrence in the plugin was the emit, the
key-list comment, and two sentences explaining its purpose; the gate itself lived
only in Phase 0's own table, six phases before the phases that execute, as advice
to *run `--no-execute`*.

**Fixed.** Both `uv-lock.md` blocks that run the audited repo's code — `gate_diff.py`,
and the frozen `uv sync` pair — open with the test, and both phases' contract
lines say so. The test is **for `yes`, never against `no`**, measured across the
three states a block can see:

| | `[ = yes ]` | `[ != no ]` |
|---|---|---|
| `MAY_EXECUTE=yes` | proceeds | proceeds |
| `MAY_EXECUTE=no` | refuses, exit 2 | refuses, exit 2 |
| **unset** | **refuses, exit 2** | **proceeds** |

A block whose handoff did not load sees the empty string, and the negative form
passes it. The one direction this must never fail in is open.

**`BRANCH_POINT` was the same defect**, found by the guard written for
`MAY_EXECUTE`: emitted, said to be read by Phase 1, and read by no block — the
rewritten-base substitution was prose telling the reader to use a different
range. It is now a conditional in Phase 1's fallback, the one path where it still
decides anything: with `$BOT_COMMITS` derivable the bot's own commits are the
bump wherever the base moved to.

**`BASE_REF` is declared a diagnostic**, in the line the new guard reads. 0.26.1
settled that it is deliberately unconsumed; the exemption is now written down
instead of inferred from nobody having used it.

**Replayed.** The gate, derived live against four PRs:

| Repo | `push` | Author | `MAY_EXECUTE` |
|---|---|---|---|
| `fpga-board-sim` #363 | true | `dependabot[bot]` | **yes** |
| `dependabot-audit` #60 | true | human | no |
| `BIRSAx2/mdcat` #6 | false | — | no |
| `cli/cli` #14147 | false | — | no |

Row two is the one worth reading: full `admin` on a repo this account owns, and
still `no`, because the classification is two questions and only one is about
permissions.

The `BRANCH_POINT` substitution needed a base that had actually been rewritten,
and no reachable PR is in that state — CONTRIBUTING's fifth row. Built one: a
root commit, a commit vendoring a `supply-chain` tree, a third commit, a PR
branching from the third and adding `manifest.txt`, then the base rewritten so
the shared ancestor falls back past the vendored tree.

```
BRANCH_POINT=ok         -> 7 file(s): manifest.txt src.py vendor-{a..e}.txt
BRANCH_POINT=rewritten  -> 1 file(s): manifest.txt
```

Without the conditional the fallback gates on seven files and Holds a one-file
manifest bump for reaching beyond the manifest — the shape `SKILL.md` records
from a real Cargo bump at 14 files and 3,682 deletions. A first attempt at that
construction force-pushed *forward* rather than rewriting, so both ranges
returned one file and agreed; agreement there would have read as *the conditional
is unnecessary*.

**Tests.** Four guards, mutation-checked in five directions. The completeness
rule now runs **both** ways: 0.26.1's guards ask that every promised name is
produced, these ask that every produced name is consumed or named inert. The
drift was already bidirectional when 0.26.1 shipped — its own changelog says so —
and only one direction had a guard.

### Phase 2 required an input the handoff never carried

> *Requires: the Phase 1 script output, and the PR's `createdAt` from Phase 0.*

`discover.py` renders `createdAt` and the emitter never wrote it, so the input
crossed by being on screen. It is the class 0.26.1 closed for `$PERMS`, and it
slipped that guard twice over on punctuation: the phrase is `Requires:` rather
than `Requires from Phase 0:`, and the name is neither `$`-prefixed nor
upper-case.

**Fixed.** `CREATED_AT` is emitted, tabled, and read — Phase 2 computes the
cooldown boundary from it rather than subtracting three days by eye, and the
window is measured from when the **PR opened**, not from now, which is the drift
Phase 7's table already calls out in itself. `python3` rather than `date -d`,
which is GNU-only.

**The pin's own publish date reaches the report.** `locked_published` was
computed and rendered nowhere, so the currency block could say when the newer
release landed and never when the current one did. On `fpga-board-sim`'s live
lockfile:

```
=== pytest: locked 9.1.1 (published 2026-06-19T10:58:31Z) IS the latest
=== ruff: locked 0.16.3 (published 2026-08-13T15:16:27Z), registry latest 0.16.4  <-- NOT CURRENT
      0.16.4       published 2026-08-20T17:43:16Z
```

**And a hole in the guard itself**, which is the more useful half. Deleting the
`CREATED_AT` emit left **every test green**. `_produced()` unions the emitter's
names with `ASSIGNED.findall(self.shell[0])`, and Phase 0 documents the entire
handoff in a bash fence of `#   NAME=<...>` comment lines — so a requires-line
could be satisfied by the very key list it is meant to be checked against.
Comments are now stripped before that scan. It is the same failure `_code_only`
exists for one file over — *a rule must not be satisfiable by a comment claiming
it* — and it had been sitting under 0.26.1's two guards since they were written.

**Replayed.** `fpga-board-sim` #363, two deliberately separate calls:

```
call 1:  CREATED_AT=2026-08-17T13:07:54Z          # emitted
call 2:  cooldown boundary: 2026-08-14T13:07:54+00:00   # sourced, computed
```

**Tests.** The requires-line guard is widened to `Requires:` and paired with a new
one refusing a bare backticked name in such a line — the exact shape `createdAt`
had, so the hole is closed as a class rather than at the instance.

### Five findings had no row in Phase 7's verdict table

The table exists for one reason, which it states: *"leaving that function
implicit is how two audits with the same evidence reach different
recommendations."* A finding that lands on the fall-through — *"Everything
derived, nothing above matched → **Merge as-is**"* — is, in the report,
indistinguishable from no finding at all.

| Finding | Old verdict |
|---|---|
| a red required check labelled **underivable** | Merge as-is |
| an actions pin that is **not a 40-hex SHA** | Merge as-is |
| `BRANCH_POINT=rewritten` | Merge as-is |
| `BRANCH_POINT=suspect` | Merge as-is |
| `BRANCH_POINT=underivable` | Merge as-is |

The first is the worst: `ci_state.py` has three labels and the table had two, so
a red **required** check whose cause could not be established resolved to *Merge
as-is* on a PR that cannot merge.

**Fixed.** Five rows, in the *not a Hold on this bump* register the `pre-existing`
row already uses. The top-down rule now admits that some rows do not end the read
— *"take the first row that matches"* and a row saying *take the verdict from the
remaining evidence* were already in tension, and the `pre-existing` row has worked
that way since 0.20.0. `report-template.md` carries the reporting obligation,
since the template is what gets copied.

**Tests.** The label vocabularies are read from the **scripts' source** rather
than typed — `attribute()`'s `label`, `branch_point()`'s `verdict` — so a fourth
label in either script fails until the table names it. Two rounds of narrowing,
both driven by mutation rather than by review:

1. The first extractor took every lower-case string constant in the function and
   returned `compared`, `basis`, `login`, `sha` — dict keys and API field names.
   It would have demanded verdict rows for things that are not labels.
2. The first *assertion* asked whether the label appeared anywhere in Phase 7.
   Deleting the `underivable` attribution row left it **green**, because the word
   also appears in the confidence table and in the `BRANCH_POINT` row two lines
   below. A label now has to be found in a row that is **about** it.

Seven mutations, each verified to have landed before its result was read. All
seven caught.

### The procedure never read an exit status, in three places

A pipeline's exit status is its **last** stage, and `sort`, `sed` and `base64`
all succeed on empty input. So a failing `git` or `gh` produced empty output at
exit 0 — and in each case the empty result is the *reassuring* answer:

| Block | Measured | Reads as |
|---|---|---|
| Phase 1's scope gate | exit 0, empty file list | **clean scope** — nothing outside the manifest |
| Phase 6's trigger read | exit 0, empty output | **no `pull_request` trigger** → "CI is green for unrelated reasons" |
| `actions.md` Phase 4's interface fetch | two empty files, `diff` exit 0 | **no interface change** |

The third is the sharpest, because `actions.md` says of exactly this method:
*"'Inert here' is a result, not silence. Reaching it deliberately is this phase
working; reaching it by not looking is the failure."* The block could reach it by
not looking.

None of this is exotic to trigger: an unfetched ref is enough. Hit by accident on
a scratch clone where `pr-363` had been deleted — `fatal: ambiguous argument`,
then exit 0 and a clean gate.

**And the plugin already states the rule, two phases away.** `SKILL.md` § Phase 5
and `references/uv-lock.md` § Phase 5: *"Gate on exit codes. `cmd | tail && next`
gates on `tail`, so a failing suite sails through."* Phase 1's gate is the
highest-stakes command in the procedure — what stops the audit before Phases 4
and 5 execute the PR's code — and it was among the places the rule was not
followed.

**Fixed** by capture-and-check in all three, plus `PARENT` in Phase 6, whose
empty value silently weakened the attribution comparison. Exit 2 is this plugin's
*could not run*, and it is the honest answer: no evidence is not evidence of
nothing.

**Not fixed with `set -o pipefail`, and the measurement says why.** It catches
one of the three:

| Instance | none | `pipefail` | `-eo pipefail` |
|---|---|---|---|
| Phase 1 gate (full block) | 0 | **0** | 128 |
| Phase 6 trigger read | 0 | **128** ✓ | — |
| `actions.md` fetch | 0 | **0** | 1 |

It misses the gate because the failing pipeline is not the block's *last*
statement, and misses the fetch because the status was already non-zero and was
discarded by the control flow. Two of the three were *status unconsumed*, not
*status wrong*, and `pipefail` only fixes the latter. It also cannot live in the
shared preamble — the one instance it alone would catch is the block with no
preamble — and after capture-and-check no meaningful pipes remain for it to
guard.

`set -e` catches all three and is ruled out on a different measurement: every
script here exits **1** to mean *ran, found something*. Under `errexit` a
`discover.py` that reports a human commit on the bot's branch kills the block,
and Phase 7's head re-check aborts at exactly the moment the head moved.

**Replayed**, each block extracted from the file rather than retyped:

```
Phase 1 gate, ref missing            exit 0, empty  ->  exit 2
Phase 1 gate, bad commit in the list exit 0, empty  ->  exit 2
Phase 6 triggers, workflow absent    exit 0, empty  ->  exit 2
Phase 6 triggers, real workflow                     ->  exit 0, `on: push:`
actions.md fetch, bad refs           exit 0, "no interface change"
                                                    ->  exit 2, zero files written
actions.md fetch, v9.0.0 -> v10.0.1                 ->  28 diff lines, the
                                                       description-only change
                                                       0.27.0 documents
```

The first attempt at that last replay reported `exit=2` for the right reason and
the wrong cause — the preamble aborted on a missing `phase0.env` and the fetch
never ran. Recorded because it is the trap this release is about, met while
verifying the fix for it.

**Tests.** One guard, class-wide: no block reads `git` or `gh` output through a
pipe. jq's `|` inside a quoted `--jq` argument is stripped first, and **line
continuations are joined** — `actions.md` wrote the fetch as `gh api … \` then
`| base64 -d`, two physical lines and one statement, so a per-line regex went
green against the very instance the guard was widened for. Found by mutation,
which is the only reason it is not still green.

Also in `CONTRIBUTING.md`: the replay gate now says **run the block as written,
and read its exit status** — extracted rather than retyped, `$?` rather than
output — with the three measured shapes that look identical to success.

## [0.27.0] — 2026-08-21

Phase 4 for actions had one source and no way to check it. An action cannot be
run locally at two versions, so reading the release notes is the method rather
than the shortcut — which makes it load-bearing that what is read is complete.
It is not. Minor: it changes what Phase 4 verifies.

Measured on `fpga-board-sim` #363, `astral-sh/setup-uv` 9.0.0 → 10.0.1, where
v10.0.0 disables the cache under `enable-cache: auto`:

| Source | Conditions it names |
|---|---|
| the release notes | **3** — `pull_request_target`, `workflow_run`, `release` |
| `action.yml` description | **5** — "GitHub-hosted runners except for release, **tag push**, `pull_request_target`, and `workflow_run`" |
| `src/utils/inputs.ts` | **5** — `isTagPush` checked *first*, its own branch and its own log line, then the three-event `||` chain |

The word *tag* appears nowhere in the notes body. They were written from the
second `if` and missed the first.

### Added

- **Phase 4 reads the action's interface at both pins.** `action.yml` ships in
  the action's own repo, so it is fetchable at any SHA, and the diff answers the
  *a default input flips* row mechanically instead of by inference.

- **A description-only diff is a finding, not a clean bill.** `default: "auto"`
  is **unchanged** across #363 — what changed is what `auto` means — so a check
  asking whether a default flipped correctly answers *no* while the behaviour
  moves underneath it. The only place the fourth condition surfaced was
  description prose.

- **Where the notes and the interface disagree, the source settles it**, and it
  ships in the same repo at the same ref.

### Fixed

- **The trigger row could not see a tag push.** It greps for event names, and a
  tag push is not one: it is `push` with a `refs/tags/` ref. Grepping
  `pull_request_target:` and `workflow_run:` finds nothing; grepping `push:`
  matches nearly every workflow ever written. The row now names the shape —
  `push:` carrying a `tags:` key — and `release:`, which it had also omitted.

### The replay

Two targets, both merged PRs on a repo this account controls, chosen to exercise
the two branches — CONTRIBUTING's fifth row, since #363 alone reaches only one:

| Replayed | `action.yml` diff | Outcome |
|---|---|---|
| #363 — setup-uv 9.0.0 → 10.0.1 | description text only | **discovers** — the fourth condition exists nowhere in the notes |
| #333 — setup-uv 8.3.2 → 9.0.0 | one line: `prune-cache` `default: "true"` → `"false"` | **confirms** — v9.0.0 announces it under *🚨 Breaking changes* |

The second is the one worth having: interface and notes agree, the read costs one
call, and it returns a falsifiable *no surprises*. A method that only ever fires
is one nobody runs. It also exercises the *default input flips* row end to end —
`fpga-board-sim` sets `prune-cache` nowhere, so it takes the new default rather
than pinning the old one, and the finding is real rather than inert.

On #363 the original verdict of *inert here* was correct, and correct **by luck**:
that repo triggers on `push: branches: [main]` and `pull_request:` only. The same
procedure on a repo with `push: tags:` reports inert about a change that is live.

### Tests

Four guards, all mutation-checked against the pre-fix prose. One asserts the
phase carries the **query** rather than the advice to compare — a phase that says
"check the interface" and hands over no command is a wish, and the existing
handoff guards would have been satisfied by the sentence alone. 246 tests.


## [0.26.1] — 2026-08-21

Phase 6 declared a requirement the handoff has never carried:

> *Requires from Phase 0: `$HEAD_SHA`, `$BASE_SHA`, `$OWNER`, `$NAME`, `$PERMS`.*

`discover.py --shell` does not write `$PERMS`. It prints it in the
human-readable report and writes only the derived `MAY_EXECUTE` to `phase0.env`,
so a phase that sourced the handoff and read `$PERMS` would get the empty string.
The Phase 0 outputs table listed it too, under a heading saying *every later phase
consumes these and nothing else*.

**Patch rather than minor**, unlike the two fixes before it. Nothing was broken in
execution: no shell block reads `$PERMS`, so the empty value never reached a
command, and Phase 6 behaves exactly as it did. This only makes an existing claim
true, which is this file's own definition of a patch.

### Fixed

- **Phase 6's requires-line drops `$PERMS`**, and the direction of the fix is
  settled by the phase it was attached to rather than by convenience: Phase 0
  says the required-checks question "moved to Phase 6, which asks it per-PR in a
  form readable at `pull`". Needing no permission tier is the whole point of that
  design, so `$PERMS` in its contract contradicted it.

- **The outputs table lists only what crosses.** `$PERMS` comes off the row —
  it is read from the report *in Phase 0*, where the execution gate and the
  actionability question both use it, and the handoff carries `MAY_EXECUTE`
  instead, which is the decision `$PERMS` was consulted to make. The reason it
  cannot cross is now stated: `$PERMS` is a set of flags, `$PERMS.push` is how
  the gate addresses it, and there is no shell form of that.

- **`$BRANCH_POINT` goes on**, which the drift ran the other way. It is emitted,
  and Phase 1 reads it, and it was missing from the table that claims to be
  complete.

### Tests

Two guards, closing the class rather than the instance, as the issue that filed
it proposed: every name in a *Requires from Phase 0* line, and every `$VAR` row in
the outputs table, must be something Phase 0 actually produces — from
`discover.py`'s emitter or from Phase 0's own shell.

The emitter's names are read from the script's **source**, since the suite is
offline and `discover.py` needs `gh`. It is located by the header it prints
rather than by its function name: found by name, a rename would silently empty
the set and every guard would pass by matching nothing. The second half of
`_produced()` is not padding either — `$SCRATCH` never passes through the script,
so an emitter-only guard would call the most-used output of all a broken promise.

Both mutation-checked against the pre-fix prose, and re-checked afterwards by
restoring `$PERMS` to the requires-line, which fails as it should. 242 tests.

### Measured

Replayed against `fpga-board-sim` #363 as two calls, sourcing a real
`phase0.env`. Phase 6's four remaining requirements resolve —
`9cea0a0e9647`, `bddcc474b732`, `Machai-Kydoimos`, `fpga-board-sim` — while
`$PERMS` is empty exactly as the corrected table now says, `$BRANCH_POINT=ok` is
carried, and `$MAY_EXECUTE=yes` is what crosses in its place.

The drift was **bidirectional**, which the issue did not catch and the guards now
do: the table promised `$PERMS` and `$SCRATCH` while the emitter writes ten names
including `BASE_REF`, `BRANCH_POINT` and `MAY_EXECUTE`. `$SCRATCH` is legitimate —
Phase 0's shell assigns it directly — and `BASE_REF` is Phase 0's own cross-check
that no later phase consumes, so the completeness rule runs one way only: the
table may omit an output nothing downstream reads, but it may not promise one
that does not exist.


## [0.26.0] — 2026-08-21

Phase 0 wrote its handoff to a directory the next call could not name. The line
was `SCRATCH=${SCRATCH:-$(mktemp -d)}`, commented *"any directory OUTSIDE the
repo"* — which says what the directory must **be** and never that it must be the
**same one** next time. Everything downstream depends on the second property:
`$SCRATCH/phase0.env` is written by one call and sourced by another, and both
worktrees are addressed across calls. Minor rather than patch: it changes what
Phase 0 can hand to the phases after it.

Found by 0.24.0's deviation clause during the #51 replays and classified by the
run itself as a prose gap (#55). Two of three rounds hit it and repaired it
unprompted — one pinned `export SCRATCH=/tmp/tmp.5tGlKz9N3s` after the fact and
re-sourced at the top of every later call. Both reached correct results, which is
the point: the workaround that works is the one nobody reports.

### Fixed

- **`$SCRATCH` is derived rather than remembered**, so any call can recompute it:

  ```bash
  SCRATCH="${SCRATCH:-${TMPDIR:-/tmp}/dbaudit-${REPO/\//-}-<N>}"; mkdir -p "$SCRATCH"
  ```

  `REPO` moves above it, since the name is derived from it. A harness-provided
  `SCRATCH` still wins, and now for a stated reason: if one is exported into every
  call's environment it is stable by definition, and the derived default is what
  applies when it is not.

### Measured

Against this harness, two separate `Bash` calls — the claim the issue flagged as
worth checking rather than assuming:

| Carried across a call boundary? | |
|---|---|
| environment variables | **no** — `export PROBE_VAR=…` in call 1 is unset in call 2 |
| shell functions | **no** — each call is a new shell process (pid 3634427 → 3634720) |
| working directory | **yes**, while it stays inside the project — but a call that ends outside has its cwd **reset** back |

So nothing ambient crosses the boundary, and the one thing that appeared to —
cwd — is unusable here precisely because `$SCRATCH` is required to be *outside*
the repo. What is left is a path each call can recompute from what it already
has, which is the repo and `<N>`.

Replayed with the real `discover.py` against `fpga-board-sim` #363, the PR the
deviation was found on, as two separate calls:

| | Call 2 resolves `$SCRATCH` to | `phase0.env` | Outputs downstream |
|---|---|---|---|
| old | `/tmp/tmp.WxRwlYLFeq` — a new directory | absent | `$DEFAULT`, `$HEAD_SHA`, `$BASE_SHA`, `$BOT_COMMITS` all **silently empty** |
| new | `/tmp/dbaudit-Machai-Kydoimos-fpga-board-sim-363` | sourced | `main`, `9cea0a0e9647`, `bddcc474b732`, `9cea0a0e9647` |

Silently empty rather than erroring is what made it survive: a truncated
`$HEAD_SHA` matches no CI run and reads exactly like *CI never ran*.

### Added

- **The scratch rule now states both requirements**, not just the one about
  location, and says why the second exists. The Phase 0 outputs table row says it
  too, since that table is what later phases are told they may consume.
- **`TMPDIR` joins `EXTERNAL`** in `tests/test_skill_prose.py` — the allowlist of
  variables supplied by the environment rather than by a phase. Without it the
  forward-reference guard reads `${TMPDIR:-/tmp}` as an output one phase owes
  another. It caught exactly that on the first run of the fix, which is the guard
  working.

### Tests

Three guards, all mutation-checked against the pre-fix prose. The third needed a
second attempt and is the more interesting one: scanning all of Phase 0 for
stability language went **green on the pre-fix file**, matching the *caching*
paragraph — "persist the answers to these … Deriving costs one call" — which has
nothing to do with where the handoff is written. Re-anchored to the paragraphs
that name the scratch directory, it fails as it should. A guard that matches
anything anywhere stops discriminating. 240 tests.

### Not done, deliberately

`$PERMS` is listed in the Phase 0 outputs table and named in Phase 6's *Requires
from Phase 0* line, and `discover.py --shell` **does not emit it** — it prints
`$PERMS` in the human-readable report and writes only `MAY_EXECUTE` to
`phase0.env`. Same class as this fix, different cause, and the remedy is a choice
between two designs rather than a correction. Filed separately.


## [0.25.0] — 2026-08-21

Phase 2 asks whether a moving major tag exists, and the recipe it used to ask
with **crashed on the answer**. `git/refs/tags/<tag>` is *get all references in a
namespace*: given a name that is a prefix rather than a ref it returns the array
of everything matching, and `.object.type` against an array is a jq type error at
exit 1. The names Phase 2 asks with — `v1`, `v4`, `v10` — are exactly the
prefixes of the point releases beneath them, so the recipe failed on the question
it existed to answer and worked only on the exact tags Phase 1 already had from
the pin comment. Minor rather than patch: it changes what Phase 2 can establish.

Found by 0.24.0's own deviation clause, on its first replay against the shipped
text — the audit worked around it in one call, reached the right answer, and
would never have mentioned it before #50.

### Fixed

- **The tag recipe branches on the array** instead of dying on it, and reports it
  as what it is: `no such tag — these share the prefix: …`, enumerating the refs
  that do exist.

  **The array is the answer, not a failed call.** It arrives exactly when the
  question is answerable, and `expected an object but got: array` reads like an
  API fault — inviting a retry that returns it again and a report calling
  currency *underivable* when it was fully derivable. The same shape as
  CONTRIBUTING's `branches/<b>/protection` table: a confident-looking error about
  the wrong thing.

  The singular `git/ref/tags/<tag>` was rejected, and the reason is recorded: it
  does not crash, and it is worse. It answers a bare `404` to both "no such tag,
  and here is what does exist" and "nothing here at all", discarding the half
  Phase 2 needs. Not crashing is not the same as answering.

### Added

- **The three outcomes, as a table in `references/actions.md` § Phase 1** — object,
  array, `404` — measured rather than described. The first row is the one that
  keeps the fix honest: `actions/checkout@v5` returns the **object** although
  `v5.0.0`, `v5.0.1` and `v5.1.0` all sit under the same prefix. An exact ref
  wins. Without that row, "an array means no such tag" generalises to "a moving
  major tag returns an array", which is false and inverts the answer for every
  action that publishes one.

- **Phase 2 checks the tag line exists before reasoning about it.** The rule that
  a newer patch is not a gap holds only where a moving major tag exists — and one
  that existed can be discontinued.

  Measured on `astral-sh/setup-uv` 2026-08-21: `v1` through `v7` are refs; `v8`,
  `v9` and `v10` are not. The moving tag stopped at v8 (2026-03-29) and every
  release since stands alone, so above v7 a newer patch **is** a gap and reads
  like a registry currency gap; at v7 and below it does not. One repository
  answers both ways depending on the major under audit, which is why it is asked
  per bump rather than settled once per action.

### The replay

Both branches, on two merged PRs in a repo this account controls — the fifth-row
lesson in CONTRIBUTING, which is that a defect on a path no reachable PR
exercises survives this gate:

| Replayed | Phase 2 asks | Old | New |
|---|---|---|---|
| `fpga-board-sim` #363 — `astral-sh/setup-uv` 9.0.0 → 10.0.1 | `v10`, `v9` | `expected an object but got: array`, **exit 1** | `no such tag — these share the prefix: refs/tags/v10.0.0, refs/tags/v10.0.1`, exit 0 |
| `fpga-board-sim` #332 — `actions/checkout` 7.0.0 → 7.0.1 | `v7` | `commit 3d3c42e5…`, exit 0 | `commit 3d3c42e5…`, exit 0 — byte-identical |

Phase 1's exact tags (`v10.0.1`, `v9.0.0`, `v7.0.1`) agree under both recipes, so
the change is non-regressive on the case that already worked. The absent case
still exits 1 with a `404`, keeping the three outcomes distinguishable.

**The exit codes above were re-measured without a pipe.** The first run of this
replay read them through `| tr`, which reports `tr`'s status and showed the
crashing case as exit 0 — the trap CONTRIBUTING already records against
`claude plugin eval | head`, hit again in the harness written to verify the fix.

### Tests

Four guards in `tests/test_skill_prose.py`, anchored to the recipe block and to
the outcome table rather than to the phase, since Phase 1 hands off to two
ecosystem files and "the word appears somewhere in Phase 1" would be satisfied by
any of it. All four mutation-checked: verified failing against the pre-fix prose
before the fix was written. 237 tests.

### Measured

`astral-sh/setup-uv` moving-major refs, and `actions/checkout` as the control —
both publish `v1`–`v7`; only checkout still publishes one above that. The claim
that `setup-uv` publishes no moving major tag was **false and was corrected
before it reached the prose**: it publishes seven and discontinued the eighth.


## [0.24.0] — 2026-08-20

An audit that works around a defect in **this plugin** reports nothing about it,
and the report's silence reads as compliance. `fpga-board-sim` #363 ran to a
complete, well-formed report under 0.22.1 while `SKILL.md` had never loaded at
all: every row in it was true, and the procedure that produced them was not this
procedure. Phase 8 could not have asked — its scope was a landmine in the
*audited* repo or a portable trap in an ecosystem, and a defect in the plugin's
own machinery is neither. Minor rather than patch: it changes what Phase 8 hands
back and what the report asserts.

### Added

- **Phase 8 hands back the audit's own deviations**, separately from what it
  found about the PR: every shell command run that `SKILL.md` did not specify,
  quoted with the gap it filled, and every plugin file read directly rather than
  invoked as written. Each classified **plugin defect**, **prose gap**, or
  **correct**; printed, never filed, on the same contract as the landmine
  hand-back above it.

  The two categories are the two commands from the #363 transcript, and the pair
  is load-bearing: a clause asking only about surprising commands misses the
  `cat SKILL.md`, and one asking only about plugin files misses the invented
  `export CLAUDE_PLUGIN_ROOT=…`.

  `correct` is a real answer and the common one. The goal is not to suppress
  improvisation — no procedure enumerates every repo it will meet — but to stop
  it being invisible, because the workaround that *works* is precisely the one
  nobody reports. That is how the shadowing shipped in 0.2.1 and survived to
  0.23.0.

- **Phase 7 discloses a deviation in the report itself**, in one line, and
  `references/report-template.md` carries the matching slot so the instruction
  has somewhere to land.

  Stated in Phase 7's **own** terms — "a command this procedure did not specify,
  or a plugin file read by hand" — rather than by deferring to Phase 8's
  classification. Phase 8 runs *after* Phase 7, and a phase that consumes what a
  later phase produces is the forward-reference defect `test_skill_prose.py`
  already guards twice.

### The replay

`fpga-board-sim` #363 — the PR that motivated the change — audited end to end in
a fresh context via `claude -p --plugin-dir`, then the hand-back checked against
that session transcript's actual `Bash` calls. The transcript is the independent
record; the model's recollection is not.

**The hand-back appeared unprompted and was substantive.** Three `gh api` calls
correctly identified as outside the procedure, two of them classified a **prose
gap** with a portable addition proposed for `references/actions.md`: `setup-uv`
v10.0.0's release notes name three events for the `enable-cache: auto` change,
while `action.yml` and `src/utils/inputs.ts` name four, so a scope grep built
from the notes checks three triggers and misses tag pushes. Filed as its own
issue. Verdict unaffected — **merge as-is, high confidence** — so the clause did
not distort the audit it rides on.

**Checked against the transcript, the list was materially complete.** One
unreported item: an `ls` of the plugin's own `scripts/` and `references/`, run to
get bearings before Phase 0.

### The round that replayed what ships

The clause above, reverted to #50's form, replayed against #363 once more —
because the second round had tested prose that is not shipping. Third run, fresh
context, and the first one whose subject is the released text.

**It found a plugin defect.** `references/actions.md` § Phase 1 documents:

```bash
gh api repos/<owner>/<repo>/git/refs/tags/<tag> --jq '.object.type, .object.sha'
```

That recipe has three outcomes, not two, and the middle one is the case Phase 2
actually asks about — *does a moving major tag exist?* Reproduced by hand,
independently of the run:

| Tag asked for | Result |
|---|---|
| `v10.0.1` — exact | `commit`, `20cfd1bf9…`, exit 0 |
| **`v10` — a prefix of existing tags** | **`expected an object but got: array ([{…`, exit 1** |
| `v999` — absent, not a prefix | clean `404 Not Found` |

GitHub's refs endpoint returns an **array** for a namespace prefix, so asking
whether `v10` exists — against a repo publishing `v10.0.0` and `v10.0.1` — kills
the documented `--jq` with what reads like an API fault. The array *is* the
answer: it lists the tags that do exist and thereby says no `v10` ref does. The
recipe crashes precisely when it has the data. Filed separately; not fixed here.

**And a prose gap.** Phase 0's `SCRATCH=${SCRATCH:-$(mktemp -d)}` assumes one
persistent shell. Each `Bash` call is a fresh shell, so `mktemp -d` yields a new
directory per invocation and the next call's `. "$SCRATCH/phase0.env"` reads a
path that does not exist. Two of the three rounds worked around it by pinning a
fixed path; nothing in `SKILL.md` says the scratch path must be *stable*, only
that it must be outside the repo. Filed separately.

Both were handed back classified, with the gap each filled, alongside a third row
of ordinary judgement marked **correct** — including the `ls` of the plugin's own
`scripts/` that round one omitted. The run also stated the contrast this whole
release exists for, unprompted:

> this time the skill loaded, `disallowed-tools` applied, and no
> `CLAUDE_PLUGIN_ROOT` export or `cat` of the procedure was needed. **The
> evidence table came out the same, which is exactly what made the original
> failure invisible.**

Verdict unchanged across all three rounds: **merge as-is, high confidence**.

### The second round, and why it is not in this release

A second round ran against an amended clause that named *path resolution* as the
row most likely to go missing, on the reading that the audit had silently
replaced every `${CLAUDE_PLUGIN_ROOT}/scripts/…` with an absolute path. **That
reading was wrong, and the amendment is reverted.** It is recorded because the
error is the exact class this release exists to surface.

`${CLAUDE_PLUGIN_ROOT}` is expanded **textually at skill load**; it is not an
environment variable. 0.23.0 said so. Settled from the round-two transcript,
where the 62,674-character skill injection delivered to the model reads:

```
D="/home/rick/Projects/dependabot-audit/skills/dependabot-audit/scripts/discover.py"
```

against a `SKILL.md` that holds `D="${CLAUDE_PLUGIN_ROOT}/…"` on disk at line
104. The model ran exactly what it was handed. **There was no substitution and no
deviation** — and round two, following the amended prose, dutifully reported one
as its first row. A clause that manufactures false deviations is worse than one
that misses an `ls`, so the clause ships as #50 proposed it.

What found this was neither round: it was a project memory recording 0.23.0's own
conclusion, read back afterwards. The replay gate proved the clause works and did
**not** catch the error in the amendment written from it — the amendment's row
looked like a success in both the report and the transcript.

### Measured

- **`$CLAUDE_PLUGIN_ROOT` is empty in the Bash tool's environment, and that is
  correct.** Claude Code 2.1.238, skill loaded and confirmed loaded, marketplace
  install and `--plugin-dir` alike: `printf 'ROOT=[%s]' "$CLAUDE_PLUGIN_ROOT"`
  gives `ROOT=[]` on both. This looks like a defect and is not one — the token is
  substituted into the skill *text* before the model ever sees it, so nothing at
  runtime needs the variable. Recorded because the naive probe points the wrong
  way, and because 0.23.0 left "whether a correctly loaded skill is given it" as
  an open question. It is not given it, and does not need to be.

### Tests

- **Six guards in `tests/test_skill_prose.py`**, one class, each mutation-checked
  against the prose that preceded it — all six fail against 0.23.0's Phase 8 and
  report-template.

  A seventh, written for the amended clause, is gone with it. It is worth its own
  line: as first written it asserted that the clause **names**
  `CLAUDE_PLUGIN_ROOT`, and it **passed against prose carrying no
  path-resolution clause at all**, because the narrative above already quotes the
  #363 `export`. Rewriting it to assert the measurement made it discriminate —
  and the measurement it then asserted was false. A guard can be made to fail
  correctly against the old text and still encode a wrong claim; mutation
  checking sizes the discrimination, never the truth.

### Not done, deliberately

- **Two candidates the second round raised** stay out: enumerating workflows at a
  ref in `references/actions.md`, and Phase 7's cleanup block leading with the
  two-worktree case so the actions exception reads as a parenthetical. Both are
  real; neither is this change, and neither has a defect behind it yet.

## [0.23.0] — 2026-08-19

A skill and a command are not in separate namespaces. Both are addressed as
`<plugin>:<name>`, so `commands/dependabot-audit.md` and
`skills/dependabot-audit/` claimed the same address — and the command won. This
is a minor bump rather than a patch because it changes which tools are withheld
while an audit runs, and removes a shipped artifact.

### Removed

- **`commands/dependabot-audit.md`.** Its entire body said *"invoke the
  `dependabot-audit` skill"*, which resolved back to the command: a delegation
  loop that closed on itself, leaving `SKILL.md` unloadable rather than merely
  deprioritised.

  Measured on Claude Code 2.1.235. The single listed entry for
  `dependabot-audit:dependabot-audit` carried the *command's* description, not
  the skill's; and a real audit — `fpga-board-sim` #363 — was handed the
  1527-character command body with `$1` expanded to `363`, never the skill's 999
  lines.

  **What that cost is `disallowed-tools`.** `Edit`, `Write` and `NotebookEdit`
  were never withheld, because the file that withholds them never loaded. The
  read-only contract the command file itself called "the whole point" was
  enforced by nothing: the #363 audit stayed read-only because the model chose
  to, and got the procedure at all only by reading `SKILL.md` off disk by hand.

  The same run had to invent `export CLAUDE_PLUGIN_ROOT=…/0.22.1`. That variable
  measures empty in the environment — `CLAUDE_PLUGIN_ROOT=[]`, which expands the
  script paths to `/skills/…` and resolves to nothing. Whether a correctly loaded
  skill is given it has never been measured, because no recorded run ever loaded
  one; that is now measurable and is deliberately left for a separate change. The
  invented workaround is worth naming, because it pins a version into a cache
  that retains every older copy — carried forward after a release, it runs a
  stale plugin silently and successfully.

  `/dependabot-audit <PR>` is unchanged for anyone typing it. With nothing
  shadowing the name, it now reaches the skill.

### Added

- **`tests/test_plugin_layout.py`.** Nothing in `tests/` had ever looked inside
  `commands/`, which is how a file whose only purpose was delegation survived
  twenty versions as the thing that broke delegation. Three guards: no command
  may claim a skill directory's name, none may claim the plugin's own name, and
  every skill's `name:` must equal its directory — the last is load-bearing for
  the other two, which compare directory names on the assumption that the
  directory *is* the address. Verified in both directions: two of the three fail
  with the command restored, all three pass without it.

- **`SKILL.md` § Arguments**, carrying the two rules that existed only in the
  command file — ask which PR when none arrived, rather than reaching for the
  most recent bump or the checked-out branch; and refuse an unrecognised flag
  rather than inferring what it might have meant.

## [0.22.1] — 2026-08-16

`CONTRIBUTING.md`'s release step stopped at the annotated tag and the CHANGELOG
entry, so a contributor following it exactly would not cut a GitHub Release. The
practice changed at 0.22.0; the instruction had not caught up, and an instruction
that omits a required step is the kind of claim this repo treats as false rather
than merely incomplete.

The step is now written out, along with the two things about it that are not
obvious:

- **It is presentation, not distribution.** `.claude-plugin/marketplace.json`
  declares `"source": "./"`, so `/plugin marketplace add` installs from the
  default branch and never resolves a Release. Publishing one is for readers who
  take a Release as more authoritative than a bare tag — and against the failure
  0.21.1 shipped into, where `releases/latest` advertised `v0.17.0` while the
  plugin was four versions ahead.
- **Releases start at `v0.22.0`.** The 35 tags before it are deliberately
  release-less. Recorded so the gap reads as a decision rather than an omission
  somebody later tidies up.

## [0.22.0] — 2026-08-16

### Fixed

- **A registry host that merely *starts with* `https://pypi.org` is no longer
  taken for PyPI.** `_is_pypi` tested the URL with
  `startswith("https://pypi.org")`, so `https://pypi.org.evil.com/simple` passed
  it. Found by CodeQL (`py/incomplete-url-substring-sanitization`) once code
  scanning was made a required check.

  **The damage was silence, not a bad hash.** Artifacts are fetched from the
  `SIMPLE` constant regardless of what the lockfile names, so a look-alike index
  was still compared against the real pypi.org and a substituted artifact would
  still have failed. What the prefix match cost was the *other* half of the
  report: classifying the package as PyPI-sourced kept it out of `non_pypi`, and
  `non_pypi` exists precisely so that a dependency this script cannot verify is
  named rather than dropped. Run against a lockfile carrying such an entry, the
  old code printed `RESULT: CLEAN — 1 package(s) NOT checked … fpga-simulator`
  and never mentioned the look-alike at all — it even reached OSV as though it
  were a real PyPI package. An under-audit indistinguishable from a clean one,
  which is the failure `non_pypi`'s own docstring names.

  Now `urllib.parse.urlsplit` is used and the **host is compared exactly**, with
  the scheme required to be `https`. A look-alike index falls into `non_pypi`
  and is reported as not checked, which is the honest answer.

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

## [0.21.1] — 2026-08-16

`claude plugin eval` does not refuse at exit 0. Re-checked while answering "how
will I know when it opens up", and the answer needed the refusal's shape to be
right (#32).

Measured directly on `claude` 2.1.233, no pipe, on both the run path and `init`:

```
$ claude plugin eval dependabot-audit >out 2>err
$ echo $?
1
$ cat out          # empty
$ cat err
`plugin eval` is currently in early access
```

Two corrections, and the second inverts the argument built on the first:

- **It exits 1**, not 0.
- **The refusal is on stderr and stdout is empty.** So *"a CI step written from
  the help text goes green while running nothing"* is backwards — at exit 1 that
  step goes red, the safe direction. The real hazard is the undocumented one: a
  check that greps the subcommand's **output** sees a clean empty result.

**How the wrong reading arose**, reproduced: `claude plugin eval … | head` returns
`head`'s status, which is 0. That is `SKILL.md` Phase 5's own trap — *"`cmd | tail
&& next` gates on `tail`, so a failing suite sails through"* — landing on the
measurement that argues for measuring. Whether the code was 0 then and is 1 now,
or was always 1 with the pipe hiding it, is not separable after the fact; the
claim is false today either way.

Corrected in `README.md`, `CONTRIBUTING.md` and `tests/test_skill_prose.py`'s
module docstring. Nothing else in #32 changes — the ten cases, the `live.yml`
placement and the "not an oracle for a new rule" boundary all stand, and the
suite is still blocked.

## [0.21.0] — 2026-08-16

`ATTRIBUTABLE` is the only one of Phase 6's three labels that produces a **Hold**,
and it said the least about its own evidence (#41). `PRE-EXISTING` ships with a
caveat and `underivable` gets a paragraph in `SKILL.md`; attribution was a bare
assertion.

### The replay

`fpga-board-sim` #332, `actions/checkout` 7.0.0 → 7.0.1. What 0.20.0 printed:

```
RED  Board-data drift  FAILURE  [CheckRun]
     ATTRIBUTABLE — green at 3a5b0b4ed (pr-<N>^)
```

Every cell true. The causal reading it invites is false. That job re-syncs
generated board sources from **other people's repositories** through the API and
requires a zero diff; the cause was an upstream ref moving, fixed in that repo's
own #335 and #336. `actions/checkout` 7.0.1 is "skip running unsafe pr check if
input is default", "trim only ascii whitespace for branch" and "escape values
passed to `--unset`" — none of which changes what `litex-boards` serves.

**The comparison could not have settled it.** `pr-332^` is from
2026-07-23T20:07:40Z, the head from 2026-07-27T13:09:25Z: **3d 17h**, on a check
whose inputs live upstream. That is the asymmetry, and it is the whole finding:

| Label | Survives a wide interval? |
|---|---|
| `pre-existing` | **yes.** If the check was already red the bump is exonerated, whatever else moved |
| `attributable` | **no.** Green-then-red across 3d 17h is equally consistent with the bump, an upstream change, a runner image roll, or a flake |

The two are not equally strong evidence and were presented as though they were.

**No Hold fired only because `Board-data drift` is not required.** Phase 7's row
reads "a red **required** check labelled attributable → Hold", so had the repo
marked it so, this would have Held a security backport released across six majors
inside 34 minutes, on an upstream board-data change. The guard was the audited
repo's branch-protection configuration, not anything in the procedure.

### Added

- **The interval travels with the row.** `ATTRIBUTABLE — green at 3a5b0b4ed
  (pr-<N>^), 3d 17h earlier`, live on #332. Minutes apart on a one-commit bot PR
  is a strong claim; most of a week is not, and the reader cannot discount what
  they are not shown.

- **A hedge on the attributable row**, as the other two labels carry: *green-then-
  red across that interval is CONSISTENT WITH the bump, not proof of it. Read the
  failing step's log at both commits before this row carries a Hold — especially
  where the check has inputs outside this repo.* The rule was already in
  `SKILL.md`, sitting under the *pre-existing* discussion where the reader has
  been told what to conclude; nothing on the attributable path prompted it.

- **`interval underivable`** rather than silence when a timestamp cannot be read.
  A missing interval must not be indistinguishable from a tight one — that is the
  original complaint reproduced inside the fix.

### Changed

- **Phase 7's Hold row keeps the verdict and drops the causal claim.** A red
  required check blocks the merge either way; what needed qualifying was the
  report saying the bump *caused* it.

- **`_gh_soft`**, for reads that qualify a row rather than establish one. The
  interval is a hedge on a claim, so a failed read weakens the row and must never
  turn Phase 6 into an exit 2. Two calls, and only when a red row exists to
  qualify.

### Tests

`TestAnAttributableRowSaysWhatItRestsOn` (5) and
`TestTheAttributableLabelIsHedgedLikeTheOthers` (3). Mutation-checked: hedging
the pre-existing row identically, making a failed date read fatal, printing
nothing for an underivable interval, and the 0.20.0 prose.

## [0.20.0] — 2026-08-16

Phase 2's prose and Phase 7's table disagreed about the case Phase 2 was written
for (#42). Phase 2: *"What outranks the hold is what this phase reads for next: a
`Security` entry or a destructive-fix bug in the gap."* Phase 7's security row
was gated on `and the gap is outside the cooldown`, so on a gap **inside** the
window it could not match, and the fall-through landed on *"Merge as-is. Do not
offer a follow-up"* — the opposite instruction.

### The replay

`fpga-board-sim` #355 and #359, both `rumdl`, three days apart. Publish times
from PyPI, PR open times from the API:

| | #355 | #359 |
|---|---|---|
| proposed | 0.2.43 → **0.2.47** | 0.2.49 → **0.2.52** |
| opened | 2026-08-03T13:11:49Z | 2026-08-10T13:11:23Z |
| the evidence in the gap | 0.2.49's `Security` section, published 16h54m before the PR opened | 0.2.53's `md084` / `md038` destructive fixes, 7h13m before |
| cooldown | **inside** | **inside** |
| OSV / GHSA / CVE | none — `audit.py` clean across 37 packages | none |
| does this repo exercise it | **no** — `[tool.rumdl]` inline, `extends` count 0 | **yes** — `entry: uv run rumdl check --fix` |
| 0.19.0's verdict | merge, do **not** follow up | merge, do **not** follow up |
| 0.20.0's verdict | merge, then follow up on the merits | merge, then follow up **at once** |
| what the maintainer did | — | merged, followed up four minutes later |

Three defects in those rows, not one. **Destructive-fix bugs had no row at all**,
though Phase 2 ranks them equal to `Security` entries — the only evidence class
this procedure finds that no security feed carries. And **the recommendation
turned on when you ran the audit**: replayed today, 0.2.49 is outside the window,
the old row 3 matches, and identical evidence produces the opposite advice.
Phase 7's stated reason for having a table at all is that leaving the function
implicit is how two audits with the same evidence reach different
recommendations.

### Changed

- **Three rows replace one, and none of them reads the clock.** The cooldown
  decides Hold-versus-follow-up; it never decides whether to look. The wait
  exempts Dependabot's *security updates* — the advisory-driven kind — not a
  version update whose changelog happens to carry a privately disclosed fix.

- **"Exercises the affected path" moved from the prose into the row**, where a
  verdict rule reads it. It was doing real work in both measured cases and no
  verdict consumed it.

- **Exposure sets the urgency of the follow-up, not the verdict.** The issue
  proposed `Hold if the repo exercises the affected path`; replaying #359 — the
  only exposed case measured — says otherwise. The gap is *newer* than what the
  PR proposes, so the bump moves toward the fix and never away: holding #359
  leaves the repo on 0.2.49, carrying **both** destructive bugs, rather than
  0.2.52 carrying neither more of them. Its maintainer merged and followed up
  four minutes later. Hold is kept for the one configuration where merging is
  what increases exposure — the bump moving *into* the bug, adopted version
  affected where the current pin is not.

- **Phase 2 asks the exposure question for `Security` entries too**, not only for
  destructive fixes. Same `grep`, and Phase 7 now takes a verdict from it.

- **The report template's Security row** carries the exposure answer and the
  config line that settles it.

### Tests

`TestSecurityEvidenceOutranksTheCooldown`, three guards, asserted on the parsed
verdict table rather than on the phase text. Mutation-checked against the 0.19.0
table: all three fire.

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

[Unreleased]: https://github.com/Machai-Kydoimos/dependabot-audit/compare/v0.38.0...HEAD
[0.38.0]: https://github.com/Machai-Kydoimos/dependabot-audit/compare/v0.37.0...v0.38.0
[0.37.0]: https://github.com/Machai-Kydoimos/dependabot-audit/compare/v0.36.0...v0.37.0
[0.36.0]: https://github.com/Machai-Kydoimos/dependabot-audit/compare/v0.35.0...v0.36.0
[0.35.0]: https://github.com/Machai-Kydoimos/dependabot-audit/compare/v0.34.0...v0.35.0
[0.34.0]: https://github.com/Machai-Kydoimos/dependabot-audit/compare/v0.33.0...v0.34.0
[0.33.0]: https://github.com/Machai-Kydoimos/dependabot-audit/compare/v0.32.0...v0.33.0
[0.32.0]: https://github.com/Machai-Kydoimos/dependabot-audit/compare/v0.31.0...v0.32.0
[0.31.0]: https://github.com/Machai-Kydoimos/dependabot-audit/compare/v0.30.1...v0.31.0
[0.30.1]: https://github.com/Machai-Kydoimos/dependabot-audit/compare/v0.30.0...v0.30.1
[0.30.0]: https://github.com/Machai-Kydoimos/dependabot-audit/compare/v0.29.0...v0.30.0
[0.29.0]: https://github.com/Machai-Kydoimos/dependabot-audit/compare/v0.28.0...v0.29.0
[0.28.0]: https://github.com/Machai-Kydoimos/dependabot-audit/compare/v0.27.0...v0.28.0
[0.27.0]: https://github.com/Machai-Kydoimos/dependabot-audit/compare/v0.26.1...v0.27.0
[0.26.1]: https://github.com/Machai-Kydoimos/dependabot-audit/compare/v0.26.0...v0.26.1
[0.26.0]: https://github.com/Machai-Kydoimos/dependabot-audit/compare/v0.25.0...v0.26.0
[0.25.0]: https://github.com/Machai-Kydoimos/dependabot-audit/compare/v0.24.0...v0.25.0
[0.24.0]: https://github.com/Machai-Kydoimos/dependabot-audit/compare/v0.23.0...v0.24.0
[0.23.0]: https://github.com/Machai-Kydoimos/dependabot-audit/compare/v0.22.1...v0.23.0
[0.22.1]: https://github.com/Machai-Kydoimos/dependabot-audit/compare/v0.22.0...v0.22.1
[0.22.0]: https://github.com/Machai-Kydoimos/dependabot-audit/compare/v0.21.1...v0.22.0
[0.21.1]: https://github.com/Machai-Kydoimos/dependabot-audit/compare/v0.21.0...v0.21.1
[0.21.0]: https://github.com/Machai-Kydoimos/dependabot-audit/compare/v0.20.0...v0.21.0
[0.20.0]: https://github.com/Machai-Kydoimos/dependabot-audit/compare/v0.19.0...v0.20.0
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
