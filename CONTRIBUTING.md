# Contributing

This plugin's whole position is that a verifier which cannot be checked is worse
than none. The rules below are what that implies for changing it.

## Running the gates

```bash
pre-commit install          # ruff, mypy and the suite, on every commit
pre-commit run --all-files  # exactly what CI runs
python3 -m unittest discover -s tests -v
```

The suite is stdlib-only, offline, and finishes in under a second. There is no
install step and nothing to set up.

Two checks live outside it, in `integration/`, because they genuinely need the
network — a replay of a real ruff bump, and a cross-check of the registry
assumptions `audit.py` makes:

```bash
RUN_NETWORK_TESTS=1 python3 -m unittest discover -s integration -v
```

They run weekly in their own CI job and are **never required**: they go red for
reasons that are not this repo's fault. Keep them that way. The value of the
hermetic suite is that it is cheap and trustworthy on every commit, and anything
that needs a registry does not belong in it.

## The required checks, and the one that is silent

`main` carries a ruleset requiring `Lint & type-check` and all four
`Test (Python 3.x)` legs. **CI gates the merge; the hooks are the fast local
pre-check.** That inverted when the repository went public — rulesets are
unavailable on a private free-org repo, so until then nothing could be required.

**Renaming a matrix leg without updating the ruleset is loud, not silent** —
measured here rather than predicted, on PR #37, by adding a required context
(`Test (Python 3.99)`) that can never report:

| | Baseline | With the unsatisfiable requirement |
|---|---|---|
| contexts in the rollup | 5 | **5** — the missing one produces no row |
| every listed `isRequired` | true | true |
| `statusCheckRollup.state` | SUCCESS | **SUCCESS** |
| `mergeable` | MERGEABLE | MERGEABLE |
| `mergeStateStatus` | CLEAN | **BLOCKED** |

Both halves are true at once and they point opposite ways. **The repository's
view is loud:** the PR stops being mergeable. **The auditor's view is silent:**
the unsatisfied requirement is absent from the rollup entirely, so a procedure
reading `isRequired` and the rollup state sees five of five required checks green
and reports all-clear.

That is Phase 6's design rationale, and this is the first time it has been
measured on a repository where the right answer was known in advance rather than
inferred from someone else's PR. `SKILL.md` already says `mergeStateStatus` is
what closes the gap; this is the controlled case behind that sentence.

**The genuinely silent failure is the inverse**, and nothing above catches it:
rename the job *and* update the ruleset, but miss a leg. That leg still runs and
still reports, is no longer required, and nothing anywhere says so. Check the
ruleset against `ci.yml`'s matrix by hand whenever either changes.

**The ruleset is also the positive control this repo never had** for the two
endpoints that look like they answer *what is required here*. Measured at
`admin`, ruleset active, no classic protection:

```
GET repos/:o/:r/rules/branches/main       -> deletion, non_fast_forward,
                                             required_status_checks
GET repos/:o/:r/branches/main/protection  -> 404 "Branch not protected"
```

Each endpoint answers about its own mechanism and returns a confident "nothing
here" about the other. Neither substitutes for asking per-PR, which is what Phase
6 does — and why `SKILL.md` Phase 0 says not to call either one at any tier.

**`branches/<b>/protection` returns four states, not three, and only the first is
your own mistake.** It requires `admin`, and GitHub answers a bare `404` rather
than a `403` so as not to confirm the resource exists — and `gh` writes the error
body to *stdout*, so redirecting the call into a file yields a well-formed
artifact asserting the opposite of the truth.

| Response | Meaning |
|---|---|
| `404 Branch not found` | wrong branch name — fix and re-run |
| `404 Branch not protected` | correct branch, no **classic** protection configured — which is what this repo returns while a ruleset requires five checks |
| **`404 Not Found`** (bare) | **you lack `admin`** — protection may exist and be invisible to you |
| `403 Upgrade to GitHub Pro…` | unavailable on this **plan**, not a permission you lack — returned even at `admin: true`, and what this repo returned before going public |

Verified from the other side too: a repo whose `main` enforces three required
checks via classic protection returns the bare `404` to a `pull`-only account
while `branches/<b>` reports `"protected": true`.

