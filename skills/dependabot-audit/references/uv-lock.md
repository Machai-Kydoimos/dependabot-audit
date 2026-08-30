# Python — PyPI and `uv.lock`

The per-phase method for a `uv.lock` bump. `SKILL.md` carries each phase's
*question*, its gate, and the outputs it consumes; this file carries how to
answer it for this ecosystem, and nothing else.

Sectioned by phase deliberately, and the headings are load-bearing: the prose
suite attributes a bash block to the phase whose heading it sits under, and that
is the check which has caught three shipped forward-reference defects. A section
retitled out of that shape takes its guard with it.

`scripts/audit.py` implements this ecosystem end-to-end because it is what the
script was written and tested against. It answers three questions:

1. Does the lockfile's recorded hash match what the registry serves today?
2. What is the registry's actual latest version, and when was it published?
3. What does the vulnerability database say about the whole locked set?

Handed another ecosystem's lockfile it exits **2** naming the format — `is a
Cargo.lock (Rust)`, `is a poetry.lock (Python, Poetry)` — rather than guessing.
Report that as the boundary it is, not as a failed audit: the
ecosystem-independent phases still ran.

## Phase 1 — Scope and provenance

Both lockfiles come out of git at the ref Phase 0 pinned, never off the working
tree:

```bash
# Fresh call: nothing survives one, so re-derive $SCRATCH and re-source Phase 0.
REPO=$(gh repo view --json nameWithOwner --jq .nameWithOwner); SCRATCH="${SCRATCH:-${TMPDIR:-/tmp}/dbaudit-${REPO/\//-}-<N>}"
. "$SCRATCH/phase0.env" || { echo "no handoff in $SCRATCH — re-run Phase 0" >&2; exit 2; }

S="${SCRIPTS:?not in the handoff — re-run Phase 0}/audit.py"

git show "pr-<N>:uv.lock"    > "$SCRATCH/pr.uv.lock"
git show "$BASE_SHA:uv.lock" > "$SCRATCH/base.uv.lock"

python3 "$S" "$SCRATCH/pr.uv.lock" --changed-vs "$SCRATCH/base.uv.lock"
```

**Do not read the package names off the PR title** — a grouped bump names none
of them, and a bot may group everything (check the `groups:` key from Phase 0).
`--changed-vs` derives the set from the diff against the merge base;
`--changed pkg-a,pkg-b` is the fallback for a diff the script cannot read, not
the default.

**Exit 2 means it could not run; exit 1 means it ran and found something.** Never
read one as the other. Quote its `RESULT` counts — and whatever it names as
unreachable — in the report, rather than writing "verified" unqualified.

The script says why each package was selected. **`ARTIFACTS CHANGED at unchanged
version` is not a routine bump** — the lockfile re-points an artifact while the
version stands still. There are innocent explanations (a wheel added for a new
platform, a re-resolution against a different index); confirm which, rather than
assuming one.

That one invocation covers this phase **plus the mechanical half of Phases 2 and
3** — it also reports the registry's true latest with publish timestamps, PEP 740
build provenance where PyPI has it, and the OSV batch across the whole lockfile.
Read its output there rather than repeating those queries by hand.

**`PUBLISHER CHANGED` outranks everything else in the output.** It means the
release being adopted was built somewhere the previous one was not. Absence of an
attestation is *not* a finding — it is normal for anything predating Trusted
Publishing — and the script distinguishes the two.

### Reading the registry by hand

The **Simple API** (PEP 691/700/714) is the specified interface and the one the
script uses:

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

### PEP 740 build provenance

Where `.files[].provenance` is present, fetching it returns
`attestation_bundles[].publisher`:

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
files. Collapsing "no attestation" into a warning would make the row noise on
most lockfiles and train the reader to skip it. And say in the report that this is
*PyPI's* summary of the bundle, not an independent signature check: stronger than
a hash echo, not proof.

### Forked packages

**`uv.lock` can contain several `[[package]]` blocks with the same name**, each
carrying its own `resolution-markers` — typically the last release supporting an
older Python alongside the current one. The script handles this; if you verify by
hand, do not assume one block per name. Do not report the *lower* block as stale
— but do check the highest one, which carries markers just the same and is still
expected to track the registry.

## Phase 2 — Currency

The mechanical half — the registry's true latest, with publish timestamps — is
**already done** by the Phase 1 script. What is left is the question `SKILL.md`
sends here: this repo runs the tool, so is it in the change's scope?

### When the changelog entry names a dependency

A compiled Python package vendors another ecosystem's dependency graph into its
wheel — `ruff`, `uv`, `rumdl` and `pydantic-core` are Rust — and its changelog
says so in the terms of *that* ecosystem. Two consequences, and the second is the
one that bites:

- **No Python-side scanner can see the advisory.** It is filed against a crate on
  crates.io. `pip-audit` reporting clean under both `-s pypi` and `-s osv` is
  correct and means nothing here.
- **Grepping this repo's config answers nothing**, because the crate was never in
  this repo's config. The question is whether it is in the binary this repo
  installs.

**Read the shipped set out of the wheel.** PEP 770 wheels carry their own SBOM:

```bash
python3 - "<pkg>" "<version>" <<'EOF'
import io, json, sys, urllib.request, zipfile
pkg, version = sys.argv[1], sys.argv[2]
req = urllib.request.Request(f"https://pypi.org/simple/{pkg}/",
                             headers={"Accept": "application/vnd.pypi.simple.v1+json"})
