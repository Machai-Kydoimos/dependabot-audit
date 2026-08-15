# dependabot-audit

A Claude Code plugin that audits an automated dependency-bump PR and produces an
**evidence-backed merge recommendation**.

It reports. It never merges.

## Why

Dependabot and Renovate PRs look trivial and usually are. The failure modes that
actually cost you something are not "is this package malicious":

1. **The proposed version is not the current one.** Registries publish faster than
   bots ingest, and since 2026-07-14 Dependabot also *holds* a release for three
   days by default — so a bump lands stale for one of two reasons, and the gap can
   contain the thing you cared about. Which reason it is decides whether the gap
   is worth acting on, and nothing in the PR says.
2. **The gap contains a fix no vulnerability database knows about.** A privately
   disclosed fix has no CVE and no GHSA, so OSV and `pip-audit` both report clean
   while the changelog says `Security`.
3. **The bump changes a *default*, not just behavior.** A linter that gains a rule
   or a formatter that widens its file scope can newly fail a *required* CI check
   that the repo's own pre-commit hooks are scoped too narrowly to catch.

All three are observed in the wild. The procedure exists for them.

There is a fourth it can now speak to, which a hash comparison structurally
cannot: **a bad artifact the registry itself is serving.** If the registry record
and the lockfile agree, agreement is the whole test — so Phase 1 also reads PyPI's
PEP 740 attestations, which name the repository and workflow that built each file,
and compares the publisher against the release being replaced.

## Install

```
/plugin marketplace add https://github.com/Machai-Kydoimos/dependabot-audit
/plugin install dependabot-audit
```

The repo is **private to the Machai-Kydoimos organization**, so installing it
requires git credentials with access — org members should have `gh auth login`
done, or an SSH key on their account, before running the first command.

## Use

Two entry points, either of which does the same thing:

```
/dependabot-audit <PR>                 # e.g. /dependabot-audit 42
/dependabot-audit <PR> --no-execute    # read-only phases only; see below
```

or say "there's a new Dependabot PR, take a look" — the skill's description
matches and it loads itself. Neither is shorthand for the other; the command
exists so the documented form is real and can declare its own argument hint,
and the natural-language path exists because that is how most people arrive.

The output is a fixed report shape: verdict, confidence, an evidence table where
every row is something that was actually run, the reasoning, what would change
the verdict, and the merge command **left un-run**.

## What it does

| Phase | |
|---|---|
| 0 | Discover the repo — required checks, bot config, the repo's own CI gates and their scopes; classify the PR; pin the head SHA, fetch it once, and build the worktree every later phase works in |
| 1 | Scope and provenance — every locked artifact's hash, size, URL, yank status and PEP 740 build provenance vs. the live registry, read out of git at the pinned ref. A **gate**: if anything here fails, the audit stops before the phases that execute code |
| 2 | Currency — the registry's true latest, publish times vs. PR open time, and changelogs across the gap |
| 3 | Known vulnerabilities — what is already known to be wrong with this. OSV batch plus the ecosystem's own auditor for `uv.lock`; GHSA's `actions` ecosystem for a workflow bump |
| 4 | Behavior change — does this change what runs here. For `uv.lock`, each gate run at the old and new versions **against the merge base**, comparing what they *do to the files*; measuring the PR's own tree reports nothing whenever the PR already contains the fixup, which is exactly when the change was real. For actions, which cannot be run locally, whether this repo's workflows are in the change's scope at all |
| 5 | Independent reproduction — frozen install and the repo's own gates in an isolated worktree; for actions, where no local reproduction exists, the run history of the workflow the bump changed |
| 6 | CI verification — the run for the exact head SHA, the required contexts specifically, and whether the changed file is reachable from a pull request at all |
| 7 | Report |
| 8 | Learning loop — hand back anything that could not have been derived |

Phase 0 derives repo specifics **every run and never caches them** — a cached
profile silently audits a repo that no longer exists. Phase 8 writes out only what
cannot be derived — the landmines you otherwise learn by getting bitten twice —
and hands it to you rather than saving it itself. Nothing derivable is cached;
nothing hard-won is re-derived.

