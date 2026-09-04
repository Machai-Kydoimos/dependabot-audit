# pre-commit

The per-phase method for a `.pre-commit-config.yaml` bump. `SKILL.md` carries each
phase's *question*, its gate, and the outputs it consumes; this file carries how
to answer it for this ecosystem, and nothing else.

Sectioned by phase deliberately, and the headings are load-bearing: the prose
suite attributes a bash block to the phase whose heading it sits under, and that
is the check which has caught three shipped forward-reference defects. A section
retitled out of that shape takes its guard with it.

**A `rev:` is a git ref on somebody else's repository, and it is neither a hash
nor a version.** That single fact reshapes three phases:

- there is no artifact to verify, so Phase 1 asks whether the pin is *immutable*
  rather than whether it is *intact* — the same substitution `references/actions.md`
  makes for `uses:`;
- the version that gets installed is declared **inside the hook repository**, not
  in the config, so Phases 2 and 3 must be pointed at that and not at the tag;
- the hook's own definition can change between two revs while the tool it wraps
  does not change at all — and that is where the defect this ecosystem was added
  for actually lived.

`scripts/precommit.py` answers all three in one call. Run it; the reasons are the
same as everywhere else here, and one of them is specific to this ecosystem: the
comparison it makes is against **someone else's YAML**, which is the input shape
least entitled to be assumed well-formed.

## The measurement this ecosystem exists for

`ruff-pre-commit` v0.16.2 → v0.16.5, on this plugin's own repository (#99):

```
!! ruff-format.types_or  [behavioural]
     before: [python, pyi, jupyter]
     after:  [python, pyi, jupyter, markdown]
```

One word, in one list, in a file nobody diffs. The hook began rewriting every
Markdown file in the repository and CI went red. **`ruff` itself did not change in
any way its changelog reports** — the release notes for 0.16.3 through 0.16.5 say
nothing about this, because the change is not in `ruff`, it is in the wrapper that
invokes it. A per-package currency check of `ruff` returns *current*; an advisory
query returns *clean*; both are correct and both are answering about the wrong
artifact.

That is the failure mode. Everything below exists to make it visible in one
command instead of a day.

## Phase 1 — Scope and provenance

**Phase 0 already derived the gate.** `discover.py` classifies a bump touching
`.pre-commit-config.yaml` as `ECOSYSTEM=pre-commit` and reads the diff's *lines*,
exactly as it does for `uses:`: every changed line must be a `rev:` line or a
comment. Read `$SCOPE_GATE`; do not re-derive one here.

The rule is deliberately narrow. A diff that also moves `args:`,
`additional_dependencies:` or `repo:` is **`beyond`** — those change what the hook
does, and a version bump may not do that quietly. `repo:` in particular repoints
the hook at a different repository, which is not a bump at all.

**There is no artifact hash, and the report must not imply one.** What the script
establishes is *immutability*, in three states:

| `rev:` | Pin | What it means |
|---|---|---|
| a 40-hex SHA | **immutable** | nobody can repoint it. This is what `pre-commit autoupdate --freeze` writes |
| a tag or branch | **mutable** | what was audited is what runs *today*. Not a Hold: the pin was mutable before this PR and the bump did not make it so |
| resolves to nothing | **underivable** | exit 2. A pin naming nothing is not a pin, and this is a finding about the config rather than the bump |

Nearly every config in the wild is the middle row, because `autoupdate` writes
tags by default. So it is reported and it does **not** set the exit code — a
signal that fires on every run is one the reader stops reading, which is the same
reasoning `references/actions.md` reaches for a `uses:` tag pin.

```bash
# Fresh call: nothing survives one, so re-derive $SCRATCH and re-source Phase 0.
REPO=$(gh repo view --json nameWithOwner --jq .nameWithOwner); SCRATCH="${SCRATCH:-${TMPDIR:-/tmp}/dbaudit-${REPO/\//-}-<N>}"
. "$SCRATCH/phase0.env" || { echo "no handoff in $SCRATCH — re-run Phase 0" >&2; exit 2; }

# The hook repo and both revs come out of the diff, which is one file and a
# handful of lines. Read them; do not take the versions off the PR title, which
# a grouped bump does not name.
git diff -U3 "$BASE_SHA...pr-<N>" -- '**/.pre-commit-config.yaml'

P="${SCRIPTS:?not in the handoff — re-run Phase 0}/precommit.py"
python3 "$P" --repo <owner>/<name> --from <old rev> --to <new rev> \
  --hook <every hook id this repo actually enables>
```