files = json.load(urllib.request.urlopen(req, timeout=60))["files"]
# Any wheel for the release: the vendored set is the same across platforms.
name = f"{pkg}-{version}-"
wheels = [f for f in files
          if f["filename"].startswith(name) and f["filename"].endswith(".whl")]
if not wheels:
    sys.exit(f"no wheel for {pkg} {version} — sdist-only, or a version that is not there")
wheel = wheels[0]
zf = zipfile.ZipFile(io.BytesIO(urllib.request.urlopen(wheel["url"], timeout=180).read()))
found = [n for n in zf.namelist() if "/sboms/" in n]
if not found:
    sys.exit(f"{wheel['filename']} carries no SBOM — exposure is UNDERIVABLE, not clean")
for name in found:
    doc = json.loads(zf.read(name))
    print(name, doc.get("bomFormat"), doc.get("specVersion"),
          len(doc.get("components", [])), "components")
    print(sorted(c["name"] for c in doc.get("components", [])))
EOF
```

Measured on the case this section came from. `rumdl` 0.2.60's release notes carry
one line — `deps: update h2 to 0.4.16`, under **Fixed**, with no `Security`
heading — and that is RUSTSEC-2026-0258, *h2 unbounded empty DATA frames*:

| Question | Answer |
|---|---|
| `rumdl-0.2.60-py3-none-manylinux_2_28_x86_64.whl` | `dist-info/sboms/rumdl.cyclonedx.json`, CycloneDX 1.5, 178 components |
| `h2` among them | **no** — nor `reqwest`, `hyper`, `jsonschema` |
| `tokio` among them | yes, so the SBOM is the real shipped set and not a stub |

Not exposed, established rather than assumed.

**`Cargo.lock` would have said the opposite, and that is the trap.** It records
`[dev-dependencies]`, which are built for the project's own tests and are not in
the binary anyone installs. `rumdl`'s `Cargo.toml` at `v0.2.60` has
`jsonschema = "0.46"` under exactly that heading, and `jsonschema` is what pulls
`reqwest` → `h2`. Reaching for the lockfile finds the crate and calls it
exposure. The SBOM is the shipped set.

**A wheel with no SBOM is `underivable`, never clean.** PEP 770 is recent and
coverage is partial, so absence of the file says nothing about absence of the
crate. Report it the way Phase 0 reports an output it could not derive — the
distinction this plugin preserves everywhere else — and say which of the two the
row is.

### When the entry names a rule this repo disables

Then the claim rests on a config line, and a config line is an assertion about a
*file* while the verdict is about the *tool*. Run it both ways:

```bash
uv run <tool> check <one representative file>              # as this repo runs it
uv run <tool> check --no-config <the same file>            # with the config out
```

Measured on `rumdl` 0.2.59's destructive `MD013` fix — *"stop reflow from joining
a setext heading into its underline"* — in a repo running `rumdl check --fix` on
every Markdown commit, where the claim was `disable = ["MD013", "MD036"]`:

```
$ uv run rumdl check README.md
Success: No issues found in 1 file (6ms)

