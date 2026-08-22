# GitHub Actions

The per-phase method for an actions bump. `SKILL.md` carries each phase's
*question*, its gate, and the outputs it consumes; this file carries how to
answer it for this ecosystem, and nothing else.

Sectioned by phase deliberately, and the headings are load-bearing: the prose
suite attributes a bash block to the phase whose heading it sits under, and that
is the check which has caught three shipped forward-reference defects. A section
retitled out of that shape takes its guard with it.

A bump retargets a `uses:` pin. There is no lockfile and no artifact hash, so
`scripts/audit.py` does not apply — Phase 1 becomes a question about the *pin*
instead. Every other phase still applies, and each has a method below.

Actions **do** have an advisory database, which earlier revisions of this
plugin's documentation denied in three separate places. GHSA carries an `actions`
ecosystem; a Phase 3 that believes otherwise skips a real check.

## Phase 1 — Scope and provenance

**Read the diff, not the PR.** Two of this phase's answers come out of it, and
neither is where the obvious source puts it:

- **The scope gate keys on the kind of line, not the number of files.** An action
  is pinned in every workflow that uses it, and a grouped bump moves several
  actions at once. Measured on `cli/cli`, all three merged: #14091 two files,
  #13981 three, #14147 four — and every changed line across them is a `uses:`
  line or a comment. That is the invariant, and `scripts/discover.py` applies it:
  Phase 0 hands Phase 1 `$SCOPE_GATE`, so read that answer rather than
  re-deriving one here. A gate phrased as "one workflow file" refuses the
  ordinary case, and refuses it in the report's language for a bump reaching into
  source.
- **The comment half is not only the trailing `# v1`.** A compiler that emits
  workflows records the pins it wrote in a header block, so a correct bump
  changes the `uses:` line **and** the comment naming the same pin — #13981 and
  #14147 both do, and a rule reading "trailing version comment" literally fires
  on two of the three PRs above. The count is reported rather than dropped: a pin
  manifest is how a generated file announces itself, which is the `DO NOT EDIT`
  finding below reached from the diff instead of a `grep`.
- **The versions under audit are not readable from the title or the body.** Phase
  1's rule against reading package *names* off the title extends to versions
  here, where no script derives them. `cli/cli` #13981 — titled and summarised
  "bump actions/checkout from 6 to 7" — moves one bare `@v6` pin to `@v7` *and*
  nine SHA pins from `v7.0.0` to `v7.0.1`: two transitions, one of them
  described. Its embedded release notes stop at v7.0.0 and are marked
  `(truncated)`, and `7.0.1` appears once in 10 KB of body, as a commit subject
  inside a collapsed list. Take the range Phase 2 reads from the `uses:` lines
  that changed.

**The real provenance question here is whether the pin is immutable.** It has
only two values:

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
gh api "repos/<owner>/<repo>/git/refs/tags/<tag>" \
  --jq 'if type == "array"
        then "no such tag — these share the prefix: \([.[].ref] | join(", "))"
        else "\(.object.type) \(.object.sha)" end'