## Scope

Two ecosystems, because together they are what a Python project's Dependabot
queue actually contains — on this plugin's own test repo the bot PRs split
`uv: 11` / `github_actions: 10`:

| | |
|---|---|
| **Python — `uv.lock`** | `scripts/audit.py`, end-to-end and tested against it: artifact hashes, PEP 740 build provenance, the registry's true latest, and the OSV batch |
| **GitHub Actions** | no lockfile and no artifact hash, so Phase 1 becomes a pin question — is it a SHA or a movable tag, and which way has that tag moved. Every later phase has an actions method too: GHSA for advisories, scope analysis where a gate cannot be run, run history where nothing can be installed |

**npm, Cargo and Go are out of scope** — not unimplemented, out of scope. Their
recipes were removed rather than left as sketches, on this file's own rule: an
unverified verifier is worse than none, because it emits confident green output
nobody checks.

That is not hypothetical. Followed faithfully against a real Cargo bump, the
recipe returned matching checksums, a current latest version and a clean OSV
batch — on a PR that raised the project's minimum Rust version past its own
declared floor. Nothing in the recipe's output looked partial. Adding the missing
check would have fixed that one bump and left the class untouched, and a hand-run
recipe also silently lacks every guard the script has already earned: the Cargo
OSV query written while investigating it re-introduced two defects this repo had
already fixed once.

What survives the cut is the half that **warns** rather than the half that
**verifies**. `references/ecosystems.md` keeps what a frozen install executes in
each ecosystem — including `cargo build --locked` running every crate's
`build.rs` with no flag that stops it — because that stays true no matter which
lockfiles this plugin will read.

Other Python lockfiles are out of scope too. The script reads `uv.lock`
specifically, not Poetry, pip-tools or PDM.

The boundary is enforced rather than stated. Handed another ecosystem's lockfile
the script exits 2 naming the format — `is a Cargo.lock (Rust)`, `is a
poetry.lock (Python, Poetry)` — and points at `references/ecosystems.md`. That
message is the edge of the tool and the first thing a reader arriving with a
different lockfile sees, so it says what they found rather than blaming itself:
before 0.10.0 a real `Cargo.lock` produced `unexpected AttributeError ... This is
a bug`, and a real `poetry.lock` produced a confident *"either this lockfile did
not change, or it is being compared against itself"*.

`scripts/gate_diff.py` (Phase 4) is the exception: it is
**ecosystem-independent**, because it parses nothing. It runs a gate once per
version in a disposable worktree and compares which files each run changed, and
how. That works for any tool in any language — the operator supplies the
invocations, and the tool's own output format is irrelevant, which is the point:
version bumps change output formats about as often as they change behavior.

## Tests

```
python3 -m unittest discover -s tests -v
```

149 cases, stdlib only, no network — they run offline and free. Every case
corresponds to a defect that actually shipped, or to a failure the audit exists
to detect. They fall into ten groups:

- **Provenance** — a corrupted hash, a size mismatch, a yanked release, an
  artifact missing from the registry, an sdist checked alongside the wheels, and
  an *absent* size, which must report as not-compared rather than as a mismatch:
  "the hash matches but the size does not" reads like tampering.
- **Build provenance** — PEP 740 attestations as three states: attested, absent
  (which is **not** a finding — Trusted Publishing postdates most of PyPI), and a
  publisher that moved between the release being replaced and the one being
  adopted, which is.
- **Registry shape** — the Simple API carries no per-file version, so files are
  attributed to releases by filename: the longest match wins, and a file that
  cannot be attributed costs a *timestamp*, never a gap entry. Plus "latest" as a
  computation — excluding pre-releases and fully-yanked releases, and including a
  release whose files could not be attributed, because omitting a real version is
  the worse error.