$ uv run rumdl check --no-config README.md
Issues: Found 32 issues in 1 file (14ms)
```

32 findings suppressed. The rule really is off, the destructive fix really is
inert here, and the difference between reporting that and asserting it is one
command. The escape hatch is spelled differently per tool — `--no-config`,
`--isolated`, `--config=/dev/null` — and every one of them is cheaper than being
wrong about which mode runs on every commit.

## Phase 3 — Known vulnerabilities

Batch-query OSV across the whole locked set, then corroborate with the
ecosystem's own auditor. **The OSV half is already done** — the Phase 1 script ran
it — so read that result instead of issuing a second query. What remains is the
auditor.

**Auditor trap.** `pip-audit` audits the environment of the interpreter it runs
under. Activating a virtualenv does **not** redirect a `pip-audit` installed
elsewhere — it will happily audit the system Python and report on distro
packages. Symptom: package names in the output the project never depended on. Run
it inside the project environment:

```bash
uv run --with pip-audit pip-audit --skip-editable
```

`--skip-editable` is required when the project installs itself editable, and
`--strict` re-escalates that skip into a fatal error — do not combine them.
Default service is PyPI's advisory DB; `-s osv` selects OSV.

`pip-audit --locked` does not necessarily parse `uv.lock`; auditing the synced
environment is the reliable path.

## Phase 4 — Behavior change

**Measure on the merge base, not on the PR's tree.** This is the difference
between finding the change and missing it, and the wrong choice fails silently:

```bash
# Fresh call: nothing survives one, so re-derive $SCRATCH and re-source Phase 0.
REPO=$(gh repo view --json nameWithOwner --jq .nameWithOwner); SCRATCH="${SCRATCH:-${TMPDIR:-/tmp}/dbaudit-${REPO/\//-}-<N>}"
. "$SCRATCH/phase0.env" || { echo "no handoff in $SCRATCH — re-run Phase 0" >&2; exit 2; }

[ "${MAY_EXECUTE:-}" = yes ] || { echo "MAY_EXECUTE='${MAY_EXECUTE:-unset}' — this block runs the PR's code; not authorised" >&2; exit 2; }

G="${SCRIPTS:?not in the handoff — re-run Phase 0}/gate_diff.py"

python3 "$G" --tree "$SCRATCH/base-<N>" \
  --run locked   "uv run --no-project --with ruff==<locked> ruff format ." \
  --run proposed "uv run --no-project --with ruff==<proposed> ruff format ." \
  --run latest   "uv run --no-project --with ruff==<latest> ruff format ."