# if type == "tag" (annotated), dereference — the ref gives you the *tag object*:
gh api "repos/<owner>/<repo>/git/tags/<sha>" --jq '.object.sha'
```

The dereference step is mandatory for annotated tags and a no-op for lightweight
ones. Skipping it compares a tag object against a commit and reports a false
mismatch. Verified live on `nickg/setup-nvc@v1`: annotated, and the undereferenced
SHA matches nothing.

**That endpoint is *get all references in a namespace*, so it answers in three
shapes, and the middle one is the case Phase 2 asks about.** Measured 2026-08-21:

| Asked for | Answer | Meaning |
|---|---|---|
| `actions/checkout@v5` — an **exact** ref, with `v5.0.0`, `v5.0.1` and `v5.1.0` under the same prefix | object: `commit fbc6f399` | the tag exists. An exact ref wins, and siblings under the prefix do not change that |
| `astral-sh/setup-uv@v10` — no such ref | **array**: `refs/tags/v10.0.0`, `refs/tags/v10.0.1` | **no such tag** — and the array names what does exist instead |
| `astral-sh/setup-uv@v999` — nothing matches | `404 Not Found`, exit 1 | no tag, and nothing beneath it either |

**The array is the answer, not a failed call.** It enumerates the refs that exist
and thereby settles that the one asked for does not, so it arrives exactly when
the question is answerable — and `.object.type` against it dies with `expected an
object but got: array` at exit 1. That message reads like an API fault, which
invites a retry that returns it again and a report calling currency *underivable*
when it was fully derivable. The same trap as `branches/<b>/protection` in
CONTRIBUTING: a confident-looking error about the wrong thing.

The singular `git/ref/tags/<tag>` does not crash, and is worse. It answers a bare
`404` to both of the last two rows, collapsing "no such tag, and here is what does
exist" into "nothing here" — so it discards the half Phase 2 needs. Not crashing
is not the same as answering.

**A workflow file can be generated, and then the bot's edit does not stick.**
Compilers that emit workflows own the `uses:` pins they write — `gh-aw` generates
`*.lock.yml` from a `.md` source — and Dependabot edits the emitted file, because
that is where the pin lives. Merging is not wrong; it is *transient*. The next
regeneration writes the pins back from the generator's own list, and that list
can be older than the bump.

Observed on `cli/cli`: #14124 merged `github/gh-aw-actions/setup` to v0.86.1
(`8914f47b`) on 2026-08-10, and the regeneration commit `ed5a99f` three days
later rewrote it to `2709137e`, v0.85.4 — `compare` reports `behind ahead=0
behind=2`. The bot's own next PR, #14147, then reads the current pin as
**0.85.4**: the version its previous merged PR had already moved past.

The detection is the header the generator writes — `DO NOT EDIT`, `automatically
generated by` — one `grep` over the files the diff touches. Where it fires, say
so: the durable fix is a bump of the generator, and the one under audit will be
undone without it.

The remaining checks are structural: every `uses:` is SHA-pinned, the workflow's
`permissions:` are minimal, and the diff does not quietly add a step or change a
trigger.

## Phase 2 — Currency

**"Current" is a question about the tag line, not the pin.** A moving major tag
picks up new releases on its own, so a newer patch is not a gap. What matters is
whether the *major* being adopted is still the newest one, and whether the tag
still points where the PR proposed.

**Check the tag line exists before reasoning about it.** The paragraph above
assumes a moving major tag. Not every action publishes one, and one that did can
stop — so ask Phase 1's recipe for the bare major and read an array as *no*.

Measured on `astral-sh/setup-uv` 2026-08-21: `v1` through `v7` are refs; `v8`,
`v9` and `v10` are not. The moving tag was discontinued at v8 (2026-03-29) and
every release since stands alone, so above v7 there is nothing to pick up a new
release, a newer patch **is** a gap, and it reads exactly like a registry currency
gap. At v7 and below, it does not. One repository answers both ways depending on
the major under audit, which is why this is asked per bump rather than settled
once per action — and a bump that crosses the boundary changes what the pin
comment promises, which no bot PR mentions.

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

Auditing an old or merged actions PR, compare against **the repo's current pin**
as well as the PR's proposal: a mismatch may already have been fixed, and the
workflow file on the default branch is what says so.

**CI cannot see any of this.** On the observed case every required check was
green, because the workflow parses and the job runs whichever commit it is
pointed at. Green says the pin resolves, not that upstream still stands behind
it. This is the actions-shaped version of the reason the whole procedure exists.

## Phase 3 — Known vulnerabilities

```bash
gh api "/advisories?ecosystem=actions&affects=<owner>/<name>" \
  --jq '.[] | "\(.ghsa_id)\t\(.severity)\t\(.summary)"'
```

Also read the action repository's own status — `archived`, `disabled`, or a
transfer to a new owner are all supply-chain facts that no advisory records.

**Do not query OSV by version for this ecosystem.** OSV carries the same
advisories, but its GitHub Actions entries have no usable version ranges, so a
version-qualified query returns empty and reads as clean. Measured against
`tj-actions/changed-files`, the 2025 compromise:

| Query | Result |
|---|---|
| package only | **2 vulns** |
| `+ version 45.0.7` (the compromised release) | 0 |
| `+ version 0.0.0` | 0 — a range check would match everything |
| PyPI control: `requests` 2.19.0, version-qualified | 10, so the pattern itself is sound |

Copying the `uv.lock` shape here — batch by `(package, version)` — therefore
reports **clean on a known-compromised action**. Query by name, or use GHSA.

## Phase 4 — Behavior change

You cannot run an action locally at two versions, so measurement is unavailable
and reading the release notes is the method rather than the shortcut. That makes
the second step load-bearing: **a change is only a finding here if this repo's
workflows are in its scope.**

Read the notes for every version in the gap, looking for changes to a *default*,
a *trigger*, an *input*, or a *runner requirement* — then find the line in this
repo's workflows that decides whether it applies:

| Change | What to grep for here |
|---|---|
| a trigger is newly restricted | `pull_request_target:`, `workflow_run:`, `release:` in this repo's workflows — **and `push:` carrying a `tags:` key**, because a tag push is not an event name. It is `push` with a `refs/tags/` ref, so the event-name grep cannot see it |
| a default input flips | that input's name — an explicit setting pins the old behaviour |
| a minimum runner or Node version | `runs-on:` — GitHub-hosted is fine, a self-hosted label is not |
| credential or token handling | `permissions:`, `persist-credentials`, and what later steps do with the token |

**Report "inert here" as a result, not as silence.** Reaching it deliberately is
this phase working; reaching it by not looking is the failure. Observed:
`actions/checkout@v7` blocks fork-PR checkout under `pull_request_target` and
`workflow_run` — a security change shipped as a plain bullet with no heading and
no ⚠️ — and it was genuinely inert on a repo that uses neither trigger. The report
should say so and name the greps that settled it.

**Read the interface, not only the notes.** The notes are prose written by the
releaser; `action.yml` is what the runner loads, it ships in the action's own
repo, and it is therefore readable at both pins:

```bash
# Fresh call: nothing survives one, so re-derive $SCRATCH and re-source Phase 0.
REPO=$(gh repo view --json nameWithOwner --jq .nameWithOwner); SCRATCH="${SCRATCH:-${TMPDIR:-/tmp}/dbaudit-${REPO/\//-}-<N>}"
. "$SCRATCH/phase0.env" || { echo "no handoff in $SCRATCH — re-run Phase 0" >&2; exit 2; }

