# Portable traps

Each of these cost a real wasted cycle somewhere. They are ecosystem-independent;
per-registry mechanics live in `ecosystems.md`.

## Currency and changelogs

**A bot's proposal is not evidence of "current".** Bots ingest registry metadata
on their own schedule and can lag the registry by a day or more. Compare the
newer release's publish time against the PR's `createdAt`: if it was published
*first*, the bot simply had not seen it. Observed twice on the same package —
once where the gap contained a security fix, once where it contained two
destructive `--fix` bugs.

**Rule out the innocent explanations before calling it lag:** a yanked release,
or an `ignore` rule in the bot's config. Both look identical from the outside.

**Changelog `Security` sections outrank every vulnerability database.** A
privately disclosed fix ships with no CVE and no GHSA, so OSV, `pip-audit`, and
`npm audit` all report clean while the changelog says otherwise. Read the
changelog for the whole gap, not just the adopted version.

**Destructive-fix bugs never appear in a security feed.** A changelog entry like
"stop deleting line endings" or "stop deleting a line when trimming a code span"
is a data-loss bug. It only matters if the repo runs that tool in **write mode**
(`--fix`, `--write`, `-i`) — but many repos do exactly that in a pre-commit hook,
silently, on every commit. Check the hook config before dismissing it.

## Behavior change

**Allow-list vs. disable-list decides whether a new rule can break the build.**
If the repo's config disables specific rules, everything else is on by default —
so a rule *added* in the new version is live the moment it lands. If the config
enables specific rules, new rules are inert. This one distinction is the
difference between "no impact" and "blocks merge".

**A green gate can stay green while its scope moves underneath it.** Comparing
two versions of a tool by exit code is the obvious approach and it misses the
most common shape of behaviour change. Observed on ruff 0.15.22 -> 0.16.0 in a
repo already compliant with both: identical exit 0, while the newer version
formatted **33 more files** — it had started formatting Python fences inside
Markdown. Nothing in the pass/fail answer moved.

**Nor can you diff the two versions' output.** The same pair prints
`Would reformat: x.py` at 0.15 and an annotated diff at 0.16: the *renderer*
changed, so a text comparison reports every line as different and the real
finding drowns. Version bumps change output formats roughly as often as they
change behaviour.

**Diff what the tool did, not what it said.** Run each version in its **write**
mode inside a disposable worktree and compare which files it changed and to
what. That is stable across output formats, independent of the tool, and
answers the question directly. On the case above it reports `0 files -> 6 files`
and names them. `scripts/gate_diff.py` does this; the three results it
distinguishes — acted-on-by-newer-only, by-older-only, and both-but-differently
— are widened scope, narrowed scope, and a changed fix respectively.

**Measure the bump against the tree you have, not the tree the PR proposes.** A
differential gate run on the PR's own tree answers a weaker question than it
appears to. If the PR already contains the fixup — someone reformatted, or
re-ran the tool, to make CI pass — then the new version is already satisfied by
that tree and the run reports **no difference**. Which is exactly backwards: a PR
carrying a fixup is a PR whose behaviour change was real enough that a human had
to deal with it. Run against the **merge base**, where the change is still
visible.

Observed on a real `ruff 0.15.22 -> 0.16.0` bump: measured on the merge base, six
Markdown files; measured on the PR's tree, nothing at all. The six were exactly
the files the maintainer had hand-reformatted onto the branch, so the run
predicted the work before it existed — and re-running it after the fact would
have reported the bump as inert.

Running both is better than choosing: base-differs-and-PR-agrees means the change
is real *and* handled, which is the answer you actually want.

**A tool's formatter and its linter have different gates.** A version can leave
the linter untouched and still change what the formatter rewrites — including
widening to file types the repo never expected, such as code fences inside
Markdown.

**Local hook scope ≠ CI scope.** A pre-commit hook scoped to specific file types
runs a strictly smaller check than a CI step invoking the same tool over the
whole repo. A clean local hook run is not evidence that CI will pass. Compare the
two invocations explicitly.

## Lockfile shape

**A package name is not a unique key.** A lockfile can pin the *same* package at
several versions under different environment constraints — uv's
`resolution-markers`, npm's nested `node_modules` entries, Cargo's semver-major
duplicates. Any tool that builds a name→entry mapping silently keeps one and
drops the rest, so its artifacts go unverified while the output looks complete.
Key by **(name, version)**, and expect a name lookup to return a *list*.

