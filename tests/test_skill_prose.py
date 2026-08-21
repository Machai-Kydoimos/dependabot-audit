"""Consistency tests for SKILL.md, the half the other suites cannot reach.

Four defects have now shipped in the prose and nowhere else: Phase 6 improvised a
check-name parse; Phase 1 referenced a branch that Phase 5 created; Phase 4 ran
against a worktree that Phase 5 created; and Phase 6 filled a placeholder in with
one specific repo's required check names. Two of them are the same
forward-reference shape. Each was found by a human reading in order, and each was
answered by editing the prose — which is the same lever that produced them.

What is checkable here is narrow and still worth having: whether the document is
consistent **with itself**. Two gaps stay open around it, and they are different
gaps — a green run here is evidence of neither.

Whether the model *follows* the phases is behavioral and belongs in
`claude plugin eval`, which is unavailable on this account — the subcommand
prints a full `--help` and then refuses on stderr with an empty stdout, so
reading the help is not checking availability and neither is grepping the output.

Whether the prose is **true** is not checkable at all. Consistency is not
correctness: every case below can pass on a phase that names a real endpoint,
consumes a properly-derived Phase 0 output, and asks it the wrong question. That
shipped in 0.10.0 — six passing guards on a Phase 6 whose attribution query, run
against the one PR the finding came from, produced a false Hold. One of those
guards asserted the property that *was* the defect, because it was written from
the fix rather than from a measurement. Replaying the PR is what caught it, and
nothing in this file could have.

    a phase may not consume what a later phase creates
    the required-context list must be read from a Phase 0 artifact, never typed
    every path the prose names must exist
    the frontmatter key that withholds tools must be the one that works

Every one of those corresponds to a defect that shipped.

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import ast
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "skills/dependabot-audit"
SKILL = PLUGIN / "SKILL.md"

# Names the skill is entitled to use without defining: the harness supplies them.
# Supplied by the environment, never by a phase, so the forward-reference guard
# must not read them as outputs one phase owes another. `TMPDIR` joined them in
# 0.26.0: Phase 0 derives `$SCRATCH` under `${TMPDIR:-/tmp}` so the path is the
# same on every call, and macOS sets TMPDIR where Linux leaves it unset.
EXTERNAL = {"CLAUDE_PLUGIN_ROOT", "HOME", "PATH", "IFS", "TMPDIR"}

PHASE_HEADING = re.compile(r"^## Phase (\d+)\b", re.MULTILINE)
FENCE = re.compile(r"^```(\w*)\n(.*?)^```", re.MULTILINE | re.DOTALL)

# Shell only. jq and awk variables are lowercase or numeric ($req, $all, $2) and
# are deliberately outside this pattern — they are scoped to the line they appear
# on, not to the phase.
ASSIGNED = re.compile(r"(?:^|[\s(])([A-Z_][A-Z0-9_]*)=")
USED = re.compile(r"\$\{?([A-Z_][A-Z0-9_]*)\}?")

# `$SCRATCH/...` paths handed from one phase to another. `<` and `>` are in the
# class because `$SCRATCH/pr-<N>` is one of them.
SCRATCH_PATH = re.compile(r"\$SCRATCH/[A-Za-z0-9_.<>-]+")
MADE_BY_REDIRECT = re.compile(r">\s*\"?(\$SCRATCH/[A-Za-z0-9_.<>-]+)")
MADE_BY_WORKTREE = re.compile(
    # Flags may sit between `add` and the path (`--detach`, `-b <name>`).
    r"git worktree add\s+(?:-\S+\s+)*\"?(\$SCRATCH/[A-Za-z0-9_.<>-]+)"
)

# The fetched branch. Not preceded by a word character or a slash, so
# `$SCRATCH/pr-<N>` does not also read as a use of the ref.
REF_USED = re.compile(r"(?<![\w/])pr-<N>")
REF_MADE = re.compile(r"git fetch \S+ \"pull/<N>/head:pr-<N>\"")

# A path under the plugin root, as the prose names it when handing off to a
# script. Module-level because both the path-existence guard and `reachable()`
# read it, and two copies would drift.
PLUGIN_PATH = re.compile(r"\$\{CLAUDE_PLUGIN_ROOT\}/([A-Za-z0-9_./-]+)")

# A reference the prose hands off to, e.g. `references/actions.md`.
REFERENCE = re.compile(r"references/([a-z0-9_-]+\.md)")


def phases(text: str) -> list[tuple[int, str]]:
    """(phase number, body) for each `## Phase N`, plus the preamble as -1."""
    marks = list(PHASE_HEADING.finditer(text))
    out = [(-1, text[: marks[0].start()])] if marks else []
    for i, mark in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        out.append((int(mark.group(1)), text[mark.start() : end]))
    return out


def bash_blocks(body: str) -> list[str]:
    return [code for lang, code in FENCE.findall(body) if lang == "bash"]


def tables(body: str) -> list[list[str]]:
    """Contiguous runs of markdown table rows, one list per table.

    Phase 0's guidance is carried as much by its tables as by its prose, and a
    guard that scans the whole phase body cannot tell "this word appears
    somewhere" from "this word is a row in the table that decides the thing".
    """
    found: list[list[str]] = []
    current: list[str] = []
    for line in body.splitlines():
        if line.startswith("|"):
            current.append(line)
        elif current:
            found.append(current)
            current = []
    if current:
        found.append(current)
    return found


def _code_only(source: str) -> str:
    """A Python module's executable text, with comments and docstrings removed.

    An AST round-trip drops comments for free; docstrings have to be taken off
    each scope explicitly. Both matter for the same reason: a guard asserting
    that a phase *asks GitHub which checks are required* must not be satisfied by
    a docstring that merely says so.
    """
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        first = node.body[0] if node.body else None
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            node.body = node.body[1:] or [ast.Pass()]
    return ast.unparse(tree)


def first_phase(hits: dict[str, list[int]], name: str) -> int:
    return min(hits[name])


class SkillHarness(unittest.TestCase):
    text: str
    phases: list[tuple[int, str]]
    shell: dict[int, str]

    @classmethod
    def setUpClass(cls):
        cls.text = SKILL.read_text(encoding="utf-8")
        cls.phases = phases(cls.text)
        # phase number -> the shell in it, concatenated
        cls.shell = {n: "\n".join(bash_blocks(body)) for n, body in cls.phases}

    def reachable(self, number: int) -> str:
        """What Phase N actually *runs*: its shell, plus every script it invokes.

        A guard that scans only the phase's own bash silently retires itself the
        moment the mechanism it protects moves into a script — and mechanising a
        phase is what this repo says to do when a trap keeps recurring, so that
        is not a hypothetical migration. Phase 6's query became `ci_state.py` and
        six guards would have gone green on an empty string.

        **Executable material only, never the prose.** The first version of this
        concatenated the phase body and immediately failed the `/protection`
        guard — on Phase 0's paragraph explaining why never to call that endpoint.
        A negative assertion over prose cannot tell a warning from an instruction,
        so it fires on the document that gets it right. These guards ask what a
        phase *calls*; the prose-content guards read the body directly and say so.

        One hop, deliberately, and not the whole repo: a guard that matches
        anything anywhere stops discriminating, which is how the `underivable`
        assertion once passed against the very prose it was written for.

        Scripts contribute their **code**, never their docstrings or comments —
        `_code_only` strips both. Mutation-checked and needed: `ci_state.py`'s
        module docstring names `isRequired`, `totalCount` and `check-runs` while
        explaining them, so deleting all three from the actual query left every
        guard green. A rule must not be satisfiable by a comment claiming it.
        """
        parts = [self.shell[number]]
        for name, section in self._handoffs(number):
            del name
            parts.extend(bash_blocks(section))
        for rel in PLUGIN_PATH.findall(dict(self.phases)[number]):
            path = ROOT / rel
            if path.suffix == ".py" and path.exists():
                parts.append(_code_only(path.read_text(encoding="utf-8")))
        return "\n".join(parts)

    def _handoffs(self, number: int) -> list[tuple[str, str]]:
        """(filename, that file's `## Phase N` section) for each reference named.

        The ecosystem references are sectioned by phase precisely so this works:
        a phase hands off to `uv-lock.md` and `actions.md`, and the guard follows
        it to the matching section rather than to the whole file. Matching the
        whole file would let Phase 1's guard be satisfied by Phase 5's prose,
        which is the kind of slack that stops a guard discriminating.

        `report-template.md` carries no `## Phase N` headings, so it contributes
        nothing here — deliberately. It is cross-cutting, and a guard that swept it
        would match almost anything. A general `traps.md` used to sit in the same
        category; 0.17.0 retired it after measuring that no run ever fetched it.
        """
        found: list[tuple[str, str]] = []
        for name in REFERENCE.findall(dict(self.phases)[number]):
            ref = PLUGIN / "references" / name
            if not ref.exists():
                continue
            for n, section in phases(ref.read_text(encoding="utf-8")):
                if n == number:
                    found.append((name, section))
        return found

    def material(self, number: int) -> str:
        """Everything Phase N *says*: its body plus each reference's Phase N.

        The counterpart to `reachable`. Guards about what a phase asserts read
        this; guards about what it calls read `reachable`. Keeping them apart is
        what stops a negative assertion firing on a paragraph that warns against
        the very thing it forbids.
        """
        parts = [dict(self.phases)[number]]
        parts.extend(section for _, section in self._handoffs(number))
        return "\n".join(parts)

    def _scan(self, pattern):
        """{match -> [phase numbers it appears in]}, in phase order."""
        found: dict[str, list[int]] = {}
        for number, code in sorted(self.shell.items()):
            for hit in pattern.findall(code):
                found.setdefault(hit, []).append(number)
        return found


class TestNoPhaseConsumesALaterPhase(SkillHarness):
    """The defect class that has shipped three times.

    Read in order — which is how the skill is meant to be read — a phase that
    uses something a later phase establishes cannot run. The failure is not
    silent, but the likely recovery is the model improvising, which is exactly
    what the phase ordering exists to prevent.
    """

    def test_no_variable_is_used_before_it_is_assigned(self):
        assigned = self._scan(ASSIGNED)
        used = self._scan(USED)
        for name, phases_using in used.items():
            if name in EXTERNAL:
                continue
            self.assertIn(name, assigned, f"${name} is used but never assigned")
            self.assertLessEqual(
                first_phase(assigned, name),
                min(phases_using),
                f"${name} is used in Phase {min(phases_using)} but first assigned in "
                f"Phase {first_phase(assigned, name)}",
            )

    def test_no_scratch_artifact_is_used_before_it_is_created(self):
        """The Phase 4 / Phase 5 worktree defect, and anything shaped like it."""
        made: dict[str, list[int]] = {}
        for number, code in sorted(self.shell.items()):
            for pattern in (MADE_BY_REDIRECT, MADE_BY_WORKTREE):
                for path in pattern.findall(code):
                    made.setdefault(path, []).append(number)
        for path, phases_using in self._scan(SCRATCH_PATH).items():
            self.assertIn(path, made, f"{path} is used but no phase creates it")
            self.assertLessEqual(
                first_phase(made, path),
                min(phases_using),
                f"{path} is used in Phase {min(phases_using)} but first created in "
                f"Phase {first_phase(made, path)}",
            )

    def test_the_pr_ref_is_fetched_before_any_phase_reads_it(self):
        """Phase 1 once read `pr-<N>` at a point where Phase 5 created it, so a
        literal reading audited the base branch instead of the PR."""
        fetched = [n for n, code in self.shell.items() if REF_MADE.search(code)]
        self.assertTrue(fetched, "no phase fetches pull/<N>/head:pr-<N>")
        reads = [n for n, code in self.shell.items() if REF_USED.search(code)]
        self.assertLessEqual(
            min(fetched),
            min(reads),
            f"pr-<N> is read in Phase {min(reads)} but fetched in Phase {min(fetched)}",
        )


class TestNoRepoSpecificLiterals(SkillHarness):
    """Phase 6 shipped one repo's required check names in a runnable snippet.

    Reused literally against a repo whose checks are named anything else, the
    match yields nothing for every context — which is indistinguishable from "no
    required checks configured". Phase 6 then verifies nothing while the report
    asserts CI was checked. Counting rows does not catch it, because the count
    still agrees with the wrong list.
    """

    # Two or more quoted literals inside brackets: a hardcoded list.
    LITERAL_LIST = re.compile(r"\[\s*\"[^\"]+\"\s*,\s*\"[^\"]+\"")

    def test_no_bash_block_hardcodes_a_list_of_names(self):
        for number, code in sorted(self.shell.items()):
            for hit in self.LITERAL_LIST.findall(code):
                # A visible placeholder is the convention everywhere else in the
                # file — `<N>`, `<ci>.yml`, `$DEFAULT` — and is fine here too.
                self.assertIn(
                    "<",
                    hit,
                    f"Phase {number} hardcodes a list where a placeholder or a "
                    f"Phase 0 artifact belongs: {hit}",
                )

    def test_phase_6_asks_the_api_which_contexts_are_required(self):
        """The structural half: the list is never authored, at any tier.

        This replaces an assertion that Phase 0 wrote `$SCRATCH/required.txt` and
        Phase 6 read it. That file is gone — it came from an `admin`-only call
        whose failure `gh` writes to *stdout*, so on a repo the auditor does not
        administer it held an error body that read as "no required checks". The
        property being guarded is unchanged: the required set comes from GitHub,
        not from the model. `isRequired` is that answer, and it is readable at
        `pull`.
        """
        self.assertIn(
            "isRequired",
            self.reachable(6),
            "Phase 6 must ask the API which contexts are required, not derive a list",
        )

    def test_no_phase_reads_required_checks_from_branch_protection(self):
        """The `admin`-only endpoint 404s without admin, and the body is stdout.

        A bare 404 is indistinguishable from an unprotected branch, so a redirect
        of this call produces a well-formed artifact asserting the opposite of the
        truth. Verified against a repo enforcing three required checks.
        """
        for number, _ in self.phases:
            self.assertNotIn(
                "/protection",
                self.reachable(number),
                f"Phase {number} calls branch protection for the required checks; "
                f"it needs admin and fails into a plausible value without it",
            )

    def test_phase_0_proves_the_merge_base_is_the_branch_point(self):
        """`git merge-base` always returns a commit, even a badly wrong one.

        When the base branch is force-pushed under an open PR the merge base
        falls back to a much older shared ancestor, and every later phase
        consumes it as fact: Phase 1's gate fires on files the bump never
        touched, and Phase 4 measures a tree the PR would never land on.
        Observed on a two-file bump whose merge-base diff was 14 files.

        `gh pr view --json files` is not a cross-check — GitHub computes the PR's
        file list from the merge base too, and agrees with the wrong answer.
        """
        reachable = self.reachable(0)
        self.assertIn(
            "base_ref_force_pushed",
            reachable,
            "Phase 0 must check whether the base branch was rewritten; a merge "
            "base cannot tell you on its own",
        )
        self.assertRegex(
            reachable,
            r"pulls/\{number\}/commits|pulls/<N>/commits|\$BASE_SHA\.\.pr-<N>",
            "Phase 0 must read the commits above the base; a genuine bot PR is "
            "one commit by the bot, and that is the corroborating signal",
        )
        self.assertIn(
            "parents",
            reachable,
            "and it must count parents: a two-parent commit is someone merging "
            "the base INTO the branch, where the merge base is still correct and "
            "the substitutions must not fire",
        )

    def test_a_rewritten_base_falls_back_to_the_bots_own_commit(self):
        """The substitute has to be named, or the gate fires on a stale base.

        Halting is the wrong response to a rewritten base: it stops the audit for
        a reason that is not true, and reads in the report exactly like a bump
        that reaches into source.
        """
        self.assertIn(
            "pr-<N>^..pr-<N>",
            self.text,
            "the fallback diff for a rewritten base must be the bot's own commit",
        )

    def test_no_phase_reads_required_checks_from_the_rules_endpoint(self):
        """Readable without admin, which is exactly what makes it a trap.

        `/rules/branches/<b>` reports rulesets only. Classic branch protection is
        invisible to it, so an empty result manufactures a false "nothing
        enforced" finding on a repo that enforces plenty.
        """
        for number, _ in self.phases:
            self.assertNotIn(
                "/rules/branches",
                self.reachable(number),
                f"Phase {number} reads the rules endpoint, which cannot see "
                f"classic branch protection and returns [] on a protected branch",
            )


class TestEveryPhaseCarriesBothEcosystems(SkillHarness):
    """A phase written for one ecosystem silently assumes it for the other.

    The supported surface is `uv.lock` and GitHub Actions. Phases 1-6 all do
    ecosystem-specific work, and the failure mode is not that the actions method
    is wrong — it is that the phase reads as though only lockfiles exist, so the
    model either skips it or invents a method.

    "Not applicable" is itself an assertion, and one shipped false: three places
    in this repo stated that GitHub Actions has no vulnerability database. GHSA
    carries an `actions` ecosystem, and a Phase 3 that believed the claim skipped
    a real check. Hence a question per phase and a method per ecosystem, rather
    than a phase that applies to one and is marked N/A for the other.
    """

    WORKING_PHASES = (1, 2, 3, 4, 5, 6)

    def test_no_phase_is_written_for_only_one_ecosystem(self):
        bodies = dict(self.phases)
        for n in self.WORKING_PHASES:
            self.assertIn(
                "actions",
                bodies[n].lower(),
                f"Phase {n} does ecosystem-specific work but never mentions "
                f"actions, so it reads as though only lockfiles exist",
            )

    def test_phase_3_names_an_advisory_source_for_actions(self):
        """The claim that there is none was false, and skipped a real check."""
        self.assertIn(
            "ecosystem=actions",
            self.reachable(3),
            "Phase 3 must name the GHSA advisory source for actions",
        )

    def test_phase_3_records_the_osv_version_trap_for_actions(self):
        """The obvious port of the uv.lock query reports clean on a compromise.

        OSV carries the actions advisories but its entries have no usable version
        ranges, so a version-qualified query returns empty. Measured against
        tj-actions/changed-files: package-only returns 2, every version-qualified
        form returns 0 — including 0.0.0, which a working range check would match.
        """
        phase3 = dict(self.phases)[3].lower()
        self.assertIn(
            "tj-actions/changed-files",
            phase3,
            "Phase 3 must keep the measured case behind the OSV version trap; "
            "without it the warning reads as caution rather than a result",
        )


class TestEveryHandoffLands(SkillHarness):
    """A phase that points at a reference section which does not exist.

    The ecosystem method moved out of `SKILL.md` in 0.15.0, which buys back
    roughly a third of the tokens a run loads — and converts every one of those
    methods from *text the model already has* into *text the model must go and
    fetch*. That trade is only sound while the pointer resolves.

    The failure is quiet in the worst way: a phase reading "see
    `references/actions.md` § Phase 4" against a file with no Phase 4 section
    leaves the model with a question, a promise, and nothing to answer it with —
    and the likeliest recovery is improvising a method, which is the exact
    failure the ecosystem boundary exists to prevent.

    Both halves are asserted because they fail independently: renaming a section
    breaks the target, and dropping a mention breaks the pointer.
    """

    # Phases doing ecosystem-specific work. Phase 2's uv answer comes out of the
    # Phase 1 script rather than a section of its own, so it is not on this list;
    # Phase 6 is ecosystem-independent by construction.
    SPLIT_PHASES = (1, 3, 4, 5)
    ECOSYSTEMS = ("uv-lock.md", "actions.md")

    def test_every_split_phase_names_both_ecosystem_references(self):
        for number in self.SPLIT_PHASES:
            named = set(REFERENCE.findall(dict(self.phases)[number]))
            for ecosystem in self.ECOSYSTEMS:
                self.assertIn(
                    ecosystem,
                    named,
                    f"Phase {number} does ecosystem-specific work and never hands "
                    f"off to {ecosystem}, so that ecosystem's method is unreachable "
                    f"from the phase that needs it",
                )

    def test_every_named_reference_has_the_section_it_is_pointed_at(self):
        for number in self.SPLIT_PHASES:
            landed = {name for name, _ in self._handoffs(number)}
            for ecosystem in self.ECOSYSTEMS:
                self.assertIn(
                    ecosystem,
                    landed,
                    f"Phase {number} points at {ecosystem} but that file has no "
                    f"`## Phase {number}` section to land in",
                )

    def test_the_retired_reference_is_named_nowhere(self):
        """`ecosystems.md` was split, not kept. A stale pointer reads as content."""
        for doc in [SKILL, *sorted((PLUGIN / "references").glob("*.md"))]:
            self.assertNotIn(
                "ecosystems.md",
                doc.read_text(encoding="utf-8"),
                f"{doc.name} still points at the retired ecosystems.md",
            )


class TestTheMergeBaseSurvivesThePRHavingLanded(SkillHarness):
    """`$BASE_SHA` collapses onto `$HEAD_SHA` the moment the PR merges.

    A merged PR's head is an ancestor of the default branch, so the merge base of
    the two is the head itself. Nothing raises: Phase 1's scope diff comes back
    empty, Phase 4's `base-<N>` worktree is the PR's own tree — reinstating the
    defect 0.6.0 exists to prevent — and Phase 6 cross-checks the head against
    itself, which labels every red check pre-existing.

    Measured on `cli/cli`'s merged bumps #14147, #14091, #13981 and #14049:
    `git merge-base trunk pr-<N>` returns the head for all four, and the scope
    diff is 0 files where GitHub reports 4, 2, 3 and 2. Taken from `baseRefOid`
    it is those four numbers, and on open PR #14148 both forms return the same
    commit — so the correction is a no-op wherever the old form worked.

    Auditing a merged PR is supported, not an edge case: Phase 6 has a row for
    it, the ecosystem references have a paragraph, and CONTRIBUTING's gate asks for
    one before every method change.
    """

    def test_the_base_commit_comes_from_the_pr_rather_than_the_local_branch(self):
        reachable = self.reachable(0)
        self.assertIn(
            "merge_base_commit",
            reachable,
            "Phase 0 must take the base commit from GitHub's compare endpoint — "
            "it is right whether or not the PR has landed, where a merge base "
            "against $DEFAULT is the PR's own head once it has. Asserted on the "
            "field, not on a name that also appears as a display label.",
        )
        self.assertNotRegex(
            reachable,
            r"git merge-base",
            "no local merge base at all: the form that collapses is only "
            "distinguishable from the form that does not by which ref is on the "
            "left, which is exactly the distinction that shipped wrong",
        )


class TestTheActionsScopeGateKeysOnTheLine(SkillHarness):
    """Phase 1's gate said "a single workflow file". Bumps are not.

    An action is pinned in every workflow that uses it, so an ordinary bump
    rewrites all of them, and a grouped bump rewrites several actions at once.
    Measured on `cli/cli`, all three merged: #14091 two files, #13981 three,
    #14147 four — and every changed line across them is a `uses:` line or its
    trailing version comment.

    A gate phrased as a file count refuses the ordinary case and stops the audit
    before Phase 4, and its report reads exactly like a bump reaching into
    source. The invariant is the kind of line, which holds at any file count.
    """

    def test_the_gate_names_the_kind_of_line_rather_than_a_file_count(self):
        self.assertIn(
            "uses:",
            dict(self.phases)[1],
            "Phase 1's gate must express the actions scope as the lines the diff "
            "may touch; a count of files refuses every multi-workflow bump",
        )


class TestCurrencySeparatesLagFromAHold(SkillHarness):
    """Dependabot now withholds a new release for three days, by default.

    Phase 2's test for lag was the publish timestamp: a version published before
    the PR opened is one the bot had not seen yet. Since 2026-07-14 that
    inference is false by default — version updates wait until a release is three
    days old, no `cooldown:` block required, and nothing in the PR says so.
    Security *updates* are exempt from the wait; a version update whose changelog
    carries a privately disclosed fix is not, and that is the case Phase 2 exists
    for.

    Measured on `cli/cli` #13996, opened 2026-07-28T14:06Z proposing
    `gh-aw-actions/setup-cli` 0.83.2 -> 0.83.3, under `cooldown: default-days: 3`:
    upstream 0.83.4 had been published 2026-07-27T09:07Z, 29 hours earlier, and
    the bot proposed it itself two days later in #14018. Read as lag, that gap
    buys a follow-up branch that hand-overrides the hold and lands the release
    the bot is deliberately waiting on.
    """

    def test_phase_2_knows_a_gap_can_be_a_configured_hold(self):
        self.assertIn(
            "cooldown",
            dict(self.phases)[2].lower(),
            "Phase 2 must rule out the cooldown before calling a gap lag; the "
            "publish timestamp stopped separating the two on 2026-07-14",
        )


class TestARedCheckIsAttributedBeforeItCarriesTheVerdict(SkillHarness):
    """Phase 6 reported conclusions and never asked whether the bump caused them.

    Observed on `BIRSAx2/mdcat` #6: `test (ubuntu-latest)` red beside two green
    siblings, which reads as a bump breaking one platform. It was a rustdoc
    intra-doc-link error under `#[deny(warnings)]`, failing identically on the
    base. A Hold driven by that row would have been correct by accident and
    unfalsifiable — every cell true, the causal claim never established.

    The same family as the rewritten base and the hand-joined required list: a
    row that is individually accurate and collectively misleading. It is also the
    direction that costs least to be wrong in, so it draws the least scrutiny — a
    false Hold looks conservative.
    """

    def test_the_comparison_point_is_the_commit_the_bot_branched_from(self):
        """`$BASE_SHA` is the wrong input, and the live run is what proved it.

        The first version of this asserted Phase 6 read `$BASE_SHA`. Run against
        the PR the finding came from, that is wrong in the direction the whole
        section exists to prevent. `mdcat` #6 carries a *human* commit under the
        bot's, so its four candidate comparison points disagree: the bot's parent
        is `failure` (pre-existing, the answer), the merge base has no such check
        at all, and the base branch's tip is `success` — which would have
        produced the false Hold.

        `pr-<N>^` is `$BASE_SHA` for a genuine one-commit bot PR, so this costs
        nothing in the ordinary case and is right in the case that is not.

        Asserted on what Phase 6 *hands* the comparison, not on the string
        appearing somewhere reachable. Mutation-checked and needed: `ci_state.py`
        spells `pr-<N>^` in the basis text it prints, so a phase that derived the
        parent wrongly and described it correctly passed the looser form. The
        same trap as the `underivable` guard, one artifact along.
        """
        shell = self.shell[6]
        self.assertRegex(
            shell,
            r"pr-<N>\^",
            "Phase 6 must derive the bot's own parent; the merge base attributes "
            "to the bump whatever happened on the branch beneath it",
        )
        self.assertNotRegex(
            shell,
            r"--parent\s+\"?\$BASE_SHA",
            "the merge base is the cross-check, never the comparison point — that "
            "substitution is defect #25, and it produced a false Hold on the one "
            "PR it had been run against",
        )

    def test_the_base_query_reads_check_runs_not_the_workflow_list(self):
        """`gh run list --json name` answers a different question.

        It returns the *workflow* name — one row reading `CI` — while the rollup
        contexts Phase 6 reads are job names like `test (ubuntu-latest)`. Matching
        one against the other yields nothing for every matrix job, and an empty
        result reads as "no run at the base", which marks it underivable. So the
        obvious query fails in precisely the direction this section exists to
        correct. Measured on a repo whose five contexts are `Test (Python 3.11)`
        through `Lint & type-check`: `gh run list --json name` returns a single
        `CI`; `commits/<sha>/check-runs` returns all five by context name.
        """
        self.assertIn(
            "check-runs",
            self.reachable(6),
            "the base conclusions must come from the endpoint keyed by check name",
        )
        self.assertNotRegex(
            self.reachable(6),
            r"gh run list[^\n]*\$BASE_SHA",
            "gh run list reports workflow names, so a per-check match against it is "
            "empty for every matrix job",
        )

    def test_a_base_with_no_run_is_underivable_rather_than_attributable(self):
        """Phase 0's third state, in the phase most able to lose it.

        The base may predate the workflow, or its run may have aged out. Both are
        "could not check", and collapsing them into "not pre-existing" hands the
        red row back to the bump by default.

        Asserted on the phrase rather than on "underivable" alone: that word was
        already in Phase 6 for `mergeStateStatus: UNKNOWN`, so the looser check
        passed against the prose that had this defect.
        """
        phase6 = dict(self.phases)[6].lower()
        self.assertIn(
            "no run at the base",
            phase6,
            "Phase 6 must name the case where the base has no run to compare against",
        )
        self.assertIn("underivable", phase6, "and give it Phase 0's third state")


class TestTheAttributableLabelIsHedgedLikeTheOthers(SkillHarness):
    """The only label that produces a Hold said the least about its evidence.

    #25 gave Phase 6 the base comparison and three labels. `PRE-EXISTING` ships
    with a caveat, `underivable` gets a paragraph, and `ATTRIBUTABLE` was a bare
    assertion. Observed on `fpga-board-sim` #332, `actions/checkout` 7.0.0 ->
    7.0.1: `Board-data drift` red at the head, green at `pr-332^`, every cell
    true — and the failing job re-syncs generated board sources from **other
    people's repositories** through the API and requires a zero diff. The cause
    was an upstream ref moving, fixed in that repo's own #335 and #336.

    `pr-332^` is from 2026-07-23T20:07:40Z and the head from
    2026-07-27T13:09:25Z: **3d 17h**. `PRE-EXISTING` survives that gap — if the
    check was already red, the bump is exonerated regardless of what else moved.
    `ATTRIBUTABLE` does not: green-then-red across 3d 17h is consistent with the
    bump, with an upstream change, with a runner image roll, or with a flake, and
    the comparison distinguishes none of them. The two labels are not equally
    strong evidence and were presented as though they were.

    This is #25's own argument pointed the other way, and no Hold fired only
    because `Board-data drift` is not a required check. Had the repo marked it
    required, Phase 7's table would have Held a security backport released across
    six majors inside 34 minutes, on an upstream board-data change. The guard was
    the audited repo's branch-protection configuration, not the procedure.
    """

    def _attribution_row(self) -> str:
        for table in tables(dict(self.phases)[6]):
            for row in table:
                if "**attributable**" in row.lower():
                    return row
        self.fail("Phase 6 has no attributable row")

    def test_the_row_does_not_read_as_a_licence_to_hold(self):
        row = self._attribution_row().lower()
        self.assertIn(
            "both commits",
            row,
            "the pre-existing row already tells the reader what it must not do; "
            "the attributable row is the one that can carry a Hold and said "
            "least — it has to name the read that would settle causation",
        )

    def test_the_interval_the_comparison_spans_reaches_the_reader(self):
        """Minutes apart on a one-commit bot PR is a strong claim; most of a week
        is not. Both print the same sentence without it."""
        self.assertIn(
            "interval",
            dict(self.phases)[6].lower(),
            "Phase 6 must say that the interval qualifies the claim",
        )
        self.assertIn(
            "CONSISTENT WITH",
            self.reachable(6),
            "and the script has to print the hedge, or it lives only where the "
            "reader of the output never sees it",
        )

    def test_phase_7s_hold_row_does_not_assert_causation_by_itself(self):
        for table in tables(dict(self.phases)[7]):
            for row in table:
                if "attributable" in row.lower() and "hold" in row.lower():
                    self.assertIn(
                        "both commits",
                        row.lower(),
                        "an evidence table saying a check is red — true — while "
                        "implying a cause it never established is the same "
                        "family as the rewritten base and the hand-joined "
                        "required list, and this is the row that acts on it",
                    )
                    return
        self.fail("Phase 7 has no verdict row for an attributable red check")


class TestPhase5SaysWhatItActuallyExercised(SkillHarness):
    """`--locked` checks every fork; the install materialises one of them.

    `uv sync --locked` asserts the whole lockfile is consistent with the
    manifest, across every `resolution-markers` fork, and Phase 1 verifies every
    fork's artifacts against the registry. The install then covers only the
    resolution matching the interpreter present — so a green row on 3.14 says
    nothing about whether the 3.11 fork's artifacts fetch or install, and nothing
    in the report distinguished the two.

    Phase 5 already insists the row name *which install* ran. The same rule was
    not applied to the interpreter, where it matters more.
    """

    def test_the_interpreter_is_read_from_the_synced_environment(self):
        """The auditor's own `python3` need not be the one uv chose."""
        self.assertIn(
            "uv run python -V",
            self.reachable(5),
            "Phase 5 must record the interpreter that produced the row, from inside "
            "the environment rather than from the shell that ran the audit",
        )

    def test_the_forks_that_were_only_verified_are_named(self):
        phase5 = self.material(5).lower()
        self.assertIn(
            "resolution-markers",
            phase5,
            "Phase 5 must say that a lockfile can fork, or the single install reads "
            "as covering every pin",
        )
        self.assertIn(
            "only verified",
            phase5,
            "the asymmetry is the finding: every fork verified, one installed. "
            "Naming the mechanism without it still leaves the row overstating",
        )

    def test_the_quoted_script_output_is_what_the_script_prints(self):
        """The prose points the reader at a line `audit.py` emits.

        A cross-artifact check, in the same family as "every path the prose names
        must exist": reword the script and the quotation goes stale, sending the
        reader to look for a line that is no longer there.
        """
        quoted = "forked packages: every pin verified, one of them installed"
        self.assertIn(quoted.split(":")[0], self.material(5))
        self.assertIn(
            quoted,
            (PLUGIN / "scripts/audit.py").read_text(encoding="utf-8"),
            "SKILL.md quotes a line audit.py no longer prints",
        )


class TestTheScopeGateIsAboutTheBumpNotTheBranch(SkillHarness):
    """Phase 1 gated on the union of every commit above the base.

    A bot PR's branch is not always all bot. On `fpga-board-sim` #334 a
    maintainer landed `style: reformat docs for ruff 0.16's markdown code-fence
    formatting` on the bot's own branch so a required check would pass again —
    correct, and necessary. Phase 0 read the authorship of all three commits
    above the base and printed `HUMAN` against two of them; Phase 1 consumed
    none of that, took its diff from the merge base, saw eight files, and Held.

    An output derived early and then dropped — the inverse of the
    forward-reference defect this suite was built for.

    The cost is not only the verdict. The gate stops the audit **before Phase
    4**, and Phase 4 was the phase that would have measured that bump: ruff
    0.15.22 -> 0.16.0, this plugin's founding Phase 4 observation, occurring for
    real. The base worktree was built and the measurement was available.
    """

    def test_phase_1_gates_on_the_bots_own_commits(self):
        self.assertIn(
            "$BOT_COMMITS",
            self.shell[1],
            "the gate's invariant is 'did the bump reach past the manifest and "
            "lockfile', not 'did this branch' — and only the bot's commits are "
            "the bump",
        )

    def test_the_human_half_reaches_phase_1_too(self):
        """Reporting it is the other half of the fix: a maintainer commit on the
        branch is a real thing a reader needs before merging. Suppressing it
        would trade a false Hold for a silent omission."""
        self.assertIn(
            "$HUMAN_COMMITS",
            self.shell[1],
            "the split has two halves and dropping the second one reports less than the union did",
        )

    def test_an_underivable_split_falls_back_rather_than_passing_empty(self):
        """`for c in $BOT_COMMITS` over an empty string iterates zero times, so
        the file list is empty and the gate passes **trivially**. That is worse
        than the false Hold it replaces: a gate that reports clean rather than
        erroring, on the one phase whose entire job is to refuse."""
        phase1 = dict(self.phases)[1].lower()
        self.assertIn(
            "underivable",
            phase1,
            "Phase 0's third state applies to the split too; an unset list is not an empty one",
        )
        self.assertIn(
            "$base_sha..pr-<n>",
            phase1,
            "and the fallback has to name the whole-diff gate it falls back to",
        )


class TestTheRepoConfigIsReadAtARef(SkillHarness):
    """Phase 0 read the repo's gate list out of whatever was checked out.

    Every other Phase 0 output is pinned to the PR, and the lockfile reads in
    `references/uv-lock.md` do it properly — `git show "pr-<N>:uv.lock"`. The gate
    read was the one that did not, and it ran in the user's working tree, so the
    gate list came from whatever branch happened to be there.

    Measured on `fpga-board-sim` #359: `ci.yml` at the checkout listed
    `uv run actionlint`, which arrived in that repo's #362, three PRs later. Run
    in the PR's worktree it exits 2 — `Failed to spawn: actionlint` — and reads as
    a Phase 5 gate failure on a gate the PR never had. Exit 2 is the status this
    procedure is most careful about everywhere else: "could not run", never "ran
    and found something". Here the procedure manufactured one.

    Auditing a merged PR makes the divergence certain — the checkout is ahead of
    every merged PR by construction, and every replay CONTRIBUTING's gate asks for
    is one. It is not only a replay problem: the same gap opens on an open PR
    whenever another branch is checked out, or the default branch has moved since
    the bot branched.
    """

    # Files whose *content the audit interprets*: the gates it reproduces, and
    # the bot config that decides whether a currency gap is lag or a hold.
    INTERPRETED = ("workflows/", ".pre-commit-config", "dependabot.yml", "renovate.json")
    AT_A_REF = re.compile(r'git show "(?:pr-<N>|\$\{?BASE_SHA\}?|\$\{?DEFAULT\}?):')

    def test_phase_0_reads_the_gate_list_at_a_ref(self):
        self.assertRegex(
            self.shell[0],
            re.compile(r'git show "(?:pr-<N>|\$\{?BASE_SHA\}?):[^"]*(?:workflows|pre-commit)'),
            "Phase 0 must read the repo's own gates from a ref. Read from the "
            "checkout, the gate list is whatever happens to be there — and a gate "
            "the PR never had exits 2, which reads as a Phase 5 failure",
        )

    def test_no_phase_reads_an_interpreted_config_out_of_the_working_tree(self):
        """The negative half: `cat`, `grep` and friends read the checkout.

        Asserted per line rather than per phase, because the defect was one line
        sitting beside several that got it right.
        """
        for number, code in sorted(self.shell.items()):
            for line in code.splitlines():
                if not any(path in line for path in self.INTERPRETED):
                    continue
                self.assertRegex(
                    line,
                    self.AT_A_REF,
                    f"Phase {number} reads a file the audit interprets without "
                    f"pinning it to a ref, so it reports on the checkout rather "
                    f"than on the PR: {line.strip()}",
                )

    def test_both_sides_of_the_bump_get_their_own_gate_list(self):
        """Phase 4 and Phase 5 run gates in *different* trees.

        One list run in both manufactures the exit 2 above in whichever tree did
        not have that gate. And a gate present on one side of the bump and not
        the other is itself a finding — quiet in both directions, since a gate
        the PR *adds* never runs at all, which is the direction that matters
        because a tooling bump can legitimately add its own.
        """
        shell = self.shell[0]
        for ref, tree in (("pr-<N>", "Phase 5 reproduces"), (r"\$BASE_SHA", "Phase 4 measures")):
            self.assertRegex(
                shell,
                rf'git show "{ref}:[^"]*workflows',
                f"Phase 0 must read the gates at the ref of the tree {tree} in; "
                f"one list serves both trees only until they differ, and the "
                f"difference is itself the finding",
            )


class TestPhase4MeasuresTheRightTree(SkillHarness):
    """Measuring on the PR's tree hides the finding whenever it is real.

    A PR that already contains the fixup — someone reformatted to make CI pass —
    has a tree the new version is already happy with, so the run reports no
    difference. Observed on a real ruff 0.15.22 -> 0.16.0 bump: six Markdown
    files on the merge base, nothing on the PR's tree, and the six were exactly
    what the maintainer had hand-reformatted onto the branch.

    Prose-only, like the four before it, and the worst of them: the others
    stalled or made noise, this one returns a confident "no change".
    """

    def test_the_gate_diff_invocation_uses_the_base_worktree(self):
        phase4 = dict(self.phases)[4]
        self.assertIn(
            "$SCRATCH/base-<N>",
            phase4,
            "Phase 4 must measure on the merge base, where the change is still visible",
        )

    def test_phase_0_creates_the_base_worktree(self):
        self.assertIn("$SCRATCH/base-<N>", self.shell[0])


class TestEverythingTheProseNamesExists(SkillHarness):
    """A renamed script breaks every phase that invokes it, silently."""

    def _docs(self):
        yield SKILL
        yield from sorted((PLUGIN / "references").glob("*.md"))

    def test_every_plugin_root_path_exists(self):
        for doc in self._docs():
            for rel in PLUGIN_PATH.findall(doc.read_text(encoding="utf-8")):
                self.assertTrue(
                    (ROOT / rel).exists(),
                    f"{doc.name} names ${{CLAUDE_PLUGIN_ROOT}}/{rel}, which does not exist",
                )

    def test_every_referenced_document_exists(self):
        for doc in self._docs():
            for name in REFERENCE.findall(doc.read_text(encoding="utf-8")):
                self.assertTrue(
                    (PLUGIN / "references" / name).exists(),
                    f"{doc.name} names references/{name}, which does not exist",
                )

    def test_the_phases_are_numbered_0_to_8_in_order(self):
        numbered = [n for n, _ in self.phases if n >= 0]
        self.assertEqual(numbered, list(range(9)), "a phase is missing, duplicated, or reordered")


class TestExecutionIsDisclosed(SkillHarness):
    """The audit runs code from the PR it audits, and once said so nowhere.

    That was a non-issue on repos whose dependencies the maintainer already ran,
    and becomes the plugin's largest unstated risk the moment it is pointed at an
    arbitrary repository's fork PR. The disclosure is prose, so it is exactly the
    kind of thing a later edit can quietly drop.
    """

    def test_the_executing_phases_are_labelled(self):
        for number in (4, 5):
            self.assertIn(
                "Executes code from the PR",
                dict(self.phases)[number],
                f"Phase {number} runs PR-controlled code and must say so in its header",
            )

    def test_the_read_only_mode_is_documented(self):
        self.assertIn("--no-execute", self.text, "the read-only mode is undocumented")

    def test_phase_1_stops_rather_than_continuing(self):
        """Running the cheap read-only checks first is worth something only if
        they are allowed to refuse."""
        self.assertIn("stop before Phase 4", dict(self.phases)[1])


class TestCleanupRunsOnEveryPath(SkillHarness):
    """The cleanup lived in Phase 5, which the correct path never reaches.

    Phase 0 registers two worktrees and a `pr-<N>` branch in the **user's** repo.
    Phase 1's gate stops the audit before Phase 4 whenever the diff reaches past
    the lockfile, and `--no-execute` skips Phases 4 and 5 outright — so on both
    of those paths the tidy-up was documented somewhere the audit had already
    stopped. One set of litter per PR audited, and the gated path is the one an
    auditor is *most* likely to take on a PR worth auditing.

    The prose admitted it: "The branch outlives an audit that stopped before
    Phase 5, too", written directly above the block that never ran.

    Phase 7 is the only phase every audit reaches — a Phase 1 stop still writes
    a report, because "stopping here is not a failed audit".
    """

    CLEANUP = re.compile(r"git worktree remove|git branch -D")

    def test_the_cleanup_is_not_in_a_phase_that_can_be_skipped(self):
        for number in (4, 5):
            self.assertNotRegex(
                self.shell[number],
                self.CLEANUP,
                f"Phase {number} executes PR code, so --no-execute and Phase 1's gate "
                f"both skip it; cleanup placed there never runs on the paths that "
                f"most need it",
            )

    def test_the_cleanup_is_in_the_phase_every_audit_reaches(self):
        self.assertRegex(
            self.shell[7],
            self.CLEANUP,
            "Phase 7 is the only phase reached on every path, including the Phase 1 "
            "gate stop; the cleanup belongs there",
        )

    def test_both_worktrees_and_the_branch_are_removed(self):
        """Removing the worktrees and leaving the branch still accumulates."""
        phase7 = self.shell[7]
        for artifact in ("$SCRATCH/pr-<N>", "$SCRATCH/base-<N>"):
            self.assertIn(artifact, phase7, f"{artifact} is created in Phase 0 and never removed")
        self.assertIn("git branch -D", phase7, "the fetched ref outlives the worktrees")


class TestTheRequiredContextListIsNotSilentlyTruncated(SkillHarness):
    """`contexts(first:100)` returns a page and says nothing about the rest.

    A repo reporting more than a hundred contexts hands back the first hundred,
    so a required check at position 101 is simply absent — which is
    indistinguishable from a check that passed, and is the same shape as the
    hand-joined required list `isRequired` was introduced to replace. The
    connection carries `totalCount`, so the truncation is detectable rather than
    inherent; not reading it is what makes it silent.

    Written from the query rather than from a measurement: no repo to hand
    reports over 100 contexts. What is asserted is therefore the *detectability*
    — that the field is requested — not a behaviour observed in the wild.
    """

    def test_the_query_asks_how_many_contexts_there_are(self):
        self.assertIn(
            "totalCount",
            self.reachable(6),
            "without totalCount a truncated page is indistinguishable from a "
            "complete list, and the missing contexts read as passing",
        )

    def test_the_prose_gives_truncation_phase_0s_third_state(self):
        """Asserted on the table that reads `totalCount`, not on the bare word.

        Mutation-checked and caught: `underivable` was already in Phase 6 for
        `mergeStateStatus: UNKNOWN`, so a phase-wide search for it passed against
        the prose that had this defect. The same trap as
        `test_a_base_with_no_run_is_underivable_rather_than_attributable`, which
        records it one guard earlier. A word that is already present cannot
        discriminate; the row that carries the meaning can.
        """
        for table in tables(dict(self.phases)[6]):
            if any("totalCount" in row for row in table):
                self.assertTrue(
                    any("underivable" in row.lower() for row in table),
                    "a context list truncated and not paged is not established, and "
                    "must not be reported as the complete required set",
                )
                return
        self.fail("Phase 6 has no table reading totalCount")


class TestExecutionRequiresARepoYouControl(SkillHarness):
    """Phase 1's gate does not defend against the case Phase 5 executes.

    The gate catches a lockfile edited after it was written honestly. It cannot
    catch a malicious *release*: Phase 1 compares the lockfile against what the
    registry serves today, so when the attacker published the artifact the record
    and the lockfile agree, and agreement is the entire test.

    So the classification, not the gate, is what has to carry "should this run at
    all". It already switched on `isCrossRepository` and a non-bot author. The
    third condition is permission: a repo you cannot merge into is one whose PR
    you had no plan to run, and the comparison to "CI would run it anyway" stops
    holding — CI runs it in a fresh container with a scoped token, this runs it on
    a workstation with the auditor's credentials in the environment.
    """

    def _classification_table(self) -> list[str]:
        for table in tables(dict(self.phases)[0]):
            if any("isCrossRepository" in row for row in table):
                return table
        self.fail("Phase 0 has no PR-classification table")

    def test_the_classification_switches_on_permission_too(self):
        table = self._classification_table()
        self.assertTrue(
            any("push" in row for row in table),
            "the classification decides whether Phases 4 and 5 may run and never "
            "asked whether this is a repo you control",
        )

    def test_the_execute_warning_says_the_gate_cannot_see_a_bad_release(self):
        """The preamble claimed Phases 1-3 "would catch a bad dependency"."""
        preamble = dict(self.phases)[-1].lower()
        self.assertIn(
            "does not catch a malicious release",
            preamble,
            "the ordering argument must scope itself, or it reads as a guarantee "
            "against the case that actually reaches Phase 5",
        )


class TestTheVerdictIsDerivedRatherThanJudged(SkillHarness):
    """Phase 7 named three verdicts and never said which evidence produces which.

    Every phase above it is rigorous about the three-state discipline, and then
    the mapping from findings to recommendation was left entirely implicit — so
    two audits with identical evidence could reach different verdicts and neither
    report would show why. The cases the three one-line definitions did not cover
    are exactly the ones the procedure worked hardest to establish:

      - a red required check labelled **pre-existing**, which Phase 6 says
        explicitly "must not produce a Hold on this bump" while the PR still
        cannot merge;
      - a Phase 4 difference that is real and *absorbed* by the PR, which is a
        finding and not an obstacle;
      - `mergeStateStatus: BLOCKED` with every check green;
      - an underivable input, tracked per row and dropped at the verdict.

    Confidence was worse: `report-template.md` asked for high/medium/low and
    nothing anywhere defined it, which makes the report's most visible field the
    one least connected to its evidence.
    """

    def _phase7(self) -> str:
        return dict(self.phases)[7]

    def test_the_three_verdicts_have_a_derivation_not_just_definitions(self):
        phase7 = self._phase7()
        self.assertIn(
            "precedence",
            phase7.lower(),
            "phases are expected to disagree — Phase 2's changelog against Phase 3's "
            "scanner is the designed case — so the order they resolve in has to be "
            "stated rather than improvised per audit",
        )

    def test_a_pre_existing_red_does_not_carry_the_verdict(self):
        """Phase 6 establishes the label; Phase 7 has to act on it."""
        phase7 = self._phase7().lower()
        self.assertIn("pre-existing", phase7, "the label Phase 6 derives is unused in Phase 7")
        self.assertRegex(
            phase7,
            r"not a hold on this bump",
            "a pre-existing red is a real finding about the repo and not a verdict "
            "about the bump; collapsing them produces the false Hold Phase 6 exists "
            "to prevent, one phase later",
        )

    def test_a_gap_inside_the_cooldown_earns_no_follow_up(self):
        """The inversion: recommending one hand-lands the held release."""
        phase7 = self._phase7().lower()
        self.assertIn(
            "cooldown",
            phase7,
            "Phase 2 separates a hold from lag; the verdict table has to consume "
            "that distinction or the separation buys nothing",
        )

    def test_confidence_is_defined_somewhere_the_writer_will_read(self):
        phase7 = self._phase7().lower()
        self.assertIn("confidence", phase7)
        self.assertIn(
            "underivable",
            phase7,
            "confidence must be a function of what could not be derived, or it is a "
            "feel — and the report's most visible field is then its least falsifiable",
        )

    def test_the_report_template_carries_the_same_confidence_rule(self):
        """The template is what gets copied; a rule only in SKILL.md drifts."""
        template = (PLUGIN / "references/report-template.md").read_text(encoding="utf-8").lower()
        self.assertIn("confidence is derived", template)
        for level in ("high", "medium", "low"):
            self.assertIn(level, template)


class TestSecurityEvidenceOutranksTheCooldown(SkillHarness):
    """Phase 2's prose and Phase 7's table disagreed about Phase 2's own case.

    Phase 2: "A gap inside the cooldown window does not earn a follow-up branch.
    [...] What outranks the hold is what this phase reads for next: a `Security`
    entry or a destructive-fix bug in the gap."

    Phase 7, read top-down taking the first row that matches: the security row
    was gated on `and the gap is outside the cooldown`, so it could not match,
    and the fall-through landed on "a gap exists **inside** the cooldown window →
    Merge as-is. Do *not* offer a follow-up" — the opposite of the prose.

    Measured on `fpga-board-sim` #355: `rumdl` 0.2.49 carries a `Security`
    section — config `extends` values expanded from the environment before
    resolution, so naming the resolved path printed environment variable values
    into the build log. Privately reported, no CVE, no GHSA; `audit.py` reported
    "no known vulnerabilities across 37 packages", correctly. The changelog is
    the only place it exists, which is the whole reason Phase 2 reads changelogs.
    0.2.49 published 17 hours before the PR opened — inside the three-day
    cooldown, so the bot was right to hold, and the gap still contained a
    security fix.

    Two more gaps in the same rows. **Destructive-fix bugs had no row at all**,
    though Phase 2 ranks them equal: `fpga-board-sim` #359, `rumdl` 0.2.53 fixing
    `md084: stop deleting line endings as invisible characters` and `md038: stop
    deleting a line when trimming a multi-line code span`, in a repo whose
    pre-commit config runs `rumdl check --fix` on every commit touching Markdown.
    And **the recommendation turned on when you ran the audit**: replayed once
    0.2.49 is thirteen days old the gap is outside the cooldown, row 3 matches,
    and the same evidence produces the opposite verdict. Phase 7's own stated
    reason for having a table is that leaving the function implicit is how two
    audits with the same evidence reach different recommendations.
    """

    def _verdict_table(self) -> list[str]:
        for table in tables(dict(self.phases)[7]):
            if any("Verdict" in row for row in table):
                return table
        self.fail("Phase 7 has no verdict table")

    def _security_rows(self) -> list[tuple[int, str]]:
        """Rows that positively *read* security-shaped evidence in the gap.

        Matched on the evidence named rather than on the word "security", which
        also appears in the row for a gap containing **nothing** security-shaped
        — and that row is legitimately conditioned on the cooldown.
        """
        return [
            (i, row)
            for i, row in enumerate(self._verdict_table())
            if "`security` entry" in row.lower() or "destructive-fix" in row.lower()
        ]

    def _cooldown_row(self) -> int:
        for i, row in enumerate(self._verdict_table()):
            low = row.lower()
            if "inside" in low and "cooldown" in low:
                return i
        self.fail("Phase 7 has no row for a gap inside the cooldown")

    def test_the_evidence_is_read_whatever_the_cooldown_says(self):
        """The cooldown decides Hold-vs-follow-up, never whether to look."""
        rows = self._security_rows()
        self.assertTrue(rows, "no row reads a Security entry in the gap")
        self.assertLess(
            min(i for i, _ in rows),
            self._cooldown_row(),
            "a security-shaped row below the cooldown row is unreachable: the "
            "table is first-match, and every gap inside the window stops there",
        )
        for _, row in rows:
            self.assertNotIn(
                "outside the cooldown",
                row.lower(),
                "gating the *reading* of a Security entry on the cooldown routes "
                "Phase 2's founding case to 'do not follow up' — the cooldown "
                "exempts Dependabot's security updates, not a version update "
                "whose changelog carries a privately disclosed fix",
            )

    def test_a_destructive_fix_bug_reaches_the_table_at_all(self):
        """Phase 2 ranks them equal to `Security` entries. Phase 7 never
        mentioned them, so the only evidence class this procedure discovers that
        no security feed carries had no verdict rule."""
        self.assertIn(
            "destructive",
            " ".join(self._verdict_table()).lower(),
            "a data-loss bug in a write mode the repo runs automatically is "
            "Phase 2's second reading target and had no row",
        )

    def test_the_row_decides_on_exposure_rather_than_on_the_clock(self):
        """ "if the repo exercises the affected path" was doing real work in both
        measured cases and sat in the prose, where no verdict reads it: #355's
        leak path is inert (the repo configures rumdl inline, with no `extends`
        anywhere), #359's `--fix` write mode is live on every Markdown commit.
        Same shape of finding, two urgencies, and the distinction is a grep the
        phase already knows how to do."""
        rows = " ".join(row for _, row in self._security_rows()).lower()
        self.assertIn(
            "affected path",
            rows,
            "the verdict has to turn on whether this repo is exposed; taking it "
            "from the calendar makes the same evidence produce opposite "
            "recommendations on different days",
        )


class TestFrontmatter(SkillHarness):
    """0.1.9 shipped `tools:`, which is not a field and withheld nothing.

    The worst shape for a safety property: visible, documented, and absent.
    """

    def _frontmatter(self) -> dict[str, str]:
        # Stdlib only — no yaml. The frontmatter here is flat `key: value`.
        block = self.text.split("---", 2)[1]
        return {
            key.strip(): value.strip()
            for key, _, value in (line.partition(":") for line in block.splitlines())
            if key.strip() and not key.startswith(" ")
        }

    def test_the_field_that_withholds_is_the_one_that_works(self):
        front = self._frontmatter()
        self.assertIn("disallowed-tools", front, "the field that removes tools is gone")
        for tool in ("Edit", "Write", "NotebookEdit"):
            self.assertIn(tool, front["disallowed-tools"])

    def test_the_inert_field_has_not_come_back(self):
        self.assertNotIn(
            "tools",
            {k for k in self._frontmatter() if k == "tools"},
            "`tools:` is not a skill frontmatter field; it reads like a control and is not one",
        )

    def test_the_name_matches_the_directory(self):
        self.assertEqual(self._frontmatter()["name"], PLUGIN.name)


class TestTheAuditHandsBackItsOwnDeviations(SkillHarness):
    """The report asserted "I followed the procedure" by silence, and was wrong.

    On 2026-08-19 `fpga-board-sim` #363 ran to a complete, well-formed report
    under 0.22.1 while `SKILL.md` had never loaded at all: `commands/` shadowed
    the skill at the same `<plugin>:<name>` address, so the procedure was
    unloadable and `disallowed-tools` never applied (#48, 0.23.0). Every evidence
    row in that report was true. What produced them was not this procedure.

    The shadowing is fixed. The part that is not is that the audit **routed
    around it silently**. Two commands from that session's transcript, neither of
    which appears anywhere in this file:

        export CLAUDE_PLUGIN_ROOT=/home/rick/.claude/plugins/cache/.../0.22.1
        cd .../skills/dependabot-audit && cat SKILL.md

    The first invents a variable the prose assumes is supplied; the second
    fetches by hand the procedure that should have been handed over. The report
    mentioned neither, and Phase 8 as written could not have asked for them — its
    scope was a landmine in the *audited* repo or a portable trap in an ecosystem,
    and a defect in the plugin's own machinery is neither.

    **These two cases are the assertions below, and that is deliberate.** They
    come from the transcript, not from the clause written to answer it — a guard
    derived from its own fix can only ever agree with it, which is how a Phase 6
    guard in 0.10.0 asserted the property that *was* the defect. A clause that
    asked only for surprising commands would still miss the `cat`; one that asked
    only about plugin files would still miss the `export`.

    What this cannot check is whether the model *does* it. That is behavioral,
    belongs in `claude plugin eval` (#32, still refused on this account), and
    until then rests on the replay in CONTRIBUTING: run an audit, ask for the
    deviation list, and check it against the transcript's actual `Bash` calls.
    """

    def _deviation_section(self) -> str:
        """The Phase 8 subsection that asks for deviations, not all of Phase 8.

        Anchored to the subsection so "these words appear somewhere in Phase 8"
        cannot satisfy the guards: the landmine hand-back above it already talks
        about evidence, handing back, and not creating files.
        """
        phase8 = dict(self.phases)[8]
        parts = re.split(r"^### ", phase8, flags=re.MULTILINE)
        for part in parts:
            if re.search(r"deviat", part, re.IGNORECASE):
                return part
        self.fail("Phase 8 has no subsection asking for deviations from the procedure")

    def test_it_asks_for_commands_the_procedure_did_not_specify(self):
        """The `export` half: a command run to fill a gap in the prose."""
        self.assertRegex(
            self._deviation_section(),
            re.compile(
                r"command.{0,120}(did not specify|this file does not)", re.IGNORECASE | re.DOTALL
            ),
            "a deviation list that does not ask for unspecified commands misses the "
            "invented CLAUDE_PLUGIN_ROOT export, which is half the #363 evidence",
        )

    def test_it_asks_for_plugin_files_read_instead_of_invoked(self):
        """The `cat SKILL.md` half: the procedure fetched by hand."""
        self.assertRegex(
            self._deviation_section(),
            re.compile(r"read directly|read by hand|rather than invoked", re.IGNORECASE),
            "asking only for surprising commands still reads as satisfied by an audit "
            "that quietly cat'd the procedure it was supposed to be handed",
        )

    def test_each_deviation_is_classified_rather_than_only_listed(self):
        """A list without the verdict column is a diary, not a hand-back.

        `plugin defect` is the class that reached the report in #363 and the one
        Phase 7 pairs with; `correct` has to be available or the clause reads as
        an instruction not to improvise, which would be the wrong lesson.
        """
        section = self._deviation_section().lower()
        for label in ("plugin defect", "prose gap", "correct"):
            self.assertIn(
                label,
                section,
                f"a deviation classified as neither {label!r} nor anything else is "
                f"handed back with its meaning left to the reader",
            )

    def test_the_deviation_list_is_handed_back_rather_than_filed(self):
        """Same contract as the landmine above: this skill does not write.

        `disallowed-tools` withholds Edit, Write and NotebookEdit, and the
        landmine hand-back is careful to say the invoking session owns the
        decision. A clause that told the audit to open an issue would ask for a
        capability the frontmatter removes.
        """
        self.assertRegex(
            self._deviation_section(),
            re.compile(r"print|hand (it|them|the list) back", re.IGNORECASE),
            "the deviation list has no home unless the phase says where it goes",
        )

    def test_phase_7_puts_a_plugin_defect_in_the_report(self):
        """Phase 8 is the maintainer's; the PR's reader needs one line of it.

        Without this the split loses the case that motivated it: an audit that
        worked around a defect in its own tooling produced every row in that
        table, and the reader of the report cannot tell that run from an
        unremarkable one.
        """
        phase7 = dict(self.phases)[7]
        self.assertRegex(
            phase7,
            re.compile(r"improvis|deviat|work(ed)? around", re.IGNORECASE),
            "Phase 7 writes the report and never mentions that the run may not have "
            "followed the procedure; compliance stays implied, which is the defect",
        )

    def test_the_report_template_carries_the_same_disclosure(self):
        """Phase 7 telling the writer something the shape has no room for is how
        the verdict table and Phase 2's prose drifted apart in 0.10.0."""
        template = (PLUGIN / "references/report-template.md").read_text(encoding="utf-8").lower()
        self.assertRegex(
            template,
            re.compile(r"improvis|deviat|work(ed)? around", re.IGNORECASE),
            "the report shape has nowhere to say the audit improvised, so Phase 7's "
            "instruction lands on a template that does not ask for it",
        )


class TestTheTagRecipeSurvivesAPrefixMatch(SkillHarness):
    """`git/refs/tags/<tag>` is *get all references in a namespace*, and it crashed.

    Found by the #50 deviation clause on its first replay against the shipped
    text, and reproduced by hand afterwards (#54). Measured 2026-08-21:

        actions/checkout    v5     -> object, commit fbc6f399  (three siblings share the prefix)
        actions/checkout    v5.1   -> array(1): refs/tags/v5.1.0
        astral-sh/setup-uv  v10    -> array(2): refs/tags/v10.0.0, refs/tags/v10.0.1
        astral-sh/setup-uv  v999   -> 404 Not Found, exit 1

    The rule the four measurements give: an exact ref returns an **object even
    when siblings share the prefix**; with no exact ref the endpoint returns the
    **array** of everything matching; with nothing matching at all, a **404**.
    `.object.type` against the array is a jq type error — `expected an object but
    got: array` — at exit 1.

    Phase 2 asks *does a moving major tag exist* with names like `v1`, `v4`,
    `v10`, which are prefixes of the point releases beneath them, so the recipe
    failed on the question it exists to answer and worked only on the exact tags
    Phase 1 already had from the pin comment.

    **The array is the answer, which is why a crash is the wrong shape.** It
    enumerates the refs that do exist and thereby settles that no `v10` does. The
    recipe died exactly when it held the data, and `expected an object but got:
    array` reads like an API fault rather than "no such tag" — so an auditor may
    treat it as transient, retry, get the same error, and report as underivable a
    question that is fully derivable. Same shape as CONTRIBUTING's
    `branches/<b>/protection` table: a confident-looking error about the wrong
    thing.

    **The singular `git/ref/tags/<tag>` does not crash and is worse.** Measured
    the same day, it answers a bare `404` to both `v5.1` and `v999` — collapsing
    "no such tag, and here is what does exist" into "nothing here", which is the
    half Phase 2 needs. Not crashing is not the same as answering.

    The assertions below come from those four measurements rather than from the
    recipe written to satisfy them: the fourth exists because "a moving major tag
    returns an array" is the natural reading of the fix and is **false**, and
    believing it inverts the answer for every action that does publish one.
    """

    def _tag_recipe(self) -> str:
        """The actions.md § Phase 1 bash that asks where a tag points.

        Anchored to the block, not the phase: Phase 1 hands off to two ecosystem
        files and carries its own shell, and "the word array appears somewhere in
        Phase 1" would be satisfied by any of it.
        """
        for name, section in self._handoffs(1):
            if name != "actions.md":
                continue
            for block in bash_blocks(section):
                if "git/refs/tags" in block:
                    return block
        self.fail("actions.md § Phase 1 has no bash block asking where a tag points")

    def _outcome_table(self) -> list[str]:
        """The actions.md § Phase 1 table with a row per outcome of that query.

        Anchored the same way. Phase 1 already carries a two-row table about the
        pin, and a guard reading "some table in this phase" would score against
        it.
        """
        for name, section in self._handoffs(1):
            if name != "actions.md":
                continue
            for table in tables(section):
                joined = "\n".join(table).lower()
                if "array" in joined and "404" in joined:
                    return table
        self.fail("actions.md § Phase 1 has no table separating the tag query's outcomes")

    def test_the_recipe_branches_on_the_array(self):
        """The defect itself: `.object.type` against an array is a type error."""
        self.assertIn(
            "array",
            self._tag_recipe(),
            "the tag query returns an array whenever the name is a prefix rather than "
            "a ref, which is the case Phase 2 asks about; a recipe that does not "
            "branch on the type dies at exit 1 on the ordinary question",
        )

    def test_the_query_has_three_outcomes_not_two(self):
        """Object, array and 404 — and the middle one is the common case."""
        rows = [row for row in self._outcome_table() if set(row) - set("|- :")]
        self.assertGreaterEqual(
            len(rows) - 1,
            3,
            "the tag query has three outcomes; a table with two lets the reader "
            "collapse the array into the 404 and read a prefix match as absence",
        )

    def test_the_array_is_a_negative_answer_rather_than_a_failure(self):
        """`expected an object but got: array` reads like an API fault. It is not.

        Written from the failure mode rather than from the recipe: an auditor who
        reads the array as an error retries it, and reports underivable a
        currency question the array had already answered.
        """
        self.assertRegex(
            self.material(1),
            re.compile(
                r"array.{0,600}(no such tag|does not exist|no .{0,20}ref)",
                re.IGNORECASE | re.DOTALL,
            ),
            "the array has to be named as the negative answer to *does this tag "
            "exist*; left as a crash or an error it reads as a failed call",
        )

    def test_a_shared_prefix_does_not_mean_the_tag_is_missing(self):
        """`actions/checkout@v5` has three siblings under it and still returns the object.

        Without this measurement the fix's own reading — array means no such tag —
        generalises to "a moving major tag returns an array", which is false and
        inverts the answer on every action that publishes one.
        """
        table = "\n".join(self._outcome_table()).lower()
        self.assertIn(
            "exact",
            table,
            "the outcome table must say the exact ref wins even when other refs "
            "share the prefix, or the array row reads as covering the moving major "
            "tag it is the negative answer about",
        )


class TestTheScratchDirectorySurvivesTheNextCall(SkillHarness):
    """Phase 0 wrote its handoff to a directory the next call could not name.

    The line was `SCRATCH=${SCRATCH:-$(mktemp -d)}`, commented "any directory
    OUTSIDE the repo" — which says what the directory must *be* and never that it
    must be the *same one* next time. Everything downstream depends on the second
    property: `$SCRATCH/phase0.env` is written by one call and sourced by another,
    and both worktrees are addressed across calls.

    Measured against this harness on 2026-08-21, two separate `Bash` calls:

        call 1  export PROBE_VAR=...   pid 3634427   cd .../skills
        call 2  PROBE_VAR=<UNSET>      pid 3634720   pwd=.../skills

    Environment variables and shell functions do **not** cross the boundary — each
    call is a new shell process. So `${SCRATCH:-...}` finds `SCRATCH` unset every
    time and `mktemp -d` hands back a different directory, after which
    `. "$SCRATCH/phase0.env"` sources a path that does not exist and `$BASE_SHA`,
    `$HEAD_SHA`, `$BOT_COMMITS` and `$DEFAULT` are all empty downstream. Run as
    the two calls above:

        new form: SCRATCH=/tmp/dbaudit-<owner>-<name>-<N> in both -> sourced OK
        old form: /tmp/tmp.wqlEPyTVGA in call 2 -> phase0.env absent, outputs empty

    **The working directory does cross it, and is still not a way out.** `pwd`
    persisted into call 2 above — but a call that ends outside the project has its
    cwd *reset* (measured: `cd /tmp` came back to the project root), and `$SCRATCH`
    is required to be outside the repo. So no ambient state reaches the next call,
    and the only thing that can is a path every call can **recompute** from inputs
    it already has.

    Found by 0.24.0's deviation clause during the #51 replays and classified by
    the run as a prose gap (#55): two of three rounds hit it and repaired it
    unprompted, one by pinning `export SCRATCH=/tmp/tmp.5tGlKz9N3s` after the
    fact and re-sourcing at the top of every later call. Both reached correct
    results, which is the point — the workaround that works is the one nobody
    reports.
    """

    def _scratch_assignment(self) -> str:
        """The line in Phase 0's shell that sets `SCRATCH`.

        Anchored to the assignment, not the phase: `$SCRATCH` is used all over
        Phase 0, and "the phase mentions mktemp somewhere" would be satisfied by
        the paragraph that discusses it.
        """
        for line in self.shell[0].splitlines():
            if re.match(r"\s*SCRATCH=", line):
                return line
        self.fail("Phase 0 has no line assigning SCRATCH")

    def test_the_scratch_directory_does_not_change_between_calls(self):
        """`mktemp -d` is a new directory per call, and the handoff is per audit."""
        self.assertNotIn(
            "mktemp",
            self._scratch_assignment(),
            "Phase 0 assigns SCRATCH from mktemp, which hands back a different "
            "directory on every Bash call; the next call then sources a phase0.env "
            "that is not there and every Phase 0 output is empty downstream",
        )

    def test_the_scratch_path_is_derived_rather_than_remembered(self):
        """No ambient state survives, so the path has to be recomputable.

        From the measurement rather than from the fix: env vars die, functions
        die, and cwd is reset the moment a call ends outside the project. What is
        left is deriving the name from inputs every call already has.
        """
        self.assertRegex(
            self._scratch_assignment(),
            re.compile(r"\$\{?REPO|\$\{?OWNER|<N>"),
            "SCRATCH must be derived from the repo and the PR number so any call "
            "can recompute it; a value that has to be carried is a value that is "
            "lost at the next call boundary",
        )

    def _scratch_rule(self) -> str:
        """Every paragraph of Phase 0 that says what the scratch directory must be.

        Anchored to paragraphs naming it. The first version of this guard scanned
        all of Phase 0 and went green on the *caching* paragraph — "persist the
        answers to these ... Deriving costs one call" — which is about not caching
        a profile and has nothing to do with where the handoff is written. A guard
        that matches anything anywhere stops discriminating.
        """
        chunks = [
            chunk
            for chunk in re.split(r"\n\s*\n", self.material(0))
            if re.search(r"scratch", chunk, re.IGNORECASE)
        ]
        if not chunks:
            self.fail("Phase 0 says nothing about the scratch directory")
        return "\n\n".join(chunks)

    def test_the_rule_says_stable_and_not_merely_outside_the_repo(self):
        """The old comment gave one of the two properties, and not the load-bearing one."""
        self.assertRegex(
            self._scratch_rule(),
            re.compile(
                r"(stable|same directory|same place|survive|recompute)",
                re.IGNORECASE,
            ),
            "the scratch rule states only that the directory sits outside the repo; "
            "the property phase0.env and both worktrees actually depend on is that "
            "the next call resolves it to the same place",
        )


def _emitted_by_discover() -> set[str]:
    """The variable names `discover.py --shell` writes, read from its source.

    Static rather than executed: the suite is offline and stdlib-only, and the
    script needs `gh`. The emitter is located by the **header it prints** rather
    than by its function name, so renaming the function cannot silently empty
    this set — which would make every guard below pass by matching nothing.
    """
    src = (PLUGIN / "scripts/discover.py").read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.FunctionDef):
            continue
        body = ast.unparse(node)
        if "Phase 0 outputs. Sourced, not transcribed." not in body:
            continue
        # `("DEFAULT", ...)` and `{"BOT_COMMITS": ...}` are bare constants;
        # `f"BRANCH_POINT={...}"` puts the name straight against an `=`.
        names = {
            c.value
            for c in ast.walk(node)
            if isinstance(c, ast.Constant)
            and isinstance(c.value, str)
            and re.fullmatch(r"[A-Z][A-Z0-9_]*", c.value)
        }
        return names | set(re.findall(r"\b([A-Z_][A-Z0-9_]*)=", body))
    raise AssertionError("discover.py has no function printing the Phase 0 shell header")


