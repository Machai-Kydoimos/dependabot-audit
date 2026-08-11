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
| 4 | Behavior change — added rules and changed defaults against this repo's config and gate scopes |
| 5 | Independent reproduction — frozen install and the repo's own gates in an isolated worktree |
| 6 | CI verification — the run for the exact head SHA, and the required contexts specifically |
| 7 | Report |
| 8 | Learning loop — persist anything that could not have been derived |

Phase 0 derives repo specifics **every run and never caches them** — a cached
profile silently audits a repo that no longer exists. Phase 8 persists only what
cannot be derived: the landmines you can otherwise learn only by getting bitten
twice. Nothing derivable is cached; nothing hard-won is re-derived.

## Ecosystem coverage

`scripts/audit.py` implements PyPI / `uv.lock` end-to-end, and is tested against
it. npm, Cargo, Go, and GitHub Actions are covered as short per-registry
procedures in `references/ecosystems.md` that the model runs directly — the same
three questions in each registry's vocabulary.

This is deliberate. An unverified verifier is worse than none: it emits confident
green output nobody checks. Don't extend the script to an ecosystem you don't
have a repo to test it against.

## Tests

```
python3 -m unittest discover -s tests -v
```

22 cases, stdlib only, no network — they run offline and free. Every case
corresponds to a defect that actually shipped, or to a failure the audit exists
to detect: a corrupted hash, a size mismatch, a yanked release, an artifact
missing from the registry, a lagging version, a marker-constrained pin that must
*not* read as stale, a package pinned at two versions under different
resolution-markers, a requested name that isn't in the lockfile, an empty
selection that must not report `CLEAN`, a lockfile compared against itself, and a
non-PyPI package that has to be named rather than dropped.

The theme is **silent** failure. An audit that reports success while verifying
less than it claimed is worse than one that crashes, so the assertions target
what gets *reported*, not just what gets returned.

Each test was mutation-checked against the original buggy implementation to
confirm it discriminates — a suite that only ever passes proves nothing.

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

The skill declares `tools: Read, Grep, Glob, Bash`. That withholds `Edit` and
`Write`, but `Bash` could reach `gh pr merge` — so "reports, never merges" is a
**contract, not a sandbox**. It is stated in the skill, and the report ends with
the merge command printed rather than executed.

## License

MIT.
