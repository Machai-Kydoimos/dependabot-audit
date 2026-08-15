# Ecosystem recipes

This plugin audits two ecosystems, and only two: **Python / `uv.lock`** and
**GitHub Actions**. Together they are what a Python project's Dependabot queue
contains.

`scripts/audit.py` implements `uv.lock` end-to-end — artifact verification,
currency, OSV — because that is what it was written and tested against. It
answers three questions:

1. Does the lockfile's recorded hash match what the registry serves today?
2. What is the registry's actual latest version, and when was it published?
3. What does the vulnerability database say about the whole locked set?

GitHub Actions has neither of the inputs the first two questions need — no
lockfile and no artifact hash — so the script does not apply to it, and its
section below is the mechanical half of Phase 1 instead.

It **does** have an advisory database, which is easy to get wrong in the
dangerous direction: earlier revisions of this file said it had none. GHSA
carries an `actions` ecosystem, and SKILL.md's Phase 3 has the query and the
version-matching trap that makes the obvious form of it report clean on a
compromised action.

**npm, Cargo and Go are out of scope.** Recipes for them used to live here and
were removed rather than deferred. They broke the rule immediately below, in
exactly the way it predicts: followed faithfully against a real Cargo bump, the
recipe returned matching checksums, a current latest version and a clean OSV
batch — on a PR that raised the project's minimum Rust version past its own
declared floor. Nothing in that output looked partial.

Do not extend the script to a new ecosystem without a repo to test it against.
An unverified verifier is worse than none — it produces confident green output
that nobody checks. **A prose recipe is a verifier too.** It carries none of the
guards the script has earned — batch limits, retries, version ordering, the
refusal to report `CLEAN` on an empty selection — so every run either re-derives
them or silently does without.

**If the PR is for an ecosystem not covered here, say so and stop.** Do not
improvise a procedure from this file's shape; that improvisation is the failure
the removals exist to prevent, and it produces a green result rather than an
error. Report what the ecosystem-independent phases established — Phase 0's
classification, Phase 6's CI state — and name plainly what was not checked.

`audit.py` enforces its half of that rather than leaving it to this file. Handed
a `Cargo.lock`, `poetry.lock`, `package-lock.json`, `Pipfile.lock`, `go.sum`,
`go.mod`, `yarn.lock`, `pnpm-lock.yaml`, or a `pyproject.toml`, it exits 2 naming
the format and pointing back here. The two manifests are on the list for the same
reason as the lockfiles: they are what a reader hands it when the file in front of
them is the one their ecosystem calls the dependency file. Anything else with
`[[package]]` blocks that are not uv-shaped is refused without a guess. Exit 2
was always the answer; before 0.10.0 the message was `unexpected AttributeError
... This is a bug`, which sent the first reader to arrive with a Rust repo
hunting for a defect in the script.

## Installing is executing

A frozen install runs code the PR controls. This is not a footnote; it is the
largest thing the audit does that the audit cannot undo:

| Ecosystem | What an install runs | How to narrow it |
|---|---|---|
| **PyPI / uv** | any sdist in the resolution builds, running `setup.py` or the PEP 517 backend | `uv sync --locked --no-build --no-install-project` |
| npm | `preinstall` / `install` / `postinstall` scripts — the standard supply-chain vector | `npm ci --ignore-scripts` |
| Cargo | every crate's `build.rs` | **nothing** — there is no flag |
| Go | nothing at install time; `go build` does not run third-party build hooks | not needed |

**Only the first row is an install this plugin performs.** The others are kept
deliberately: the hazard is true whatever this plugin audits, and someone reading
this file while looking at an out-of-scope repository should find the warning
rather than silence. This is the half of a removed recipe that fails *safe* — a
warning that is ignored costs nothing, where a verification that is wrong reports
green. Their presence is not an invitation to audit those ecosystems.

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

The narrowed form is the **documented default**. It costs something real: a
dependency that ships only an sdist is refused rather than built, so the frozen
install proves slightly less than it would otherwise. That is a trade worth making
by default and worth reversing deliberately — **say in the report which one you
ran.** "Frozen install passed" is not the same claim in the two cases.

**The worktree is not isolation.** It isolates the user's working tree from the
audit and nothing more; it does not isolate the machine from the PR, and a source
build runs whatever its backend wants. Mitigation has to come from outside the
tool — a container, a throwaway VM, or a Landlock confinement. If you have none
and no reason to trust the PR, `--no-execute` is the honest answer: Phases 0–3
and 6–7 are all network reads and cover most of the ground.

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

**A fork is verified in full and installed in part**, and the two halves of the
audit disagree about how much they covered:

| Step | Scope |
|---|---|
| `audit.py` provenance | **every** fork's artifacts, against the registry |
| `uv sync --locked` | asserts the whole lockfile is consistent with the manifest |
| the install that follows | **one** resolution — the interpreter and platform present |

