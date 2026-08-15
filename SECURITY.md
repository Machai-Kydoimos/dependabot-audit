# Security

## Reporting

Use GitHub's **private vulnerability reporting** — the *Report a vulnerability*
button on this repository's [Security
tab](https://github.com/Machai-Kydoimos/dependabot-audit/security/advisories/new).
It is enabled, and it is the channel.

Do not open a public issue for anything with a security dimension. There is no
email alias to fall back to, deliberately: a single advertised channel that is
known to work beats two where one is unmonitored.

This replaced the pre-public arrangement, where the repository was visible only to
the Machai-Kydoimos organization and an issue therefore *was* a private report.
Private vulnerability reporting is a public-repository feature and could not be
switched on before the flip.

## The risk most worth understanding is not a bug

**This plugin executes code from the pull request it audits.** That is by design
— reproducing the repo's own gates and test suite is the point of Phase 5 — and it
is the largest thing it does that cannot be undone.

| Phase | What runs |
|---|---|
| 4 | the repo's gates, at a version taken from the diff under audit, through a shell |
| 5 | a frozen install (npm lifecycle scripts, sdist builds, `build.rs`) and the PR's own test suite, from the PR's tree |

Phases 0–3 and 6–8 are network reads and `git` queries and execute nothing.

**What a frozen install actually runs, per ecosystem.** Only the first row is an
install this plugin performs. The rest are here deliberately: the hazard is true
whatever you are looking at, and someone arriving with an out-of-scope repository
should find the warning rather than silence. Their presence is not an invitation
to audit those ecosystems — see the scope section of the README for why they were
removed.

| Ecosystem | What an install runs | How to narrow it |
|---|---|---|
| **PyPI / uv** | any sdist in the resolution builds, running `setup.py` or the PEP 517 backend | `uv sync --locked --no-build --no-install-project` |
| npm | `preinstall` / `install` / `postinstall` scripts — the standard supply-chain vector | `npm ci --ignore-scripts` |
| Cargo | every crate's `build.rs` | **nothing** — there is no flag |
| Go | nothing at install time; `go build` does not run third-party build hooks | not needed |

This is the half of a removed recipe that fails *safe*. A warning that is ignored
costs nothing; a verification that is wrong reports green.

What the plugin does about it:

- **Phase 1 gates.** A diff reaching past the manifest and lockfile, or a
  provenance discrepancy, stops the audit before any phase that executes.
- **Phase 0 classifies.** Neither Dependabot nor Renovate opens a fork PR, so a
  cross-repository or non-bot-authored "bump" is a finding and switches the run to
  `--no-execute`.
- **`--no-execute`** runs the read-only phases only. It is most of the procedure's
  value and the right default for a PR you have no reason to trust.
- **Narrowed installs are the default** — `npm ci --ignore-scripts`,
  `uv sync --locked --no-build`. Cargo offers nothing equivalent for `build.rs`,
  and the per-ecosystem references say so rather than implying parity.

What the plugin does **not** do:

- **It is not a sandbox.** The Phase 5 worktree isolates your working tree from
  the audit. It does not isolate the machine from the PR. Isolation has to come
  from outside — a container, a throwaway VM, a Landlock confinement — and the
  plugin cannot verify that you have one.
- **`disallowed-tools` is a contract, not a sandbox.** It removes `Edit`, `Write`,
  and `NotebookEdit` while the skill is active. `Bash` remains, and could reach
  `gh pr merge`. "Reports, never merges" is honored, not enforced.

## In scope

- A path by which the plugin executes PR-controlled code at a phase that is
  documented as read-only — Phases 0–3, 6, 7, or 8.
- Anything that makes an audit report a stronger claim than it verified: a
  discrepancy that does not surface, a failure that exits as though it were a
  finding, a count that overstates what was checked.
- Credential or token exposure through any script, recipe, or report shape.

## Out of scope

- The fact that Phases 4 and 5 execute PR code. That is documented above, in the
  README, and in `SKILL.md`. A report that this is *undocumented* is welcome; a
  report that it happens is not a finding.
- Vulnerabilities in the packages an audit inspects. Report those upstream — this
  tool is how you find them.

## Attack surface

`scripts/audit.py` and `scripts/gate_diff.py` import nothing outside the Python
standard library, deliberately: they run under whatever bare `python3` the audited
repository has. There is no lockfile here to compromise and no dependency to
confuse. The plugin's own supply chain is its GitHub Actions pins, which are
SHA-pinned, and its pre-commit hook revisions.
