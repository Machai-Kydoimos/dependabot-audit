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

The detection is check 4 below. Where it fires, say so: the durable fix is a
bump of the generator, and the one under audit will be undone without it.

**The remaining four checks are structural, and each one has a command.** They
are cheap and they are usually quiet; the quiet is the point, because the case
this phase exists to catch is a pin bump that is also something else.

```bash
# Fresh call: nothing survives one, so re-derive $SCRATCH and re-source Phase 0.
REPO=$(gh repo view --json nameWithOwner --jq .nameWithOwner); SCRATCH="${SCRATCH:-${TMPDIR:-/tmp}/dbaudit-${REPO/\//-}-<N>}"
. "$SCRATCH/phase0.env" || { echo "no handoff in $SCRATCH — re-run Phase 0" >&2; exit 2; }

# 1. Every `uses:` is SHA-pinned. Derive the list, then filter what you captured
#    — piping `git grep` into `grep` reports the filter's status and throws
#    git's away, which is how a failed read becomes a clean bill.
USES=$(git grep -nE '^[[:space:]]*-?[[:space:]]*uses:' pr-<N> -- '.github/workflows/'); RC=$?
[ "$RC" -le 1 ] || { echo "the uses: grep failed ($RC) — underivable, not clean" >&2; exit 2; }
#    `printf '%s'`, no `\n`: on an *empty* capture `%s\n` prints a blank line,
#    which no filter below excludes, so it reaches stdout and flips the exit to
#    `0` — this check's *found something* answer, on a tree with nothing in it.
echo "uses: lines: $(printf '%s' "$USES" | grep -c .)"
printf '%s' "$USES" | grep -vE '@[0-9a-f]{40}([[:space:]]|$)'
echo "unpinned exit: $?"

# 2. What each workflow grants, and which one grants nothing and so inherits.
#    The list is captured and checked before it is iterated: `for f in $(git
#    ls-tree …)` throws the status away, and a read that failed then iterates
#    zero times and prints nothing — which is what a repo whose workflows were
#    all read and all fine looks like from here.
WF=$(git ls-tree --name-only "pr-<N>:.github/workflows/") \
  || { echo "cannot list workflows at pr-<N> — underivable, not clean" >&2; exit 2; }
for f in $WF; do
  printf '%-30s ' "$f"
  git grep -cE '^[[:space:]]*permissions:' pr-<N> -- ".github/workflows/$f" \
    || echo "none — inherits the repo default, read below"
done
git grep -nE -A3 '^[[:space:]]*permissions:' pr-<N> -- '.github/workflows/'
gh api "repos/$OWNER/$NAME/actions/permissions/workflow" \
  --jq '"default=\(.default_workflow_permissions)\tcan_approve_prs=\(.can_approve_pull_request_reviews)"'
echo "repo default exit: $?"

# 3. Nothing but pins and comments changed — Phase 0's scope-gate invariant,
#    reported rather than used to stop. Captured and checked for check 1's
#    reason, which this line was the one place in the block not to follow:
#    `git diff | grep` reports the grep's status and throws git's away.
DIFF=$(git diff "$BASE_SHA...pr-<N>" -- '.github/workflows/') \
  || { echo "the workflow diff failed — underivable, not clean" >&2; exit 2; }
CHANGED=$(printf '%s' "$DIFF" | grep -E '^[+-]' | grep -vE '^(\+\+\+|---)')
echo "changed lines: $(printf '%s' "$CHANGED" | grep -c .)"
printf '%s' "$CHANGED" | grep -vE '^[+-][[:space:]]*-?[[:space:]]*uses:' | grep -vE '^[+-][[:space:]]*#'
echo "residue exit: $?"

# 4. A generated workflow: the bot's edit is transient, because the next
#    regeneration writes the pins back from the generator's own list.
git grep -nE 'DO NOT EDIT|automatically generated by' pr-<N> -- '.github/workflows/'
echo "generated grep exit: $?"
```

**On checks 1 and 3, exit `1` is the clean answer.** Both end in a `grep -v`, so
a tree with nothing wrong leaves nothing to print and the filter reports no
match. That is inverted from every other status in this document, and a reader
who chains `&&` onto either one silently drops the finding. Measured on
`fpga-board-sim` #436 (setup-uv 10.0.1 → 10.1.0): 27 `uses:` lines, all pinned,
unpinned exit `1`; 18 changed lines, residue exit `1`. Planting an added
`- run: curl … | sh` step and a bare `@v1` pin fires each of them at exit `0`.

**An empty capture is not an empty answer.** `$(…)` strips trailing newlines, so
an empty capture printed through `%s\n` is a blank line, which neither `grep -v`
excludes — it reaches stdout and carries the exit to `0`, this block's *found
something* answer. `printf '%s'` without the newline prints nothing for an empty
capture and the same lines for a non-empty one, so only the empty case moves.
Measured on git 2.55.0 and GNU grep 3.12; 0.53.0's CHANGELOG entry has the
working.

**A workflow with no `permissions:` block is not a workflow with minimal
permissions.** It inherits the repository default, which is a different question
answered by a different call — which is why the loop names the absence rather
than letting it fall out of the grep as silence. On `fpga-board-sim` the default
is `read`; where it is `write`, a workflow declaring nothing is running with
write on every job.

**That call needs collaborator read, and fails the way Phase 2's does.** On a
repository you are not a collaborator on it answers `403` — `gh` exits **1** and
writes the body to **stdout**, so a capture succeeds holding
`{"message":"You must have repository read permissions"…}` and reads as an
answer. Measured on `astral-sh/setup-uv`. Key on the exit status, and report the
row underivable: an unreadable default is not a minimal one.

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

**Where the tag line is gone, currency is a question about releases** — so ask
the releases, because the tag cannot answer:

```bash
gh api repos/<owner>/<repo>/releases/latest \
  --jq '"\(.tag_name)\t\(.published_at)\tprerelease=\(.prerelease)"'
```

Measured 2026-09-16: `astral-sh/setup-uv` answers `v10.1.0`, published
2026-09-10 — so a PR proposing v10.0.1 carries a gap no tag check can see. The
endpoint excludes prereleases and drafts, which is what this row wants.

**A failure here is underivable, not current.** The call 404s on a repository
that publishes no releases at all — measured on `git/git` and `torvalds/linux`,
`gh` exit 1 — and an action that only ever moves tags is exactly that shape. `gh`
writes the error body to **stdout**, so a capture succeeds and holds
`{"message":"Not Found"…}` while looking like an answer: key on the exit status,
not on what came back. Report the row underivable and say why — a question that
could not be asked is not a pin confirmed current.

It names the newest release, not the highest version. If the major it reports is
*below* the one the PR proposes, a backport is on the line rather than a gap, and
`releases?per_page=15` shows the order before you call it either.

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
workflow files on the default branch are what say so — every one of them, by
subpath too, since `github/codeql-action/init@…` is how that action is pinned:

```bash
# Fresh call: nothing survives one, so re-derive $SCRATCH and re-source Phase 0.
REPO=$(gh repo view --json nameWithOwner --jq .nameWithOwner); SCRATCH="${SCRATCH:-${TMPDIR:-/tmp}/dbaudit-${REPO/\//-}-<N>}"
. "$SCRATCH/phase0.env" || { echo "no handoff in $SCRATCH — re-run Phase 0" >&2; exit 2; }

git ls-tree --name-only "origin/$DEFAULT:.github/workflows/"; echo "list exit: $?"
git grep -nE 'uses:[[:space:]]*"?<owner>/<action>[/@]' "origin/$DEFAULT" -- '.github/workflows/'
```

**CI cannot see any of this.** On the observed case every required check was
green, because the workflow parses and the job runs whichever commit it is
pointed at. Green says the pin resolves, not that upstream still stands behind
it. This is the actions-shaped version of the reason the whole procedure exists.

## Phase 3 — Known vulnerabilities

```bash
gh api "/advisories?ecosystem=actions&affects=<owner>/<name>" \
  --jq '.[] | "\(.ghsa_id)\t\(.severity)\t\(.summary)"'
```

**Also read the action repository's own status** — `archived`, `disabled`, a
fork, or a transfer to a new owner are all supply-chain facts that no advisory
records, and one call carries every one of them:

```bash
gh api repos/<owner>/<action> \
  --jq '"archived=\(.archived)\tdisabled=\(.disabled)\tfork=\(.fork)\tfull_name=\(.full_name)"'
```

**`full_name` is what answers the transfer, and it is the field nobody thinks to
read.** The API follows a rename silently: the call succeeds, every other field
looks ordinary, and the only sign is that the name coming back is not the name
that went in. Measured 2026-09-16:

| Asked for | `full_name` came back as | `archived` |
|---|---|---|
| `astral-sh/setup-uv` | the same | `false` — ordinary, and this is what most look like |
| `actions/setup-ruby` | the same | **`true`** — archived under its own name |
| `ambv/black` | **`psf/black`** | `false` — transferred, and the request still worked |
| `kubernetes-incubator/kube-aws` | **`kubernetes-retired/kube-aws`** | **`true`** — both at once |

A workflow pinned to the old name keeps working, because GitHub redirects the
clone and the API alike. So none of this is visible from the repo under audit,
none of it is a gate, and all of it is a fact about the supplier that the report
is the only place to put.

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

**Read the notes for every version in the gap** — which means fetching them, and
the call that does it carries one field beyond the body:

```bash
# The gap first: every release, newest by publication date. Both stamps raw:
# comparing them inside the --jq would answer "not edited" for a response that
# carried no update stamp at all, because jq sorts null below every string.
# Print the fields; a missing one is then visible as `null`.
gh api "repos/<owner>/<action>/releases?per_page=100" \
  --jq '.[] | "\(.tag_name)\tpublished=\(.published_at)\tupdated=\(.updated_at)"'

# Then the notes, one call per version in the gap — both stamps above the body,
# so an edit is visible in the same output you are reading for behaviour.
gh api "repos/<owner>/<action>/releases/tags/<tag>" \
  --jq '"\(.tag_name)\tpublished=\(.published_at)\tupdated=\(.updated_at)\n\(.body)"'
```

**A release body is mutable, and `.body` alone cannot tell you it changed.**
`published_at` never moves; `updated_at` does. Measured 2026-09-17 on
`actions/checkout`: **17 of the last 58 releases** have `updated_at >
published_at`, **8 of them more than a day later**.

So this is not a stale-cache worry. **Two audits of the same pin, months apart,
can read different notes and reach different verdicts, and nothing in `.body`
says so.** Where `updated=` is later than `published=` for a version in the gap,
say it in the row: what you are quoting is the current text, not the
announcement, and the commit range is the half that cannot be rewritten. Where
`updated=` reads `null`, the field is gone and the question is **unanswered** —
which is not the same as answered *no*.

**A version with no release fails the way Phase 2's currency call does.** `gh`
exits **1** and writes `{"message":"Not Found"…}` to **stdout** — and `--jq
.body` does not filter it, so a capture holds 133 bytes of plausible-looking
text where the notes should be. Measured on `astral-sh/setup-uv`. Key on the
exit status, and see § Phase 2 for the same trap on `releases/latest`.

With the notes in hand, look for changes to a *default*, a *trigger*, an
*input*, or a *runner requirement* — then find the line in this repo's workflows
that decides whether it applies:

| Change | What to grep for here |
|---|---|
| a trigger is newly restricted | `pull_request_target:`, `workflow_run:`, `release:` in this repo's workflows — **and `push:` carrying a `tags:` key**, because a tag push is not an event name. It is `push` with a `refs/tags/` ref, so the event-name grep cannot see it |
| a default flips — of an input, or of an environment variable the action reads | an **input**: its name, and an explicit setting pins the old behaviour. An **environment variable**: its name across the whole tree, any case — a `run:` line or any file can set one, and so can a runner or a repo setting, where no grep reaches. So no hit is `inert here` only beside Row 3 — every job's `runs-on:`, resolved — exiting 0; otherwise `underivable` |
| a minimum runner or Node version | `runners.py` — exit 0 is every job on a GitHub-hosted label; 1 names each that is not, or that the tree cannot resolve |
| credential or token handling | `permissions:`, `persist-credentials`, and what later steps do with the token |

Those are four reads, not four phrasings of one, and they run against the PR's
own ref because that is the tree the bump lands in:

```bash
# Fresh call: nothing survives one, so re-derive $SCRATCH and re-source Phase 0.
REPO=$(gh repo view --json nameWithOwner --jq .nameWithOwner); SCRATCH="${SCRATCH:-${TMPDIR:-/tmp}/dbaudit-${REPO/\//-}-<N>}"
. "$SCRATCH/phase0.env" || { echo "no handoff in $SCRATCH — re-run Phase 0" >&2; exit 2; }

# The list first, as Phase 0 derived it — so "the grep found nothing" is
# distinguishable from "there are no workflows".
git ls-tree --name-only "pr-<N>:.github/workflows/"; echo "list exit: $?"

# Row 1, by event name.
git grep -nE '^[[:space:]]*(pull_request_target|workflow_run|release):' pr-<N> -- '.github/workflows/'

# Row 1 again — a SEPARATE grep, not a longer alternation. A tag push is `push`
# carrying a `refs/tags/` ref, so the line above cannot see it at any width.
# Captured, not piped: a pipeline reports the LAST stage's status, and here that
# would turn a failed read into "no tag trigger". `1` is checked apart from `2`
# because grep exits 1 for *found nothing*, which in this table is an answer.
PUSH=$(git grep -nE -A2 '^[[:space:]]*push:' pr-<N> -- '.github/workflows/'); RC=$?
[ "$RC" -le 1 ] || { echo "the push grep failed ($RC) — underivable, not inert" >&2; exit 2; }
printf '%s\n' "$PUSH" | grep -E 'tags:'

# Row 2. A hit means this repo pins the old behaviour; no hit means it takes
# whatever the new default is, which is when a flipped default is a finding.
git grep -nE '^[[:space:]]*<the input the notes named>:' pr-<N> -- '.github/workflows/'

# Row 2, for an environment variable: the whole tree, any case. Its silence
# answers nothing until Row 3 exits 0.
git grep -niw '<the variable the notes named>' pr-<N> --

# Row 3: every job's runner, a matrix or a ternary on the repository resolved.
python3 "${SCRIPTS:?not in the handoff — re-run Phase 0}/runners.py" --ref pr-<N> --repo "$OWNER/$NAME"

# Row 4.
git grep -nE '^[[:space:]]*(permissions|persist-credentials):' pr-<N> -- '.github/workflows/'
```

**Every grep there exits 1 on no match**, which is the answer this table returns
most of the time — so do not chain them with `&&`, and read an empty result as
*inert here* rather than as a read that failed — except Row 2's environment
variable, whose silence waits on Row 3. Row 3's 1 is a finding: a runner that
is not GitHub-hosted, or one the tree cannot resolve, each named.

**When the question is whether a file exists at that ref, the command is
`git cat-file -e` — and `git ls-tree <ref> -- <path>` is the one that looks
right and is not.** Measured:

| Form | present | absent |
|---|---|---|
| `git cat-file -e <ref>:<path>` | exit 0 | exit **128** |
| `git ls-tree <ref>:<path>` | exit 0 | exit **128** |
| `git ls-tree <ref> -- <path>` | exit 0 | exit **0**, printing nothing |

The third cannot tell *absent* from *the lookup failed*, which is the distinction
this whole procedure is built on.

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
description says four, the notes say three" into which one is true — and it is
one grep, because `action.yml` has already named the file that runs:

```bash
# Fresh call: nothing survives one, so re-derive $SCRATCH and re-source Phase 0.
REPO=$(gh repo view --json nameWithOwner --jq .nameWithOwner); SCRATCH="${SCRATCH:-${TMPDIR:-/tmp}/dbaudit-${REPO/\//-}-<N>}"
. "$SCRATCH/phase0.env" || { echo "no handoff in $SCRATCH — re-run Phase 0" >&2; exit 2; }

# `runs.main` from the diff above — the bundled entry point, which is what the
# runner loads. Raw, NOT `--jq .content`: see below.
for R in <old-ref> <new-ref>; do
  gh api -H "Accept: application/vnd.github.raw" \
    "repos/<owner>/<action>/contents/<runs.main>?ref=$R" > "$SCRATCH/bundle-$R.js" \
    || { echo "cannot read the bundle at $R" >&2; exit 2; }
  printf '%s  ' "$R"; grep -c '<the term the two sources disagree about>' "$SCRATCH/bundle-$R.js"
done
```

**The `--jq .content` idiom three blocks up silently returns nothing here**, and
that is worth more than a footnote because it is the same failure that block's own
comment warns about, one file along. Above **1 MiB** the contents API describes
the file and declines to carry it: `200`, a real `size`, a real `sha`, a working
`download_url`, `content` an **empty string**, and the only notice is `encoding`
flipping from `base64` to `none` — the one field this idiom never reads. GitHub
documents it as working *"as normal"*, so it is a success path and not an error.

Every link then behaves correctly and the result is a clean bill: `gh` exits 0 on
the 200, `--jq .content` prints a bare newline, `base64 -d` accepts a lone newline
as valid base64 for zero bytes and exits 0, and `diff` on two empty files exits 0
saying nothing. Measured on `astral-sh/setup-uv` at v9.0.0 — `dist/setup/index.cjs`
is 3,966,481 bytes, `.content` came back length **0**, and the raw media type
returned all of it. The boundary is the binary megabyte, not 1,000,000: in
`python/cpython`, `Python/executor_cases.c.h` at 1,028,882 bytes still inlines and
`configure` at 1,074,405 does not.

**The `action.yml` block above keeps the decode on purpose**, because a manifest
cannot plausibly reach that size and the base64 round-trip there buys a second
checked failure — the two `||` lines that make an unreadable ref loud. The rule is
about the *artifact*, not the endpoint: reach for the raw media type whenever the
path could be a build output. `scripts/precommit.py` already did, and its
docstring already gave this reason, which is where the answer was sitting while
this file went without it.

And it answers the question the notes could not. Measured across the same bump:

| | `isTagPush` in the bundle |
|---|---|
| v9.0.0 | **0 occurrences** |
| v10.0.1 | 2, one of them `isTagPush = eventName === "push" && process.env.GITHUB_REF?.startsWith("refs/tags/")` |

Zero-to-two across a bump is a falsifiable answer to *which source is right*,
arrived at in one call, on the artifact that actually runs rather than on prose
about it. Read the `src/` file too where the bundle is minified past reading —
it ships at the same ref — but the bundle is the authority, because a repo can
carry source that was never rebuilt into it.

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
  release dates, not just the line you are on — `gh api
  'repos/<owner>/<action>/releases?per_page=15' --jq '.[] | "\(.tag_name)\t\(.published_at)"'`
  is the whole check, and on `actions/checkout` it puts v7.0.1, v6.1.0, v5.1.0,
  v4.4.0, v3.7.0 and v2.8.0 in the first six lines, 33 minutes apart.
- **Version-coupled actions must move together.** `upload-artifact` and
  `download-artifact` ship majors in lockstep — the v7/v8 pair went out eight
  seconds apart. If the bump moves one half, check the sibling's pin in the same
  workflow and say whether the repo is now split across generations.

## Phase 5 — Independent reproduction

There is nothing to install and no way to execute an action outside GitHub's
runners, so local reproduction is unavailable. The substitute is **evidence that
this pin has already run**: ask the workflow the bump changed.

```bash
# Fresh call: nothing survives one, so re-derive $SCRATCH and re-source Phase 0.
REPO=$(gh repo view --json nameWithOwner --jq .nameWithOwner); SCRATCH="${SCRATCH:-${TMPDIR:-/tmp}/dbaudit-${REPO/\//-}-<N>}"
. "$SCRATCH/phase0.env" || { echo "no handoff in $SCRATCH — re-run Phase 0" >&2; exit 2; }

# `<workflow>` is the same derived list Phase 0 and Phase 6 use, narrowed to what
# this PR touched. Derive it; do not guess a filename.
git diff --name-only "$BASE_SHA...pr-<N>" -- '.github/workflows/'
echo "changed-workflow list exit: $?"

# The date to read that history against. `mergedAt` is null while the PR is
# open, and that is an answer: nothing in the list below can have run this pin.
gh pr view <N> --json state,createdAt,mergedAt \
  --jq '"state=\(.state)\tcreated=\(.createdAt)\tmerged=\(.mergedAt // "null — still open")"'

gh run list --workflow <workflow> --limit 10 \
  --json conclusion,headBranch,createdAt,displayTitle \
  --jq '.[] | "\(.conclusion)\t\(.headBranch)\t\(.createdAt)\t\(.displayTitle)"'
```

**`headBranch` was always in that query and never in its output** — asked for and
thrown away — and it is the column that says whether a run is evidence at all. A
green run on the default branch after the merge exercised the new pin; a green run
on the bot's own branch exercised it too, and one on any other branch did not.
Without the column every row looks alike.

Read it against the merge date, and be strict about what it proves. Runs *after*
the bump landed exercised the new pin; runs before it did not, and a green history
that predates the merge says nothing at all about the version being adopted.

**`mergedAt` is the merge date, and `null` is the common case.** Measured: an open
PR answers `state=OPEN merged=null`; a merged one answers with an ISO-8601 stamp
and the merge commit. Where it is null there is no "since the bump landed" to
read — every run in that list predates the pin, whatever their conclusions say,
and the honest row is the second or third below rather than the first.

| Situation | What you can honestly report |
|---|---|
| the workflow ran green on this pin since the bump landed | reproduced — the strongest evidence available for an actions bump |
| the workflow has not run since | **not reproduced.** State it; do not let Phase 6's green stand in for it |
| the workflow is not PR-triggered and the PR is open | reproduction is impossible before merge. That is a property of the change, and it belongs in the report |

Observed: a bump to `actions/upload-artifact` in a release-only workflow, merged
alongside a `download-artifact` pin two majors behind. Nothing in the PR could
show whether the pair still interoperated — seven green release runs over the
following month did.
