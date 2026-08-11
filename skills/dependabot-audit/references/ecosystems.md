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

Metadata by hand: `https://pypi.org/pypi/<pkg>/json` →
`.info.version` (latest), `.releases["<ver>"][]` with `.digests.sha256`, `.size`,
`.url`, `.yanked`, `.upload_time_iso_8601`.

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

Frozen install is `npm ci`. Auditor is `npm audit --json`.

## Rust — crates.io, `Cargo.lock`

Each `[[package]]` carries `checksum` (hex sha256) matching
`https://crates.io/api/v1/crates/<name>/<version>` → `.version.checksum`.
Currency from `https://crates.io/api/v1/crates/<name>` → `.crate.max_stable_version`,
with `.versions[].created_at` for timestamps and `.versions[].yanked`.

Frozen install is `cargo build --locked`. Auditor is `cargo audit`.

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
