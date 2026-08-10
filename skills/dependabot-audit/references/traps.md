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

**A tool's formatter and its linter have different gates.** A version can leave
the linter untouched and still change what the formatter rewrites — including
widening to file types the repo never expected, such as code fences inside
Markdown.

**Local hook scope ≠ CI scope.** A pre-commit hook scoped to specific file types
runs a strictly smaller check than a CI step invoking the same tool over the
whole repo. A clean local hook run is not evidence that CI will pass. Compare the
two invocations explicitly.

## Registry and pinning

**Annotated tags need dereferencing.** For an action pinned by SHA,
`GET git/refs/tags/<tag>` returns the *tag object's* SHA when
`.object.type == "tag"`, not the commit — comparing that against the workflow's
pin gives a false mismatch. Dereference via `GET git/tags/<sha>` and read
`.object.sha`. Lightweight tags return `commit` directly and need no indirection.

**Bots never propose a downgrade.** If a moving tag rolls backward, or a pin
needs to retreat, the bot cannot express it. Fix by hand.

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

**Frozen installs prove the lockfile; unfrozen ones hide drift.** `uv sync
--locked`, `npm ci`, `cargo build --locked`. Without the flag the resolver is
free to paper over an inconsistent lockfile.

**Auditors can measure the wrong environment.** A vulnerability scanner installed
outside the project audits *its own* interpreter or environment, not the
project's, and reports confidently about the wrong package set — the output looks
normal, just describes something else. Make the scanner run *inside* the project
environment and sanity-check that the package names in its output belong to the
project.

**Reproduce in an isolated worktree.** Never mutate the user's working tree to
audit a branch.