- **Currency** — a lagging version; a package pinned at two versions under
  different resolution-markers, where the *held-back* fork must not read as stale
  and the *live* one must still be checked; a publish time taken from the earliest
  artifact rather than an arbitrary one; a pre-release that has no business in the
  gap, next to a post-release that does; and an epoch release, which the previous
  ordering sorted *below* unversioned releases and out of the gap entirely.
- **Version ordering** — the PEP 440 comparator the currency check now rests on:
  epochs, `1.9 < 1.10`, the full `dev → a → b → rc → final → post` cycle,
  `1.0 == 1.0.0`, spelling variants, and a version it cannot order raising rather
  than sorting to the bottom.
- **Under-auditing** — a requested name that isn't in the lockfile, an empty
  selection that must not report `CLEAN`, a lockfile compared against itself, a
  non-PyPI package that has to be named rather than dropped, an artifact swapped
  at an unchanged version (which a version-keyed diff selects nothing for), and an
  OSV batch past the 1000-query limit, which must chunk rather than take the whole
  vulnerability phase down with it.
- **Failure vs. finding** — an unreadable lockfile, an unreachable registry, an
  OSV outage, and an *unforeseen* exception, each of which has to exit 2 rather
  than borrow the status that means "found something" — and a guard that must not
  swallow a real verdict on the way.
- **Gate differential** — the three ways a bump moves a gate (widened scope,
  narrowed scope, a changed fix), a deleted file counting as a change, and the
  safety properties: a dirty tree is refused, and the worktree is restored
  between runs — including when the gate *staged* its change, which
  `git checkout -- .` cannot undo. That group matters most: without it run two
  inherits run one's edits and every comparison after it is fiction. It also
  covers a *staged rename*, which `git status --porcelain -z` emits as two fields
  rather than one — the parse that assumed one turned `tracked.txt` into
  `cked.txt`, reporting a path that never existed as deleted while the real
  deletion went unreported.
- **Ecosystem coverage** — no phase from 1 to 6 may be written for only one of
  the two supported ecosystems, Phase 3 must name an advisory source for actions,
  and it must keep the measured case behind the OSV version trap. This group
  exists because *"not applicable" is an assertion too*: three places in this repo
  stated that GitHub Actions has no vulnerability database, and GHSA carries an
  `actions` ecosystem. A phase that believed it skipped a real check.
- **Skill prose** — `SKILL.md` checked against itself: no phase may consume what
  a later phase creates, Phase 4 must measure on the merge base rather than the
  PR's tree, the required contexts must come from the API rather than an authored
  list — and specifically not from the two endpoints that fail into a plausible
  answer — every script and reference path the prose names must exist, the phases
  that execute PR code must say so, the actions scope gate must key on the kind of
  line the diff touches rather than a count of files, Phase 2 must rule out a
  cooldown before calling a gap lag, and the frontmatter key that withholds tools
  must be the one that works. Since 0.12.0 it also checks that the cleanup lives
  in a phase every audit reaches rather than one `--no-execute` skips, that the
  rollup query reads `totalCount` so a truncated page cannot pass as a complete
  required-check list, and that the classification asks whether this is a
  repository you control before letting Phases 4 and 5 run. Each corresponds to a
  defect that shipped in the prose, where the other groups cannot reach.

The theme is **silent** failure. An audit that reports success while verifying
less than it claimed is worse than one that crashes, so the assertions target
what gets *reported*, not just what gets returned.

Each test was mutation-checked against the original buggy implementation to
confirm it discriminates — a suite that only ever passes proves nothing.

### Gates

```
pre-commit install          # ruff, mypy and the suite, on every commit
pre-commit run --all-files  # exactly what CI runs
```

**The hooks are the enforcing gate; CI is advisory.** This repo is private on a
free org, where branch protection and repository rulesets are both unavailable
(`403 Upgrade to GitHub Pro or make this repository public`), so no check can be
marked required. CI earns its place on the one thing the hooks cannot do: run the
suite on Python 3.11 through 3.14, because `audit.py` runs under whatever bare
`python3` the repo under audit happens to have, and `tomllib` puts the floor at
3.11. The maintainer's machine is the newest of those, so the older legs are the
ones carrying information.