## The gate with no script

**A change to a phase's method is replayed against the PR that motivated it
before it is committed, and the commit says what the replay showed.** Replayed,
not reasoned about: issue the phase's actual query against that PR and read what
comes back. Every defect below survived someone reasoning carefully about the
same change, including the author who had just written the fix.

No round of this has yet come back empty:

| Replayed | What it found |
|---|---|
| `fpga-board-sim` #359, #355 | `uv sync --locked --no-build`, shipped as Phase 5's documented default, fails on any project with a `[project]` table — installing itself editable *is* a build |
| `fpga-board-sim` #334 | Phase 4 measured the PR's own tree, which already carried the maintainer's fixup, so it reported `GATES AGREE` on the ruff bump this plugin's founding observation came from |
| `mdcat` #15, #14, #6 | two releases' worth, #19 and #20 among them; 0.8.0 narrowed the supported surface rather than patching what it found |
| `mdcat` #6, replayed against the fix written for it | Phase 6's new attribution query read `$BASE_SHA`, wrong in exactly the direction the fix existed to prevent (#25) |
| this repo's #26, against the corrected rule | `pr-<N>^` can have no runs to compare against, and the rule as committed discarded an answer sitting on the merge base |
| `cli/cli` #13996, #13981, #14124 | Dependabot's 2026-07-14 default cooldown, which Phase 2 read as ingestion lag; and two actions facts the PR does not state — the versions adopted, and whether the file it edits is generated |
| `cli/cli`, eleven bumps, against the *corrected* Phase 1 gate | `$BASE_SHA` collapses onto `$HEAD_SHA` on any PR that has landed, so Phase 1's diff is empty and Phase 4 measures the PR against itself |

**The fourth row is why this is a gate and not a habit.** Three prose guards went
with it, taking Phase 6's total to six, each mutation-checked against the previous
`SKILL.md` in the usual way, alongside ruff, mypy, 136 tests and CI green on four
interpreters — and one of the three asserted that Phase 6 read `$BASE_SHA`, which
*was* the defect. Mutation checking proves a guard discriminates between the old
prose and the new. It says nothing about whether the new prose is right, and a
guard written from the fix cannot supply that. The replay costs one `gh` call and
would have caught this before the commit rather than one commit later.

**The fifth row is the same gate showing its limit.** `mdcat` #6 could not have
found it — there the bot's parent has runs — so it took a second replay against a
PR exercising the other path. A defect on a path no PR you can reach exercises
survives this gate. Choose the target for what it *exercises* rather than for
recency, and prefer one whose consequences the repository's history already
records: #334 was decisive because the right answer was known before the phase
ran.

**The seventh row is what breadth buys.** Ten of those eleven bumps agreed with
the corrected gate and the eleventh did not, and the one that did not was a
merge commit sitting where a bot commit was assumed — a defect in a *different*
phase from the one being replayed. One PR would have passed. The cost of the
other ten was a loop.

**Since 0.12.0 the replay targets do not execute by default, and that is a
constraint on this gate rather than a bug.** Phase 0 now switches to
`--no-execute` when `$PERMS.push` is false, and measured across the table above
only `dependabot-audit` itself returns `push: true` — `cli/cli` and
`BIRSAx2/mdcat` are both `pull`-only. So replaying a **Phase 4 or Phase 5** method
change against them exercises everything *except* the phase being changed, and
the replay passes while proving nothing about it.

Two honest ways out, and the first is usually right: replay Phase 4 and Phase 5
changes against a repository you control — `#334` is already the canonical target
and is one of those. Where only a foreign PR exercises the path, authorise
execution deliberately for that run and say so in the commit body, because the
audit will otherwise report those rows as *not run* and the gate will read as
satisfied when it was not.

**This is a checklist item, and checklists are visibly skippable.** The box lives
in `.github/PULL_REQUEST_TEMPLATE.md`, and it asks for what the replay showed
rather than for a tick — a tick is an assertion, the pasted output is evidence,
and that distinction is the whole product. Know how little it reaches: the web UI
applies the template automatically, `gh pr create` takes it only via `-T`, and a
supplied `--body` or `--body-file` bypasses it entirely, which is how every PR
here has been opened so far.

