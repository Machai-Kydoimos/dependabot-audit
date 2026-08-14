# Per-ecosystem recipes

`scripts/audit.py` implements **PyPI / `uv.lock`** end-to-end (artifact
verification, currency, OSV) because that is what it was written and tested
against. Everything else below is a short API comparison you run directly — the
work is the same three questions in each registry's vocabulary:

1. Does the lockfile's recorded hash match what the registry serves today?
2. What is the registry's actual latest version, and when was it published?
3. What does the vulnerability database say about the whole locked set?

Do not extend the script to a new ecosystem without a repo to test it against.
An unverified verifier is worse than none — it produces confident green output
that nobody checks.

## Installing is executing

Every frozen install below runs code the PR controls. This is not a footnote; it
is the largest thing the audit does that the audit cannot undo:

| Ecosystem | What an install runs | How to narrow it |
|---|---|---|
| npm | `preinstall` / `install` / `postinstall` scripts — the standard supply-chain vector | `npm ci --ignore-scripts` |
| PyPI / uv | any sdist in the resolution builds, running `setup.py` or the PEP 517 backend | `uv sync --locked --no-build --no-install-project` |
| Cargo | every crate's `build.rs` | **nothing** — there is no flag |
| Go | nothing at install time; `go build` does not run third-party build hooks | not needed |

**`--no-install-project` is not optional there, and the reason is worth knowing.**
`--no-build` refuses *every* source build, including the project's own — and a
project with a `[project]` table installs itself editable, which is a build. On
its own it fails outright:

```
error: Distribution `<project>==<v> @ editable+.` can't be installed because it is
       marked as `--no-build` but has no binary distribution
```

uv has `--no-build-package` to exclude specific packages but no inverse, so there
is no single flag for "build my project, nothing else". Verified against uv 0.12.

That makes Phase 5 two steps for Python, which is a better shape anyway because
the steps prove different things:

```bash
uv sync --locked --no-build --no-install-project   # 1. the dependency set, zero source builds
uv sync --locked                                    # 2. add the project, to run its suite
```

Step 1 is the one with the security value: if it succeeds, every dependency in
the lockfile resolved to a **wheel** and no third-party build code ran at all. If
it fails, it names the package that needs an sdist build — which is a thing worth
knowing about a dependency bump, not an obstacle. Step 2 then builds only the
project's own code, which is code the repo already runs.

The narrowed forms are the **documented default**. They cost something real: a
package that genuinely needs its install script is not exercised, so the frozen
install proves slightly less than it would otherwise. That is a trade worth making
by default and worth reversing deliberately — **say in the report which one you
ran.** "Frozen install passed" is not the same claim in the two cases.

Cargo has no equivalent, so for a crate bump the only mitigations are outside the
tool: a container, a throwaway VM, or a Landlock confinement. The Phase 5 worktree
isolates the user's working tree from the audit; it does nothing about this. If
you have no isolation and no reason to trust the PR, `--no-execute` is the honest
answer — Phases 0–3 and 6–7 are all network reads and cover most of the ground.

## Python — PyPI, `uv.lock`

Covered by the script:

```bash
S="${CLAUDE_PLUGIN_ROOT}/skills/dependabot-audit/scripts/audit.py"
python3 "$S" "$SCRATCH/pr.uv.lock" --changed-vs "$SCRATCH/base.uv.lock"
```

Both lockfiles come out of git at the ref Phase 0 pinned, never off the working
tree. The script reports how many packages and artifacts it checked, and names
anything it could not reach — a package resolved from git, a path, or a private
index is outside its scope and is listed rather than dropped.

Metadata by hand — the **Simple API** (PEP 691/700/714), which is the specified
interface and the one the script uses:

```bash
curl -sH 'Accept: application/vnd.pypi.simple.v1+json' https://pypi.org/simple/<pkg>/
```

→ `.versions[]` (every release; **unordered**, so "latest" has to be computed)
and `.files[]` with `.filename`, `.hashes.sha256`, `.size`, `.url`, `.yanked`,
`.upload-time`, and `.provenance`.

Two things to know before verifying by hand:

- **`files` carries no version.** It is one flat list for the whole project, so a
  file has to be attributed to a release by its filename. Measured across 24,512
  real files: 2 unattributable, both old sdists whose filename version predates
  normalisation. Which versions *exist* comes from `.versions`, which is complete
  — so a mis-attributed file costs a timestamp, never a release.
- **The legacy `https://pypi.org/pypi/<pkg>/json` still works**, and its
  `.info.version` is the one thing the Simple API does not provide. It is
  undocumented, long-discouraged, and does not expose `provenance` — useful as a
  cross-check, not as the source.