Tool versions live in `.pre-commit-config.yaml` and nowhere else — CI invokes the
hooks rather than installing its own ruff and mypy, so there is no second pin to
drift from.

**Not covered:** whether the model *follows* the procedure. The prose group checks
`SKILL.md` against itself — that no phase consumes what a later phase creates,
that the required contexts come from the API rather than an authored list. It
cannot check
whether Phase 6 gets run at all, or whether an unexpected file in the diff
actually stops the audit. That is behavioral and belongs in `claude plugin eval`,
which is in early access and **still unavailable on this account**.

The subcommand is present in the CLI and prints a complete `--help` — options for
graders, ablation arms, cost ceilings, thresholds — which reads exactly like a
feature you can use. Invoking it does not:

```
$ claude plugin eval dependabot-audit
`plugin eval` is currently in early access
$ echo $?
0
```

**It exits 0.** So a CI step added on the strength of the help text would go
green while running nothing at all — the same shape as every other failure this
repo collects, arriving in the tool that was supposed to close the gap. Checked
0.12.0; worth re-checking rather than assuming, in either direction.

That gap is real, and it is where the defects keep turning up. Seven have now
shipped in the prose and nowhere else:

- Phase 6 improvised a check-name parse, mangling `Lint & type-check` into `Lint`.
- Phase 1 referenced a branch that Phase 5 created, so a literal reading audited
  the base branch instead of the PR — which is why the script now refuses to
  report `CLEAN` on an empty selection.
- Phase 4 ran against a worktree that Phase 5 created, so read in order it could
  not run at all.
- Phase 6 shipped one specific repo's required check names in the only snippet in
  the file that filled a placeholder in rather than showing one.
- **Phase 4 measured on the PR's own tree**, which reports no difference whenever
  the PR already contains the fixup — and a PR carrying a fixup is one whose
  behaviour change was real enough that a human had to deal with it. Found by
  auditing the exact bump this plugin's founding observation came from: six
  Markdown files on the merge base, nothing on the PR's tree.
- **Phase 0 read the required checks from an `admin`-only endpoint.** Without
  admin it returns a bare `404`, `gh` writes that body to *stdout*, and the
  redirect produced a well-formed file that read as "no required checks" — about a
  repo enforcing three. The report then said CI was verified.
- **Phase 1's scope gate fired on a merge base that was not the branch point.**
  A force-pushed base sends `git merge-base` back to a much older ancestor, so a
  two-file bump presented as fourteen files and 3,682 deletions, and the gate
  stopped the audit for a reason that was not true.

The last three are the ones that matter, and they share a shape the first four do
not: each returned a **confident false statement** rather than stalling. Phase 4
reported "no change" from the highest-yield phase; the other two reported on
repository state they had not actually read. A run that stops is cheap. A row that
is wrong is not, because the report's whole proposition is that its rows are
things that were established.

Two of the seven are the same forward-reference shape, and two are the same
fail-into-a-plausible-value shape. That recurrence is what turned the prose group
from an idea into a necessity: a defect class that repeats is cheaper to gate than
to keep finding, and prose was the only lever being spent on it.

### Live checks

```
RUN_NETWORK_TESTS=1 python3 -m unittest discover -s integration -v
```

Two things the hermetic suite cannot reach, in their own directory and their own
CI job — scheduled weekly, **never required**, because they go red for reasons
that are not this repo's fault:

- **The `gate_diff` replay.** Dependabot's ruff `0.15.22` → `0.16.0` PR, against a
  checked-in fixture: the newer version reformats six Markdown files the older one
  leaves alone, **while both exit 0**. This is the observation `gate_diff.py` was
  built from, and until now the README asserted it while nothing re-ran it — it
  had been verified by hand, once, against a tree in another repository. It is the
  only case in either suite that runs a real tool rather than a shell one-liner,
  which is why it needs the network and cannot join the hermetic set.