By this file's own ordering that is the middle lever, and no stronger one is
available here: for *is this claim true about the world* there is no script, only
contact with the world. A tool that claimed to be one, and could not itself be
verified, would be the unverified verifier this repo exists to argue against — so
do not build one, however productive it would look.

## Tests

**Every case corresponds to a defect that actually shipped, or to a failure the
audit exists to detect.** If a change does not fix a real defect and does not
close a real failure mode, it probably does not need a test; if it does, the test
should be nameable as one or the other.

**Write the test first and watch it fail.** A test that has only ever passed
proves nothing about whether it discriminates. Every case here was mutation-checked
against the buggy implementation it was written for, and the commit message says
what the old code did when it ran. If you cannot make your new test fail against
the current code, the fix is not doing what you think.

**Clear `__pycache__` between mutations, or run `python3 -B`.** Three mutation
runs in 0.16.0 reported "not caught" against defects the tests do catch: the
module was edited and the test imported the previously compiled one. A
verification method that silently checks the wrong artifact is this repo's own
theme one level up — and it fails in the reassuring direction, because an
uncaught mutation reads as "this test is weak" rather than "this run was a lie".

**Write the check from the evidence, not from the change.** A test or a caveat
derived from the fix it guards can only ever confirm it, errors included — it is
the same failure as a test that has only ever passed, one level up, and rereading
it will not reveal anything because it agrees with itself by construction. Two
instances in one session shipping 0.10.0: a Phase 6 prose guard written from the
fix asserted the very property that *was* the defect, and went green; a
reference paragraph written from its own diagnosis restated what that file
already said two other places. The control is the guard written from a
**measurement** — `gh run list --json name` returns `CI`, not the check name —
which caught a real error. The discriminator is not the artifact and not the
reviewer; it is which direction the writing flows from.

**Assert on what gets *reported*, not only on what gets returned.** The theme is
silent failure: an audit that claims success while verifying less than it said is
worse than one that crashes. Several cases drive `main()` and read stdout for
exactly this reason.

**Adding a test module means editing `pyproject.toml`.** The mypy override lists
test modules explicitly, because a `test_*` wildcard silently matches nothing —
mypy's patterns work on dot-separated components — and the override then vanishes
with no warning, not even under `--warn-unused-configs`. Forgetting is loud, which
is the safer direction, but it is still a step.

## The prose is part of the product

`SKILL.md` is the largest thing here and the least mechanically checkable. Four
defects have shipped in it and nowhere else, two of them the same
forward-reference shape.

`tests/test_skill_prose.py` closes the *consistency* half: no phase may consume
what a later phase creates, the required-context list must be read from a Phase 0
artifact rather than typed, every script and reference path named must exist, and
the frontmatter key that withholds tools must be the one that works. If you add a
variable or an artifact that crosses phases, add it to the **Phase 0 outputs**
table — the test reads it.

It closes that half and no other. **Two gaps stay open, and they are different
gaps** — treating the suite's green as coverage of either is the mistake:

- **Whether the model follows the phases.** Behavioral, belongs in
  `claude plugin eval`, still unavailable on this account. The README says so and
  should keep saying so — and note *how* it is unavailable, because the shape is
  this repo's own theme: the subcommand exists, prints a full `--help`, and then
  refuses with ``plugin eval` is currently in early access` **on stderr, with an
  empty stdout**, at exit 1. A check written from the help text has nothing to
  grep and reports a clean empty result; the exit code is the only signal.
  Verify by invoking it, not by reading `--help` — and **not through a pipe**:
  this claim read *"at exit 0"* until 2026-08-16, measured as
  `claude plugin eval … | head`, which returns `head`'s status. Phase 5's own
  trap, landing on the measurement that argues for measuring.

  When it does open up, know in advance what it will and will not discharge. It
  can replay PRs whose right answer a human already established and grade whether
  the procedure still reaches it — a regression test on *execution*. It is not an
  oracle for whether a **new** rule is correct, and it does not replace the
  replay gate above.
