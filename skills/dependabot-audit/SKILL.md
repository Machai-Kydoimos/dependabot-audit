---
name: dependabot-audit
description: Audit an automated dependency-bump PR and produce an evidence-backed merge recommendation — verify lockfile artifact hashes against the registry, cross-check the true latest version, read changelogs for security and behavior changes, reproduce the repo's own checks in an isolated worktree, and report. Verifies uv.lock, GitHub Actions and pre-commit hooks end to end; any other ecosystem gets the ecosystem-independent phases and a stated boundary rather than an improvised recipe. Use when the user asks to review, audit, check, or decide on a Dependabot or Renovate PR, a dependency bump, a lockfile PR, or asks "is this safe to merge".
disallowed-tools: Edit, Write, NotebookEdit
---

# Dependabot Audit

**This skill reports; it does not decide.** It ends in a recommendation plus the
evidence behind it, and stops. Do not merge, approve, close, comment on, rebase,
or push to the PR. Print the merge command; do not run it.

`disallowed-tools` removes `Edit`, `Write`, and `NotebookEdit` from the pool while
this skill is active, but `Bash` remains and can reach `gh pr merge`. That
restraint is a **contract, not a sandbox** — honor it. There is no exception:
Phase 8 hands its memory entry back rather than writing it, precisely because
reaching for `Bash` to do what the withheld tools would have done makes the
withholding theatre.

## This audit executes the code it audits

The contract above governs what *this skill* writes. It says nothing about what
the audited code does, and two phases run it:

- **Phase 5** installs frozen and runs the PR's own test suite from the PR's tree.
  `uv sync` builds any sdist in the resolution, which runs `setup.py` or the
  project's build backend.
- **Phase 4** runs the repo's gates at a version taken from the diff under audit.
- **`gate_diff.py`** passes its `--run` commands to a shell, and those commands are
  transcribed from the audited repo's CI config, which an actions bump legitimately
  modifies.

**The worktree isolates the user's working tree from the audit. It does not
isolate the machine from the PR.** Nothing here is a sandbox; if you need one, it
has to come from outside — a container, a throwaway VM, or a Landlock confinement
— and this skill cannot verify that you have one.

The ordering is the mitigation available inside the skill, and it is worth being
exact about what that buys. **Phase 1 is a gate**: if the diff reaches beyond the
manifest and lockfile, or provenance fails, stop there. Do not continue into the
phases that execute. A procedure whose thesis is "verify before you trust" must
not run the artifact before it has finished deciding whether to trust it.

**What the gate catches is a lockfile edited after it was written honestly** — a
hash, size, URL or yank status that disagrees with the registry — and a diff that
reaches into source. **It does not catch a malicious release.** Phase 1 compares
the lockfile against what the registry serves *today*, so when the attacker
published the artifact, the record and the lockfile agree — and agreement is the
entire test. A bump to a version whose maintainer account was compromised passes
Phase 1 clean and arrives at Phase 5's install with the gate's blessing.

The one signal that speaks to it is PEP 740 build provenance: `PUBLISHER CHANGED`
means the release being adopted was built somewhere the previous one was not.
Coverage is partial and version-dependent, so where there is no attestation there
is no signal. Read the ordering as what it is — it removes the cases it can see,
and `--no-execute` is the answer for the rest.

**`--no-execute`** runs Phases 0–3 and 6–7 only. Every one of those is a network
read: provenance, currency, changelogs, OSV, CI state. That is most of this
procedure's value, and it is the right default for a PR you have no reason to
trust yet. Use it when the user asks, and when Phase 0 classifies the PR as one
the bots did not open. Say in the report which phases did not run.

**That claim is a property of each phase's commands, not of its number**, and it
has already been false once: until 0.34.0 the `uv.lock` recipe for Phase 3 was
`uv run --with pip-audit …`, which syncs the project — installing it editable and
building any sdist in the resolution — so the mode that exists for a PR you do not
trust ran that PR's build code. A phase in this set that gains a command has to be
checked against this sentence, which is what `tests/test_skill_prose.py` now does
mechanically.

## Arguments

`/dependabot-audit <PR> [--no-execute] [--comment]`, and the same words said in
prose. The PR number is the only one that is required.

**If no PR number arrived, ask which one before starting.** Do not reach for the
most recent bump, and do not audit whatever branch happens to be checked out.
Both read as helpful, and both audit something the user did not ask about — the
report that comes back is then about the wrong PR while looking exactly like a
report about the right one.

- **`--no-execute`** — as above: Phases 0–3 and 6–7, and name the skipped phases
  in the report.
- **`--comment`** — produce the report and print it, then *offer* to post it.
  Posting is a separate action the user asks for explicitly; the flag requests
  the offer, not the post.
- **Anything else is not a flag this procedure knows.** Say so, rather than
  inferring what it might have meant.

## Why this procedure exists

The failure modes that bite are not "is this package malicious" — they are a
proposal that is already stale, a gap containing a fix no vulnerability database
knows about, and a bump that changes a *default* rather than a behavior. All
three are observed, not hypothetical. Phases 2 and 4 exist for them, and each
carries the measurement it came from.

## Phase 0 — Discover the repo (derive every run; never cache)

Never persist the answers to these. Required checks get added, CI jobs get
renamed, and a cached profile silently audits a repo that no longer exists.
Deriving costs one call each.

**Derive with the script; mutate by hand.** `scripts/discover.py` answers every
derivable question and tags each answer **derived / absent / underivable**. It is
read-only — no fetch, no worktree, no local `git` at all — so the two things Phase
0 changes in the user's repository stay visible in this file, where a plugin whose
contract is "reports, never merges" should keep them.

```bash
D="${CLAUDE_PLUGIN_ROOT}/skills/dependabot-audit/scripts/discover.py"
REPO=$(gh repo view --json nameWithOwner --jq .nameWithOwner)
# OUTSIDE the repo, and the SAME directory on every later call — derived, not remembered
SCRATCH="${SCRATCH:-${TMPDIR:-/tmp}/dbaudit-${REPO/\//-}-<N>}"; mkdir -p "$SCRATCH"

python3 "$D" --repo "$REPO" --number <N>                              # the report
python3 "$D" --repo "$REPO" --number <N> --shell > "$SCRATCH/phase0.env"
. "$SCRATCH/phase0.env"
```

Source the outputs rather than transcribing them. Four of them are
40-character SHAs, and a wrong one is not detectable downstream: a truncated
`$HEAD_SHA` matches no CI run and reads exactly like *CI never ran*, and a wrong
`$BASE_SHA` gives a scope diff that is wrong rather than empty. What the file
defines:

```bash
#   SCRIPTS=<abs path>     this plugin's own scripts/ — the one output not about
#                          the PR. Derived from discover.py's own location, so a
#                          reference can name a script; see below
#   DEFAULT=<branch>       the repo's default branch, derived
#   HEAD_SHA=<40 hex>      the commit under audit
#   BASE_REF=<40 hex>      GitHub's own base for the PR
#   BASE_SHA=<40 hex>      the merge base, from GitHub's compare endpoint
#   OWNER=<owner>          for Phase 6's GraphQL variables
#   NAME=<name>
#   CREATED_AT=<iso8601>   when the PR was opened — Phase 2's cooldown test
#   BRANCH_POINT=<ok|rewritten|suspect|underivable>
#   MAY_EXECUTE=<yes|no>   whether Phases 4 and 5 are authorised
#   HUMAN_COMMITS=<shas>   non-bot commits on the branch — a finding, not a gate
#   ECOSYSTEM=<uv.lock|github-actions|unsupported|unknown|underivable>
#   SCOPE_GATE=<clean|beyond|underivable>   Phase 1's gate, already decided
```

**An underivable output is emitted commented-out, so it stays unset.** That is
deliberate: a later phase then fails loudly on an empty value instead of quietly
on a plausible one, which is the distinction this whole phase exists to preserve.

**Exit 2 means it could not run; exit 1 means it ran and found something.** Never
read one as the other. Exit 1 here does not stop the audit — it means the shape of
the audit changes, and the report has to say how.

Read out of its output: `$DEFAULT`, `$HEAD_SHA`, `$BASE_SHA`, `$PERMS`, whether
the merge base is the **branch point**, and whether Phases 4 and 5 may run at all.
Take `$BASE_SHA` from it rather than from `git merge-base`, and the reason is the
next section.

Then the part that changes state, which is yours:

```bash
# Fresh call: nothing survives one, so re-derive $SCRATCH and re-source Phase 0.
REPO=$(gh repo view --json nameWithOwner --jq .nameWithOwner); SCRATCH="${SCRATCH:-${TMPDIR:-/tmp}/dbaudit-${REPO/\//-}-<N>}"
. "$SCRATCH/phase0.env" || { echo "no handoff in $SCRATCH — re-run Phase 0" >&2; exit 2; }

git worktree prune          # a previous run's registrations, if $SCRATCH is gone
git fetch origin "pull/<N>/head:pr-<N>" "$DEFAULT"
git worktree add "$SCRATCH/pr-<N>" "pr-<N>"
git worktree add --detach "$SCRATCH/base-<N>" "$BASE_SHA"   # Phase 4 measures here
```

**`prune` first, and it is not defensive clutter.** `$SCRATCH` lives under
`$TMPDIR`, so a reboot or a tmp sweep between two audits of the same PR deletes
the worktrees and leaves their registrations behind. Git then refuses **the
fetch** — one command before any `worktree add` — with a message naming a
directory that is not there:

```
fatal: refusing to fetch into branch 'refs/heads/pr-<N>' checked out at '<a path that no longer exists>'
```

That is this, not a permissions or ref problem, and the stale-worktree paragraph
below does not reach it: that paragraph is keyed to `git worktree add` refusing,
and Phase 0 never gets that far. `prune` is a no-op when state is clean, needs no
path argument, and clears `pr-<N>` and `base-<N>` together.

**Create the worktrees only where Phase 4 or Phase 5 will run.** They are the two
phases that need a tree; every other read here reaches the PR through
`git show` at a ref. Four paths run neither, and the condition is the phases
rather than any one of the four:

| Condition | Already on disk as | Why neither phase runs |
|---|---|---|
| an actions bump | `$ECOSYSTEM=github-actions` | `references/actions.md` reads the diff with `git show "pr-<N>:…"` throughout, Phase 4 reads release notes, and Phase 5's substitute is `gh run list` |
| an ecosystem this plugin does not cover | `$ECOSYSTEM`, `$SCOPE_GATE=beyond` or `underivable` | Phase 1's boundary stops the audit before either |
| a `pull` tier, a non-bot author, or a cross-repository head | `$MAY_EXECUTE=no` | both phases open by testing it for `yes` |
| `--no-execute` | **not** `$MAY_EXECUTE` — the flag is yours, and `discover.py` never sees it | the arguments section defines the run as Phases 0–3 and 6–7 |
| Phase 1 finding anything | `$SCOPE_GATE=beyond` | both phases name it as a reason to skip |

