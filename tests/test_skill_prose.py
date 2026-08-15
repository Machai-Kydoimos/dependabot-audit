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
prints a full `--help` and then refuses at exit 0, so reading the help is not
checking availability.

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
EXTERNAL = {"CLAUDE_PLUGIN_ROOT", "HOME", "PATH", "IFS"}

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

        `traps.md` and `report-template.md` carry no `## Phase N` headings, so
        they contribute nothing here — deliberately. They are cross-cutting, and a
        guard that swept them would match almost anything.
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
        self.assertIn(
            "base_ref_force_pushed",
            self.shell[0],
            "Phase 0 must check whether the base branch was rewritten; merge-base "
            "cannot tell you on its own",
        )
        self.assertRegex(
            self.shell[0],
            r"git log[^\n]*\$BASE_SHA\.\.pr-<N>",
            "Phase 0 must attribute the commits above the merge base; a genuine "
            "bot PR is one commit by the bot",
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
        self.assertIn(
            "baseRefOid",
            self.shell[0],
            "Phase 0 must take the base commit from the PR; a merge base against "
            "$DEFAULT is the PR's own head once it has landed",
        )
        self.assertNotRegex(
            self.shell[0],
            r"git merge-base \"\$DEFAULT\"",
            "the local default branch as the left-hand side is the collapsing form",
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


if __name__ == "__main__":
    unittest.main()