```

The question is what the new version does to *the code you have*, which is the
base. A PR that already contains the fixup — because someone reformatted to make
CI pass — has a tree the new version is already happy with, so measuring there
reports no difference. And that is precisely the case where the behaviour change
was real enough that a human had to deal with it. Observed: on a real
`ruff 0.15.22 -> 0.16.0` bump, the base tree reports six Markdown files and the
PR's tree reports nothing.

**Then optionally re-run on `$SCRATCH/pr-<N>`.** The two trees answer different
questions, and together they say something neither says alone:

| base | PR | Reading |
|---|---|---|
| differs | agrees | a real behaviour change, and this PR already absorbs it — check *how* |
| differs | differs | a real behaviour change the PR does **not** handle — it will land on you |
| agrees | agrees | no behaviour change on this repo's code |

**`--no-project` is right for a self-contained tool and wrong for a gate that
imports the project.** ruff needs nothing but the files; a type checker or a test
suite needs the project's dependencies to say anything true. Denied them, every
expression coming from one degrades to `Any` under `ignore_missing_imports`, and
`warn_return_any` — which `strict = true` implies — then fires on code that is
fine. `gate_diff.py` reports the difference faithfully and the difference is an
artifact of the environment. Drop the flag for those gates; `--with` still pins
the version under test, measured on uv 0.12.5 against a project locking
`iniconfig 2.1.0`, where `uv run --with iniconfig==2.0.0` resolves to 2.0.0:

```bash
# Fresh call: nothing survives one, so re-derive and re-source exactly as above.
REPO=$(gh repo view --json nameWithOwner --jq .nameWithOwner); SCRATCH="${SCRATCH:-${TMPDIR:-/tmp}/dbaudit-${REPO/\//-}-<N>}"
. "$SCRATCH/phase0.env" || { echo "no handoff in $SCRATCH — re-run Phase 0" >&2; exit 2; }

[ "${MAY_EXECUTE:-}" = yes ] || { echo "MAY_EXECUTE='${MAY_EXECUTE:-unset}' — this block runs the PR's code; not authorised" >&2; exit 2; }

G="${SCRIPTS:?not in the handoff — re-run Phase 0}/gate_diff.py"

python3 "$G" --tree "$SCRATCH/base-<N>" \
  --run locked   "PYTHONDONTWRITEBYTECODE=1 uv run --group <group> --with mypy==<locked> mypy ." \
  --run proposed "PYTHONDONTWRITEBYTECODE=1 uv run --group <group> --with mypy==<proposed> mypy ."
```

**A gate that imports the project also writes into the tree it is measured in,
and one shape of that residue is reported as a finding.** Not the shape it looks
like — checking that `.mypy_cache/` is gitignored is busywork. Measured with
`gate_diff.py` itself, mypy 1.18.2 / ruff 0.14.2 / pytest 8.4.2:

| What the gate leaves behind | Reaches `snapshot_changes` |
|---|---|
| `.mypy_cache/`, `.ruff_cache/`, `.pytest_cache/` | **never** — each tool writes a `.gitignore` of `*` into its own cache, so git omits it whatever the repo ignores |
| another untracked directory, `__pycache__/` above all | **not by default** — `git status --porcelain` collapses it to `dir/`, and a directory has no content to hash, so the entry is dropped. **Yes** where the repo sets `status.showUntrackedFiles=all`, which un-collapses it |
| a file at a non-ignored path — `.coverage`, `coverage.xml`, `junit.xml` | **always** |

The two live rows invent a difference out of the tool's own bookkeeping and
report it in the vocabulary of a real finding. A `coverage 7.6.1 -> 7.13.0` bump
with `.coverage` unignored gives `~ .coverage  both act, different result — the
fix itself changed`; under `showUntrackedFiles=all`, `pytest 8.4.2 -> 9.1.1`
gives a `+`/`-` pair on
`tests/__pycache__/test_ok.cpython-313-pytest-9.1.1.pyc` — *widened scope* and
*narrowed scope* — because pytest stamps its own version into every file it
rewrites. Neither touches a line of the repo's code.

Neutralise it in the command, identically on every run, rather than trusting the
repo's `.gitignore` — the runs have to be treated alike for the comparison to
mean anything:

```bash
REPO=$(gh repo view --json nameWithOwner --jq .nameWithOwner); SCRATCH="${SCRATCH:-${TMPDIR:-/tmp}/dbaudit-${REPO/\//-}-<N>}"
. "$SCRATCH/phase0.env" || { echo "no handoff in $SCRATCH — re-run Phase 0" >&2; exit 2; }

[ "${MAY_EXECUTE:-}" = yes ] || { echo "MAY_EXECUTE='${MAY_EXECUTE:-unset}' — this block runs the PR's code; not authorised" >&2; exit 2; }