class TestPhase0PromisesOnlyWhatItProduces(SkillHarness):
    """Phase 6 declared a requirement the handoff has never carried.

    Its contract line read:

        *Requires from Phase 0: `$HEAD_SHA`, `$BASE_SHA`, `$OWNER`, `$NAME`, `$PERMS`.*

    and the Phase 0 outputs table listed `$PERMS` under a heading saying *every
    later phase consumes these and nothing else*. `discover.py --shell` does not
    write it. Measured against a real `phase0.env` from `fpga-board-sim` #363:

        DEFAULT HEAD_SHA BASE_REF BASE_SHA OWNER NAME BRANCH_POINT
        MAY_EXECUTE BOT_COMMITS HUMAN_COMMITS

    Ten names, no `PERMS`. The script prints `$PERMS` in its **human-readable
    report** and writes only the derived `MAY_EXECUTE` to the shell output, so a
    phase that sourced the handoff and read `$PERMS` would get the empty string.

    Nothing broke, which is why it survived: no shell block reads `$PERMS`, so
    the empty value never reached a command, and the forward-reference guard —
    which scans shell, not prose — was right to stay quiet. The contract was
    still false, and a requires-line is exactly the sentence someone trusts when
    adding a step that branches on permissions.

    **Phase 6 does not want it anyway**, which is what settles the direction of
    the fix. Phase 0 says the required-checks question "moved to Phase 6, which
    asks it per-PR in a form readable at `pull`" — the whole point of that design
    is that Phase 6 needs no permission tier at all. `$PERMS` in its requires-line
    contradicted the phase it was attached to.

    Guarded as a class rather than as the instance, per the issue that filed it:
    a name in a requires-line, or a row in the outputs table, must be something
    Phase 0 actually produces — from the script's emitter or from its own shell.
    """

    REQUIRES = re.compile(r"Requires from Phase 0:(.*)", re.IGNORECASE)

    def _produced(self) -> set[str]:
        """What a later phase can actually receive: the emitter, plus Phase 0's shell.

        The second half is not padding — `$SCRATCH` is assigned by Phase 0
        directly and never passes through `discover.py`, so a guard reading only
        the emitter would call the most-used output of all a broken promise.
        """
        return _emitted_by_discover() | set(ASSIGNED.findall(self.shell[0]))

    def test_every_requires_line_names_something_phase_0_produces(self):
        produced = self._produced()
        seen = 0
        for number, body in self.phases:
            for line in body.splitlines():
                found = self.REQUIRES.search(line)
                if not found:
                    continue
                seen += 1
                for name in re.findall(r"\$([A-Z_][A-Z0-9_]*)", found.group(1)):
                    self.assertIn(
                        name,
                        produced,
                        f"Phase {number} requires ${name} from Phase 0, which neither "
                        f"discover.py --shell writes nor Phase 0's shell assigns; a phase "
                        f"sourcing the handoff and reading it gets the empty string",
                    )
        self.assertGreater(seen, 0, "no phase states what it requires from Phase 0")

    def test_the_outputs_table_lists_only_real_outputs(self):
        """The table says later phases consume these *and nothing else*."""
        produced = self._produced()
        rows = re.findall(r"^\|\s*`\$([A-Z_][A-Z0-9_]*)`", dict(self.phases)[0], re.MULTILINE)
        self.assertTrue(rows, "Phase 0 has no outputs table")
        for name in rows:
            self.assertIn(
                name,
                produced,
                f"the Phase 0 outputs table promises ${name}, which nothing in Phase 0 "
                f"writes into the handoff; the table is what later phases are told they "
                f"may consume",
            )