So a green Phase 5 on 3.14 says nothing about whether the 3.11 fork's artifacts
fetch or its older release installs. The script prints the pins for any forked
package it selected; the report names the interpreter (`uv run python -V` from
inside the synced environment, not the auditor's `python3`) and says which forks
were verified without being installed. A second `uv sync --locked --python
<floor>` is the thorough answer and is worth the interpreter download only when
the un-installed fork belongs to a package under audit.

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

## GitHub Actions

A bump retargets a `uses:` pin. There is no lockfile and no artifact hash, so
`scripts/audit.py` does not apply — this recipe is Phase 1's mechanical half.
Advisories are Phase 3's, and they exist: see SKILL.md.

**Read the diff, not the PR.** Two of Phase 1's answers come out of it, and
neither is where the obvious source puts it:

- **The scope gate keys on the kind of line, not the number of files.** An action
  is pinned in every workflow that uses it, and a grouped bump moves several
  actions at once. Measured on `cli/cli`, all three merged: #14091 two files,
  #13981 three, #14147 four — and every changed line across them is a `uses:`
  line or its trailing version comment. That is the invariant. A gate phrased as
  "one workflow file" refuses the ordinary case, and refuses it in the report's
  language for a bump reaching into source.
- **The versions under audit are not readable from the title or the body.** Phase
  1's rule against reading package *names* off the title extends to versions
  here, where no script derives them. `cli/cli` #13981 — titled and summarised
  "bump actions/checkout from 6 to 7" — moves one bare `@v6` pin to `@v7` *and*
  nine SHA pins from `v7.0.0` to `v7.0.1`: two transitions, one of them
  described. Its embedded release notes stop at v7.0.0 and are marked
  `(truncated)`, and `7.0.1` appears once in 10 KB of body, as a commit subject
  inside a collapsed list. Take the range Phase 2 reads from the `uses:` lines
  that changed.

**Phase 1's real question here is whether the pin is immutable.** That is the
provenance answer for this ecosystem, and it has only two values:

| Pin | What it is |
|---|---|
| `owner/action@<40-hex>` | content-addressed and immutable. What you audit is what will run |
| `owner/action@v1`, `@main`, `docker://img:tag`, or no tag at all | a **promise someone else can revoke.** What you audit is what runs *today* |

Everything below assumes the first. Under the second there is no pinned artifact
to compare, so the checks move up a level — to the tag line rather than the
commit — and the report has to say which of the two it was auditing. A repo that
pins nothing by SHA is not a repo with a stale pin; it is a repo whose pins are
not evidence.

**The tag is a claim in a comment, not part of the pin.** The convention is
`uses: owner/action@<40-hex>  # v1`, and only the SHA is load-bearing. The `# v1`
is unverified metadata that can be stale or simply wrong. Read it as the claim to
check, and note that a bump leaving the comment unchanged — `# v1` on both sides —
means the bot is tracking a **moving** tag.

```bash
gh api repos/<owner>/<repo>/git/refs/tags/<tag> --jq '.object.type, .object.sha'
# if type == "tag" (annotated), dereference — the ref gives you the *tag object*:
gh api repos/<owner>/<repo>/git/tags/<sha> --jq '.object.sha'
```

The dereference step is mandatory for annotated tags and a no-op for lightweight
ones. Skipping it compares a tag object against a commit and reports a false
mismatch. Verified live on `nickg/setup-nvc@v1`: annotated, and the undereferenced
SHA matches nothing.

**When the tag does not point at the proposed SHA, that is a question, not a
verdict.** Ask which way it moved:

```bash
gh api repos/<owner>/<repo>/compare/<proposed>...<where the tag points now> \
  --jq '"\(.status) ahead=\(.ahead_by) behind=\(.behind_by)"'
```

| Result | Meaning |
|---|---|
| identical | the pin is exactly the tag; nothing to do |
| `ahead` | the tag moved on after the PR was opened — ordinary lag, same shape as a registry currency gap |
| **`behind`** | **the tag rolled backward.** Upstream withdrew those commits from the tag line, and merging pins a commit the tag no longer covers |
| `diverged` | the tag was repointed to another line entirely — treat as a finding and read the commits |

The `behind` case is the one worth the trouble, because **a bot cannot fix it**:
retargeting to where the tag now points is a downgrade, and Dependabot will not
propose one. `@dependabot recreate` will not help either. It needs a hand-written
PR, and the bot's PR should be closed rather than merged.

Observed end to end on `nickg/setup-nvc`: a bump proposed the branch tip
`8bdacf7f`, upstream then moved `v1` back two commits to `48f966df` — dropping
"Bump ESLint version" and "Bump Actions SDK" — and `compare` reports the proposal
as two commits *ahead* of the tag. The bot PR was closed and replaced by hand.

**A workflow file can be generated, and then the bot's edit does not stick.**
Compilers that emit workflows own the `uses:` pins they write — `gh-aw` generates
`*.lock.yml` from a `.md` source — and Dependabot edits the emitted file, because
that is where the pin lives. Merging is not wrong; it is *transient*. The next
regeneration writes the pins back from the generator's own list, and that list
can be older than the bump.

Observed on `cli/cli`: #14124 merged `github/gh-aw-actions/setup` to v0.86.1
(`8914f47b`) on 2026-08-10, and the regeneration commit `ed5a99f` three days
later rewrote it to `2709137e`, v0.85.4 — `compare` reports `behind ahead=0
behind=2`, the same shape as the rolled-back tag above. The bot's own next PR,
#14147, then reads the current pin as **0.85.4**: the version its previous merged
PR had already moved past.

The detection is the header the generator writes — `DO NOT EDIT`, `automatically
generated by` — one `grep` over the files the diff touches. Where it fires, say
so: the durable fix is a bump of the generator, and the one under audit will be
undone without it.

Auditing an old or merged actions PR, compare against **the repo's current pin**
as well as the PR's proposal: a mismatch may already have been fixed, and the
workflow file on the default branch is what says so.

**CI cannot see any of this.** On the observed case every required check was
green, because the workflow parses and the job runs whichever commit it is
pointed at. Green says the pin resolves, not that upstream still stands behind
it. This is the actions-shaped version of the reason the whole procedure exists.

The remaining checks are structural: every `uses:` is SHA-pinned, the workflow's
`permissions:` are minimal, and the diff does not quietly add a step or change a
trigger.
