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
S="${CLAUDE_PLUGIN_ROOT}/skills/dependabot-audit/scripts/audit.py"

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
G="${CLAUDE_PLUGIN_ROOT}/skills/dependabot-audit/scripts/gate_diff.py"

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

**Do not read the exit codes as the answer** — see `references/traps.md`; both
versions can exit 0 while the scope moves underneath them.

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
uv sync --locked --no-build --no-install-project   # every dep resolved to a wheel
uv sync --locked                                   # then add the project itself
```

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

Then run the repo's own gates from Phase 0, and its full test suite.

### `--locked` checks the whole lockfile; the install materialises one resolution

Those are different claims and the row must not merge them:

| Step | Scope |
|---|---|
| `audit.py` provenance | **every** fork's artifacts, against the registry |
| `uv sync --locked` | asserts the whole lockfile is consistent with the manifest |
| the install that follows | **one** resolution — the interpreter and platform present |

So a green row here on 3.14 says nothing about whether the 3.11 fork's artifacts
still fetch or its older release still installs. **Ask the environment which one
it built**, rather than the auditor's own `python3`, which may not be the
interpreter uv chose:

```bash
uv run python -V                  # inside the synced environment
uv pip list --format=freeze       # the versions actually materialised
```

The Phase 1 script prints the fork list — `forked packages: every pin verified,
one of them installed` — so the names and versions to reconcile against are
already in the output. Name the interpreter and the fork in the reproduction
row; do not report the install as though it covered every pin.

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

**Three things qualify this phase's row, and "reproduced" alone asserts past all
of them.** Each has a green result that is true of *one* configuration and reads
as true of every one:

| Qualifier | Why the bare row overstates it |
|---|---|
| **which install** | the script-suppressing flags are the documented default and they weaken the proof: a package that genuinely needs its install script is not exercised. Re-running without them is a legitimate choice — say which produced the row |
| **which interpreter** | the install materialised one fork of a forked lockfile. `uv run python -V`, not the auditor's `python3` |
| **which forks were only verified** | Phase 1 checked all of them and Phase 5 installed one. Name the others rather than letting the install stand for them |

None of the three is a failure to disclose. "Frozen install passed under
`--no-build --no-install-project` on CPython 3.14; the 3.11 fork of `rpds-py` was
verified but not installed" is a stronger row than "frozen install passed",
because it is one a reader can falsify.

Gate on exit codes. `cmd | tail && next` gates on `tail`, so a failing suite sails
through; use `set -o pipefail` or separate calls.
