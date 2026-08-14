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

**Write the check from the evidence, not from the change.** A test or a caveat
derived from the fix it guards can only ever confirm it, errors included — it is
the same failure as a test that has only ever passed, one level up, and rereading
it will not reveal anything because it agrees with itself by construction. Two
instances in one session shipping 0.10.0: a Phase 6 prose guard written from the
fix asserted the very property that *was* the defect, and went green; a
`traps.md` paragraph written from its own diagnosis restated what the file
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
  `claude plugin eval`, unavailable on this account. The README says so and
  should keep saying so.
- **Whether the prose is *true*.** Consistency is not correctness. Every guard
  can pass on a phase that names a real endpoint, consumes a properly-derived
  Phase 0 output, and asks it the wrong question. That shipped in 0.10.0: six
  passing guards on a Phase 6 that produced a false Hold on the only PR it had
  ever been run against. Nothing here could have caught it, and no plausible tool
  could — a checker for whether the prose is true, that could not itself be
  verified, is the unverified verifier this repo exists to argue against. It is
  closed by replaying the PR a finding came from, and by nothing else. See #27.

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
  against.** `references/ecosystems.md` documents those as procedures instead,
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
`.claude-plugin/plugin.json`, and an entry in `CHANGELOG.md`.

## Security

Anything with a security dimension goes through [`SECURITY.md`](SECURITY.md)
rather than a public issue. Note in particular that the audit **executes code from
the PR it audits**, by design, in Phases 4 and 5 — that is documented, not a
finding.