- **The live registry.** `audit.py` reads the Simple API, which has no
  `info.version`, so "latest" is now the script's own computation. This checks that
  computation against what the legacy endpoint still declares, across fourteen
  real projects, and asserts the response still has the shape the script reads.
  It is the standing guard on the one real cost of that migration.

## What it executes

**The audit runs code from the PR it is auditing.** Two of the eight phases do,
and it is worth knowing which before pointing this at a repository you do not
control:

| Phase | What runs |
|---|---|
| 4 | the repo's own gates, at a version taken from the diff under audit, through a shell |
| 5 | a frozen install, which builds any sdist in the resolution — running `setup.py` or the project's PEP 517 backend — and then the PR's own test suite, from the PR's tree |

Phases 0–3 and 6–8 are network reads and `git` queries, and execute nothing.

Three things follow, all of them in the skill:

- **Phase 1 is a gate**, and it is worth knowing what it can see. If the diff
  reaches past the manifest and lockfile, or provenance fails, the audit stops
  there rather than continuing into the phases that execute. What that catches is
  a lockfile edited after it was written honestly. It does **not** catch a
  malicious *release*: the hash is compared against what the registry serves
  today, so when the attacker published the artifact the record and the lockfile
  agree — and agreement is the whole test. PEP 740 `PUBLISHER CHANGED` is the one
  signal that speaks to that case, and its coverage is partial.
- **`--no-execute` runs Phases 0–3 and 6–7 only** — provenance, currency,
  changelogs, OSV, CI state. That is most of the value, and it is the right
  default for a PR you have no reason to trust. Phase 0 switches to it on three
  observations, any one of which is enough: a cross-repository PR, a non-bot
  author — neither Dependabot nor Renovate opens a fork PR — or **an account
  without `push` on the repo**. The last is the asymmetry worth stating: a bot PR
  on a repo you control proposes code your own CI would run anyway, while a PR
  you cannot merge proposes code you had no plan to run, and CI would run *that*
  in a fresh container with a scoped token rather than on your workstation.
- **The narrowed install is the documented default** — `uv sync --locked
  --no-build --no-install-project`, which succeeds only if every dependency in
  the lockfile resolved to a **wheel**, so no third-party build code runs at all.
  If it fails it names the package that needs an sdist build, which is worth
  knowing about a bump rather than an obstacle. The report names which form ran,
  because the narrowed and full installs prove different things.

**This is not a sandbox and does not pretend to be one.** The Phase 5 worktree
isolates *your working tree from the audit*; it does nothing to isolate *the
machine from the PR*. If you need that, it comes from outside — a container, a
throwaway VM, a Landlock confinement — and the plugin cannot verify you have one.

## Read-only

That is a separate claim, about what the plugin itself writes. The skill declares
`disallowed-tools: Edit, Write, NotebookEdit`, which removes
those from Claude's pool while it is active. `Bash` remains and could reach
`gh pr merge`, so "reports, never merges" is still a **contract, not a sandbox**.
It is stated at the top of the skill, and the report ends with the merge command
printed rather than executed.

Two caveats worth knowing, since both were wrong here until `0.1.9`:

- **`tools:` is not a skill frontmatter field.** Earlier releases declared it and
  it withheld nothing — an unrecognized key that read like a control. The field
  that grants is `allowed-tools`; the field that removes is `disallowed-tools`.
  Reaching for the wrong one gives you the opposite of what you meant.
- **`disallowed-tools` is a Claude Code extension, not part of the [Agent Skills]
  (https://agentskills.io) spec's six fields.** So it takes a recent Claude Code
  to have any effect — older builds ignore unrecognized frontmatter silently, and
  `plugin.json` has no way to enforce a floor — and it means this skill cannot be
  packaged for claude.ai upload or the Skills API, which reject non-spec keys with
  a hard error. That is a deliberate trade: enforcement in the channel this plugin
  actually ships through beats portability to one it doesn't.

## License

MIT.
