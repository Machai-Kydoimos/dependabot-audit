# Changelog

Every release is tagged, and every tag is annotated. `git log` carries the full
reasoning behind each change; this file is the readable index of it.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). This
plugin's public surface is its **procedure**, not an API, so the versioning rule
is: a change to what a phase verifies, or to what the report asserts, is a minor
bump even when no code moved. A fix that only makes an existing claim true is a
patch.

## [Unreleased]

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

[Unreleased]: https://github.com/Machai-Kydoimos/dependabot-audit/compare/v0.4.0...HEAD
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