Observed: a `uv.lock` pinning `rpds-py` at both `0.30.0` (Python < 3.11) and
`2026.6.3` — 231 artifacts across the two entries, of which a name-keyed audit
checked 116.

**A version is not the only thing a lockfile diff can move.** A PR can rewrite an
artifact's URL and hash and leave the version exactly where it was — same package,
same version, different bytes. A changed-set keyed on `(name, version)` selects
*nothing* for it, which is the one lockfile change most worth catching. Key the
comparison on the artifacts too, and report an artifact that moved at an unchanged
version as its own kind of finding: it is not a routine bump, and the innocent
explanations (a new platform wheel, a re-resolution) are worth confirming rather
than assuming.

The failure this produces is subtle in the wrong direction. A tool that refuses an
empty selection fails *safe* — but its message says "nothing changed", which is
the one explanation that is not true, and an operator who believes it dismisses a
correctly-refused audit.

**A constrained pin is not a stale pin — but only the *lower* fork is
constrained.** An entry held back by an environment marker (the last release
supporting an older interpreter) trails the registry permanently and by design,
and reporting it as "not current" invites a follow-up bump that can never be
made. The trap is the correction: uv stamps `resolution-markers` on **every**
block of a forked package, including the highest one — the pin that actually gets
installed on a current interpreter and *is* expected to track the registry. Treat
the presence of markers as an exemption and you exempt the only pin a bump could
ever move, so staleness on it becomes invisible.

Compare against the package's other pins, not against the markers alone: the
highest pin is live, the rest are held back.

## Registry and pinning

**A hash comparison cannot catch what the registry itself is serving.** Comparing
a lockfile's recorded hash against what the registry serves today catches a
lockfile edited after it was written honestly. It cannot catch a bad artifact the
registry is serving, because in that case the record and the lockfile agree — and
agreement is the entire test. Build provenance (PyPI's PEP 740 attestations, npm
provenance) closes that gap by naming the repository and workflow that produced
the file. Where it exists, compare the publisher against the release being
replaced: *the previous version was built by the project's own CI and this one was
not* is the signal, and it needs no external source of truth.

Report it as three states. **Absent is not a warning** — coverage is partial and
version-dependent everywhere it exists, so treating absence as suspicious makes
the row noise on most lockfiles and trains the reader to skip it. And be explicit
that reading a registry's *summary* of an attestation is not verifying the
signature: it is stronger than a hash echo, not independent of the registry.

**A version is not a dotted tuple of integers.** Splitting on `.` and comparing
numerically is the obvious ordering and it is wrong at the edges that matter. A
PEP 440 epoch (`2!1.0`) lives in the *first* segment, so the obvious parse makes
that segment non-numeric and sorts the whole version *below* unversioned
releases — out of any "what is between locked and latest" range, which is exactly
the set whose changelogs you were going to read. Epochs exist because a project
changed versioning scheme, which is when its changelog matters most.

The same shape bites elsewhere: `1.9` vs `1.10` under a string sort, a
pre-release that must not be proposed next to a post-release that may be, and
`1.0` vs `1.0.0`, which are the same version. If a tool computes "latest" itself
rather than reading it from the registry, its version comparator is load-bearing
and needs its own tests.

**A version that cannot be ordered is a version whose currency cannot be judged.**
Sorting it to the bottom and carrying on is how one silently leaves the range.
Refuse instead — this is a "could not run", not a finding.

**Annotated tags need dereferencing.** For an action pinned by SHA,
`GET git/refs/tags/<tag>` returns the *tag object's* SHA when
`.object.type == "tag"`, not the commit — comparing that against the workflow's
pin gives a false mismatch. Dereference via `GET git/tags/<sha>` and read
`.object.sha`. Lightweight tags return `commit` directly and need no indirection.

**Bots never propose a downgrade.** If a moving tag rolls backward, or a pin
needs to retreat, the bot cannot express it — and `@dependabot recreate` will not
help, because recreating still cannot produce a downgrade. Close the bot's PR and
fix by hand.

