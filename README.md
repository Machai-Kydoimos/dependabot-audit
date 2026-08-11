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

## Install

```
/plugin marketplace add https://github.com/Machai-Kydoimos/dependabot-audit
/plugin install dependabot-audit
```

The repo is **private to the Machai-Kydoimos organization**, so installing it
requires git credentials with access — org members should have `gh auth login`
done, or an SSH key on their account, before running the first command.

## Use

```
/dependabot-audit 359
```

or just say "there's a new Dependabot PR, take a look" — the description matches
and the skill loads itself.

The output is a fixed report shape: verdict, confidence, an evidence table where
every row is something that was actually run, the reasoning, what would change
the verdict, and the merge command **left un-run**.

## What it does

| Phase | |
|---|---|
| 0 | Discover the repo — required checks, bot config, the repo's own CI gates and their scopes; pin the PR's head SHA and fetch it once |
| 1 | Scope and provenance — every locked artifact's hash, size, URL, yank status vs. the live registry, read out of git at the pinned ref |
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

51 cases, stdlib only, no network — they run offline and free. Every case
corresponds to a defect that actually shipped, or to a failure the audit exists
to detect. They fall into five groups:

- **Provenance** — a corrupted hash, a size mismatch, a yanked release, an
  artifact missing from the registry, and an sdist checked alongside the wheels.
- **Currency** — a lagging version; a package pinned at two versions under
  different resolution-markers, where the *held-back* fork must not read as stale
  and the *live* one must still be checked; a publish time taken from the earliest
  artifact rather than an arbitrary one; and a pre-release that has no business in
  the gap, next to a post-release that does.
- **Under-auditing** — a requested name that isn't in the lockfile, an empty
  selection that must not report `CLEAN`, a lockfile compared against itself, and
  a non-PyPI package that has to be named rather than dropped.
- **Failure vs. finding** — an unreadable lockfile, an unreachable registry, and
  an OSV outage, each of which has to exit 2 rather than borrow the status that
  means "found something".
- **Gate differential** — the three ways a bump moves a gate (widened scope,
  narrowed scope, a changed fix), a deleted file counting as a change, and the
  safety properties: a dirty tree is refused, and the worktree is restored
  between runs. That last one matters most — without it run two inherits run
  one's edits and every comparison after it is fiction.

`gate_diff.py` is additionally validated end-to-end against a real historical
bump: replaying Dependabot's ruff `0.15.22` → `0.16.0` PR against the tree as it
stood that day reproduces the six Markdown files the newer version started
formatting — while both versions exit 0.

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

**Not covered:** the skill's prose. These tests exercise `audit.py`, the
deterministic half. Whether the model actually *follows* Phase 6, or stops on an
unexpected file in the diff, is behavioral and belongs in `claude plugin eval`
— which is in early access and unavailable on this account. That gap is real, and
it is where the defects keep turning up: Phase 6 once improvised a check-name
parse, and Phase 1 referenced a branch that Phase 5 created, so a literal reading
audited the base branch instead of the PR. Both lived in the prose, where these
tests cannot reach — the second is why the script now refuses to report `CLEAN`
on an empty selection.

## Read-only

The skill declares `disallowed-tools: Edit, Write, NotebookEdit`, which removes
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
