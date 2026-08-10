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
/plugin marketplace add <this repo's git URL>
/plugin install dependabot-audit
```

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
| 0 | Discover the repo — required checks, bot config, the repo's own CI gates and their scopes |
| 1 | Scope and provenance — every locked artifact's hash, size, URL, yank status vs. the live registry |
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

## Read-only

The skill declares `tools: Read, Grep, Glob, Bash`. That withholds `Edit` and
`Write`, but `Bash` could reach `gh pr merge` — so "reports, never merges" is a
**contract, not a sandbox**. It is stated in the skill, and the report ends with
the merge command printed rather than executed.

## License

MIT.