class TestPhase4ReadsTheActionsInterfaceNotOnlyItsNotes(SkillHarness):
    """The release notes understated the change, and the method had no second source.

    `references/actions.md` § Phase 4 said: read the notes for every version in
    the gap, then grep this repo's workflows for what they name. An action cannot
    be run locally at two versions, so reading is the method rather than the
    shortcut — which makes it load-bearing that what is read is complete.

    It is not. Measured on `fpga-board-sim` #363, `astral-sh/setup-uv`
    9.0.0 → 10.0.1, where v10.0.0 disables the cache under `enable-cache: auto`:

        release notes      3 conditions — pull_request_target, workflow_run, release
        action.yml         5 — "GitHub-hosted runners except for release, tag push,
                               pull_request_target, and workflow_run"
        src/utils/inputs.ts 5 — isTagPush checked FIRST, its own branch, its own
                               log line, then the three-event `||` chain

    The word *tag* does not appear anywhere in the notes body. The notes were
    written from the second `if` and missed the first.

    **Two things the diff shows that no reading of the notes does.** `default:
    "auto"` is **unchanged** across the bump — what changed is what `auto` means,
    so a check asking "did a default flip?" correctly answers no while the
    behaviour changed underneath it. And the only place the fourth condition
    surfaced at all was **description prose**, which is why a description-only
    diff is a finding rather than a clean bill.

    **The trigger is not an event name, which is why the grep row missed it.**
    Tag push is `eventName === "push"` *and* `GITHUB_REF` starting `refs/tags/`.
    Grepping a workflow for `pull_request_target:` and `workflow_run:` finds
    nothing; grepping for `push:` matches nearly every workflow ever written.
    Neither answers the question. The check is `push:` carrying a `tags:` key.

    On #363 the verdict was "inert here" and was right — that repo triggers on
    `push: branches: [main]` and `pull_request:` only. It was right by luck: the
    same procedure on a repo with `push: tags:` reports inert about a change that
    is live. Per CONTRIBUTING's fifth row, a defect on a path no reachable PR
    exercises survives the replay gate, and this one did until the notes were
    checked against the source.
    """

    def _phase4(self) -> str:
        for name, section in self._handoffs(4):
            if name == "actions.md":
                return section
        self.fail("Phase 4 does not hand off to actions.md")

    def test_the_interface_is_read_at_both_pins(self):
        """`action.yml` ships in the action's own repo, so it is readable at any pin."""
        self.assertIn(
            "action.yml",
            self._phase4(),
            "Phase 4 for actions reads only the release notes, which are prose written "
            "by the releaser; action.yml is the interface the runner actually loads and "
            "it is fetchable at both SHAs",
        )

    def test_it_is_a_command_rather_than_an_instruction_to_look(self):
        """A phase that says 'check the interface' and hands over no query is a wish."""
        blocks = "\n".join(bash_blocks(self._phase4()))
        self.assertRegex(
            blocks,
            re.compile(r"action\.yml"),
            "Phase 4 for actions must carry the query that fetches the interface at "
            "both pins, not only the advice to compare them",
        )

    def test_a_description_only_diff_is_not_a_clean_bill(self):
        """`default: "auto"` never changed; the meaning of `auto` did.

        From the measurement rather than the fix: the fourth condition existed
        *only* in description prose, so a reader who treats description churn as
        cosmetic discards the one place the change was visible.
        """
        self.assertRegex(
            self._phase4(),
            re.compile(r"description", re.IGNORECASE),
            "the diff's description-only case has to be called out, or it reads as "
            "the clean result it looks like — which is how the fourth condition hid",
        )

    def test_the_tag_push_shape_is_named_rather_than_the_event_list(self):
        """`push:` + `tags:` — grepping the three event names cannot find it."""
        self.assertIn(
            "tags:",
            self._phase4(),
            "the trigger row greps for event names, and tag push is not one: it is "
            "`push` plus a refs/tags ref, so the three-name grep returns nothing and "
            "reports inert on a repo that publishes tags",
        )