Every row's input exists before the decision: `discover.py` writes `$ECOSYSTEM`,
`$SCOPE_GATE` and `$MAY_EXECUTE` one command earlier, and the flag is a word in
the invocation. **This rule used to name the ecosystem instead of the property**,
and two live runs deviated from it independently — each reasoning out that an
uncovered ecosystem consumes no worktree either, and each writing the gap up
rather than acting on it (#111). Naming one of five conditions needed four more
exceptions; the property is the same in all five, so it is stated once.

The **fetch** stays on every path: `git show` needs the ref, Phase 6's merge
simulation needs `pr-<N>`, and Phase 7 still has a branch to remove. Where the
ecosystem is not yet known, the scope diff settles it and costs one command.

And the part no script can read for you — the bot's configuration, which decides
whether a currency gap in Phase 2 is lag or a deliberate hold, and the repo's
**own** verification commands. That stays here for the same reason the mutations
do: `pytest` may be `uv run pytest`, `tox`, `nox`, or a `make` target, and only
the workflow says so.

**Read every one of them at a ref.** The rest of Phase 0 is pinned to the PR and
these were not — they ran in the user's checkout, so the answers came from
whatever branch happened to be there:

```bash
# Fresh call: nothing survives one, so re-derive $SCRATCH and re-source Phase 0.
REPO=$(gh repo view --json nameWithOwner --jq .nameWithOwner); SCRATCH="${SCRATCH:-${TMPDIR:-/tmp}/dbaudit-${REPO/\//-}-<N>}"
. "$SCRATCH/phase0.env" || { echo "no handoff in $SCRATCH — re-run Phase 0" >&2; exit 2; }

# Derive the workflow list before reading it, at both refs. A repo can have
# several, and a guessed `ci.yml` either fails loudly on a repo that spells it
# `tests.yml` or — worse — succeeds and silently narrows the gate list to one.
git ls-tree --name-only "pr-<N>:.github/workflows/";    echo "pr list exit: $?"
git ls-tree --name-only "$BASE_SHA:.github/workflows/"; echo "base list exit: $?"

git show "pr-<N>:.github/dependabot.yml" 2>/dev/null || git show "pr-<N>:renovate.json"
git show "pr-<N>:.pre-commit-config.yaml"

# Then every name each list gave, at its own ref. Not one file: `<workflow>`
# stands for the whole list, and Phase 6 asks for the same list narrowed to what
# the diff touched.
git show "pr-<N>:.github/workflows/<workflow>"       # the gates Phase 5 reproduces
git show "$BASE_SHA:.github/workflows/<workflow>"    # the gates Phase 4 measures with
```

`ls-tree` on a directory that is not there exits **128** and says so, rather than
printing nothing at exit 0 — so a repo with no workflows is distinguishable from
a read that failed, which is the distinction the two lists exist to support.

**Each phase's gates come from the tree it runs them in**, and the two trees are
not the same one: Phase 5 reproduces in `$SCRATCH/pr-<N>`, Phase 4 measures in
`$SCRATCH/base-<N>` — or `$SCRATCH/tip-<N>`. One list run in both manufactures
this phase's own worst outcome, an **exit 2** that reads downstream as a gate
*failure*. Observed auditing a merged bump: the checkout's `ci.yml` listed
`uv run actionlint`, which arrived three PRs later, so in the PR's worktree it
could not spawn — "could not run" reported as "ran and found something", by the
procedure that is most careful about that distinction everywhere else.

**A gate on only one side of the bump is itself a finding**, and it is quiet in
both directions. A gate since *removed* runs against a tree that never had it; a
gate the PR *adds* never runs at all — and the second is the one that matters,
because an actions or tooling bump can legitimately add its own. Diff the two
lists and report the difference rather than picking a side.

If `git worktree add` refuses because the path already exists, a previous run
left it there. **Prove it still points at this PR's head before reusing it** — a
stale worktree silently audits the wrong commit and every result downstream is
wrong. Compare against the pinned SHA rather than eyeballing a log line:

```bash
# Fresh call: nothing survives one, so re-derive $SCRATCH and re-source Phase 0.
REPO=$(gh repo view --json nameWithOwner --jq .nameWithOwner); SCRATCH="${SCRATCH:-${TMPDIR:-/tmp}/dbaudit-${REPO/\//-}-<N>}"
. "$SCRATCH/phase0.env" || { echo "no handoff in $SCRATCH — re-run Phase 0" >&2; exit 2; }

test "$(git -C "$SCRATCH/pr-<N>" rev-parse HEAD)" = "$HEAD_SHA"
git -C "$SCRATCH/pr-<N>" status --porcelain                       # must be empty
```

If either check fails, remove it and re-add — with
`python3 "$SCRIPTS/cleanup.py" --scratch "$SCRATCH" --pr <N>` rather than a bare
`git worktree remove`. The second check fails precisely when the tree is dirty,
and a plain `remove` refuses on exactly that, so the remedy would fail on the
state it was written for — and a previous run's Phase 5 residue would be
discarded unread rather than saved.

**Phase 0 outputs.** Every later phase consumes these and nothing else:

| | |
|---|---|
| `$SCRIPTS` | this plugin's `scripts/` directory, absolute. **This is how `references/*.md` name a script**, and the only output that is not about the PR — `discover.py` derives it from its own location. See the paragraph below for why the references cannot use `${CLAUDE_PLUGIN_ROOT}` and Phase 0 can |
| `$DEFAULT` | the repo's default branch, derived |
| `$SCRATCH` | scratch directory, outside the repo — and **derived, so every later call resolves it to the same place**. Nothing else here survives a call boundary, so every consuming block re-derives this and re-sources `phase0.env` before reading any row below |
| `$HEAD_SHA` | the full 40-character commit under audit |
| `$BASE_SHA` | the merge base, from GitHub's own `compare` endpoint — never a local `git merge-base` against `$DEFAULT`, which collapses onto the head once the PR lands. **And whether it is the bot's branch point**, which is a separate answer |
| `pr-<N>` | the fetched branch, registered in the **user's** repo |
| `$SCRATCH/pr-<N>` | worktree at the PR's head — Phase 5 reproduces in it. **Created only where Phase 4 or Phase 5 will run**; the table above says how that is read off `$ECOSYSTEM`, `$SCOPE_GATE` and `$MAY_EXECUTE` before either phase is reached |
| `$SCRATCH/base-<N>` | worktree at the merge base — **Phase 4 measures in it**, and the reason is below. Same condition, and not a second exception |
| the repo's gates | read at a ref, **once per tree they will run in**: `pr-<N>` for Phase 5, `$BASE_SHA` for Phase 4. A gate on only one side is a finding |
| `$OWNER`, `$NAME` | the repo's owner and name, for Phase 6's GraphQL variables |
| `$CREATED_AT` | when the PR was opened, ISO-8601. **Phase 2 compares release publish times against it** — the cooldown asks whether a release was three days old *then*, not now |
| `$BRANCH_POINT` | `ok`, `rewritten`, `suspect` or `underivable` — **the tip-worktree block below gates on it**, and the table there says what each one means |
| `$MAY_EXECUTE` | `yes` or `no` — **Phases 4 and 5 gate on it**, and the gate tests for `yes` so an unset value refuses. The classification below is what sets it |
| `$ECOSYSTEM` | `uv.lock`, `github-actions`, `pre-commit`, `unsupported`, `unknown` or `underivable` — **which Phase 1, 3, 4 and 5 method applies**, derived from the files the bump changed rather than inferred from the PR |
| `$SCOPE_GATE` | `clean`, `beyond` or `underivable` — **Phase 1's gate, already decided** from the bot's own commits. That is the invariant the gate is about, and it is why `$BOT_COMMITS` does not cross: the loop that consumed it is now the script's |
| `$HUMAN_COMMITS` | every non-bot commit on the branch, merges included. Its files are a **finding to report**, never a Hold |

**`$SCRIPTS` is on that list because `${CLAUDE_PLUGIN_ROOT}` reaches only this
file.** The token is substituted into `SKILL.md`'s *text* at skill load — which
is why the `D=` line above resolves, and why the variable itself measures empty
in every shell (`ROOT=[]`, marketplace install and `--plugin-dir` alike). A
reference file is never injected: the model reads it off disk, so the token
arrives at the shell intact and the path collapses to
`/skills/dependabot-audit/scripts/…`. Two lines of `references/uv-lock.md`
shipped that way from 0.15.0 until the first `uv.lock` replay ran them.

So the bootstrap happens **once, here**, and everything downstream derives from
it. `discover.py` reports its own directory, and a path taken from the file that
just ran cannot name a different copy than the one running — which is also the
answer to the stale-cache hazard, where an invented
`export CLAUDE_PLUGIN_ROOT=…/0.22.1` pins a release into a cache that keeps every
older version and then audits with it, silently and successfully.

**`$PERMS` is not on that list, and the distinction is the point.** It is read off
`discover.py`'s report *here in Phase 0*, where the execution gate and the
actionability question both use it. It is **not** written to `phase0.env`: the
shell handoff carries `MAY_EXECUTE`, which is the decision `$PERMS` was consulted
to make. So a later phase that sources the handoff and reads `$PERMS` gets the
empty string, and the table above is the list that crosses — anything else is
Phase 0's own working state.

The reason is that `$PERMS` is a set of flags rather than a value: `$PERMS.push`
is how the gate below addresses it, and there is no shell form of that. Reducing
it to the one bit later phases actually branch on is what `MAY_EXECUTE` is.

**And they branch on it, rather than being trusted to remember this table.** The
blocks in Phases 4 and 5 that run the audited repo's code open with

    [ "${MAY_EXECUTE:-}" = yes ] || { echo "not authorised" >&2; exit 2; }

quoted here as an illustration — the runnable copies live in
`references/uv-lock.md` § Phase 4 and § Phase 5, which is where they are read
from.

Tested **for** `yes`, never against `no`, and the difference is the whole guard:
a block whose handoff did not load sees the empty string, and `!= no` is true of
it. The one direction this must never fail in is open.

**Diagnostics — emitted, deliberately unread:** `BASE_REF`. It is Phase 0's own
cross-check on the `compare` call and no later phase consumes it. Every *other*
name the emitter writes is read by a block; that is the rule, and an exemption is
a decision written down here rather than a name nobody happened to use.

If a later phase needs something not on this list, it belongs here rather than
there. A phase that consumes what a later phase creates cannot be run in order,
and that has now shipped twice — `tests/test_skill_prose.py` is what stops the
third.

**An output that could not be derived is not an output.** Every row above has
*three* states, not two: **derived**; **absent**, which is often a finding in its
own right; and **underivable**, where the call failed or its precondition did not
hold. `discover.py` tags each one and leaves an underivable output *unset* rather
than emitting a plausible value.

The third state is the dangerous one because the ways it happens do not raise —
they produce a real-looking answer that travels downstream as fact, and the
report then says something false with full confidence. A phase handed an
underivable input says so in its evidence row instead of proceeding on the value,
and Phase 7 does not print a row whose input was never established. "Could not
check" is a legitimate thing for this procedure to report. "Checked, found
nothing" when you could not check is not.

**Classify the PR before trusting it enough to run it.** Dependabot and Renovate
push their branches *into* the repository, so a dependency bump arriving from a
fork did not come from the bot:

| Observation | Meaning |
|---|---|
| `isCrossRepository: false`, author `dependabot[bot]` or `renovate[bot]`, `push: true` | the ordinary case |
| `isCrossRepository: true` | a fork PR — neither bot opens one |
| any other author | a human PR shaped like a bump, which it may well be, and may not |
| **`$PERMS.push` false** | **not a repository you control.** You cannot merge this PR, so nothing is gained by letting it run on your machine |

Any of the last three is a **finding** in its own right, and each changes the
default: run `--no-execute`, report what the read-only phases found, and let the
user decide whether to authorise Phases 4 and 5. Say plainly that those phases
would run the PR's code.

The `push` row is the one easiest to argue away, so name the asymmetry it rests
on. A bot PR on a repo you control proposes code you were going to run anyway,
under gates you already trust — your own CI would run it too. A PR on a repo you
cannot merge into proposes code you had no plan to run, and the comparison to CI
stops holding: CI runs it in a fresh container with a scoped token, and this
procedure runs it on your workstation with your credentials in the environment.
`$PERMS` is already derived above, so this costs nothing to check.

**`$PERMS` has the same three states, and the script gates on the call rather
than the value.** A failed `repos/:owner/:repo` writes its error body to
**stdout**, so a capture succeeds and holds `{"message": "Not Found", ...}` — at
which point `push` is not `true` and reads exactly like a `pull`-only account.
The exit code is 1, which is what separates this from the branch-protection trap
below where the same shape arrives at exit 0. Failing closed is right; the report
saying "you lack `push` here" when the audit could not tell is not, and
`discover.py` prints `underivable` rather than a permission set.

**Pin the head SHA here and audit that one commit everywhere.** The lockfile
Phase 1 reads, the worktree Phase 5 reproduces in, and the CI run Phase 6 checks
must all describe the *same* commit, or the report's evidence table asserts a coherence it
does not have. Bots rebase, so this is not hypothetical: a rebase mid-audit leaves
Phases 1–5 describing a commit that no longer exists while Phase 6 reports on the
new one. Fetching once and working from `pr-<N>` makes them consistent by
construction, and Phase 7 re-checks the SHA before you write.

**Never audit the working tree.** Whatever branch the user happens to have checked
out is not the PR, and a lockfile read from it is indistinguishable from one read
from the PR — it just quietly reports no changes. That holds for the repo's
**config** as much as its content: the gate list and the bot config are read
above at a ref for exactly this reason, and they were the last two reads here that
were not.

**`SCRATCH` has two requirements, and only one of them is about where it is.**
Never place it inside the repo under audit — it pollutes `git status`, and a gate
that walks the tree (a linter, a formatter, a test collector) will descend into a
full second copy of the project and report on it. And it must resolve to the
**same directory on every later call**, because `$SCRATCH/phase0.env` is written
by one call and sourced by another, and both worktrees are addressed the same way.

`SCRATCH=${SCRATCH:-$(mktemp -d)}` satisfied the first and failed the second, and
that is why the line above derives the name instead. Measured against this
harness, two separate calls: an `export` in the first is **unset** in the second,
shell functions likewise, and each call is a new shell process. So `${SCRATCH:-…}`
found `SCRATCH` unset every time, `mktemp -d` returned a new directory, and the
next call sourced a `phase0.env` that was not there — leaving `$BASE_SHA`,
`$HEAD_SHA` and `$DEFAULT` silently empty downstream rather than erroring.

**The working directory does survive, and is still not a way out.** It carried
between calls when it stayed inside the project; a call that ends outside has its
cwd reset back. `SCRATCH` is required to be outside the repo, so it can never be
reached that way. Nothing ambient crosses the boundary — only a path each call
can recompute from what it already has, which is the repo and `<N>`.

**Recomputable is not recomputed, and that distinction shipped as a defect.**
Deriving `$SCRATCH` made the handoff *findable* from a later call; it did not make
any later call go and find it. So every block below that consumes a Phase 0 output
opens with the same three lines, and they are not decoration:

```bash
# Fresh call: nothing survives one, so re-derive $SCRATCH and re-source Phase 0.
REPO=$(gh repo view --json nameWithOwner --jq .nameWithOwner); SCRATCH="${SCRATCH:-${TMPDIR:-/tmp}/dbaudit-${REPO/\//-}-<N>}"
. "$SCRATCH/phase0.env" || { echo "no handoff in $SCRATCH — re-run Phase 0" >&2; exit 2; }
```

Repeated rather than stated once, because a step that is merely implied is one
that gets skipped — the same argument that put the gate list at a ref. The `||`
is the load-bearing half: a `.` on a missing file returns 1 and keeps going, so
without it the block runs on with every output empty, which is the state this
whole phase exists to make impossible.

**What that empties, measured**, running each consuming block in a fresh call with
nothing sourced: Phase 1's lockfile read, Phase 4, Phase 6 and Phase 7's cleanup
all fail loudly — a `Permission denied`, two exit 2s, an exit 128 — and Phase 1's
**authorship gate passed silently**, because `for c in $BOT_COMMITS` over an unset
variable iterates zero times and handed the gate an empty file list. The gate is
`discover.py`'s from 0.29.0 and no longer reads that variable, but the class is
unchanged and the handling belongs in the shell rather than only in the paragraph
that describes it: an unset `$SCOPE_GATE` compares equal to nothing, so Phase 1's
`[ "$SCOPE_GATE" = clean ]` fails **closed** into a stop rather than open into a
pass.

`git` state is the exception and needs no reload. The `pr-<N>` ref Phase 0 fetches
is in the repository, so `git show "pr-<N>:…"` works from any call. Only the shell
handoff is lost.

A harness-provided `SCRATCH` still wins, and now for a reason: if one is exported
into every call's environment it is stable by definition. The derived default is
what applies when it is not.

Re-running the same audit reuses the directory rather than littering a new one,
so Phase 7's cleanup is addressable from any call — but a stale worktree from an
interrupted run is then in the way, which is a thing to remove rather than to
work around.

**Why the base comes from `compare` and not from `git merge-base`.** Once a PR
has landed its head *is* an ancestor of the default branch, so the merge base of
the two is the head — and auditing a merged PR is a supported thing to do here:
Phase 6 has a row for it, `references/actions.md` has a paragraph, and every
replay this project's own gate asks for is one. Measured on `cli/cli`'s merged
bumps #14147, #14091, #13981 and #14049: `git merge-base trunk pr-<N>` returns
the PR's own head for all four, so the scope diff is **0 files** where GitHub
reports 4, 2, 3 and 2. GitHub's `compare` endpoint returns the real branch point
in both states, which is why the script uses it and why no phase runs a local
merge base at all.

**Prove the merge base is where the bot branched.** A merge base always exists,
and when the base branch has been rewritten under an open PR it is far too old —
silently, with every later phase consuming it as fact. `discover.py` decides this
and prints which case fired; what matters here is that the three cases are *not*
interchangeable:

| `BRANCH_POINT` | What fired | What you do |
|---|---|---|
| `ok` | no force-push, and nothing anomalous above the base | proceed |
| `rewritten` | a `base_ref_force_pushed` event — GitHub says so, with an actor and a timestamp | **substitute**, and report the rewritten base as its own finding |
| `suspect` | a non-bot one-parent commit above the base on a **bot** PR, with no force-push event | corroboration without the authority. Read the commits before deciding; do not substitute on it alone |
| `underivable` | the event list could not be read | say so; do not proceed as though it were `ok` |

The substitutions, when `rewritten` fires:

- **Phase 1** needs no substitution: its gate reads the bot's own commits, and a
  commit carries its own diff with no range to be wrong about. Where the
  authorship split is *also* underivable there is nothing safe to fall back to —
  the whole-diff range is the entire divergence — so the gate answers
  `underivable` rather than guessing.
- **Phase 4** measures in `$SCRATCH/tip-<N>` rather than `$SCRATCH/base-<N>`,
  because the tree this PR would land on is the default branch's tip.

```bash
# Fresh call: nothing survives one, so re-derive $SCRATCH and re-source Phase 0.
REPO=$(gh repo view --json nameWithOwner --jq .nameWithOwner); SCRATCH="${SCRATCH:-${TMPDIR:-/tmp}/dbaudit-${REPO/\//-}-<N>}"
. "$SCRATCH/phase0.env" || { echo "no handoff in $SCRATCH — re-run Phase 0" >&2; exit 2; }

[ "$BRANCH_POINT" = rewritten ] || { echo "base not rewritten — no tip worktree" >&2; exit 0; }
git fetch origin "$DEFAULT"
git worktree add --detach "$SCRATCH/tip-<N>" "origin/$DEFAULT"
```

Say both substitutions in the report. "The base branch was rewritten under this
PR" is a true and useful finding; "this bump reaches beyond the manifest and
lockfile" is not, and they produce the same diff.

**A two-parent head is not a moved base**, and the script will not treat it as
one. Measured on `cli/cli` #14049, whose head is *"Merge branch 'trunk' into
dependabot/…"* by a maintainer above the bot's own commit: zero force-push
events, and a correct two-file scope diff from the merge base. Read as a moved
base it would substitute the `pr-<N>^` diff — 20 files, 1,101 lines — and halt
the audit on a bump that changes four workflow lines. There, `pr-<N>^` is the
branch *tip*, not the branch point.

Observed at the other end: a two-file `Cargo.toml` / `Cargo.lock` bump whose
merge-base diff was 14 files and 3,682 deletions, appearing to delete the repo's
entire vendored `supply-chain/` tree. The base had been force-pushed eleven
minutes after the PR opened, and the merge base fell back nineteen months.

**`gh pr view --json files` is not a cross-check on any of this**, which is why
Phase 0 does not fetch it. GitHub computes the PR's file list from the merge base
too, so on the force-pushed bump above it reported the same wrong 14 files. It
agrees with the wrong answer rather than correcting it.

**`$PERMS` decides two separate things, and conflating them gets both wrong.**
The tier that can *merge* is `push`; the tier that can read branch protection is
`admin`. The common case — a maintainer with `push` but not `admin` — sits
between them, and at `pull` only the verdict becomes a recommendation the reader
cannot act on, so offer `--comment` text instead.

Do **not** try to read the required checks here at any tier. That question moved
to Phase 6, which asks it per-PR in a form readable at `pull`. The two endpoints
that look like they answer it both fail into a plausible value, in opposite
directions: `branches/<b>/protection` needs `admin` and answers a bare `404` that
is indistinguishable from an unprotected branch, while `rules/branches/<b>` reads
at any tier and reports **rulesets only**, so classic protection returns `[]` and
manufactures a false "nothing enforced". Both measured, in `CONTRIBUTING.md`.

Recalled project memory may already name landmines for this repo (Phase 8 writes
them). Treat those as leads to check, not as facts — verify before repeating.

## Phase 1 — Scope and provenance

*Requires from Phase 0: `$SCRATCH`, `$ECOSYSTEM`, `$SCOPE_GATE`, `$HUMAN_COMMITS`, `pr-<N>`.*

**Phase 0 derived this gate; this phase acts on it.** `discover.py` reads the
files **`$BOT_COMMITS`** changed — never the branch's, because a maintainer can
land the fixup the bump *requires* on the bot's own branch, and gated on the
union that produces a Hold in language that reads exactly like a bump reaching
into source. It answers in Phase 0's three states:

| `$SCOPE_GATE` | What it established | What this phase does |
|---|---|---|
| `clean` | every changed file is the manifest and the lockfile, or every changed **line** is a `uses:` pin | continue |
| `beyond` | the diff reaches past the pin — the report output names the files or the lines | a finding. Report it and **stop before Phase 4** |
| `underivable` | the gate could not be evaluated: a patch the API withheld, a file list at its cap, a lockfile from an ecosystem this plugin does not cover, or a rewritten base with no authorship split to fall back from | **not a clean scope.** Report what could not be established, and stop |

The count of files is not the invariant and never was: an action is pinned in
every workflow that uses it, and a grouped bump moves several actions at once, so
ordinary merged bumps touch two, three or four files. `references/actions.md` has
the measurements, and the rule for reading the versions out of that diff rather
than off the title.

**`underivable` is not `clean`, and the asymmetry is the point.** Every way this
gate fails quietly arrives as *no objection* rather than as an error — an unset
`$BOT_COMMITS` iterating zero times, an empty file list, a capped page hiding
file 301 — and it is the gate that refuses Phases 4 and 5 a shell. Where the
split is underivable the script falls back to the whole `$BASE_SHA..pr-<N>` diff
and prints which source it used; read that line before quoting the verdict.

```bash
# Fresh call: nothing survives one, so re-derive $SCRATCH and re-source Phase 0.
REPO=$(gh repo view --json nameWithOwner --jq .nameWithOwner); SCRATCH="${SCRATCH:-${TMPDIR:-/tmp}/dbaudit-${REPO/\//-}-<N>}"
. "$SCRATCH/phase0.env" || { echo "no handoff in $SCRATCH — re-run Phase 0" >&2; exit 2; }

echo "ecosystem: $ECOSYSTEM"
# a maintainer's commit on the bot's branch: read it, report it, do not Hold on it
HUMANS=$(for c in $HUMAN_COMMITS; do git show --name-only --format= "$c" || exit 1; done) \
  || { echo "cannot read a commit in \$HUMAN_COMMITS" >&2; exit 2; }
printf '%s\n' "$HUMANS" | sort -u
# Last, so the half above still reports. Unset compares equal to nothing, so an
# empty $SCOPE_GATE stops here rather than reading as clean.
[ "$SCOPE_GATE" = clean ] \
  || { echo "scope $SCOPE_GATE — report it, and STOP before Phase 4" >&2; exit 1; }
```

A merge commit is in that list and normally prints nothing — its content arrived
from the branch it merged. What it *does* print is what the merge itself changed,
which is the one thing worth seeing there.

A provenance discrepancy stops the audit here too. Phases 4 and 5 execute the
PR's code, and the point of running the cheap read-only checks first is that they
can refuse to hand it a shell; continuing anyway spends the ordering for nothing.
Stopping here is not a failed audit but a complete one that reached a verdict
early — write the report with the phases that ran, and say which did not.

**The method is per-ecosystem; the gate above is not.** Each reference is
sectioned by phase, so read the section for this one:

| Ecosystem | Method |
|---|---|
| `uv.lock` | `references/uv-lock.md` § Phase 1 — `scripts/audit.py` verifies every pinned artifact's hash, size, URL and yank status against the live registry, plus PEP 740 build provenance |
| GitHub Actions | `references/actions.md` § Phase 1 — no lockfile and no artifact hash, so the question becomes whether the pin is **immutable**: a 40-hex SHA, or a tag someone else can revoke. The scope gate keys on `uses:` lines, never on a count of files |
| `pre-commit` | `references/pre-commit.md` § Phase 1 — a `rev:` is a git ref on another repository: no hash either, so immutability again. `scripts/precommit.py` also resolves what the rev *installs*, which is declared in the hook repo's packaging and is not the tag. The scope gate keys on `rev:` lines |

**This plugin covers `uv.lock`, GitHub Actions and `pre-commit`, and nothing
else.** For any
other ecosystem, say so and stop. Do not improvise a procedure from the shape of
the ones that are here: an unverified verifier reports green rather than erroring,
which is why npm, Cargo and Go were removed rather than left as sketches.

That is not hypothetical. Followed faithfully against a real Cargo bump, an
improvised recipe returned matching checksums, a current latest version and a
clean OSV batch — on a PR that raised the project's minimum Rust version past its
own declared floor. Nothing in the output looked partial. A hand-run recipe also
lacks every guard the script has earned: batch limits, retries, version ordering,
and the refusal to report `CLEAN` on an empty selection.

`audit.py` enforces its half rather than leaving it to prose. Handed a
`Cargo.lock`, `poetry.lock`, `package-lock.json`, `Pipfile.lock`, `go.sum`,
`go.mod`, `yarn.lock`, `pnpm-lock.yaml` or a `pyproject.toml`, it exits **2**
naming the format. Report that as the boundary it is, not as a failed audit: the
ecosystem-independent phases still ran, so say what Phase 0's classification,
Phase 2's currency read, Phase 3's vulnerability queries and Phase 6's CI state
established, and name plainly what was not checked.

**Phases 2, 3 and 6 run for an uncovered ecosystem. Phases 1, 4 and 5 do not.**
The line is not which phases happen to be ecosystem-independent — it is what a
phase *asserts*. Phase 2 asks a registry which version is newest and Phase 3 asks
a vulnerability database what it holds; both answers are falsifiable against the
same public source the reader can open, and neither claims that an artifact is
what the registry says it is. That claim is Phase 1's alone, and it is the one
the boundary withholds. The Cargo failure above was an improvised **verifier**
reporting green about artifact integrity — not a currency read, which is why the
warning does not reach these two.

Left unsaid, this cost a run a deviation row: on a `pre-commit` bump the
enumeration above named only Phases 0 and 6, so running Phase 2 and Phase 3 had
to be defended as improvisation. Both were load-bearing — the proposed version
was the true latest of both the mirror and the tool it pins, and the advisory
databases were empty at every version, which is most of what a reader weighing a
boundary Hold has to go on.

**That particular bump is no longer on this path**: `pre-commit` became a covered
ecosystem in 0.38.0, which is what the run was arguing for (#109). The rule
survives its own example, because the next uncovered ecosystem arrives the same
way — and the reason it was worth covering rather than leaving here is exactly
the enumeration above. Phases 2 and 3 were reaching a real answer by hand every
month, on the half of this repository's own bump traffic that landed on the
boundary, and a monthly Hold nobody can act on trains the reader to discount the
gate.

## Phase 2 — Currency

*Requires from Phase 0: `$CREATED_AT`. Plus the Phase 1 script output.*

**A bot's proposal is not evidence of "current".** Ask the registry what the
latest version actually is, and compare publish timestamps against the PR's
`createdAt`. What that comparison stopped settling on 2026-07-14 is *why*:
Dependabot now holds a version update until the release is **three days old**, by
default, with no `cooldown:` block required and nothing in the PR to show it. So
read the *age* of the gap and not only its existence — inside that window the bot
is waiting, outside it the bot is behind.

**For GitHub Actions "current" is a question about the tag line, not the pin** —
a moving major tag picks up new releases on its own, so a newer patch is not a
gap. `references/actions.md` § Phase 2 has the `compare` that separates a tag
that merely moved *ahead* from one that rolled **behind**, which is the case a
bot cannot fix because it cannot propose a downgrade.

**For `pre-commit` it is two questions, and the tag answers neither.** The
`rev:` is a ref on the hook repository; the version that gets installed is
declared inside that repository, and `scripts/precommit.py` has already resolved
it. Ask the registry about **that**, never about the rev — `v0.16.5` is not a
PyPI version of anything, so a query built from the tag returns an empty result
that reads exactly like *current and clean*. `references/pre-commit.md` § Phase 2
also says which registries this leaves covered: a `language: python` hook is
PyPI and verified end to end, and anything else is the boundary again.

Rule out the innocent explanations before reporting a gap: a yanked release; a
**cooldown** (`cooldown:` in `dependabot.yml`, `minimumReleaseAge` in
`renovate.json`), which now applies even when the file says nothing; or an
`ignore` rule, which can name `"*"` and be scoped by `update-types`, so "no rule
names this dependency" is not "no rule covers it".

**A gap inside the cooldown window does not earn a follow-up branch.**
Recommending one hand-lands the release the bot is deliberately waiting on, which
inverts the control rather than clearing it. What outranks the hold is what this
phase reads for next: a `Security` entry or a destructive-fix bug in the gap. The
cooldown exempts Dependabot's *security updates* — the advisory-driven kind — and
not a version update whose changelog happens to carry a privately disclosed fix,
which is exactly the case below.

**The cooldown boundary is a subtraction, so do it rather than eyeball it.** The
window is measured from when the **PR opened**, not from now, so the same gap
moves in and out of it as the audit ages — which is the failure Phase 7's table
calls out in itself:

```bash
# Fresh call: nothing survives one, so re-derive $SCRATCH and re-source Phase 0.
REPO=$(gh repo view --json nameWithOwner --jq .nameWithOwner); SCRATCH="${SCRATCH:-${TMPDIR:-/tmp}/dbaudit-${REPO/\//-}-<N>}"
. "$SCRATCH/phase0.env" || { echo "no handoff in $SCRATCH — re-run Phase 0" >&2; exit 2; }

python3 -c 'import datetime,sys; t=datetime.datetime.fromisoformat(sys.argv[1].replace("Z","+00:00")); print("cooldown boundary:", (t-datetime.timedelta(days=3)).isoformat())' "$CREATED_AT"
```

A gap release published **after** that boundary was inside the window when the
bot decided; one published before it was not, and the bot is behind rather than
waiting. `python3` rather than `date -d`, which is GNU-only — every script here
already requires 3.11.

**A bot's ignore state is not always in a config file.** `@dependabot ignore this
major version` records the hold in the *PR*, not the repo, so a dependency can be
pinned indefinitely with nothing in `dependabot.yml` to show it. The evidence is a
closed bot PR carrying a comment like "OK, I won't notify you again about this
release". When a gap looks unexplained, list closed bot PRs for the same
dependency before reporting it as lag:

```bash
gh pr list --state closed --author "app/dependabot" --search "<dependency>" \
  --json number,title,closedAt
```

Then **read the changelog for every version in the gap**, plus the versions being
adopted. Look for two things, in this order:

- **`Security` sections.** These outrank every vulnerability database. A privately
  disclosed fix ships with no CVE, and scanners will report clean.
- **Destructive-fix bugs.** Entries like "stop deleting…" or "no longer removes…"
  in a tool the repo runs in **write mode** (`--fix`, `--write`, `-i`) are
  data-loss bugs in a mode that runs automatically. They never appear in a
  security feed.

**Then ask whether this repo is in the change's scope**, for either kind. Phase 7
takes the verdict from that answer, so it is a finding and not a footnote: read
the advisory or the bug for the setting, flag or mode it lives in, and grep this
repo's config for it. A `Security` entry whose leak path the repo never
configures is a follow-up on the merits; a destructive fix in a write mode the
repo runs on every commit is not. Same shape of evidence, two urgencies — it is
the same question Phase 4 asks of an actions bump, where `references/actions.md`
calls the answer "inert here", a result and not silence.

**The grep answers it only when the change is in the tool's own surface**, and
three cases fall outside that. All three are ordinary, and all three return a
confident `inert here` that was never established:

| The entry names | Why the config cannot answer it | What does |
|---|---|---|
| a **dependency** rather than a rule or a flag | it is not in this repo's config, and for a compiled wheel it is not even in this repo's *ecosystem* — a Rust crate inside a Python package, where the advisory lives on crates.io and every PyPI-side scanner is correctly clean | `references/uv-lock.md` § Phase 2 — read the shipped set out of the wheel's own SBOM. `references/actions.md` § Phase 2 for the tag-line question |
| a rule this repo **disables** | the claim is then about the config *file*, and the verdict is about the *tool*. Config is interpreted: another file can win, a key can be spelled for a different version, a section can go unread | run the gate twice, once with the config and once without, and read the difference |
| a **file type** or a **document shape** rather than a setting | there is no config key to grep for. `stop rewriting Rust source when formatting doc comments` is about `.rs` files, and `stop reading a lazy continuation as a setext underline` is about a blockquote followed by a setext underline — neither is a line any config could carry, and "no config line matches" reads as `inert here` | grep the **content** of the tree instead, below |

**The third row is the one with no command in the table**, because its commands
contain a pipe and a table cell cannot carry one — an escaped `\|` renders
correctly and reaches *this* reader, who reads the raw file, as a backslash that
breaks the regex:

```bash
git grep -lE '^(=+|-{2,})[ \t]*$' -- '*.md'; echo "shape scan exit: $?"
git ls-files '*.rs';                         echo "type scan exit: $?"
```

**Read the exit code, and do not pipe these into `wc`.** `git grep` exits `1` on
no match and `128` when it could not run, and both print nothing — so
`git grep … | wc -l` reports `0` at exit 0 either way, turning "could not run"
into `inert here`, which is the failure this whole row exists to prevent. `1` is
a real zero; `128` is `underivable`.

Exposure is how many files carry the shape, and **zero is a finding like any
other** — the same `inert here` the first two rows earn by running something,
rather than by finding nothing to grep. Both commands come from a run that
improvised them unaided, because the phase said "grep this repo's config" and no
config line could answer.

That second row is Phase 6's rule one phase over. A red check does not carry a
verdict until it is attributed; a config line does not carry `inert` until the
tool has been run both ways. Measured on `rumdl` 0.2.59's destructive `MD013`
fix, against a repo that runs `rumdl check --fix` on every Markdown commit:
`rumdl check README.md` is clean, `rumdl check --no-config README.md` finds 32.
The suppression is real — and one command is the difference between reporting
that and asserting it.

## Phase 3 — Known vulnerabilities

*Requires: the Phase 1 output for this ecosystem.*
*Runs under `--no-execute`, so nothing here may execute the PR's code — and for
`uv.lock` that is a property of the auditor's flags, not of the phase.*

**The question: what does the world already know is wrong with this?** Expect it
to agree with Phase 2 only sometimes — that divergence is the point, not a
contradiction. The method differs by ecosystem; the question does not.

| Ecosystem | Method |
|---|---|
| `pre-commit` | `references/pre-commit.md` § Phase 3 — query the **package the hook installs**, never the `rev:`. `v0.16.5` is not a PyPI version of anything, so a query built from the tag comes back empty and reads exactly like *no known vulnerabilities* |
| `uv.lock` | `references/uv-lock.md` § Phase 3 — the OSV batch is **already done** by the Phase 1 script, so read that result rather than re-querying; what remains is the ecosystem's own auditor — and the obvious invocation of it **executes the PR's code**, in a phase `--no-execute` runs |
| GitHub Actions | `references/actions.md` § Phase 3 — GHSA carries an `actions` ecosystem, and the obvious port of the `uv.lock` query reports **clean on a known-compromised action** |

That second row is why this phase has a guard in the test suite. *"Not
applicable" is an assertion too*, and it shipped false: three places in this repo
once stated that GitHub Actions has no vulnerability database. A phase that
believed it skipped a real check — measured against `tj-actions/changed-files`,
where a package-only query returns two advisories and every version-qualified
form returns zero.

## Phase 4 — Behavior change (the highest-yield phase)

*Requires from Phase 0: the `$SCRATCH/base-<N>` worktree — or `$SCRATCH/tip-<N>`
if Phase 0 found the base rewritten — and the repo's own gates.*
*Executes code from the PR. Requires `MAY_EXECUTE=yes`. Skipped under
`--no-execute`; skip it if Phase 1 found anything.*

**The question: does this change what runs here, or what this repo's gates
accept?** Not "is it safe". For `uv.lock` you can measure it, and you must —
predicting it from the changelog is what this phase exists to replace. For
GitHub Actions you cannot run the thing at all, so the method is different and
the section for it is below.

| Ecosystem | Method |
|---|---|
| `pre-commit` | `references/pre-commit.md` § Phase 4 — **the hook definition is where the behaviour lives.** `scripts/precommit.py` diffs `.pre-commit-hooks.yaml` between the two revs field by field; then measure the blast radius in this repo, because the hook says what it now *selects* and not how much of this tree that is |
| `uv.lock` | `references/uv-lock.md` § Phase 4 — **measure it.** `scripts/gate_diff.py` runs each gate at the locked, proposed and latest versions in `$SCRATCH/base-<N>` and compares what each run *did to the files* |
| GitHub Actions | `references/actions.md` § Phase 4 — an action cannot be run locally at two versions, so the method is reading the release notes **and then establishing whether this repo's workflows are in the change's scope at all** |

**Measure on the merge base, not on the PR's tree.** This is the difference
between finding the change and missing it, and the wrong choice fails silently: a
PR that already contains the fixup — someone reformatted to make CI pass — has a
tree the new version is already happy with, so measuring there reports no
difference. And that is exactly the case where the change was real enough that a
human had to deal with it. Observed on a real `ruff 0.15.22 -> 0.16.0` bump: six
Markdown files on the base, nothing on the PR's tree.

**Do not read the exit codes as the answer.** Both versions can exit 0 while the
scope moves underneath them — the founding observation of this phase, measured on
ruff 0.15.22 → 0.16.0 in a repo already compliant with both: identical exit 0,
while the newer version formatted **33 more files**, having started formatting
Python fences inside Markdown. Nothing in the pass/fail answer moved. Diffing the
two versions' *output* does not rescue it either, because a bump changes output
format about as often as behavior — which is why `gate_diff.py` compares what
each run did to the files.

**A gate that rewrites files the repo excludes has defeated the exclusion, and
that is a finding about the exclusion as much as about the tool.** Where the repo
configures an exclusion covering files the bump newly reaches, and they were
rewritten anyway, establish *why* before reporting the cause. Two check-only runs
settle it: once as the gate invokes the tool, then again forced to the
repo-root manifest with the tool's own `--config`-style flag. If only the second
excludes the file, the root config is being shadowed — a manifest nested inside
the excluded directory is the nearest one for files beneath it, so the root's
exclusion is never consulted. Measured on a `ruff-pre-commit` v0.16.2 → v0.16.5
bump, where `extend-exclude` was believed to protect six Markdown fixtures and
never had: the nearest config reformatted them, the root config reported no files
at all. Reported without that, the reader goes looking for a wrong exclusion
pattern, and the remedy is in a different file from the one they open.

**The contradiction can surface outside this phase.** An uncovered ecosystem
skips Phase 4 entirely, and the same observation — a gate rewrote files the
config declares out of scope — then arrives in a CI log instead. The
reconciliation is owed there too. It is two read-only commands and it runs
nothing from the PR, so `--no-execute` is not a reason to skip it.

**"Inert here" is a result, not silence.** Reaching it deliberately is this phase
working; reaching it by not looking is the failure.

## Phase 5 — Independent reproduction

*Requires from Phase 0: the `$SCRATCH/pr-<N>` worktree, `$HEAD_SHA`, `pr-<N>`.*
*Executes code from the PR — the most of any phase. Requires `MAY_EXECUTE=yes`.
Skipped under `--no-execute`; skip it if Phase 1 found anything.*

**The question: has this been shown to work, independently of the bot saying so?**
For `uv.lock` you answer it by building and running the thing. For GitHub Actions
no local reproduction exists at all, so the answer has to come from somewhere
else — and "no reproduction available" is a result to report, not a phase to skip.

| Ecosystem | Method |
|---|---|
| `uv.lock` | `references/uv-lock.md` § Phase 5 — install **frozen** and run the repo's own gates and suite in `$SCRATCH/pr-<N>` |
| `pre-commit` | `references/pre-commit.md` § Phase 5 — `pre-commit run --all-files` in `$SCRATCH/pr-<N>`, which is usually the whole of CI's lint job. **Not hermetic**: it installs each hook's environment from the network at run time, so say what that green is worth beside a frozen install |
| GitHub Actions | `references/actions.md` § Phase 5 — nothing to install and no way to run an action off GitHub's runners, so the substitute is **evidence that this pin has already run**: the history of the workflow the bump changed |

Phase 0 built the worktree and proved it points at `$HEAD_SHA`, so the user's
working tree is untouched throughout. That isolation protects the *working tree*,
not the machine — see the execution section at the top.

**"No reproduction available" is a result to report, not a phase to skip.** For an
actions bump on an open PR whose workflow is not PR-triggered, reproduction is
impossible before merge; that is a property of the change and belongs in the
report.

**Say what the row actually covered.** A green reproduction is true of *one*
configuration and reads as true of every one, so name which install ran, which
interpreter produced it, which dependency groups it covered, and anything
verified but not installed. "Frozen install passed under `--no-build
--no-install-project` on CPython 3.14, `dev` group included; the 3.11 fork of
`rpds-py` was verified but not installed" is a stronger row than "frozen install
passed", because it is one a reader can falsify. A bump into a group the sync
does not install is absent from the environment with nothing to show for it —
the reference has the measurement and the reconciliation that catches it.
`uv run python -V` from inside the synced environment is where the interpreter
comes from — not the auditor's own `python3` — and `resolution-markers` is why
the two can differ.

Gate on exit codes. `cmd | tail && next` gates on `tail`, so a failing suite sails
through; use `set -o pipefail` or separate calls.

The worktrees and the `pr-<N>` branch are cleaned up in Phase 7, not here — an
audit that stops at Phase 1's gate never reaches this phase and still has to
tidy up after itself.

**Expect the gates to leave this tree dirty, and do not tidy it yourself.** Where
the repo's gates are fix-mode — `ruff format`, `rumdl check --fix`, a `pre-commit`
run that stages what it fixes — they rewrite tracked files here, and that is a
*result*: the repo's own gates changed this tree at the proposed version, which is
Phase 4's question reached from the other direction. Nothing restores `pr-<N>` the
way `gate_diff.py` restores `base-<N>`, and that is deliberate. Phase 7 saves the
diff and reports it; a `git checkout .` here destroys the finding before anyone
sees it.

## Phase 6 — CI verification

*Requires from Phase 0: `$HEAD_SHA`, `$BASE_SHA`, `$OWNER`, `$NAME`.*

Confirm the green you are trusting belongs to **this** commit, **and that it
exercised the change**. Those are two questions, and an actions bump routinely
passes the first while failing the second. A third follows whenever something is
red: whether the bump is why.

**Check that the changed file is reachable from a pull request.** A workflow
triggered only by `push: tags:` or `schedule:` never runs on a PR, so every check
on it comes from *other* workflows and none of them execute the changed line:

```bash
# Fresh call: nothing survives one, so re-derive $SCRATCH and re-source Phase 0.
REPO=$(gh repo view --json nameWithOwner --jq .nameWithOwner); SCRATCH="${SCRATCH:-${TMPDIR:-/tmp}/dbaudit-${REPO/\//-}-<N>}"
. "$SCRATCH/phase0.env" || { echo "no handoff in $SCRATCH — re-run Phase 0" >&2; exit 2; }

# `<workflow>` is Phase 0's derived list, narrowed to what this PR's diff
# touched — the same derivation, the same token, one filter added.
git diff --name-only "$BASE_SHA...pr-<N>" -- '.github/workflows/'
echo "changed-workflow list exit: $?"

# Then, for each name it gave, read its triggers. Captured, not piped:
# `sed` succeeds on empty input, so a failed read prints nothing at exit 0 and
# reads as "this workflow has no pull_request trigger" — the reassuring answer.
TRIGGERS=$(git show "pr-<N>:<workflow>") \
  || { echo "cannot read <workflow> at pr-<N>" >&2; exit 2; }
printf '%s\n' "$TRIGGERS" | sed -n '/^on:/,/^[a-z]/p'
```

If the intersection of "workflows the diff touched" and "workflows a
`pull_request` can trigger" is **empty**, say so plainly: CI is green and it is
green for reasons unrelated to this diff. Then fall back to Phase 5's run-history
substitute. Observed: a PR changing only `release.yml`, which triggers on
`push: tags: [<prefix>-*]`, carried three green checks — all of them from the
repo's separate test workflow.

**Run the script; it is this phase's three questions in one call.** Every query
below used to be issued by hand, and three of the seven defects that have shipped
in this file were here — each of them a real endpoint asked the wrong question,
answering in a well-formed way. A hand-run query cannot be regression-tested.

```bash
# Fresh call: nothing survives one, so re-derive $SCRATCH and re-source Phase 0.
REPO=$(gh repo view --json nameWithOwner --jq .nameWithOwner); SCRATCH="${SCRATCH:-${TMPDIR:-/tmp}/dbaudit-${REPO/\//-}-<N>}"
. "$SCRATCH/phase0.env" || { echo "no handoff in $SCRATCH — re-run Phase 0" >&2; exit 2; }

C="${SCRIPTS:?not in the handoff — re-run Phase 0}/ci_state.py"
PARENT=$(git rev-parse "pr-<N>^") \
  || { echo "cannot resolve pr-<N>^ — was the ref fetched?" >&2; exit 2; }

python3 "$C" --owner "$OWNER" --name "$NAME" --number <N> \
  --head-sha "$HEAD_SHA" --parent "$PARENT" --base-sha "$BASE_SHA"
```

**Exit 2 means it could not run; exit 1 means it ran and found something.** Never
read one as the other.

It asks GitHub **which checks are required** rather than deriving a list and
joining it by hand — `isRequired` is a field on the rollup contexts, evaluated for
this PR against whatever enforces it (classic protection, a ruleset, a path-scoped
rule) and readable at `pull`. The join happens server-side, so there is no list to
retype and no `awk` matching to get wrong. It pages `contexts` to exhaustion, reads
`mergeStateStatus` alongside the rollup, compares against `pr-<N>^`, and labels
every red context. What it will not do is decide the verdict; that is Phase 7's
table, and putting it in both places is how the two drift.

Read **three** fields out of its output, and never substitute one for another:

| Field | What it settles |
|---|---|
| `isRequired`, per context | which checks gate merge — a repo can report far more than it requires |
| `statusCheckRollup.state` | whether the checks that *reported* passed |
| `mergeStateStatus` | whether anything still blocks, **including what never reported** |

**A green rollup is not a mergeable PR.** Verified on a real PR: 39 contexts, 3
required, every one `SUCCESS`, rollup `SUCCESS` — and `mergeStateStatus: BLOCKED`
with `reviewDecision: REVIEW_REQUIRED`. A procedure that stops at the required
contexts reports all-green and recommends a merge GitHub will refuse. The old
recipe could not see this at any permission tier.

**`isRequired` only sees contexts that reported.** A required check that never ran
is absent from the list entirely — the failure the hand-written join existed to
catch. `mergeStateStatus` covers it, because an unsatisfied required check yields
`BLOCKED` and never `CLEAN`. Two traps travel with it: it is `UNKNOWN` on a merged
PR, and GitHub computes it lazily, so an open PR may need the query re-issued
before it settles. `UNKNOWN` is underivable, not "nothing blocks" — the script
says so rather than leaving it to be remembered.

**Zero required contexts is a finding only when `mergeStateStatus` agrees**, and
the script reads them together:

| Required contexts | `mergeStateStatus` | Reading |
|---|---|---|
| none | not `BLOCKED` | the repo enforces nothing — real, and it changes what a green run is worth |
| none | `BLOCKED` | something gates this PR that you cannot see. **Underivable**, per Phase 0 — do not report it as "no enforced checks" |
| some | `BLOCKED` | read `reviewDecision` and the unsettled contexts; the checks alone do not explain it |

**The context list can be truncated, and the script refuses to hide it.**
`contexts(first:100)` is a page: a repo reporting more returns the first hundred
and says nothing about the rest, so a required check at position 101 is absent —
indistinguishable from one that passed, and the same failure as the hand-written
join one level up. It pages on `pageInfo`, and where it cannot it reports the
required set as **underivable** rather than complete:

| `totalCount` vs. what was held | What the required set is |
|---|---|
| equal | complete |
| greater, paged to exhaustion | complete |
| greater, could not be paged | **underivable**, per Phase 0 — not "these are all of them" |

**A red check is not evidence that the bump caused it.** Phase 6 reports check
conclusions, and a failing required context is the row most likely to carry the
verdict — so it is the one that must not assert more than it established. "This
check is red" is established. "This bump broke it" is a *causal* claim, and
nothing above tests it.

The script asks whether it was already red **on the commit the bot branched
from** — the parent of the bot's own commit, not the merge base — and labels the
row in three states, never two:

| At `pr-<N>^` | Label | What it means for the verdict |
|---|---|---|
| the same check is green | **attributable** | the bump is *implicated*, not convicted. Read the interval the comparison spans, and the failing step's log at **both commits**, before this row carries a Hold |
| the same check is red | **pre-existing** | the tree the bump landed on was already red. A real finding, a *different* one, and it must not produce a Hold on this bump |
| **no run at the base**, or no check by that name | **underivable**, per Phase 0 | say so rather than defaulting to attributable |

**The three labels are not equally strong evidence, and the interval is why.**
`pre-existing` survives any gap between the two commits: if the check was already
red, the bump is exonerated regardless of what else moved in between.
`attributable` does not. Green-then-red across days is consistent with the bump,
with an upstream change, with a runner image roll, or with a flake — and this
comparison distinguishes none of them. The script prints the span for that
reason, and prints `interval underivable` rather than nothing when it cannot, so
a missing interval never reads as a tight one:

```
RED  Board-data drift  FAILURE  [CheckRun]
     ATTRIBUTABLE — green at 3a5b0b4ed (pr-<N>^), 3d 17h earlier
```

Measured on `actions/checkout` 7.0.0 → 7.0.1. Every cell true; the causal reading
false. The failing job re-syncs generated sources from **other people's
repositories** and requires a zero diff, so its inputs are outside this repo
entirely — an upstream ref had moved, and 7.0.1 is three argument-handling fixes.
No Hold fired only because that check is not required; had it been, this table
would have Held a security backport released across six majors inside 34 minutes.
The guard was the audited repo's configuration, not this procedure.

That is the pre-existing argument pointed the other way. A Hold on an unread
attributable row is unfalsifiable in exactly the same manner — an evidence table
saying a check is red, which is true, while implying a cause it never
established — and it is the direction that costs least to be wrong in, so nobody
goes back and checks.

**`pr-<N>^` and `$BASE_SHA` are the same commit for a genuine one-commit bot PR**,
which is the ordinary case — so preferring the parent costs nothing there and is
right when they differ. When they differ, `$BASE_SHA` attributes to the bump
everything that happened on the branch beneath it, which is the same mistake as
diffing scope against a rewritten base and gets the same substitution (#19).

Measured on `BIRSAx2/mdcat` #6, the PR this section comes from. Its branch
carries a *human* commit under the bot's, so the four candidate comparison
points disagree:

| Commit | `test (ubuntu-latest)` |
|---|---|
| the bot's commit — the PR head | `failure` |
| `pr-<N>^`, the human commit below it | `failure` — **pre-existing**, and the answer |
| `git merge-base` | the check does not exist there at all |
| the base branch's tip | `success` — which would have said **attributable** |

Two of those four produce the false Hold this section exists to prevent, and one
of them is the merge base.

**When `pr-<N>^` has no runs at all the script falls back to `$BASE_SHA` and marks
the claim weaker.** An intermediate commit of a multi-commit branch is often never
built — CI ran on the head and nowhere else — so the parent has nothing to compare
against while the merge base, being on the default branch, does. That fallback
answers a *different* question:

| Compared against | What a red result establishes |
|---|---|
| `pr-<N>^` | it was red **before this commit** — attribution to the bump |
| `$BASE_SHA` | it was red **before this branch** — everything below the bump is inside the claim |

Reaching for the second is legitimate and better than reporting nothing; passing
it off as the first is the failure, so carry the weakened wording into the report
rather than dropping it. Observed on this plugin's own PR #26: `pr-26^` is an
intermediate commit of the branch and carries zero check runs.

**The underivable row has two more causes and they look identical.** The commit
may predate the workflow or its run may have aged out — or the check may simply be
*named* something else there. Names drift: `mdcat`'s `main` now reports `test` and
`test-windows` where the PR reports `test (ubuntu-latest)`, so a name match
against a distant commit finds nothing and reads as "never ran". The script prints
the whole name list at the comparison point for exactly this reason; read it
rather than the one name you are chasing.

**A red check on a workflow the diff never touched is a strong prior for
pre-existing**, and Phase 6 already derives which workflows the diff touched for
the PR-reachability check above. Share that input rather than deriving it twice.

Matching on name and conclusion establishes that the check was *already
failing*, not that it is failing for the same reason. Where the distinction
decides the verdict, read the failing step's log at both commits.

Observed on `BIRSAx2/mdcat` #6: `test (ubuntu-latest)` red beside two green
siblings, which reads exactly like a dependency bump breaking one platform. The
failure was `unresolved link to pulldown-cmark-mdcat` — a rustdoc intra-doc-link
error under `#[deny(warnings)]`, failing identically on the base commit and
having nothing to do with the dependency. A Hold driven by that row would have
been **correct by accident and unfalsifiable in the report**: every cell in it
true, the causal claim never established. That is the same family as the
rewritten base and the hand-joined required list — rows that are individually
accurate and collectively misleading.

It is also the direction that costs least to be wrong in, and therefore gets
least scrutiny: a false Hold looks conservative, so nobody goes back to check
whether the bump was the cause.

**A red check can be stale because the *base* moved, not the PR.** CI runs on
`refs/pull/<N>/merge` — the base merged with the head — so a fix landing on the
default branch invalidates every result here, with no event on the PR to
re-trigger them. The labels above cannot see it: their comparison is the head
against `pr-<N>^`, and **both of those commits are unchanged**. The row reads
`attributable`, correctly, about a merge that no longer exists.

The script detects it, and what it compares is a **timestamp, not a ref**: the
base branch's tip committed *after* a settled check started means that check
could not have contained it. It prints `THE BASE MOVED UNDER THESE RESULTS`,
names every row that predates the tip, and marks those rows procedural. Measured
on this plugin's own #99 — red `Lint & type-check` started 2026-09-01T01:14:41Z
and the fix for what it caught landed on `main` 2d 16h later — and silent on the
same PR after Dependabot rebased it, which is the half that keeps the signal
worth reading.

**Reading the merge ref instead does not work**, and it is the obvious thing to
reach for. `refs/pull/<N>/merge` and `potentialMergeCommit` are recomputed
lazily, and *querying* `mergeable` is what pokes them — so by the time either is
read it usually names the current base while the check results still do not.
Commit dates are not recomputed by being read, and the comparison needs no local
`git`, which is what lets `ci_state.py` keep it.

**Detecting it is the script's; settling it is yours.** Simulate the merge and
read the result:

```bash
# Fresh call: nothing survives one, so re-derive $SCRATCH and re-source Phase 0.
REPO=$(gh repo view --json nameWithOwner --jq .nameWithOwner); SCRATCH="${SCRATCH:-${TMPDIR:-/tmp}/dbaudit-${REPO/\//-}-<N>}"
. "$SCRATCH/phase0.env" || { echo "no handoff in $SCRATCH — re-run Phase 0" >&2; exit 2; }

# Exit 1 is a conflicting merge — a real answer, and a different finding from
# staleness. Do not read it as "could not run".
MERGED=$(git merge-tree --write-tree "origin/$DEFAULT" "pr-<N>") \
  || { echo "the merged tree conflicts — report that, not staleness" >&2; exit 1; }
git show "$MERGED:<the file the failing check named>"
```

`--write-tree` writes a tree to the object store and prints its hash: nothing is
checked out, nothing is merged, nothing from the PR is executed. **Reading out of
that tree is read-only and available under `--no-execute`. Re-running the repo's
gate against it is not** — a check-only gate still executes the bumped tool, which
is the whole of `$MAY_EXECUTE`, so extracting the tree and gating it takes Phase 5's
permission and belongs beside it in the report.

Verified on #99 while writing this: `git merge-tree --write-tree origin/main
pr-99` printed `7716296`, and reading `.pre-commit-config.yaml` out of that tree
showed the bumped `rev: v0.16.5` **and** the `exclude: ^integration/fixtures/`
that had landed on `main` underneath it — the reconciliation the red check could
not have seen, in one command that ran no gate at all.

**Say which kind of hold this is.** "Red, and the base still explains it" is a
finding about the bump. "Red, and the base has moved" is a wait for a re-run, and
the reader is owed the difference: the first names a cause, the second names a
commit and an action. Phase 7's table has a row for each, and the second is
**Hold, pending a re-run**.

**Three CI-state traps the script cannot cover**, because each is about whether
the answer is *current* rather than how to read it:

- **A merge state can read `CLEAN` on stale checks.** Right after a push the API
  can serve the *previous* commit's results. Gate on a run reporting for the
  current full head SHA, not on merge state alone.
- **A run is `success` only if every job is, and only the latest run counts.** A
  duplicate event can cancel an earlier one, and `cancelled` is not `failure`.
- **A bot's own rebase does not re-trigger CI** — push-recursion suppression on
  the bot's token. So a green you are reading may belong to the commit before the
  rebase. Close and reopen under your own auth, or ask the bot to recreate.

## Phase 7 — Report

*Requires from Phase 0: `$HEAD_SHA`.*

**Re-check the head SHA before you write anything.** Every row above describes the
commit pinned in Phase 0. If the bot rebased mid-audit, Phases 1–5 now describe a
commit that no longer exists while Phase 6 reports on the new one — and the table
silently asserts that they agree:

```bash
# Fresh call: nothing survives one, so re-derive $SCRATCH and re-source Phase 0.
REPO=$(gh repo view --json nameWithOwner --jq .nameWithOwner); SCRATCH="${SCRATCH:-${TMPDIR:-/tmp}/dbaudit-${REPO/\//-}-<N>}"
. "$SCRATCH/phase0.env" || { echo "no handoff in $SCRATCH — re-run Phase 0" >&2; exit 2; }

test "$(gh pr view <N> --json headRefOid --jq .headRefOid)" = "$HEAD_SHA"
```

If it moved, say so and re-run from Phase 1. Do not reconcile the two by hand.

Use the exact shape in `references/report-template.md`: verdict, confidence,
evidence table, reasoning, what would change the verdict, and the **un-run** merge
command. Lead with evidence; the recommendation is a conclusion drawn from it,
not a headline it decorates.

**If this audit had to improvise, the report says so.** One line, wherever it ran
a command this procedure did not specify or read a plugin file by hand instead of
invoking it — the evidence rows were then produced by a procedure working around
its own tooling, and nothing else in the report can tell the reader that. Do not
wait on Phase 8's classification: that hand-back is written for this plugin's
maintainer and comes *after* this phase, while what the reader here needs is one
sentence and the PR. On `fpga-board-sim` #363 the table read identically either
way.

**Mark each row's provenance**, and reuse only where it is legitimate. What
invalidates a row is not how old it is but what it depends on:

- **Registry and CI rows — always fresh.** A release or an advisory can land
  mid-session, CI can re-run, a required context can be added. These are one
  call each, so reuse buys nothing and risks reporting a world that moved.
- **Changelog rows — reusable.** Published release notes are immutable.
- **Reproduction rows — reusable only against an unchanged head SHA**, with the
  Phase 5 worktree check passing. State that basis in the column.

The inversion is worth internalizing: the **cheap** evidence is what must be
fresh, and a full test suite is the only kind expensive enough to be worth
reusing at all. Re-running everything is nearly always the right default.

Verdicts are one of:

- **Merge as-is** — clean and current.
- **Merge as-is, then follow up** — clean, but a newer version exists and the gap
  matters. Merge the bot's PR **exactly as written**, then take the newer version
  on a **separate branch**. Never push onto the bot's branch; that stops it
  managing the PR.
- **Hold** — a discrepancy, a regression, or a behavior change that breaks a gate.
- **Hold, pending a re-run** — the PR cannot merge and nothing about the bump is
  why: a result that describes a state which no longer exists. It is a *wait*,
  not a finding, and the two are not interchangeable — this verdict owes the
  reader the commit that invalidated the result and the action that settles it,
  where **Hold** owes a cause. Reporting one as the other is how a report Holds a
  bump on a defect somebody already fixed.

### Which evidence produces which verdict

Every row above is a finding; the verdict is a function of them, and leaving that
function implicit is how two audits with the same evidence reach different
recommendations. Read the table top-down and take the **first** row that matches.

Some rows say **not a Hold on this bump** rather than naming a verdict. Those are
*reporting* rows: they settle that a real finding does not carry the verdict, and
the read continues past them. They exist because the alternative is the reader
reaching the fall-through — *"nothing above matched"* — and a finding that lands
there by exhaustion is indistinguishable in the report from no finding at all.

| Evidence | Verdict |
|---|---|
| Phase 1's gate fired — scope, a provenance discrepancy, or `PUBLISHER CHANGED` | **Hold** |
| OSV or GHSA reports a vulnerability in a version being **adopted** | **Hold** |
| A `Security` entry or a destructive-fix bug in the gap, and the bump moves **into** it — the version being adopted is affected where the **current pin** is not | **Hold.** Merging is what increases exposure here; take the fixed version instead |
| A `Security` entry or a destructive-fix bug in the gap, **and this repo exercises the affected path** — cooldown notwithstanding | **Merge as-is, then follow up at once.** The bump is still an improvement; the urgency is the follow-up's |
| A `Security` entry or a destructive-fix bug in the gap, **inert here** — cooldown notwithstanding | **Merge as-is, then follow up** on the merits. The evidence is real and the exposure is not |
| Actions: the tag rolled **behind** the proposed SHA | **Hold.** Close the bot's PR and replace it by hand; a bot cannot express a downgrade |
| Phase 4: base differs, PR differs — the change is real and unabsorbed | **Hold** |
| Phase 5: the frozen install failed, or a repo gate failed | **Hold** |
| A red required check the script marked **stale — the base moved under it** — and the simulated merge reads clean | **Hold, pending a re-run.** Name the base commit that invalidated it, say the merge was simulated and what the tree showed, and give the reader the re-run rather than a cause. The label on the row stays right about two commits that did not change and silent about the one that did, so do not report it as **attributable** |
| A red required check the script marked **stale**, and the merged tree was **not** read | **Not a Hold on this bump** — the red is unattributed *and* may describe a merge that no longer exists, which are two separate reasons it cannot carry a verdict. Report the check, the base commit, and that the simulation was not made. If this row would decide the verdict, confidence is **low** |
| A red **required** check labelled **attributable** | **Hold** — the PR cannot merge either way. But read the failing step's log at **both commits** before the report says the bump *caused* it: green-then-red is consistent with the bump, not proof, and the wider the interval the weaker the claim |
| A red required check labelled **pre-existing** | **Not a Hold on this bump.** Report it as its own finding, take the verdict from the remaining evidence, and say the PR is unmergeable until someone fixes it |
| A red required check labelled **underivable** | **Not a Hold on this bump** — nothing established the cause, and a Hold that rests on an unattributed red row is correct only by accident. Report the red check *and* that the comparison could not be made; the PR is unmergeable until it is fixed. If this row would decide the verdict, confidence is **low** |
| Actions: the pin is a tag or branch, **not a 40-hex SHA** | **Not a Hold on this bump** — the pin was mutable before this PR and the bump did not make it so. Report that what was audited is what runs *today*, so this repo's pins are not evidence, and take the verdict from the rest |
| `BRANCH_POINT=rewritten` — the base branch was rewritten under this PR | **Not a Hold on this bump.** A fact about the branch, not the dependency. Report it, and say that Phase 4's tree came from `$SCRATCH/tip-<N>`. Phase 1's gate needs no substitution — it reads the bot's own commits — unless the authorship split was *also* underivable, where it answers `underivable` and that caps confidence |
| `BRANCH_POINT=suspect` — non-bot commits above the base, no force-push event | **Not a Hold.** Corroboration without the authority: read the commits, report what they changed, and say the base was *not* substituted on it alone |
| `BRANCH_POINT=underivable` — the event list could not be read | **Not a Hold**, and not `ok` either. Report that the merge base was never proved to be the branch point, which caps confidence at **medium** — or **low** where the scope diff is what the verdict turns on |
| Phase 4: base differs, PR agrees — real and already absorbed | **Merge as-is**, naming what the PR absorbed and how |
| `mergeStateStatus: BLOCKED` with every check green | **Merge as-is** on the bump's merits; name what blocks it, usually `reviewDecision` |
| Actions: the workflow file is generated (`DO NOT EDIT`) | **Merge as-is, then follow up** on the generator — this bump is transient without it |
| A gap exists, outside the cooldown, nothing security-shaped in it | **Merge as-is, then follow up** |
| A gap exists **inside** the cooldown window, nothing security-shaped in it | **Merge as-is.** Do *not* offer a follow-up: it hand-lands the release the control exists to delay |
| Everything derived, nothing above matched | **Merge as-is** |

**The cooldown decides Hold-versus-follow-up. It never decides whether to look.**
The wait exempts Dependabot's *security updates* — the advisory-driven kind — and
not a version update whose changelog happens to carry a privately disclosed fix,
which is exactly the evidence Phase 2 reads for. Gating those rows on the gap
being outside the window makes them unreachable on the case they were written
for, and the fall-through then says *merge, do not follow up*.

It also makes the recommendation a function of **when you ran the audit**: the
same PR, replayed once the release ages past three days, matches a different row
and gets the opposite advice on identical evidence. That is the failure this
table exists to prevent, reproduced inside the table.

**"Exercises the affected path" is a grep, and it decides which row.** Both halves
are measured, on the same dependency, three days apart:

| Observed | Exposure | Verdict |
|---|---|---|
| a `Security` entry — config `extends` values expanded from the environment, so naming the resolved path printed environment variable values into the build log — in a repo that configures the tool inline with no `extends` anywhere | inert | merge, then follow up on the merits |
| two destructive-fix bugs, "stop deleting line endings as invisible characters" and "stop deleting a line when trimming a multi-line code span", in a repo whose pre-commit config runs that tool's `--fix` write mode on every commit touching Markdown | live | merge, then follow up **at once** |

Neither had a CVE, a GHSA, or an OSV hit — `audit.py` reported no known
vulnerabilities across 37 packages, correctly, and the changelog was the only
place either existed. Both were inside the cooldown.

**Exposure sets the urgency of the follow-up, not the verdict**, and the reason
is worth being exact about, because "Hold" reads as the cautious choice here and
is not. The gap is *newer* than what the PR proposes, so the bump moves toward
the fix and never away from it: holding the second case above leaves the repo on
a version carrying **both** destructive bugs rather than one carrying neither
more of them. Its maintainer merged and followed up four minutes later, which is
what the row now says. The one configuration where Hold is right is the first
row's — the bug lives in the version being *adopted*, so merging is the thing
that increases exposure.

**When phases disagree, this is the precedence** — and they are *expected* to
disagree, which is why more than one of them exists:

1. Phase 1's gate
2. Changelog `Security` entries across the gap
3. OSV / GHSA
4. Phase 4's measured difference
5. Phase 5's reproduction
6. Phase 6's CI state

A changelog `Security` entry outranking a clean OSV batch is not a contradiction
to explain away — a privately disclosed fix ships with no CVE, so *clean scanner,
dirty changelog* is the expected reading and the whole reason Phase 2 reads
changelogs at all.

### Confidence

Not a feel. It is a function of how much of the evidence was actually derived,
which the three-state rule has already recorded per row:

| Condition | Confidence |
|---|---|
| Every verdict-bearing input derived, and the executing phases ran | **high** |
| One or more verdict-bearing inputs **underivable**, none of them decisive | **medium** |
| `--no-execute`, with a Phase 4-shaped question still open | **medium** — say what running Phase 4 would add |
| A **decisive** input underivable — one whose value would change the verdict | **low**, and name which one |

"Verdict-bearing" is the test, not "present in the table": an underivable row that
no verdict rule reads does not lower confidence, and saying it does trains the
reader to discount the field. Conversely a single underivable input that would
flip the recommendation caps it at **low** however green everything else is.

If the user asked for `--comment`, print the report and offer to post it; posting
is a separate, explicitly requested action.

**Close the loop, whatever phase the audit reached.** The two worktrees *and* the
`pr-<N>` branch Phase 0 created are registered in the **user's** repo, and they
accumulate one set per PR audited. This step lives here rather than in Phase 5
because Phase 5 is skippable and this is not: `--no-execute` skips it, and Phase
1's gate stops before it — which is the path where the audit was *most* right to
stop, and the one that used to litter every time. Phase 7 is the only phase every
audit reaches, including the one that ends at the gate.

```bash
# Fresh call: nothing survives one, so re-derive $SCRATCH and re-source Phase 0.
REPO=$(gh repo view --json nameWithOwner --jq .nameWithOwner); SCRATCH="${SCRATCH:-${TMPDIR:-/tmp}/dbaudit-${REPO/\//-}-<N>}"
. "$SCRATCH/phase0.env" || { echo "no handoff in $SCRATCH — re-run Phase 0" >&2; exit 2; }

python3 "${SCRIPTS:?not in the handoff — re-run Phase 0}/cleanup.py" \
  --scratch "$SCRATCH" --pr <N>
echo "cleanup exit: $?"
```

**Read the exit code; do not chain on it.** `0` is clean, `2` is could-not-run, and
**`1` means residue was found and the worktree was still removed** — a finding for
the report, not a cleanup failure. `cleanup.py … && echo done` treats that finding
as an error and hides it, the same shape as Phase 5's `cmd | tail && next`.

The script removes exactly what Phase 0 created and nothing else, discovering which
of `pr-<N>`, `base-<N>` and `tip-<N>` are actually there — so a rewritten base needs
no extra line and an actions bump, which creates no worktree at all, needs none
removed.

**Why a script, when it was three commands.** Because the three commands said
nothing about what does or does not dirty a worktree, and two separate audits
therefore reasoned it out and reached the same wrong answer: that Phase 5's
`uv sync` leaves a `.venv/` which makes `remove` refuse. It does not — `remove`
gates on `git status --porcelain`, which omits *ignored* files, and uv, pytest,
ruff and mypy each write a `.gitignore` containing `*` inside their own directory.
A run that types the command re-derives that question every time; a run that calls
the script never asks it.

**The refusal that is real is a Phase 5 finding.** Phase 5 runs the repo's own
gates in `$SCRATCH/pr-<N>`, and where those are fix-mode — `pre-commit` stages its
own edits — tracked files end up modified or staged. Phase 4 mutates `base-<N>` and
`gate_diff.py` restores it after every run; nothing restores `pr-<N>`. So the
script writes the residue to `$SCRATCH/residue-<tree>.diff` **before** removing:
`$SCRATCH` outlives the worktrees, so the evidence outlives the tree that held it.
Forcing without writing would discard something the audit produced and had not yet
reported, which is the loss `gate_diff.py`'s `restore()` declines `-x` to avoid.

*"The repo's own gates rewrote N tracked files at the proposed version"* is the
strongest row Phase 5 can produce. Put it in the report, with the path.

Keeping the worktrees is reasonable when a follow-up run is likely — say so in the
report, with the command above, so the user knows what is there. Silently keeping
them is what this step exists to prevent.

## Phase 8 — Learning loop (the only thing worth persisting)

Facts that were *derivable* were derived in Phase 0 and must not be saved. Facts
that were **learned the hard way cannot be re-derived — only re-suffered.** Those
are worth writing down.

If this audit surfaced a repo-specific landmine — a tool whose defaults collide
with this repo's config, a hook whose scope hides a CI failure, an invocation that
silently measures the wrong thing — **write it out and hand it over**: the
filename, the frontmatter, and the body of a `project` memory, with the evidence
and how it was caught. Do not create the file; the session that invoked this skill
can, and it is the one that owns the decision. If no memory directory exists,
offer the same text as an addition to the repo's `CONTRIBUTING.md` gotchas
section.

A generally portable trap belongs in this plugin rather than in one project's
memory — in the phase it applies to if it is a rule, or in that ecosystem's
reference if it is a recipe. Say which you are proposing, and why.

### Hand back the deviations too

Everything above hands back what the audit learned about the **repo**. This hands
back what it learned about **itself**, and it is a separate question.

The report asserts *"I followed this procedure"* by silence, and on 2026-08-19
that assertion was false with nothing anywhere to catch it. `fpga-board-sim` #363
ran to a complete, well-formed report under 0.22.1 while this file had never
loaded at all: a `commands/` entry shadowed the skill at the same
`<plugin>:<name>` address, so `SKILL.md` was unloadable and `disallowed-tools`
never applied. Every evidence row in that report was true. The procedure that
produced them was not this one — the audit had reached them by running two
commands that appear nowhere here, an invented `CLAUDE_PLUGIN_ROOT` export and a
`cat` of the procedure it should have been handed — and the report mentioned
neither.

So, separately from what the audit found about the PR, hand back:

- **every shell command run that this file did not specify** — quoted, with the
  gap it filled;
- **every plugin file read directly rather than invoked as written.**

Classify each as **plugin defect**, **prose gap**, **unproven**, or **correct**.
All four are real answers and `correct` is the common one: no procedure enumerates every repo
it will meet, and improvising is usually the right call. The goal is not to
suppress the improvisation but to stop it being invisible — a workaround that
*works* is precisely the one nobody reports, which is how the shadowing shipped
in 0.2.1 and survived to 0.23.0.

**A `plugin defect` row carries its evidence, or it is not that row.** Name the
command that failed and the exit status it returned. Where the run went straight
to a workaround and never issued the form this file specifies, the class is
**`unproven`**: the deviation is real and still worth handing back, but its
*cause* was inferred, and a row that does not say so is read as measured.

That distinction has cost two rounds. On 2026-08-30 and again on 2026-09-01, two
different audits handed back the same claim — that Phase 7's `git worktree
remove` fails because Phase 5's `uv sync` leaves a `.venv/` — each with a stated
mechanism, neither having run the plain command. Measured on git 2.55.0 and uv
0.12.8, in a worktree of a repo with **no `.gitignore` at all**: `remove` exits
**0**. It gates on `git status --porcelain`, which omits *ignored* files, and uv
writes a `.gitignore` containing `*` inside `.venv/`. The mechanism was never
there, and both rows read as though it had been observed.

**The cheapest check is "did this happen?", not "is this mechanism right?"** —
search this session's own history for the command the row says failed. Where it
was never issued, that settles the row in seconds, before any measurement.

**And an `unproven` row is a question, not a ticket.** Verify it before filing.
Both rounds above became issues on the strength of a mechanism that reads as
observation; the second had labelled *itself* unproven and was filed anyway.

Read it off what you actually ran. Reconstructing the list from the phase
headings returns a clean sheet every time, because the headings are what you
*meant* to run; the deviation least likely to be recalled is the small one that
felt too obvious to mention, and that is the shape both #363 commands had.

Print it; do not file it — the same contract as the hand-back above. This skill
does not write, and the session that invoked it owns what happens next.