G="${SCRIPTS:?not in the handoff — re-run Phase 0}/gate_diff.py"

python3 "$G" --tree "$SCRATCH/base-<N>" \
  --run locked   "PYTHONDONTWRITEBYTECODE=1 COVERAGE_FILE=../cov-locked   uv run --group <group> --with pytest==<locked>   pytest -q --cov=<pkg>" \
  --run proposed "PYTHONDONTWRITEBYTECODE=1 COVERAGE_FILE=../cov-proposed uv run --group <group> --with pytest==<proposed> pytest -q --cov=<pkg>"
```

`gate_diff.py` runs each command with the tree as its working directory, so the
relative `../` lands the data file beside the worktree instead of inside it, and
needs no handoff to resolve. Both false findings above go to *no difference*
under it, measured.

**Through the script, not beside it — including the gates with no write mode.**
A type checker or a test suite writes nothing, so the temptation is to run it
twice by hand and diff the output, which is what a replay of #365 did: it took
ruff and rumdl through `gate_diff.py` and compared mypy with a bare `cd` into the
worktree and two `uv run` calls. That loses `require_clean_worktree`, loses the
`reset --hard` between runs — so run two inherits run one — and loses the tree
snapshot entirely. And the residue above is not a lesser concern for a read-only
gate but the **only** thing its tree delta can contain: a gate that writes
nothing, measured as having written something, has measured its own cache.

**Compare the tool's output, not `uv run`'s.** The first invocation provisions
the environment and says so — `Creating virtual environment`, `Installed 35
packages`, the hardlink warning — and the second, warm, says none of it. Captured
whole and diffed, every comparison therefore reports a difference on the first
run alone. Measured on this phase's own replay, where two mypy runs both
reported `Success: no issues found in 157 source files` and the diff came back
eight lines long, all of them uv's. Warm the cache with a throwaway run, or strip
what uv wrote before comparing; a read-only gate has no tree diff to fall back
on, so this capture *is* the measurement. Which is a reason to run it **inside**
`gate_diff.py`, not outside: the script already captures each run's output and
compares them, and warming beforehand is a `--run` you discard, not a licence to
drive the tool by hand.

Repos that have been bitten by this say so in their own configuration. The one
this section came from pins its mypy hook local rather than to `mirrors-mypy`,
and the comment gives the reason: *"the isolated env that mirrors-mypy builds
lacks those deps, degrading expressions to Any and tripping warn_return_any —
false failures that CI's `uv run mypy .` never sees."*

**And `--no-project` does not mean isolated.** Measured, uv 0.12.5, one command
and one difference:

| The working directory | a project dependency, under `--no-project --with …` |
|---|---|
| has a `.venv` beside it | **importable**, and the project's own `src/` is on `sys.path` |
| has none | absent |

The overlay is layered on a `.venv` when it finds one. Phase 4's worktree is
fresh, so isolation is what it gets today — which is the half that is wrong for a
type checker. It is worth knowing anyway, because a re-run after anything has
synced into that tree is a different measurement wearing an identical command,
and this phase compares three of them.

**Give the tool's write mode, not `--check`** — the measurement is what each
version does to the files, and `--check` does nothing to them. Run it once per
gate from Phase 0, at *each* scope: a hook scoped to `types_or: [python, pyi]`
and a CI step running the same tool over `.` are different gates, and this is
the phase that turns on the difference. Add the `latest` run whenever Phase 2
found a newer version, because that is the one you would be recommending.

Read the result as three distinct findings:

| Result | Meaning |
|---|---|
| only in the newer run | widened scope, or a rule that now fires |
| only in the older run | narrowed scope |
| both, different result | the fix itself behaves differently |

The last is the one no security feed reports: a formatter that used to delete
something and no longer does, in a write mode many repos run on every commit.

**Do not read the exit codes as the answer** — both versions can exit 0 while the
scope moves underneath them, which is why this phase measures the tree.

`allow-list vs disable-list` is no longer something to work out in advance; the
run settles it. Keep it for the *report*, to explain why a difference fired:
under a config that disables specific rules a newly added rule is live the moment
it lands, and under one that enables specific rules it is inert.

A gate with no write mode — a type checker, a test suite — leaves the tree
untouched and `gate_diff` says so. But **"no run changed any file" has three
causes**, and the tool deliberately does not choose between them: you gave a
read-only invocation, or the tree already satisfies every version, or the gate has
nothing to write. Only the first is a mistake; the second is a real agreement.
Decide which, and say so — do not report the weaker reading by default.

## Phase 5 — Independent reproduction

Install **frozen** — that proves the lockfile is self-consistent and resolves
nothing. For Python this is two commands, and they prove different things:

```bash
# Fresh call: nothing survives one, so re-derive $SCRATCH and re-source Phase 0.
REPO=$(gh repo view --json nameWithOwner --jq .nameWithOwner); SCRATCH="${SCRATCH:-${TMPDIR:-/tmp}/dbaudit-${REPO/\//-}-<N>}"
. "$SCRATCH/phase0.env" || { echo "no handoff in $SCRATCH — re-run Phase 0" >&2; exit 2; }
[ "${MAY_EXECUTE:-}" = yes ] || { echo "MAY_EXECUTE='${MAY_EXECUTE:-unset}' — this block runs the PR's code; not authorised" >&2; exit 2; }