**Exit 2 means it could not run; exit 1 means it ran and found something.** Never
read one as the other.

`--hook` narrows the comparison to the hooks the audited repo enables, and it is
worth passing. `ruff-pre-commit` ships three hooks and a repo typically runs two;
a report naming a field that moved on a hook nobody runs is noise in the row a
reader uses to size the blast radius.

## Phase 2 — Currency

**Two questions, and a bot PR answers neither.** The `rev:` and the tool version
are different things, and a bump can be current in one and stale in the other.

1. **Is this the newest rev of the hook repository?** `gh api repos/<o>/<n>/tags`.
2. **Is the tool version it installs the newest of the tool?** This is the one
   that matters, and the script has already derived it — `installs at to` names
   the package and version out of the hook repo's own packaging.

Take answer 2 to the registry the tool actually lives in, and **check which one
that is before you do**:

| Hook `language` | Registry | Covered here |
|---|---|---|
| `python` | PyPI | **yes** — `scripts/changelog.py --package` reads the gap end to end |
| anything else | npm, rubygems, crates.io … | **no.** Report the boundary. Do not improvise a recipe from the shape of the PyPI one |

**`scripts/audit.py` does not apply here, and this file used to say it did.** It
takes a lockfile positional and sniffs the content; handed a package name it exits
with `cannot read <name>`. A `pre-commit` bump has no lockfile, so the currency
read is two commands rather than one script:

```bash
# Fresh call: nothing survives one, so re-derive $SCRATCH and re-source Phase 0.
REPO=$(gh repo view --json nameWithOwner --jq .nameWithOwner); SCRATCH="${SCRATCH:-${TMPDIR:-/tmp}/dbaudit-${REPO/\//-}-<N>}"
. "$SCRATCH/phase0.env" || { echo "no handoff in $SCRATCH — re-run Phase 0" >&2; exit 2; }

# The registry's true latest AND when it was published. Phase 2 compares that
# against $CREATED_AT, never against now, so the timestamp is not optional —
# it is what separates "the bot is behind" from "the bot is inside the cooldown".
python3 -c 'import json,sys,urllib.request as u; d=json.load(u.urlopen("https://pypi.org/pypi/"+sys.argv[1]+"/json")); v=d["info"]["version"]; f=d["releases"][v][0]; print("latest="+v, "published="+f["upload_time_iso_8601"], "yanked="+str(f["yanked"]))' <pkg>

# The gap's changelog, which IS scripted — `--package`, no lockfile needed.
python3 "${SCRIPTS:?not in the handoff — re-run Phase 0}/changelog.py" \
  --scratch "$SCRATCH" --package <pkg> --from <old version> --to <new version>
```

Measured on `ruff`, 2026-09-04: `latest=0.16.6 published=2026-09-03T16:56:40Z
yanked=False`; and `changelog.py` resolved `astral-sh/ruff` from PyPI's metadata
on its own, read all three rungs, and reported **266 of 307 commits unreconciled**
against what the release notes claim. That reconciliation is the reason to run it
rather than reading the notes.

The script prints the language and says which of those two rows applies. This is
the whole of #109's argument: for a Python hook, resolving the requirement puts
Phases 2 and 3 back on **covered** ground rather than the hand-run footing a
boundary Hold leaves them on. For a node hook it does not, and saying so is the
point of naming it.

**Where the two answers disagree, say which one you checked.** A mirror can lag
the tool it mirrors by days. `mirrors-mypy` and `ruff-pre-commit` both release
within hours, so the gap is usually zero — and "usually zero" is a reason to read
the number, not to assume it.

**A tag that looks like a version is making a claim.** The script compares it
against the packaging pin and reports a disagreement as a finding, because a
reader takes `rev: v0.16.5` to mean the tool is 0.16.5 and nothing enforces that.
It makes the comparison only where the tag is shaped like a dotted version: a
branch pin, a SHA pin, or a mirror whose tags do not track the tool would
otherwise disagree on every single bump.

