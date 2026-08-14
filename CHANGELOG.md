# Changelog

Every release is tagged, and every tag is annotated. `git log` carries the full
reasoning behind each change; this file is the readable index of it.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). This
plugin's public surface is its **procedure**, not an API, so the versioning rule
is: a change to what a phase verifies, or to what the report asserts, is a minor
bump even when no code moved. A fix that only makes an existing claim true is a
patch.

## [Unreleased]

### Added

- `CHANGELOG.md`, and annotated tags for every release back to `v0.1.0` — a
  dependency-provenance tool that ships untagged gives a user pinning to a
  version nothing to pin to. Each tag points at the last commit declaring that
  version in `plugin.json`, derived from the file rather than from commit
  subjects.
- Issue templates, shaped like this repo's own bug reports: ecosystem, lockfile
  excerpt, the exact command, and the exit status.

### Fixed

- `.gitignore` now covers `.claude/settings.local.json`, which was untracked only
  because of the maintainer's *global* ignore file. A contributor without that
  rule would see it as untracked and could commit absolute scratch paths and
  session identifiers.

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

[Unreleased]: https://github.com/Machai-Kydoimos/dependabot-audit/compare/v0.2.0...HEAD
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