cd "$SCRATCH/pr-<N>"
uv sync --locked --no-build --no-install-project   # every dep resolved to a wheel
uv sync --locked                                   # then add the project itself
```

**First check that what you installed contains what the PR changed.** uv's
`default-groups` is `["dev"]`, so the plain command covers a bump into `dev` and
covers nothing else. Measured on uv 0.12.5:

| Command | a `dev` package | a package in any other group |
|---|---|---|
| `uv sync --locked` | **installed** | absent |
| `uv sync --locked --group <name>` | installed | **installed** |

A bump into `lint`, `test`, `docs` — or into anything at all once
`tool.uv.default-groups` is narrowed — is therefore *not installed*, and nothing
fails. Phase 5 then reports a green frozen install for an environment that never
contained the package under audit, which reads exactly like a reproduction.

The fix is not a flag to memorise: `--group dev` is a no-op where the group is
already default and still wrong where it is not. **Reconcile instead** — Phase 1
already derived which packages moved, and the environment will say which ones it
has:

```bash
uv pip list --format=freeze | grep -E '^(<the packages Phase 1 named>)='
```

Every name Phase 1 listed should come back at the version the PR proposes. A name
that does not is the group question, and re-syncing with `--group <name>` is what
answers it. Say in the report which groups the row covers.

Step 1 is the one with the security value: if it succeeds, every dependency in
the lockfile resolved to a **wheel** and no third-party build code ran at all. If
it fails, it names the package that needs an sdist build — which is a thing worth
knowing about a dependency bump, not an obstacle. Step 2 then builds only the
project's own code, which is code the repo already runs.

**`--no-install-project` is not optional, and the reason is worth knowing.**
`--no-build` refuses *every* source build, including the project's own — and a
project with a `[project]` table installs itself editable, which is a build. On
its own it fails outright:

```
error: Distribution `<project>==<v> @ editable+.` can't be installed because it is
       marked as `--no-build` but has no binary distribution