## Phase 3 — Known vulnerabilities

OSV and GHSA, queried for the **package the hook installs** at the version being
adopted — never for the `rev:`. `v0.16.5` is not a PyPI version of anything, so a
query built from the tag returns an empty result that reads exactly like *no known
vulnerabilities*. That is the failure this phase is most exposed to here, and it
is silent.

**`audit.py` does that batch for `uv.lock` and cannot do it here**, because it
needs a lockfile. So the two queries are yours, and they are short:

```bash
# OSV, across the whole gap rather than only the version being adopted: a bump
# that steps over an affected release still spent time on it, and Phase 7's table
# asks whether the bump moves *into* the advisory or past it.
python3 -c 'import json,sys,urllib.request as u; vs=sys.argv[2:]; q={"queries":[{"package":{"name":sys.argv[1],"ecosystem":"PyPI"},"version":v} for v in vs]}; r=json.load(u.urlopen(u.Request("https://api.osv.dev/v1/querybatch",data=json.dumps(q).encode(),headers={"Content-Type":"application/json"}))); [print(sys.argv[1],v,len(x.get("vulns") or []),"advisory(ies)") for v,x in zip(vs,r["results"])]' <pkg> <every version in the gap>

# GHSA, and note it is UNQUALIFIED by version. A version-qualified query reads
# clean whether or not the package has advisories, which is the reassuring
# direction; ask for all of them and read the ranges yourself.
gh api graphql -f query='{securityVulnerabilities(first:20, ecosystem:PIP, package:"<pkg>"){nodes{advisory{ghsaId severity}vulnerableVersionRange firstPatchedVersion{identifier}}}}' \
  --jq '.data.securityVulnerabilities.nodes[] | .advisory.ghsaId+"  "+.advisory.severity+"  "+.vulnerableVersionRange'
```

**Both were run while writing this, and the GHSA one was run against a control.**
`ruff` returns zero from each, at 0.16.2, 0.16.5 and 0.16.6. A check that has only
ever returned empty proves nothing about whether it discriminates, so the same
query was pointed at `requests`, which returns `GHSA-gc5v-m9x4-r6x2 MODERATE
< 2.33.0` and two more. Point it at a package you know is affected before you
trust a clean answer from it.

Where the language is not `python`, the ecosystem is outside this plugin: report
that the advisory check was not made, rather than making one whose result you
cannot stand behind.

## Phase 4 — Behavior change

**`pre-commit` is very often not on `PATH`, and this phase and the next both call
it.** This repository's own `ci.yml` does `pip install pre-commit` first, so even
here it is not assumed. Derive the invocation once, at the top of this phase, and
reuse it in Phase 5:

```bash
PC=$(command -v pre-commit >/dev/null && echo "pre-commit" || echo "uvx pre-commit")
$PC --version || { echo "no pre-commit and no uvx — Phases 4 and 5 cannot run" >&2; exit 2; }
```

`uvx pre-commit` fetches it on demand and needs no install. Measured here on
2026-09-04: `command -v pre-commit` exits **1**, and `uvx pre-commit --version`
prints `pre-commit 4.6.2`.

**An absent `pre-commit` is exit 2, not a failing hook.** "Could not run" reported
as "ran and found something" is the distinction this procedure is most careful
about everywhere else, and the easiest one to lose here — a shell that cannot find
the binary exits non-zero exactly like a hook that found a problem.

**This is the phase, and the hook definition is where it lives.** Diff
`.pre-commit-hooks.yaml` between the two revs — which is what the script did in
Phase 1, so read its output rather than re-fetching.

A field is **behavioural** when it changes which files the hook touches or what it
runs on them: `entry`, `args`, `language`, `files`, `exclude`, `types`,
`types_or`, `exclude_types`, `additional_dependencies`, `pass_filenames`,
`stages`, `always_run`, `require_serial`. A field the script does not recognise
counts as behavioural too, deliberately: `pre-commit` gains keys, and a new
selector must not arrive as cosmetic because a list predates it.

**The hook says what it now selects. It does not say how much of this repo that
is** — and the report needs the second. That measurement is in the audited tree,
so it takes `$MAY_EXECUTE` like the rest of Phase 4:

