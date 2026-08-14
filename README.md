# dependabot-audit

A Claude Code plugin that audits an automated dependency-bump PR and produces an
**evidence-backed merge recommendation**.

It reports. It never merges.

## Why

Dependabot and Renovate PRs look trivial and usually are. The failure modes that
actually cost you something are not "is this package malicious":

1. **The proposed version is not the current one.** Registries publish faster than
   bots ingest, so a bump can land already stale — and the gap can contain the
   thing you cared about.
2. **The gap contains a fix no vulnerability database knows about.** A privately
   disclosed fix has no CVE and no GHSA, so OSV, `pip-audit`, and `npm audit` all
   report clean while the changelog says `Security`.
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
| 3 | Known vulnerabilities — OSV batch plus the ecosystem's own auditor |
| 4 | Behavior change — each gate run at the old and new versions, comparing what they *do to the files* |
| 5 | Independent reproduction — frozen install and the repo's own gates in an isolated worktree |
| 6 | CI verification — the run for the exact head SHA, and the required contexts specifically |
| 7 | Report |
| 8 | Learning loop — hand back anything that could not have been derived |

Phase 0 derives repo specifics **every run and never caches them** — a cached
profile silently audits a repo that no longer exists. Phase 8 writes out only what
cannot be derived — the landmines you otherwise learn by getting bitten twice —
and hands it to you rather than saving it itself. Nothing derivable is cached;
nothing hard-won is re-derived.

## Ecosystem coverage

`scripts/audit.py` implements PyPI / `uv.lock` end-to-end, and is tested against
it. npm, Cargo, Go, and GitHub Actions are covered as short per-registry
procedures in `references/ecosystems.md` that the model runs directly — the same
three questions in each registry's vocabulary.

`scripts/gate_diff.py` (Phase 4) is **ecosystem-independent**, because it parses
nothing. It runs a gate once per version in a disposable worktree and compares
which files each run changed, and how. That works for any tool in any language —
the operator supplies the invocations, and the tool's own output format is
irrelevant, which is the point: version bumps change output formats about as
often as they change behavior.

This is deliberate. An unverified verifier is worse than none: it emits confident
green output nobody checks. Don't extend the script to an ecosystem you don't
have a repo to test it against.

## Tests

```
python3 -m unittest discover -s tests -v
```

109 cases, stdlib only, no network — they run offline and free. Every case
corresponds to a defect that actually shipped, or to a failure the audit exists
to detect. They fall into nine groups:

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
  inherits run one's edits and every comparison after it is fiction.
- **Skill prose** — `SKILL.md` checked against itself: no phase may consume what
  a later phase creates, the required-context list must be read from a Phase 0
  artifact rather than typed, every script and reference path the prose names
  must exist, the phases that execute PR code must say so, and the frontmatter key
  that withholds tools must be the one that works. Each corresponds to a defect
  that shipped in the prose, where the other groups cannot reach.

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
that the required-context list is derived rather than typed. It cannot check
whether Phase 6 gets run at all, or whether an unexpected file in the diff
actually stops the audit. That is behavioral and belongs in `claude plugin eval`,
which is in early access and unavailable on this account.

That gap is real, and it is where the defects keep turning up. Four have now
shipped in the prose and nowhere else:

- Phase 6 improvised a check-name parse, mangling `Lint & type-check` into `Lint`.
- Phase 1 referenced a branch that Phase 5 created, so a literal reading audited
  the base branch instead of the PR — which is why the script now refuses to
  report `CLEAN` on an empty selection.
- Phase 4 ran against a worktree that Phase 5 created, so read in order it could
  not run at all.
- Phase 6 shipped one specific repo's required check names in the only snippet in
  the file that filled a placeholder in rather than showing one.

Two of those four are the same forward-reference shape, which is what turned the
prose group from an idea into a necessity: a defect class that recurs is cheaper
to gate than to keep finding, and prose was the only lever being spent on it.

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
| 5 | a frozen install — npm lifecycle scripts, an sdist build, `build.rs` — and then the PR's own test suite, from the PR's tree |

Phases 0–3 and 6–8 are network reads and `git` queries, and execute nothing.

Three things follow, all of them in the skill:

- **Phase 1 is a gate.** If the diff reaches past the manifest and lockfile, or
  provenance fails, the audit stops there rather than continuing into the phases
  that execute. Running the cheap read-only checks first is only worth something
  if they are allowed to refuse.
- **`--no-execute` runs Phases 0–3 and 6–7 only** — provenance, currency,
  changelogs, OSV, CI state. That is most of the value, and it is the right
  default for a PR you have no reason to trust. Phase 0 flags a cross-repository
  or non-bot-authored bump and switches to it, because neither Dependabot nor
  Renovate opens a fork PR.
- **The narrowed install is the documented default** — `npm ci --ignore-scripts`,
  `uv sync --locked --no-build`. Cargo has no equivalent for `build.rs`, and the
  reference says so rather than implying parity. The report names which form ran,
  because they prove different things.

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