```

uv has `--no-build-package` to exclude specific packages but no inverse, so there
is no single flag for "build my project, nothing else". Verified against uv 0.12.

The narrowed form is the **documented default**. It costs something real: a
dependency that ships only an sdist is refused rather than built, so the frozen
install proves slightly less than it would otherwise. That is a trade worth making
by default and worth reversing deliberately — **say in the report which one you
ran.** "Frozen install passed" is not the same claim in the two cases.

#### When the refusal names a dependency, narrow it — do not drop the claim

The error above names the package it refused. If that package is the **project**,
`--no-install-project` is the answer and it is already in the command. If it is a
**dependency**, the loss is not all-or-nothing, and the two wrong moves are
dropping to a plain `uv sync --locked` (which proves nothing about wheels) and
hand-rolling the exclusion list differently each time. Both have happened on the
same PR.

There is no inverse flag, but the lockfile already names the offenders: a package
with **no `wheels` array** is one uv must build. Exclude every package that *does*
have one, and the offenders are the only source builds left.

The one distinction that matters is **local versus remote**, not registry versus
everything. Measured on uv 0.12.7, a lockfile entry's `source` is one of:

| `source` key | What it is | Exclude it? |
|---|---|---|
| `registry`, `url`, `git` | a package fetched from elsewhere | **yes, if it has `wheels`** — that is the claim being made |
| `editable`, `virtual`, `directory` | the project itself, or a path dependency | **never** — these are built from source by design, and excluding one fails the sync for the reason `--no-install-project` exists |

Filtering on `registry` alone looks equivalent and is not: a `url` or `git`
dependency then escapes the exclusion *and* the denominator, so a package built
from source goes unnamed in a row claiming to name them all.

```bash
# Fresh call: nothing survives one, so re-derive $SCRATCH and re-source Phase 0.
REPO=$(gh repo view --json nameWithOwner --jq .nameWithOwner); SCRATCH="${SCRATCH:-${TMPDIR:-/tmp}/dbaudit-${REPO/\//-}-<N>}"
. "$SCRATCH/phase0.env" || { echo "no handoff in $SCRATCH — re-run Phase 0" >&2; exit 2; }
[ "${MAY_EXECUTE:-}" = yes ] || { echo "MAY_EXECUTE='${MAY_EXECUTE:-unset}' — this block runs the PR's code; not authorised" >&2; exit 2; }

cd "$SCRATCH/pr-<N>"
mapfile -t ROWS < <(python3 - uv.lock <<'PY'
import sys, tomllib
LOCAL = {"editable", "virtual", "directory"}   # the project and its path deps
remote: dict[str, bool] = {}
for p in tomllib.load(open(sys.argv[1], "rb"))["package"]:
    if LOCAL & set(p.get("source", {})):
        continue
    # Count PACKAGES, not lockfile blocks: a forked package has one block per
    # fork, and counting blocks inflates both halves of the row. It can be held
    # to a wheel only if EVERY one of its forks has one, hence the `and`.
    # No `wheels` key -> uv must build it, so it is one of the concessions.
    remote[p["name"]] = remote.get(p["name"], True) and bool(p.get("wheels"))
print(len(remote))                     # first line: the denominator
for name, wheeled in remote.items():
    if wheeled:
        print(name)
PY
)
TOTAL="${ROWS[0]}"; WHEELED=("${ROWS[@]:1}")