**PEP 740 build provenance.** Where `.files[].provenance` is present, fetching it
returns `attestation_bundles[].publisher`:

```json
{ "kind": "GitHub", "repository": "pyca/cryptography", "workflow": "pypi-publish.yml" }
```

That is a materially stronger claim than a hash comparison: *this wheel was built
by the project's own CI*, rather than *this wheel is what PyPI is serving today*.
A hash check cannot catch a bad artifact the registry itself is serving, because
then the record and the lockfile agree, and agreement is the whole test.

Read it as three states, never two:

| State | Meaning |
|---|---|
| attested, publisher consistent with the release it replaces | the strongest available |
| **no attestation** | normal for anything predating Trusted Publishing — **not a finding** |
| attested, publisher changed | a real finding, and a loud one |

Coverage is partial and version-dependent — for `cryptography`, 1104 of 3637
files. Collapsing "no attestation" into a warning would make the row noise on most
lockfiles and train the reader to skip it, which is the failure this file opens
by warning about. And say in the report that this is *PyPI's* summary of the
bundle, not an independent signature check: stronger than a hash echo, not proof.

**`uv.lock` can contain several `[[package]]` blocks with the same name**, each
carrying its own `resolution-markers` — typically the last release supporting an
older Python alongside the current one. The script handles this; if you verify by
hand, do not assume one block per name. Do not report the *lower* block as stale
— but do check the highest one, which carries markers just the same and is still
expected to track the registry.

**Auditor trap.** `pip-audit` audits the environment of the interpreter it runs
under. Activating a virtualenv does **not** redirect a `pip-audit` installed
elsewhere — it will happily audit the system Python and report on distro
packages. Symptom: package names in the output that the project never depended
on. Run it inside the project environment:

```bash
uv run --with pip-audit pip-audit --skip-editable
```

`--skip-editable` is required when the project installs itself editable, and
`--strict` re-escalates that skip into a fatal error — do not combine them.
Default service is PyPI's advisory DB; `-s osv` selects OSV.

`pip-audit --locked` does not necessarily parse `uv.lock`; auditing the synced
environment is the reliable path.

## JavaScript — npm, `package-lock.json`

Lockfile entries carry `resolved` (URL) and `integrity`
(`sha512-<base64>`). Compare against
`https://registry.npmjs.org/<pkg>` → `.versions["<ver>"].dist.integrity` and
`.dist.tarball`; `.["dist-tags"].latest` for currency, `.time["<ver>"]` for
publish timestamps.

Note the digest is **base64 SHA-512**, not hex — decode before comparing to
anything computed locally.

Frozen install is `npm ci --ignore-scripts` by default; see *Installing is
executing* above before dropping the flag. Auditor is `npm audit --json`.

## Rust — crates.io, `Cargo.lock`

Each `[[package]]` carries `checksum` (hex sha256) matching
`https://crates.io/api/v1/crates/<name>/<version>` → `.version.checksum`.
Currency from `https://crates.io/api/v1/crates/<name>` → `.crate.max_stable_version`,
with `.versions[].created_at` for timestamps and `.versions[].yanked`.

Frozen install is `cargo build --locked`, which runs every crate's `build.rs` and
offers no flag to stop it — isolation for a crate bump has to come from outside
the tool. Auditor is `cargo audit`.

## Go — module proxy, `go.sum`

`go.sum` records `h1:` dirhashes, not plain artifact hashes. The verification is
`go mod verify`, and `GONOSUMDB`/`GONOSUMCHECK` must not be disabling the
checksum database. Currency from
`https://proxy.golang.org/<module>/@latest`. Auditor is `govulncheck`, which is
call-graph aware — it reports reachability, so a "no findings" result on a
vulnerable dependency is meaningful rather than a miss.

## GitHub Actions

A bump retargets a `uses:` pin. Confirm the new value is a 40-hex commit SHA and
that it really is the commit the claimed tag points at:

```bash
gh api repos/<owner>/<repo>/git/refs/tags/<tag> --jq '.object.type, .object.sha'
# if type == "tag" (annotated), dereference:
gh api repos/<owner>/<repo>/git/tags/<sha> --jq '.object.sha'
```

The dereference step is mandatory for annotated tags and a no-op for lightweight
ones. Skipping it produces a false drift report.

There is no lockfile and no vulnerability database here. The meaningful checks
are: every `uses:` is SHA-pinned, the workflow's permissions are minimal, and the
diff does not quietly add a step. A bot cannot roll a pin *backward*, so a tag
that moved backward needs a hand-written PR.