- **Whether the prose is *true*.** Consistency is not correctness. Every guard
  can pass on a phase that names a real endpoint, consumes a properly-derived
  Phase 0 output, and asks it the wrong question. That shipped in 0.10.0: six
  passing guards on a Phase 6 that produced a false Hold on the only PR it had
  ever been run against. Nothing here could have caught it, and no plausible tool
  could. It is closed by replaying the PR a finding came from and by nothing else,
  which is why that replay is a gate rather than a habit — see **The gate with no
  script** above, including why the tool you are about to reach for is the
  unverified verifier this repo exists to argue against.

**A new inline trap is a signal that something wants mechanising.** Prose is the
weakest of the three levers — a trap a script refuses cannot be skipped, one on a
checklist is visibly skipped, one in prose is silently skipped. A trap only earns
prose when it cannot be enforced. That rule is why `SKILL.md` shrank in 0.2.0
after nine commits of growth.

## Constraints that are load-bearing

- **`audit.py` and `gate_diff.py` import nothing outside the standard library.**
  They run under whatever bare `python3` the audited repository has, and `tomllib`
  puts the floor at 3.11. That is why CI runs 3.11 through 3.14 and why the local
  hooks cannot be the whole story.
- **Do not extend a script to an ecosystem you have no repository to test it
  against.** The per-ecosystem references document what is in scope instead,
  deliberately.
- **Observations stay specific.** `rpds-py` at 231 artifacts of which a name-keyed
  audit checked 116; ruff formatting 33 more files; the six Markdown files. These
  name public packages at public versions, and generalising them would convert
  verified evidence into assertion — which is the failure this plugin exists to
  prevent.

## Gotchas that have cost time here

- **A `# noqa` only suppresses diagnostics reported on its own line, and
  `ruff check --fix` deletes any code that is not.** `S603` belongs on the
  `subprocess.run(` call and `S607` on the argv one line below. Grouping them on
  one line looks tidier and silently loses one.
- **`uv run` in this directory treats it as a project**, purely because
  `pyproject.toml` exists to configure the linters, and writes a `uv.lock` that
  locks nothing with a `requires-python` taken from whichever interpreter you
  invoked. Use `uv run --no-project`. `[tool.uv] managed = false` does not suppress
  it (tested). The stray lockfile is gitignored either way.
- **Tool versions live in `.pre-commit-config.yaml` and nowhere else.** CI invokes
  the hooks rather than installing its own ruff and mypy, so there is no second pin
  to drift.

## Commits and releases

Subjects carry the version: `fix: <what changed> (0.3.1)`. The body carries the
reasoning, at length — the commit log is the record, and `CHANGELOG.md` is the
readable index into it. Say what the old code did when it ran, not only what the
new code does.

Versioning: this plugin's public surface is its **procedure**, not an API. A
change to what a phase verifies, or to what the report asserts, is a minor bump
even when no code moved. A fix that only makes an existing claim true is a patch.

Every release gets an annotated tag matching the version in
`.claude-plugin/plugin.json`, an entry in `CHANGELOG.md`, and a published
[GitHub Release](https://github.com/Machai-Kydoimos/dependabot-audit/releases)
cut from that tag — `gh release create <tag> --verify-tag`, with notes drawn from
the CHANGELOG entry.

The Release is presentation, not distribution: `.claude-plugin/marketplace.json`
declares `"source": "./"`, so `/plugin marketplace add` installs from the default
branch and never resolves a Release. It is published anyway because a Release
reads as more authoritative than a bare tag, and because the alternative is what
0.21.1 shipped into — a repo whose sidebar advertised `v0.17.0` as *Latest* while
the plugin was four versions ahead. Let `--latest` default so `releases/latest`
tracks the newest version rather than drifting.

Releases start at `v0.22.0`. The tags before it are deliberately release-less and
are not to be backfilled.

## Security

Anything with a security dimension goes through [`SECURITY.md`](SECURITY.md)
rather than a public issue. Note in particular that the audit **executes code from
the PR it audits**, by design, in Phases 4 and 5 — that is documented, not a
finding.