The detection is a two-way `compare` between the proposed SHA and wherever the tag
points now, not a bare equality check: *ahead* means the tag simply moved on and
the PR is stale, *behind* means the tag was rolled back and merging would pin a
commit the tag no longer covers. Only the second is a finding, and an equality
check reports both identically.

Observed: a `setup-nvc` bump proposed the branch tip, upstream then moved the `v1`
tag back two commits, and the bot's PR was left pinned to the commit the tag had
retreated from. It was closed and replaced by a hand-written PR.

## CI state

**`gh run list --commit` needs the full 40-character SHA.** A short SHA matches
nothing, returns empty, and reads exactly like "CI never ran".

**A merge state can read `CLEAN` on stale checks.** Right after a push, the API
can report the *previous* commit's results. Gate on the CI run for the current
full head SHA reporting `completed:success`, not on merge state alone.

**`UNSTABLE` is mergeable.** It means every *required* check is green and
something non-required is unsettled. `BLOCKED` is the state that matters.

**A run is `success` only if every job is.** Find the *latest* run for the SHA —
a duplicate event can cancel an earlier run, and `cancelled` is not `failure`.

**Check names contain spaces and ampersands.** Post-processing `gh pr checks`
with whitespace-splitting tools mangles them — `awk '{print $1}'` turns
`Lint & type-check` into `Lint`, and you end up confidently reporting on a check
that does not exist. Query structured output (`--json statusCheckRollup`) and
match names as whole strings.

**A check that never reported produces no row.** Filtering a rollup by name
yields nothing for a context that was never posted, which looks identical to
"not printed because it passed". Count returned rows against the required list
and treat any absence as *unreported*, not green.

**"No required checks" and "you asked the wrong question" look the same.**
Querying branch protection for a branch that does not exist returns an empty
context list, exactly like a branch with no protection — so a mistyped or
guessed branch name silently turns the whole required-checks verification into a
no-op. The HTTP status and message do separate the cases (`404 Branch not
found` = wrong branch; `404 Branch not protected` = genuinely unprotected;
`403 Upgrade to GitHub Pro…` = protection unavailable on that plan, e.g. a
private repo on a free plan), but only if you read them instead of discarding
stderr.

**Neutral / "skipping" security-scan results are normal** on diffs that do not
touch the scanned surface. Not a failure, and usually not a required check.

**A bot's own rebase does not re-trigger CI** (push-recursion suppression on the
bot's token). Close and reopen under your own auth, or ask the bot to recreate.
If neither fires the workflow, an empty commit will — but see the next entry.

**Never push onto the bot's branch.** A manual push makes the bot stop managing
the PR. Take follow-up work on a separate branch; merge the bot's PR exactly as
written.

## Verification hygiene

**`cmd | tail && next` gates on `tail`.** A failing test suite, linter, or type
check sails straight through into the next command. Use `set -o pipefail`, or run
the gate as its own call and read the exit code.

**A lockfile path resolves against whatever happens to be checked out.** A bare
`uv.lock` in a command reads the user's current branch, not the PR's — and the
base branch's lockfile parses perfectly, yields no changed packages, and produces
a confident audit of nothing. Read both sides out of git at a ref you pinned
(`git show <ref>:uv.lock`) rather than off the working tree, and make the tool
refuse an empty selection, so that failure cannot present itself as a pass.

**Frozen installs prove the lockfile; unfrozen ones hide drift.** `uv sync
--locked`, `npm ci`, `cargo build --locked`. Without the flag the resolver is
free to paper over an inconsistent lockfile.

**Auditors can measure the wrong environment.** A vulnerability scanner installed
outside the project audits *its own* interpreter or environment, not the
project's, and reports confidently about the wrong package set — the output looks
normal, just describes something else. Make the scanner run *inside* the project
environment and sanity-check that the package names in its output belong to the
project.

**A check that failed and a check that found something exit the same way unless
you make them differ.** An unhandled exception exits 1, which is also the
conventional "found something" status — so a verifier whose registry lookup times
out reports exactly as though it had found a vulnerability, and one handed an
unreadable file reports as though the file was bad. Give "could not run" its own
status (2 by convention) and route every foreseeable failure through it. The same
applies in reverse when you consume someone else's tool: before treating its
non-zero exit as a finding, check that it distinguishes the two at all.

**Reproduce in an isolated worktree.** Never mutate the user's working tree to
audit a branch.