```bash
# Fresh call: nothing survives one, so re-derive $SCRATCH and re-source Phase 0.
REPO=$(gh repo view --json nameWithOwner --jq .nameWithOwner); SCRATCH="${SCRATCH:-${TMPDIR:-/tmp}/dbaudit-${REPO/\//-}-<N>}"
. "$SCRATCH/phase0.env" || { echo "no handoff in $SCRATCH — re-run Phase 0" >&2; exit 2; }
[ "${MAY_EXECUTE:-}" = yes ] || { echo "MAY_EXECUTE='${MAY_EXECUTE:-unset}' — this block runs the PR's code; not authorised" >&2; exit 2; }

# The denominator, stated — `report-template.md` requires a count attributed to a
# class of file to come from a command, and this is the phase that tempts the split.
git -C "$SCRATCH/pr-<N>" ls-files | wc -l
git -C "$SCRATCH/pr-<N>" ls-files '*.md' | wc -l     # or whatever class the field selects

# 1. WITH the repo's config, both revs, against the SAME tree. This is what
#    actually happens on a commit.
( cd "$SCRATCH/base-<N>" && $PC run <hook id> --all-files )
( cd "$SCRATCH/pr-<N>"   && $PC run <hook id> --all-files )

# 2. WITHOUT it — the tool alone, at each rev's pinned version, over the files the
#    new `types_or`/`types`/`files` now selects. This is the pair that separates
#    *the tool changed* from *the wrapper's selection changed*, and nothing else does.
cd "$SCRATCH/pr-<N>"
uvx <tool>@<old version> format --check $(git ls-files '*.md')
uvx <tool>@<new version> format --check $(git ls-files '*.md')
```

**Read the two runs against each other, not each on its own.** Measured on this
repository at `ruff-pre-commit` v0.16.2 → v0.16.5:

| Run | Result |
|---|---|
| hook **with** config, either rev | `ruff format … Passed` — 0 files rewritten |
| tool **without** config, ruff 0.16.5 | `6 files would be reformatted, 10 already formatted` |
| tool **without** config, ruff **0.16.2** | `6 files would be reformatted, 10 already formatted` |

**The old and new tool versions agree exactly.** So the tool did not change what it
does to a Markdown file; the *wrapper* changed which files it is handed. That is
the finding, and only the second pair of rows can produce it — the first pair
passes at both revs and says nothing.

The 6 are removed by `exclude: ^integration/fixtures/` on the hook, which is why
the run is green. **State the exclusion as the reason**, per `SKILL.md`'s Phase 4:
a gate that would rewrite files the repo excludes has not been shown harmless, it
has been shown *currently excluded*, and those are different claims.

Report the difference between those two runs, by **file class and counted from a
command** — `report-template.md` is explicit that a count attributed to a class of
file has to come from one, and this is the phase that tempts the split.

**A gate that rewrites files the repo excludes has defeated the exclusion**, and
`SKILL.md`'s Phase 4 says what is owed then: two check-only runs, one at the
nearest config and one forced to the root manifest, because a nested config
shadows the root for files beneath it. That rule came from this exact bump.

## Phase 5 — Independent reproduction

`$PC run --all-files` in `$SCRATCH/pr-<N>` — `$PC` from Phase 4 — which is the repo's own gate
and usually the whole of CI's lint job.

**It is not hermetic, and the report must say so.** `pre-commit` builds each
hook's environment by installing from the network at run time, so a green run
proves the hook works *with what the registry served just now*. That is weaker
than `uv.lock`'s frozen install and stronger than nothing; name the distinction
rather than letting "the gate passed" carry the same weight in both ecosystems.

Two failure shapes worth separating in the report, because they have different
verdicts:

| Result | Reading |
|---|---|
| the hook **fails** on files it also failed on at the base | pre-existing. A real finding and a different one; not a Hold on this bump |
| the hook **fails** on files that passed at the base | the behaviour change from Phase 4, measured. This is the Hold |
| `pre-commit` cannot build the environment at all | **exit 2 territory** — could not run. Do not report it as the hook failing |

The third row is the one that gets misread, and it is common: a hook whose
`language: node` needs a node toolchain the machine may not have. "Could not run"
reported as "ran and found something" is the distinction this whole procedure is
most careful about everywhere else.