def _every_block() -> list[tuple[str, int, str]]:
    """(file, phase, code) for every bash block in the skill and its references.

    `SkillHarness.shell` concatenates a phase's blocks into one string, which is
    right for the ordering guards and wrong here: the question below is asked of
    each block *as a unit*, because a block is what a reader runs in one call.
    Two blocks joined would let Phase 0's own reload satisfy a later phase's.
    """
    found: list[tuple[str, int, str]] = []
    for path in (SKILL, PLUGIN / "references/uv-lock.md", PLUGIN / "references/actions.md"):
        for number, body in phases(path.read_text(encoding="utf-8")):
            found.extend((path.name, number, code) for code in bash_blocks(body))
    return found


class TestEveryConsumerReloadsTheHandoff(SkillHarness):
    """Phase 0's handoff was written once, sourced once, and read by seven blocks.

    `SKILL.md` states the measurement that makes this fatal, in its own Phase 0:

        Measured against this harness, two separate calls: an `export` in the
        first is **unset** in the second, shell functions likewise, and each call
        is a new shell process.

    Re-measured 2026-08-21 across two Bash calls in one session: `PROBE_VAR` set
    and exported in the first reads `<UNSET>` in the second, a function defined
    in the first is `not found`, and the pids differ.

    0.26.0 fixed `$SCRATCH`'s *derivation* so the next call could name the
    directory. The consequence — that the next call must then actually re-derive
    it and re-source the file — never reached the phases that consume it, so
    `. "$SCRATCH/phase0.env"` appeared exactly once in the plugin, inside the
    phase that writes it.

    Measured cost, running each consuming block in a fresh shell with nothing
    sourced: Phases 1 (uv.lock), 4, 6 and 7 fail loudly — `Permission denied`,
    two exit 2s, and exit 128 — while Phase 1's authorship gate **passes
    silently**, because `for c in $BOT_COMMITS` over an unset variable iterates
    zero times and the gate's file list is empty. `SKILL.md` names that outcome
    "the one outcome worse than a false Hold".
    """

    def _handoff_names(self) -> set[str]:
        """What the handoff carries: the emitter's names, plus `SCRATCH` itself.

        `SCRATCH` never passes through `discover.py` — Phase 0's shell assigns it
        — but it is lost across a call boundary exactly like the rest, and it is
        the one every other value is reached *through*.
        """
        return _emitted_by_discover() | {"SCRATCH"}

    def test_every_block_reading_a_handoff_value_reloads_it(self):
        names = self._handoff_names()
        for file, number, code in _every_block():
            # Only the block that *writes* the handoff is exempt, not the whole
            # phase. Phase 0's later blocks run in their own calls like any
            # other, and the exemption being phase-wide left the sharpest case
            # of all still open: `git show "$BASE_SHA:.github/workflows/ci.yml"`
            # with $BASE_SHA empty exits **0** and serves 17,623 bytes from the
            # index — the user's working tree — which is the exact failure
            # Phase 0's "read every one of them at a ref" rule exists to prevent.
            if "--shell >" in code:
                continue
            used = sorted(n for n in USED.findall(code) if n in names)
            if not used:
                continue
            self.assertIn(
                "phase0.env",
                code,
                f"{file} Phase {number} reads {used} in a block that never sources "
                f"the handoff. Shell state does not cross a Bash call, so every one "
                f"of those is the empty string when the block runs on its own",
            )

    def test_the_scratch_derivation_is_stated_identically_wherever_it_appears(self):
        """Seven copies of a formula is seven places for it to drift.

        The failure this guards is not hypothetical in shape: 0.26.0 changed the
        derivation once already, and a copy left on the old `mktemp` form would
        point a later phase at a directory the handoff is not in — which is the
        same silent-empty outcome, reached through a different door.
        """
        # The assignment alone, not the line it sits on: Phase 0 appends
        # `mkdir -p` to its copy and a later block has nothing to create.
        forms = {
            found for _, _, code in _every_block() for found in re.findall(r'SCRATCH="[^"]*"', code)
        }
        self.assertEqual(
            len(forms),
            1,
            f"the $SCRATCH derivation appears in {len(forms)} different forms; every "
            f"call has to resolve it to the same directory, so they must be one "
            f"string: {sorted(forms)}",
        )

    def test_a_declared_unset_fallback_is_implemented_where_the_block_gates(self):
        """The measured silent failure, stated as the document's own broken promise.

        `for c in $BOT_COMMITS` over an unset variable iterates zero times and the
        gate passes with an empty file list. `SKILL.md` already knows this and
        says what to do instead — *"Where `$BOT_COMMITS` is unset, gate on the
        whole `$BASE_SHA..pr-<N>` diff"* — but the fallback lived only in prose,
        so the runnable block did the silent thing.

        Keyed on the prose declaring a fallback rather than on a name: that is
        what separates `$BOT_COMMITS`, which gates, from `$HUMAN_COMMITS`, which
        is reported and where iterating zero times is the truthful answer. Any
        future output whose absence the prose promises to handle is covered.
        """
        loop = re.compile(r"for\s+\w+\s+in\s+\$\{?([A-Z_][A-Z0-9_]*)\}?")
        names = self._handoff_names()
        bodies = dict(self.phases)
        seen = 0
        for file, number, code in _every_block():
            if file != SKILL.name:
                continue
            for name in loop.findall(code):
                if name not in names:
                    continue
                # Does this phase promise to handle the value being unset?
                if not re.search(rf"\${name}[^\n]*is unset", bodies.get(number, "")):
                    continue
                seen += 1
                self.assertRegex(
                    code,
                    re.compile(rf"\$\{{{name}[+:-]|-z\s+\"?\${name}|-n\s+\"?\${name}"),
                    f"{file} Phase {number} promises a fallback for an unset ${name} "
                    f"and then iterates it anyway; unset it iterates zero times and "
                    f"the gate passes silently, which is worse than a false Hold",
                )
        self.assertGreater(seen, 0, "no phase declares an unset-fallback for a value it gates on")


if __name__ == "__main__":
    unittest.main()