for R in <old-sha> <new-sha>; do
  # Two statements, each checked. Piped into `base64` and left unchecked, a
  # failed fetch writes an empty file — and `diff` on two empty files exits 0,
  # reporting "no interface change", which is this method's *finding*.
  gh api "repos/<owner>/<action>/contents/action.yml?ref=$R" --jq .content \
    > "$SCRATCH/action-$R.b64" || { echo "cannot read action.yml at $R" >&2; exit 2; }
  base64 -d < "$SCRATCH/action-$R.b64" > "$SCRATCH/action-$R.yml" \
    || { echo "action.yml at $R is not valid base64" >&2; exit 2; }
done
diff -u "$SCRATCH/action-<old-sha>.yml" "$SCRATCH/action-<new-sha>.yml"
```

An input added, removed, renamed, or with its `default:` changed shows up here or
does not, which is a falsifiable answer to the *default input flips* row above
rather than an inference from someone's summary.

**A description-only diff is a finding, not a clean bill.** Measured on
`astral-sh/setup-uv` 9.0.0 → 10.0.1, whose v10.0.0 disables the cache under
`enable-cache: auto`:

| Source | Conditions it names |
|---|---|
| the release notes | **3** — `pull_request_target`, `workflow_run`, `release` |
| `action.yml` description | **5** — "GitHub-hosted runners except for release, **tag push**, `pull_request_target`, and `workflow_run`" |
| `src/utils/inputs.ts` | **5** — `isTagPush` checked *first*, its own branch and its own log line, then the three-event `||` chain |

The word *tag* appears nowhere in the notes body. They were written from the
second `if` and missed the first. And `default: "auto"` is **unchanged** across
the bump — what changed is what `auto` means — so a check asking whether a
default flipped correctly answers *no* while the behaviour moves underneath it.
The only place the fourth condition surfaced was description prose. Treat that
prose as the signal it is.

**Where the notes and the interface disagree, the source settles it**, and it
ships in the same repo at the same ref. That is the read that turned "the
description says four, the notes say three" into which one is true.

On `fpga-board-sim` #363 the verdict was *inert here* and was correct — that repo
triggers on `push: branches: [main]` and `pull_request:` only. It was correct by
luck. The same procedure, on a repo with `push: tags:`, reports inert about a
change that is live.

**Most of the time the diff confirms rather than discovers, and that is the
result you want.** Same action one release earlier — `fpga-board-sim` #333,
setup-uv 8.3.2 → 9.0.0 — and the diff is a single clean line, `prune-cache`
`default: "true"` → `"false"`, which v9.0.0's notes announce under *🚨 Breaking
changes*. Interface and notes agree, so the read costs one call and returns a
falsifiable *no surprises*. A method that only ever fires is one nobody runs.

That bump is also the *default input flips* row working end to end: the repo sets
`prune-cache` nowhere, so it takes the new default rather than pinning the old
one, and the finding is real rather than inert.

**Two signals that the notes alone will not give you.** Both were observed:

- **A coordinated release across every supported major is a security backport.**
  `actions/checkout` published v7.0.1, v6.1.0, v5.1.0, v4.4.0, v3.7.0 and v2.8.0
  within 35 minutes of each other; the backports carry `[BREAKING]` and a
  changelog link that the original major's notes do not. Check the sibling majors'
  release dates, not just the line you are on.
- **Version-coupled actions must move together.** `upload-artifact` and
  `download-artifact` ship majors in lockstep — the v7/v8 pair went out eight
  seconds apart. If the bump moves one half, check the sibling's pin in the same
  workflow and say whether the repo is now split across generations.

## Phase 5 — Independent reproduction

There is nothing to install and no way to execute an action outside GitHub's
runners, so local reproduction is unavailable. The substitute is **evidence that
this pin has already run**: ask the workflow the bump changed.

```bash
gh run list --workflow <changed>.yml --limit 10 \
  --json conclusion,headBranch,createdAt,displayTitle \
  --jq '.[] | "\(.conclusion)\t\(.createdAt)\t\(.displayTitle)"'
```

Read it against the merge date, and be strict about what it proves. Runs *after*
the bump landed exercised the new pin; runs before it did not, and a green history
that predates the merge says nothing at all about the version being adopted.

| Situation | What you can honestly report |
|---|---|
| the workflow ran green on this pin since the bump landed | reproduced — the strongest evidence available for an actions bump |
| the workflow has not run since | **not reproduced.** State it; do not let Phase 6's green stand in for it |
| the workflow is not PR-triggered and the PR is open | reproduction is impossible before merge. That is a property of the change, and it belongs in the report |

Observed: a bump to `actions/upload-artifact` in a release-only workflow, merged
alongside a `download-artifact` pin two majors behind. Nothing in the PR could
show whether the pair still interoperated — seven green release runs over the
following month did.