ARGS=(); for p in "${WHEELED[@]}"; do ARGS+=(--no-build-package "$p"); done
uv sync --locked "${ARGS[@]}"
echo "held ${#WHEELED[@]} of $TOTAL third-party packages to wheels"
```

Measured on uv 0.12.7, replaying `fpga-board-sim` #365: this installs the project
editable and every wheeled dependency from a wheel, and yields *"36 of 37
third-party packages resolved to wheels; `actionlint-py` was the only sdist
built"* — falsifiable, and strictly stronger than the plain sync.

**Say "packages" only if you counted packages.** That lockfile has 39 blocks, 38
of them remote, but only 37 remote *packages* — `rpds-py` is forked across two
blocks. A block-wise count reports "37 of 38" and passes the duplicate to
`--no-build-package` twice; the numbers are then wrong in a row whose entire value
is that a reader can check them.

Two things about it are worth knowing, and both belong in the report:

- **It proves less than a true `--no-build`.** The conceded package's build code
  did run, and a PEP 517 build is arbitrary code — it can fetch from the network,
  which is the whole reason `--no-build` is worth wanting. The claim is
  "everything except these named packages came from a wheel", not "no third-party
  build code ran". Name the concessions in the row so the reader can weigh them.
- **The lockfile read is a predictor; the sync is the proof.** A package can carry
  wheels for other platforms and none for this one, and it will be refused despite
  having a `wheels` array. uv **fails fast — one package per run** (measured: with
  two sdist-only dependencies the error names one), so if the sync still fails,
  add the newly-named package to the conceded set and re-run. It converges, and
  the conceded set is the answer.

Then run the repo's own gates from Phase 0, and its full test suite.

### `--locked` checks the whole lockfile; the install materialises one resolution

Those are different claims and the row must not merge them:

| Step | Scope |
|---|---|
| `audit.py` provenance | every fork's artifacts **of the packages this PR changed** — the lockfile's other forks are not checked at all |
| `uv sync --locked` | asserts the whole lockfile is consistent with the manifest |
| the install that follows | **one** resolution — the interpreter and platform present |

Three different scopes, and the first is the narrow one. It is easy to read the
provenance row as lockfile-wide because its neighbours are.

So a green row here on 3.14 says nothing about whether the 3.11 fork's artifacts
still fetch or its older release still installs. **Ask the environment which one
it built**, rather than the auditor's own `python3`, which may not be the
interpreter uv chose:

```bash
uv run python -V                  # inside the synced environment
uv pip list --format=freeze       # the versions actually materialised
```

The Phase 1 script prints the fork list — `forked packages: uv pins these at more
than one version` — covering the **whole lockfile**, so the names and versions to
reconcile against are already in the output and there is nothing to derive by
hand. It arrives in two groups, and the split is the point:

| Group in `audit.py`'s output | What Phase 5 may say about it |
|---|---|
| `artifacts verified against the registry above` | this run checked every pin's artifacts; the install exercised one. Name which |
| `NOT audited by this run` | the pin **count and versions only**. Phase 1 checked nothing here — the package is outside the changed set |

Name the interpreter and the fork in the reproduction row; do not report the
install as though it covered every pin, and **never write "verified" against a
name from the second group** — that asserts a check the run did not make.

**When the bumped package is itself forked, a second sync is the thorough
version** and it is a deliberate escalation, not the default:

```bash
uv sync --locked --python <floor>   # the floor from requires-python
```

It costs an interpreter download and can fail for reasons that have nothing to
do with the bump. The cheap version — installing once and disclosing which fork
that was — is honest and is what this phase requires. The second sync is worth
it when the fork you did *not* install is one of the packages under audit, and
the report should say which of the two you did.

**Four things qualify this phase's row, and "reproduced" alone asserts past all
of them.** Each has a green result that is true of *one* configuration and reads
as true of every one:

| Qualifier | Why the bare row overstates it |
|---|---|
| **which install** | the script-suppressing flags are the documented default and they weaken the proof: a package that genuinely needs its install script is not exercised. Re-running without them is a legitimate choice — say which produced the row |
| **which interpreter** | the install materialised one fork of a forked lockfile. `uv run python -V`, not the auditor's `python3` |
| **which forks were only verified, and which were not checked at all** | of the forks Phase 1 audited it checked every pin and Phase 5 installed one; the lockfile's other forks got neither. Name both sets rather than letting the install stand for them |
| **which groups** | the sync installs the default groups, and a bump outside them is absent with nothing to show for it. Reconcile against the set Phase 1 named, above |

None of the four is a failure to disclose. "Frozen install passed under
`--no-build --no-install-project` on CPython 3.14, `dev` group included; `rpds-py`
is forked and unchanged, so its 3.11 pin was neither audited nor installed" is a
stronger row than "frozen install passed", because it is one a reader can falsify.

Note which half of that sentence the fork clause makes: **unaudited and not
installed**, not "verified but not installed". The verified-but-not-installed row
is the right one only for a fork of a package the PR actually changed — and on a
real audit of #365 the wrong version of this sentence was written about `rpds-py`,
which the run had never looked at.

Gate on exit codes. `cmd | tail && next` gates on `tail`, so a failing suite sails
through; use `set -o pipefail` or separate calls.
