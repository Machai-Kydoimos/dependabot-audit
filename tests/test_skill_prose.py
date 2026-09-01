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
import subprocess
import sys
import tempfile
import unittest
from typing import ClassVar

ROOT = pathlib.Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "skills/dependabot-audit"
SKILL = PLUGIN / "SKILL.md"

# Names the skill is entitled to use without defining, so the forward-reference
# guard must not read them as outputs one phase owes another. `TMPDIR` joined them
# in 0.26.0: Phase 0 derives `$SCRATCH` under `${TMPDIR:-/tmp}` so the path is the
# same on every call, and macOS sets TMPDIR where Linux leaves it unset.
#
# `CLAUDE_PLUGIN_ROOT` is here on a different footing, and the comment used to say
# "the harness supplies them", which is measurably false — it is empty in every
# shell. What the harness supplies is the *substitution*, into `SKILL.md`'s text
# at load. So it resolves in this one file and the guard below is what keeps it
# there; everywhere else the plugin's scripts are reached through `$SCRIPTS`.
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

# The same handoff as the reader now spells it outside Phase 0's bootstrap:
# `$SCRIPTS` is the Phase 0 output carrying this plugin's own `scripts/`. The
# optional `:?message` is bash's fail-on-unset, which the blocks use so an
# unsourced handoff stops rather than running `/ci_state.py`.
SCRIPTS_PATH = re.compile(r"\$\{?SCRIPTS(?::[^}]*)?\}?/([A-Za-z0-9_.-]+)")

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


def _code_only(source: str, *, printed: bool = True) -> str:
    """A Python module's executable text, with comments and docstrings removed.

    An AST round-trip drops comments for free; docstrings have to be taken off
    each scope explicitly. Both matter for the same reason: a guard asserting
    that a phase *asks GitHub which checks are required* must not be satisfied by
    a docstring that merely says so.

    `printed=False` additionally drops the string constants a script hands to
    `print()`. **That is for negative assertions only, and it is not the default.**
    What a script prints is output, not a call — `audit.py` advises the reader to
    run ``uv run python -V`` inside the synced environment, and once `reachable()`
    began following scripts named in the ecosystem references, that sentence made
    the `--no-execute` guard fire on Phase 1: the guard that catches a phase
    *executing* the audited project, defeated by a phase *mentioning* it. Same
    failure the harness docstring names, one artifact over.

    It stays opt-in because printed text is exactly what several positive guards
    are about — Phase 6's hedge is asserted as `CONSISTENT WITH` in `reachable(6)`
    precisely so the hedge reaches the reader of the output and not only the
    reader of the prose. Stripping it by default silently retired that guard,
    which is how this parameter came to exist rather than a blanket rule.
    """
    tree = ast.parse(source)
    if not printed:
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "print"
            ):
                node.args = [
                    arg
                    for arg in node.args
                    if not (isinstance(arg, ast.Constant) and isinstance(arg.value, str))
                ]
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


def _scripts_named_in(text: str) -> list[pathlib.Path]:
    """Every plugin script `text` invokes, under either spelling.

    Two exist because only one of them works in only one place. Phase 0
    bootstraps with `${CLAUDE_PLUGIN_ROOT}`, substituted into `SKILL.md`'s text at
    skill load; everything after it reads `$SCRIPTS` out of the handoff, because
    a file the harness never injects reaches the shell with the token intact and
    an empty variable.

    Kept in one function because `reachable()` and the path-existence guard both
    ask it, and because forgetting the second spelling is not a loud failure:
    converting Phase 6's `C=` line silently emptied `reachable(6)`, and four
    guards asserting on `ci_state.py`'s query went to matching nothing. That is
    the exact defect the `reachable()` docstring was written for, one spelling
    later.
    """
    found = []
    for rel in PLUGIN_PATH.findall(text):
        found.append(ROOT / rel)
    for name in SCRIPTS_PATH.findall(text):
        found.append(PLUGIN / "scripts" / name)
    return [p for p in found if p.suffix == ".py" and p.exists()]


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

    def reachable(self, number: int, *, printed: bool = True) -> str:
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

        **A script named in an ecosystem reference counts too.** Until 0.36.0
        this followed scripts named in `SKILL.md`'s own body and nowhere else,
        while `audit.py`, `gate_diff.py` and `changelog.py` are all invoked from
        `references/uv-lock.md` — so their code was in no phase's `reachable()`
        and a guard about any of them was reading the invocation line alone. That
        is the retirement this docstring warns about, sitting in the function
        that warns about it.
        """
        parts = [self.shell[number]]
        named = dict(self.phases)[number]
        for name, section in self._handoffs(number):
            del name
            parts.extend(bash_blocks(section))
            named += "\n" + section
        for path in _scripts_named_in(named):
            parts.append(_code_only(path.read_text(encoding="utf-8"), printed=printed))
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
            "reads the bot's own commits",
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

    # Phases doing ecosystem-specific work. Phase 6 is ecosystem-independent by
    # construction and stays off the list.
    #
    # Phase 2 joined in 0.30.0. Its uv answer used to be nothing but the Phase 1
    # script's `latest` line, which is why it was exempt — but the scope half of
    # the phase is per-ecosystem and always was: a vendored crate is read out of
    # a wheel's SBOM, a moving major tag out of a `compare`. Both had a home in
    # `actions.md` and only one had one anywhere.
    SPLIT_PHASES = (1, 2, 3, 4, 5)
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
    fork's artifacts **of the packages it audits** — which is the changed set,
    not the lockfile. The install then covers only the resolution matching the
    interpreter present — so a green row on 3.14 says nothing about whether the
    3.11 fork's artifacts fetch or install, and nothing in the report
    distinguished the two.

    The unqualified version of that sentence stood here until 0.32.0 and was the
    same claim the reference made; #88 is what it cost.

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

        The quotation is *read out of the prose* rather than written here as a
        third copy. Hardcoding it meant the literal lived in two places and this
        test passed by agreeing with itself; derived, a reworded script fails
        until the reference is brought along, which is the coupling it exists for.
        """
        # Fenced blocks first: ``` pairs off against the inline spans and drags
        # the scan out of alignment, which reads as "the prose stopped quoting
        # it". The quotation has to be in prose anyway — a heading named inside
        # a bash block is not the reader being told where to look.
        phase5 = re.sub(r"```.*?```", "", self.material(5), flags=re.DOTALL)
        # Whitespace-collapsed on both sides: the quotation wraps across a line
        # in the Markdown, and a guard that a reflow can break is one that gets
        # deleted rather than fixed.
        quoted = [
            " ".join(q.split())
            for q in re.findall(r"`([^`]+)`", phase5)
            if q.lstrip().startswith("forked packages")
        ]
        self.assertEqual(
            len(quoted), 1, "Phase 5 should quote audit.py's fork heading exactly once"
        )
        script = " ".join((PLUGIN / "scripts/audit.py").read_text(encoding="utf-8").split())
        self.assertIn(
            quoted[0],
            script,
            "Phase 5 quotes a line audit.py no longer prints",
        )

    def test_the_prose_names_both_groups_the_script_prints(self):
        """The fork list is lockfile-wide and split; the prose must carry both.

        Widening the list without the split is what produced the defect it fixes:
        an unaudited fork printed beside audited ones, and a report that called
        it "verified by Phase 1". The heading a reader has to *not* misread is
        the unaudited one, so that is the one held here.
        """
        script = (PLUGIN / "scripts/audit.py").read_text(encoding="utf-8")
        self.assertIn("NOT audited by this run", script)
        self.assertIn(
            "NOT audited by this run",
            self.material(5),
            "Phase 5 must name the group heading whose rows it forbids calling verified",
        )

    def test_an_unaudited_fork_is_not_described_as_verified(self):
        """The reference modelled the exact wrong sentence.

        `audit.py` only ever audits the changed set, so "the 3.11 fork of
        `rpds-py` was verified but not installed" — about a package no bump
        touched — asserts a check that never ran. It was the example row, and a
        real #365 report reproduced it almost verbatim.
        """
        phase5 = self.material(5)
        self.assertNotIn(
            "fork of `rpds-py` was verified",
            phase5,
            "the example sentence claims Phase 1 verified an unchanged package",
        )
        self.assertIn(
            "neither audited nor installed",
            phase5,
            "the worked example must model the unaudited-fork wording, not the verified one",
        )


class TestPhase5SalvagesARefusedNoBuild(SkillHarness):
    """A refused `--no-build` has one documented answer, not two improvised ones.

    `uv sync --locked --no-build --no-install-project` fails when a *dependency*
    ships only an sdist, and the reference described that as an accepted loss.
    Two consecutive replays of `fpga-board-sim` #365 then handled the same
    refusal differently: round 10 fell back to a plain `uv sync --locked` and
    dropped the wheels claim entirely, round 11 improvised `--no-build-package`
    for 36 of 38 packages. Both are defensible readings, which is the defect —
    Phase 5's own text says "'Frozen install passed' is not the same claim in
    the two cases", so this row must not be non-deterministic across runs.

    Held structurally, per the 0.31.0 lesson: a guard that only asks whether the
    phase *mentions* `--no-build-package` passes against a sentence that never
    elicits the call. The block has to build the exclusion and run the sync.
    """

    def _salvage_block(self) -> str:
        for _, section in self._handoffs(5):
            for block in bash_blocks(section):
                if "--no-build-package" in block:
                    return block
        self.fail("Phase 5 documents no block that runs the narrowed --no-build")

    def test_the_salvage_is_a_whole_invocation_not_a_flag_to_mention(self):
        block = self._salvage_block()
        self.assertIn("uv sync --locked", block, "the salvage has to reach an actual sync")
        self.assertIn(
            "uv.lock",
            block,
            "the exclusion list is derived from the lockfile; a hand-written list is "
            "the improvisation this replaces",
        )

    def test_the_salvage_excludes_the_wheeled_packages_not_the_offender(self):
        """The inverse is the easy mistake and it is a no-op.

        `--no-build-package <offender>` refuses the build that was already
        failing. The recipe has to name every package that *has* a wheel, so the
        offenders are the only source builds left.

        The documented snippet is **run**, against a lockfile with a known
        sdist-only package, rather than matched for substrings. Inverting the
        condition to `not p.get("wheels")` leaves every substring in place — a
        pattern-matching guard cannot tell this recipe from the one that does
        nothing, which is the distinction the whole issue is about.
        """
        match = re.search(r"<<'PY'\n(.*?)\nPY\n", self._salvage_block(), re.DOTALL)
        if match is None:
            self.fail("the salvage derives its list with an embedded PY heredoc")
        snippet = match.group(1)

        # Every `source` kind uv 0.12.7 was observed to emit, so the local/remote
        # split is falsifiable here rather than asserted: `url` carries wheels and
        # must be excluded, `directory` and `editable` must not be.
        lock = """
[[package]]
name = "wheeled-one"
version = "1.0"
source = { registry = "https://pypi.org/simple" }
wheels = [{ url = "https://x/w1-1.0-py3-none-any.whl", hash = "sha256:aa", size = 1 }]

[[package]]
name = "sdist-only"
version = "2.0"
source = { registry = "https://pypi.org/simple" }
sdist = { url = "https://x/s-2.0.tar.gz", hash = "sha256:bb", size = 1 }

[[package]]
name = "url-wheeled"
version = "3.0"
source = { url = "https://x/u-3.0-py3-none-any.whl" }
wheels = [{ url = "https://x/u-3.0-py3-none-any.whl", hash = "sha256:cc", size = 1 }]

[[package]]
name = "git-built"
version = "4.0"
source = { git = "https://example.invalid/g" }

[[package]]
name = "path-dep"
version = "5.0"
source = { directory = "vendor/path-dep" }
wheels = [{ url = "https://x/p-5.0-py3-none-any.whl", hash = "sha256:dd", size = 1 }]

[[package]]
name = "half-forked"
version = "6.0"
source = { registry = "https://pypi.org/simple" }
resolution-markers = ["python_full_version >= '3.11'"]
wheels = [{ url = "https://x/h-6.0-py3-none-any.whl", hash = "sha256:ee", size = 1 }]

[[package]]
name = "half-forked"
version = "0.9"
source = { registry = "https://pypi.org/simple" }
resolution-markers = ["python_full_version < '3.11'"]
sdist = { url = "https://x/h-0.9.tar.gz", hash = "sha256:ff", size = 1 }

[[package]]
name = "the-project"
version = "0.1.0"
source = { editable = "." }
"""
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "uv.lock"
            path.write_text(lock, encoding="utf-8")
            done = subprocess.run(
                [sys.executable, "-", str(path)],
                input=snippet,
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(done.returncode, 0, done.stderr)
        total, *wheeled = done.stdout.split()
        self.assertEqual(
            wheeled,
            ["wheeled-one", "url-wheeled"],
            "the exclusion list is every REMOTE package that has wheels. `sdist-only` "
            "and `git-built` are the concessions; `half-forked` has a fork with no "
            "wheel so uv must build it; `path-dep` and `the-project` are local and "
            "must never be excluded, whatever they carry",
        )
        self.assertEqual(
            total,
            "5",
            "the denominator counts PACKAGES, not lockfile blocks: five remote "
            "packages across six blocks, because `half-forked` is forked. Counting "
            "blocks inflates the row and passes the duplicate to "
            "--no-build-package twice",
        )

    def test_the_boundary_is_stated_with_the_recipe(self):
        """The narrowing proves less than a true `--no-build`, and a row that
        does not say so overstates in the direction this phase exists to stop."""
        material = self.material(5)
        self.assertIn(
            "fails fast",
            material,
            "uv names one refused package per run, so the reader needs to know it may "
            "have to re-run rather than concluding the set is complete",
        )
        self.assertRegex(
            material,
            r"[Pp]roves less than a true `--no-build`",
            "the conceded package's build code did run; the report must not read as "
            "'no third-party build code ran'",
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
        """Moved from the shell to the prose in 0.29.0, with the rule itself.

        The block used to iterate `$BOT_COMMITS` and the reader judged the file
        list; `discover.py` derives both now, so what `SKILL.md` still owes the
        reader is *which diff the gate was taken from* — the claim the report
        makes. `test_discover.py` holds the mechanism.
        """
        self.assertIn(
            "$BOT_COMMITS",
            dict(self.phases)[1],
            "the gate's invariant is 'did the bump reach past the manifest and "
            "lockfile', not 'did this branch' — and only the bot's commits are "
            "the bump",
        )
        self.assertIn(
            "$SCOPE_GATE",
            self.shell[1],
            "and the phase has to read the derived answer, or the gate is stated "
            "and never consulted",
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
    # The property is **being pinned**, not which plumbing does the pinning.
    # `git show` reads a file at a ref, `git ls-tree` lists a directory at one —
    # both in `<ref>:<path>` form — and `git diff <ref>...<ref> -- <path>` names
    # two refs and reads neither working tree. 0.36.0 added one of each while
    # deriving the workflow list, and a `show`-only pattern called both
    # working-tree reads.
    AT_A_REF = re.compile(
        r'git (?:show|ls-tree[^"\n]*) "(?:pr-<N>|\$\{?BASE_SHA\}?|\$\{?DEFAULT\}?):'
        r'|git diff[^"\n]*"\$\{?BASE_SHA\}?\.{2,3}pr-<N>"'
    )

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


class TestPhase4DoesNotReadItsOwnResidueAsAFinding(SkillHarness):
    """The gate writes into the tree Phase 4 is measuring it in.

    0.30.0 dropped `--no-project` for a gate that imports the project, which is
    what makes this live: a type checker or a test suite leaves caches and data
    files behind, and the phase compares the tree before and after.

    The intuition is that the cache directories are the hazard, and measurement
    says they are the one shape that never was. `.mypy_cache/`, `.ruff_cache/`
    and `.pytest_cache/` each carry a `.gitignore` of `*` the tool writes
    itself; any other untracked directory is collapsed to `dir/` by
    `git status --porcelain` and then dropped, because a directory has no
    content to hash. What does reach the comparison is a *file* -- and a real
    `coverage 7.6.1 -> 7.13.0` bump reports `~ .coverage`, *both act, different
    result*, which is the destructive-fix vocabulary spent on coverage's own
    data file.

    So the guard is that the reference gives a neutralisation in the command
    rather than a `.gitignore` audit: the two runs have to be treated alike for
    the comparison to mean anything, and only the command reaches both.
    `tests/test_gate_diff.py` pins the mechanics this prose describes.
    """

    def test_the_neutralisation_is_something_the_run_carries(self):
        material = self.material(4)
        for token in ("PYTHONDONTWRITEBYTECODE", "COVERAGE_FILE"):
            self.assertIn(
                token,
                material,
                f"{token} is how a run stops writing residue into the measured "
                "tree; a check on the repo's .gitignore reaches neither run",
            )

    def test_the_residue_that_actually_reaches_the_comparison_is_named(self):
        material = self.material(4)
        self.assertIn(
            ".coverage",
            material,
            "the file-shaped residue is the row that fires by default",
        )
        self.assertIn(
            "status.showUntrackedFiles",
            material,
            "and the repo config that un-collapses the directory row is the "
            "only thing that makes the rest of it visible",
        )


class TestPhase4CallsTheScriptForEveryGate(SkillHarness):
    """A `--run` fragment does not elicit the call it is a fragment of.

    Measured, not reasoned: the round-10 replay of `fpga-board-sim` #365 took
    ruff and rumdl through `gate_diff.py` and compared **mypy** -- the
    project-importing gate the `--no-project` exception exists for -- with a bare
    `cd` into the worktree and two `uv run` calls. Every other code block in
    Phase 4 is a whole `python3 "$G" --tree ...` invocation; that one was a lone
    `--run` line, and it read as advice about a flag rather than a call to make.

    The bypass costs more than the flag. It loses `require_clean_worktree`, loses
    the `reset --hard` between runs -- so run two inherits run one -- and loses
    the tree snapshot the phase exists to take. It also silently voided 0.30.1,
    whose neutralisation is a `--run` fragment and so reaches nothing when no
    `--run` is built: that fix shipped green, mutation-checked, and inert.

    So the guard is structural rather than textual. A prose assertion that the
    phase *says* to use the script would have passed against the text that did
    not elicit it -- 298 tests did. This asks that every block teaching a gate
    invocation actually contains one.
    """

    def _phase4_blocks(self) -> list[tuple[str, str]]:
        found: list[tuple[str, str]] = []
        for path in (SKILL, PLUGIN / "references/uv-lock.md"):
            for number, body in phases(path.read_text(encoding="utf-8")):
                if number == 4:
                    found.extend((path.name, code) for code in bash_blocks(body))
        return found

    def _blocks_containing(self, token: str) -> list[tuple[str, str]]:
        hits = [(name, code) for name, code in self._phase4_blocks() if token in code]
        self.assertTrue(hits, f"Phase 4 no longer carries a block containing {token!r}")
        return hits

    def test_every_gate_example_is_a_whole_invocation(self):
        """A block that pins a version under test must also make the call."""
        for token in ("--with mypy", "--with pytest", "--with ruff"):
            for name, code in self._blocks_containing(token):
                self.assertIn(
                    "gate_diff.py",
                    code,
                    f"{name}: the {token} example is a fragment. #85 measured that a bare "
                    "--run line is compared by hand instead, losing the clean-tree guard, "
                    "the reset between runs, and the tree snapshot",
                )
                self.assertIn("--tree", code, f"{name}: {token} example names no tree")

    def test_the_residue_neutralisation_reaches_a_call(self):
        """0.30.1's fix was inert precisely because it sat in a fragment."""
        for name, code in self._blocks_containing("PYTHONDONTWRITEBYTECODE"):
            self.assertIn(
                "gate_diff.py",
                code,
                f"{name}: the neutralisation only takes effect on a --run the script is "
                "handed; in a fragment it is text",
            )


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

    # The mechanism, wherever it lives. Phase 7 was three git commands until
    # 0.35.0 and is `cleanup.py` from then on. A guard keyed only to the commands
    # went green on a Phase 7 that no longer contained them — the retirement
    # `reachable()`'s own docstring warns about, arrived at for real rather than
    # hypothetically, and caught by this suite on the commit that mechanised it.
    CLEANUP = re.compile(r"git worktree remove|git branch -D|cleanup\.py")

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
            self.reachable(7),
            self.CLEANUP,
            "Phase 7 is the only phase reached on every path, including the Phase 1 "
            "gate stop; the cleanup belongs there",
        )

    def test_every_artifact_phase_0_creates_is_removed(self):
        """Removing the worktrees and leaving the branch still accumulates.

        Read through `reachable`, because the names moved into `cleanup.py`'s
        `WORKTREES` when Phase 7 was mechanised. `_code_only` strips docstrings and
        comments, so the script's own explanation of the tuple cannot satisfy this
        — only the tuple can. `tip-<N>` is in the list now: Phase 0 creates it when
        the base was rewritten, and the prose used to ask the reader to add a line
        for it, which is the kind of instruction that gets skipped.

        Matched quote-agnostically: `_code_only` normalises source through
        `ast.unparse`, so a double-quoted argv in the script arrives here
        single-quoted and a literal match silently fails.
        """
        material = self.reachable(7)
        for artifact in ("pr-{n}", "base-{n}", "tip-{n}"):
            self.assertIn(artifact, material, f"{artifact} is created in Phase 0 and never removed")
        self.assertRegex(
            material,
            r"""['"]branch['"],\s*['"]-D['"]""",
            "the fetched ref outlives the worktrees",
        )

    def test_the_cleanup_exit_code_is_read_not_chained(self):
        """`cleanup.py … && next` turns a finding into a swallowed error.

        Exit 1 means residue was found and the worktree was still removed. It is
        the same shape as Phase 5's `cmd | tail && next` trap, one phase over, and
        the same shape as `gate_diff.py`'s 1-is-a-finding contract — so the phase
        that consumes it has to say which of 0, 1 and 2 it is looking at.
        """
        phase7 = dict(self.phases)[7]
        self.assertIn(
            "do not chain on it",
            phase7,
            "Phase 7 must tell the reader that exit 1 is a finding, not a failure",
        )
        self.assertNotRegex(
            self.shell[7],
            r"cleanup\.py[^\n]*&&",
            "chaining on cleanup.py hides the residue exit",
        )

    def test_phase_5_says_its_gates_may_dirty_the_tree(self):
        """Otherwise the run tidies up and destroys the finding before Phase 7.

        Phase 4 restores `base-<N>` after every gate run; nothing restores
        `pr-<N>`, deliberately. A run that reads the dirt as its own mess and
        `git checkout .`s it has thrown away the strongest row Phase 5 produces.
        """
        self.assertIn(
            "do not tidy it yourself",
            dict(self.phases)[5],
            "Phase 5 must say the fix-mode residue is expected and is Phase 7's to report",
        )


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

    # `Requires:` as well as `Requires from Phase 0:`. Phase 2 used the shorter
    # form and named its input in prose — `the PR's ``createdAt`` from Phase 0` —
    # so it slipped this guard on punctuation, twice over: the phrase did not
    # match, and the name was neither `$`-prefixed nor upper-case.
    REQUIRES = re.compile(r"\*Requires(?: from Phase 0)?:(.*)", re.IGNORECASE)
    # A bare backticked identifier in a requires-line: the shape `createdAt` had.
    BARE = re.compile(r"`([A-Za-z_][A-Za-z0-9_]*)`")

    def _produced(self) -> set[str]:
        """What a later phase can actually receive: the emitter, plus Phase 0's shell.

        The second half is not padding — `$SCRATCH` is assigned by Phase 0
        directly and never passes through `discover.py`, so a guard reading only
        the emitter would call the most-used output of all a broken promise.
        """
        # Comment lines stripped, and that is load-bearing rather than tidy.
        # Phase 0 documents the whole handoff in a bash fence of `#   NAME=<...>`
        # lines, so an un-stripped scan lets a requires-line be satisfied by the
        # very key list it is meant to be checked against. Caught by mutation:
        # deleting `CREATED_AT` from the emitter left every guard green, because
        # the comment describing it was still there. Same failure `_code_only`
        # exists for one file over — a rule must not be satisfiable by a comment
        # claiming it.
        executable = "\n".join(
            line for line in self.shell[0].splitlines() if not line.lstrip().startswith("#")
        )
        return _emitted_by_discover() | set(ASSIGNED.findall(executable))

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

    def test_no_requires_line_names_an_input_in_prose_instead_of_as_a_variable(self):
        """The convention is the shell name, because that is what has to exist.

        Phase 2's line read *"the PR's `createdAt` from Phase 0"*. `discover.py`
        renders `createdAt` in its human-readable report and the emitter never
        wrote it, so the input crossed by being on screen. Naming the variable is
        what puts a requires-line under the guard above at all.
        """
        for number, body in self.phases:
            for line in body.splitlines():
                found = self.REQUIRES.search(line)
                if not found:
                    continue
                for name in self.BARE.findall(found.group(1)):
                    self.fail(
                        f"Phase {number} requires `{name}`, named as prose rather than "
                        f"as the variable the handoff carries. A name without a `$` is "
                        f"one no guard can check and no block can source"
                    )

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
        if not seen:
            # From 0.29.0 nothing in SKILL.md hand-iterates a gating handoff
            # value — `discover.py` derives the gate and its fallback. The
            # per-loop check above still covers anything that comes back; this
            # is what stops the class becoming a silent no-op in the meantime.
            self.assertIn(
                "$SCOPE_GATE",
                dict(self.phases)[1],
                "nothing gates on a handoff list any more and Phase 1 does not read "
                "the derived gate either — the gate has gone missing rather than moved",
            )


class TestEveryEmittedOutputIsConsumedOrDeclaredInert(SkillHarness):
    """0.26.1 closed this class in one direction only.

    Its two guards run table-and-requires-line -> emitter: every name a phase
    *promises* must be one Phase 0 produces. Nothing ran the other way, and the
    drift was already bidirectional when it shipped — the changelog says so:

        the table promised `$PERMS` and `$SCRATCH` while the emitter writes ten
        names including `BASE_REF`, `BRANCH_POINT` and `MAY_EXECUTE`.

    `BASE_REF` was settled there deliberately — "Phase 0's own cross-check that
    no later phase consumes" — and that is a fine answer. `MAY_EXECUTE` was not
    in the same position. Phase 0's prose calls it *"the one bit later phases
    actually branch on"*, offered as the value that crosses in `$PERMS`'s place,
    and no phase branched on it: every occurrence in the plugin was the emit, the
    key-list comment, and two sentences explaining why it exists.

    So the completeness rule now runs both ways, with the exemption written down
    rather than inferred: an emitted name is consumed, or it is named as a
    diagnostic. That is what separates the two cases, and it is a decision
    someone has to make rather than an omission nobody notices.
    """

    DIAGNOSTIC = re.compile(r"Diagnostics? —[^\n]*", re.IGNORECASE)

    def _declared_inert(self) -> set[str]:
        line = self.DIAGNOSTIC.search(dict(self.phases)[0])
        return set(re.findall(r"`\$?([A-Z_][A-Z0-9_]*)`", line.group(0))) if line else set()

    def test_every_emitted_name_is_read_somewhere_or_declared_inert(self):
        inert = self._declared_inert()
        read = {n for _, _, code in _every_block() for n in USED.findall(code)}
        for name in sorted(_emitted_by_discover()):
            if name in inert:
                continue
            self.assertIn(
                name,
                read,
                f"discover.py --shell emits {name} and no block reads it. Either a "
                f"phase should branch on it or Phase 0 should name it a diagnostic, "
                f"the way BASE_REF is — an emitted value nobody reads and nobody "
                f"exempted is a promise that quietly went false",
            )

    def test_the_diagnostics_line_names_only_things_that_are_emitted(self):
        """The exemption must not drift into covering a name that no longer exists."""
        emitted = _emitted_by_discover()
        for name in sorted(self._declared_inert()):
            self.assertIn(
                name,
                emitted,
                f"Phase 0 exempts {name} as a diagnostic, but the emitter does not "
                f"write it; a stale exemption silently widens to whatever is added next",
            )


class TestTheExecutionGateIsReadWhereExecutionHappens(SkillHarness):
    """Phase 0 decided whether the PR may run, and the phases that run it never asked.

    `discover.py` derives the classification — a fork PR, a non-bot author, or an
    account without `push` — and reduces it to `MAY_EXECUTE`. The gate then lived
    only in Phase 0's own prose table, which tells the reader to *run
    `--no-execute`*, six phases before the phases that execute.

    Measured on this plugin's handoff: `MAY_EXECUTE=yes` crosses on every audit
    and nothing has ever read it.
    """

    def _executing(self) -> list[int]:
        return [n for n, body in self.phases if n > 0 and "Executes code from the PR" in body]

    def test_the_phases_that_execute_say_so_and_gate_on_it(self):
        found = self._executing()
        self.assertTrue(found, "no phase declares that it executes the PR's code")
        for number in found:
            self.assertIn(
                "MAY_EXECUTE",
                self.reachable(number),
                f"Phase {number} declares that it executes code from the PR and never "
                f"reads the bit Phase 0 derived to authorise it",
            )

    def test_the_gate_fails_closed_on_an_empty_value(self):
        """An unset handoff yields the empty string, not `no`.

        From the measurement rather than the fix: a block whose handoff never
        loaded sees `MAY_EXECUTE` unset, and `!= no` is true of the empty string.
        The test has to be *for* the authorising value, so anything that is not
        `yes` — including nothing at all — refuses.
        """
        seen = 0
        for file, number, code in _every_block():
            # Uses it, rather than merely naming it: Phase 0's key-list block
            # documents `MAY_EXECUTE=<yes|no>` and tests nothing.
            if not re.search(r"\$\{?MAY_EXECUTE", code):
                continue
            seen += 1
            self.assertRegex(
                code,
                re.compile(r"MAY_EXECUTE[^\n]*=\s*[\"']?yes"),
                f"{file} Phase {number} tests MAY_EXECUTE without testing for `yes`; "
                f"an unset handoff is the empty string, and a negative test passes it",
            )
        self.assertGreater(seen, 0, "no block gates on MAY_EXECUTE")


def _label_values(script: str, function: str, key: str) -> set[str]:
    """Every value one function can put in `key` — its label vocabulary.

    Read from the script's source rather than typed, for the reason the
    required-context guard is: a typed list is a second copy, and it drifts
    silently because the guard keeps passing against its own stale copy.

    Narrow on purpose. A first version collected every lower-case string
    constant in the function and came back with `compared`, `basis`, `login`
    and `sha` — dict keys and API field names — which would have demanded rows
    in Phase 7 for things that are not labels at all. Only two shapes carry a
    label: a `"key": value` entry, and an assignment to a variable of that name.
    Module-level constants (`UNDERIVABLE = "underivable"`) are resolved.
    """
    src = (PLUGIN / "scripts" / script).read_text(encoding="utf-8")
    tree = ast.parse(src)
    consts = {
        t.id: n.value.value
        for n in tree.body
        if isinstance(n, ast.Assign) and isinstance(n.value, ast.Constant)
        for t in n.targets
        if isinstance(t, ast.Name) and isinstance(n.value.value, str)
    }

    def literal(node: ast.AST) -> set[str]:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return {node.value}
        if isinstance(node, ast.Name) and node.id in consts:
            return {consts[node.id]}
        if isinstance(node, ast.IfExp):
            return literal(node.body) | literal(node.orelse)
        return set()

    for node in ast.walk(tree):
        if not (isinstance(node, ast.FunctionDef) and node.name == function):
            continue
        found: set[str] = set()
        for inner in ast.walk(node):
            if isinstance(inner, ast.Dict):
                for k, v in zip(inner.keys, inner.values, strict=True):
                    if isinstance(k, ast.Constant) and k.value == key:
                        found |= literal(v)
            elif isinstance(inner, ast.Assign):
                names = [t.id for t in inner.targets if isinstance(t, ast.Name)]
                if key in names:
                    found |= literal(inner.value)
                for tgt in inner.targets:
                    if isinstance(tgt, ast.Tuple) and isinstance(inner.value, ast.Tuple):
                        for t, v in zip(tgt.elts, inner.value.elts, strict=False):
                            if isinstance(t, ast.Name) and t.id == key:
                                found |= literal(v)
        if not found:
            raise AssertionError(f"{script}:{function} sets no `{key}` this guard can read")
        return found
    raise AssertionError(f"{script} has no function {function}")


class TestEveryDerivedLabelLandsInTheVerdictTable(SkillHarness):
    """Phase 7's table is the reason two audits on the same evidence agree.

        Every row above is a finding; the verdict is a function of them, and
        leaving that function implicit is how two audits with the same evidence
        reach different recommendations.

    A label the mechanised phases can print and the table does not name falls
    through to the last row — *"Everything derived, nothing above matched →
    Merge as-is"* — and arrives there by exhaustion, which reads in the report
    exactly like arriving there because nothing was wrong.

    Three were in that state. `attributable` and `pre-existing` had rows;
    **`underivable` did not**, so a red *required* check whose cause could not be
    established fell through to `Merge as-is` on a PR that cannot merge. And
    `discover.py` reports `rewritten` and `suspect` as findings, with no row for
    either.

    The table already handles the fourth case of this shape explicitly —
    `$HUMAN_COMMITS` is "a **finding to report**, never a Hold" — which is the
    pattern: where a finding must not carry the verdict, the procedure says so
    rather than trusting the reader to reach the fall-through and read it right.
    """

    def _phase7(self) -> str:
        return dict(self.phases)[7].lower()

    def _verdict_rows(self) -> list[str]:
        """Rows of the table headed `| Evidence | Verdict |`, lower-cased.

        The whole phase is the wrong haystack, and mutation said so: deleting the
        `underivable` attribution row left the guard green, because the word also
        appears in the confidence table and in the `BRANCH_POINT` row two lines
        down. A label has to be found in a row that is *about* it.
        """
        for rows in tables(dict(self.phases)[7]):
            if rows and "verdict" in rows[0].lower() and "evidence" in rows[0].lower():
                return [r.lower() for r in rows[2:]]
        raise AssertionError("Phase 7 has no `| Evidence | Verdict |` table")

    def _assert_row(self, label: str, about: str, why: str) -> None:
        """A row naming both the label and what it is a label *of*."""
        self.assertTrue(
            any(label.lower() in r and about.lower() in r for r in self._verdict_rows()), why
        )

    def test_every_attribution_label_has_a_verdict_row(self):
        labels = _label_values("ci_state.py", "attribute", "label")
        self.assertIn("underivable", labels, "the three-state label set moved")
        for label in sorted(labels):
            self._assert_row(
                label,
                "check",
                f"ci_state.py can label a red context `{label}` and no verdict row "
                f"names that label against a check, so a red required check carrying "
                f"it falls through to Merge as-is",
            )

    def test_every_branch_point_finding_has_a_verdict_row(self):
        """`ok` is the no-finding case; the rest are what `analyse()` reports."""
        verdicts = _label_values("discover.py", "branch_point", "verdict") - {"ok"}
        self.assertEqual(
            verdicts,
            {"rewritten", "suspect", "underivable"},
            "branch_point's verdict vocabulary moved; the table has to move with it",
        )
        for verdict in sorted(verdicts):
            self._assert_row(
                verdict,
                "branch_point",
                f"discover.py reports `{verdict}` as a finding and no verdict row "
                f"names it against BRANCH_POINT; the reader reaches the fall-through",
            )

    def test_a_pin_that_is_not_a_sha_has_a_verdict_row(self):
        """The instance, and it is the one that costs most to leave out.

        `references/actions.md`: a tag or branch pin is "a **promise someone else
        can revoke**", and "a repo whose pins are not evidence". Without a row,
        *we verified the pin* and *this repo's pins are not evidence* produce the
        same verdict and the report has nothing that separates them.
        """
        self.assertRegex(
            self._phase7(),
            re.compile(r"not a 40-hex sha|not sha-pinned|tag or branch"),
            "actions.md makes a mutable pin a finding and the verdict table has no "
            "row for it, so it lands on Merge as-is by exhaustion",
        )


class TestTheScopeGateChecksWhatProducesItsEvidence(SkillHarness):
    """The gate read its file list through a pipe, so a failing git passed it.

    A pipeline's exit status is its **last** stage. `sort` succeeds on empty
    input, so a `git` that fails yields an empty file list at exit 0 — and an
    empty file list is exactly what the gate reads as *nothing outside the
    manifest and lockfile*. Measured in a real clone:

        out=$(for c in deadbeefdeadbeef; do git show --name-only --format= "$c" \
              2>/dev/null; done | sort -u)
        pipeline exit=0  files=[]

    Both branches had it, the fallback added in 0.28.0 included — so that release
    put a second door on the room it had just locked. Hit by accident on a
    scratch clone where `pr-363` had been deleted: `fatal: ambiguous argument`,
    and exit 0.

    The plugin already states the rule, in Phase 5 and in `uv-lock.md` § Phase 5:
    *"Gate on exit codes. `cmd | tail && next` gates on `tail`, so a failing
    suite sails through."* Phase 1's gate is the highest-stakes command in the
    procedure — it is what stops the audit before Phases 4 and 5 execute the PR's
    code — and it was the one place the rule was not followed.
    """

    # A non-comment statement that runs git and pipes onward — the shape that
    # discards git's status, whether the pipe hangs off the command itself or off
    # the `done` of a loop containing it. Both were present.
    # A single `|`, never `||`: the logical-or is how the fixed form *checks*
    # the status, so a guard that counts it as a pipe fires on the fix.
    PIPED = re.compile(r"^(?!\s*#)[^\n]*\b(?:git|gh)\s[^\n]*(?<!\|)\|(?!\|)", re.MULTILINE)
    # Quoted spans come out first. `--jq '.[] | "\(.x)"'` is jq's pipe inside a
    # single-quoted argument, not a shell pipeline, and four blocks use it.
    QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"")

    def _gate_block(self) -> str:
        """The block that *uses* the split, not the one that documents it.

        `"BOT_COMMITS" in code` picked Phase 0's key-list fence — `#   BOT_COMMITS
        =<shas>` — where there is no git and no pipe, so the pipe guard passed
        against a comment. Requiring a `$` is what separates a use from a mention,
        the same discriminator the MAY_EXECUTE guard needs.
        """
        found = [c for _, _, c in _every_block() if re.search(r"\$\{?HUMAN_COMMITS", c)]
        self.assertTrue(found, "no block reads $HUMAN_COMMITS")
        self.assertEqual(len(found), 1, f"expected one gate block, found {len(found)}")
        return found[0]

    def test_no_block_reads_git_or_gh_output_through_a_pipe(self):
        """Class-wide, because the gate was one of three.

        Measured, each in a real clone:

            Phase 1 gate            exit 0, empty file list  -> "clean scope"
            Phase 6 trigger read    exit 0, empty output     -> "no PR trigger"
            actions.md Phase 4      two empty files, diff 0  -> "no interface change"

        Every one lands on the reassuring answer, which is what makes the class
        worth a guard rather than three fixes.
        """
        for file, number, code in _every_block():
            # Line continuations joined first. `actions.md` wrote the fetch as
            # `gh api … \` / `  | base64 -d > file` — two physical lines, one
            # statement — and a per-line regex misses it, which is how the guard
            # went green against the very instance it was widened for.
            joined = re.sub(r"\\\n\s*", " ", code)
            stripped = self.QUOTED.sub("''", joined)
            for hit in self.PIPED.findall(stripped):
                self.fail(
                    f"{file} Phase {number} pipes git/gh output onward, so the "
                    f"pipeline reports the LAST stage's status and a failure "
                    f"yields empty output at exit 0: {hit.strip()[:70]}"
                )

    # `NAME=$(… git …)` and whatever follows the closing paren.
    CAPTURE = re.compile(r"([A-Z_]+)=\$\((?P<body>(?:[^()]|\([^()]*\))*)\)(?P<after>[^\n]*)")

    def test_every_capture_of_git_output_is_checked(self):
        """Capturing is not enough on its own; something has to act on it.

        Asserted per capture rather than once per block: the block opens with the
        handoff preamble, whose own `|| … exit 2` satisfied a block-wide search
        while the gate's two captures went unchecked. A guard that can be
        satisfied by a *different* line than the one it is about is the same
        failure as one satisfied by a comment.
        """
        block = self._gate_block()
        seen = 0
        for found in self.CAPTURE.finditer(block):
            if "git " not in found.group("body"):
                continue
            seen += 1
            tail = found.group("after") + block[found.end() : found.end() + 120]
            self.assertRegex(
                tail,
                re.compile(r"\|\|\s*(?:\\\n\s*)?\{[^}]*exit 2"),
                f"`{found.group(1)}` captures git output and nothing checks it. "
                f"Exit 2 is this plugin's `could not run`, and it is the honest "
                f"answer: no evidence is not evidence of nothing",
            )
        # One from 0.29.0: the bot half is `discover.py`'s and never reaches a
        # shell. The human half still is, and is still the shape that discards
        # git's status when written as a pipeline.
        self.assertGreaterEqual(seen, 1, "the reported half of the split is still captured")


if __name__ == "__main__":
    unittest.main()


class TestThePluginRootResolvesWhereItIsUsed(SkillHarness):
    """`references/uv-lock.md` named its scripts with a token nothing expands.

    Two measurements this repo already had, never put together:

    - `${CLAUDE_PLUGIN_ROOT}` is substituted into `SKILL.md`'s **text** at skill
      load (0.24.0, settled from a 62,674-character injection whose `D=` line
      reads as an absolute path against a file holding the token on disk).
    - `$CLAUDE_PLUGIN_ROOT` is **empty** in the Bash tool's environment, on
      marketplace install and `--plugin-dir` alike (#52, `ROOT=[]`).

    A reference file is never injected — the model reads it off disk — so the
    token arrives at the shell intact and the path collapses to
    `/skills/dependabot-audit/scripts/audit.py`. Two lines shipped that way from
    0.15.0, and `references/actions.md` happens to contain none, which is why
    three replay rounds on an actions bump never ran one.

    The old guards blessed it: `EXTERNAL` called the variable harness-supplied,
    and the path check asked only whether the *target file* exists. Both pass on
    a line that cannot execute.
    """

    def _docs(self):
        yield SKILL
        yield from sorted((PLUGIN / "references").glob("*.md"))

    def test_the_token_is_absent_from_every_file_the_harness_does_not_inject(self):
        for doc in sorted((PLUGIN / "references").glob("*.md")):
            found = PLUGIN_PATH.findall(doc.read_text(encoding="utf-8"))
            self.assertEqual(
                [],
                found,
                f"{doc.name} names ${{CLAUDE_PLUGIN_ROOT}}/{found[0] if found else ''}. "
                f"Only SKILL.md is substituted at skill load; a reference is read off "
                f"disk, so the token reaches the shell where the variable is empty and "
                f"the path resolves to /skills/... — use $SCRIPTS from the handoff",
            )

    def test_the_bootstrap_happens_once_and_in_phase_0(self):
        """One line may depend on the substitution, because one line has to."""
        whole = SKILL.read_text(encoding="utf-8")
        self.assertEqual(
            1,
            len(PLUGIN_PATH.findall(whole)),
            "SKILL.md should reach the plugin root exactly once — Phase 0's "
            "bootstrap. Every later use is a second dependency on a substitution "
            "that holds in one file and one file only",
        )
        self.assertTrue(
            PLUGIN_PATH.search(dict(self.phases)[0]),
            "the one bootstrap is not in Phase 0, so a phase that runs earlier "
            "than the derivation depends on it",
        )

    def test_every_script_reached_through_the_handoff_exists(self):
        for doc in self._docs():
            for name in SCRIPTS_PATH.findall(doc.read_text(encoding="utf-8")):
                self.assertTrue(
                    (PLUGIN / "scripts" / name).exists(),
                    f"{doc.name} runs $SCRIPTS/{name}, which is not in scripts/",
                )

    def test_the_scripts_directory_is_an_output_phase_0_actually_emits(self):
        self.assertIn(
            "SCRIPTS",
            _emitted_by_discover(),
            "the references name $SCRIPTS and discover.py --shell does not write "
            "it, so every one of them resolves to /audit.py",
        )

    def test_the_directory_is_derived_from_the_script_rather_than_written_down(self):
        """A literal path pins a version; `__file__` cannot name the wrong copy.

        0.23.0 recorded the hazard from the other direction — an invented
        `export CLAUDE_PLUGIN_ROOT=…/0.22.1` pins a release into a cache that
        keeps every older copy, and carried forward it audits with a stale plugin
        silently and successfully. The emitter closes it only while the value
        comes from the file doing the emitting.
        """
        src = (PLUGIN / "scripts/discover.py").read_text(encoding="utf-8")
        emitter = next(
            ast.unparse(node)
            for node in ast.walk(ast.parse(src))
            if isinstance(node, ast.FunctionDef)
            and "Phase 0 outputs. Sourced, not transcribed." in ast.unparse(node)
        )
        line = next(ln for ln in emitter.splitlines() if "SCRIPTS=" in ln)
        self.assertIn(
            "__file__",
            line,
            f"SCRIPTS is emitted as {line.strip()!r} rather than derived from the "
            f"script's own location; a written-down path is one that can name a "
            f"different version than the one running",
        )


class TestPhase5NamesTheGroupsTheInstallCovered(SkillHarness):
    """A frozen install can contain none of the packages under audit and pass.

    `uv sync --locked` installs `tool.uv.default-groups`, which defaults to
    `["dev"]`. Measured on uv 0.12.5 against a project with two groups:

        uv sync --locked                  dev package installed, other absent
        uv sync --locked --group lint     both installed

    So a bump into `lint`, `test`, `docs` — or into anything once
    `default-groups` is narrowed — is not installed, nothing fails, and Phase 5
    reports a green reproduction for an environment that never held the package
    the PR is about.

    The `fpga-board-sim` #365 hand-back proposed `--group dev` as the fix, on the
    reasoning that the plain form installs nothing under audit for a dev bump.
    The table above says otherwise: that repo declares its group as `dev` and no
    `[tool.uv]` at all, so the flag was a no-op and the bump *was* exercised. The
    defect is the row underneath, and a memorised flag does not close it — the
    environment has to be asked, which is what the phase already does for the
    interpreter and the forks.
    """

    def test_the_row_is_qualified_by_which_groups_it_covered(self):
        """Asserted against the qualifier table, not the phase body.

        A body-wide search for "group" passes on `--group`, on `default-groups`,
        and on the word appearing anywhere at all — mutation-checked and it did.
        The claim is narrower than that: the table listing what "reproduced"
        asserts past must carry a row for this, because that table is what a
        writer reads when composing the row.
        """
        qualifiers = [
            table
            for table in tables(self.material(5))
            if any("which install" in row.lower() for row in table)
        ]
        self.assertTrue(qualifiers, "Phase 5 has no table of what qualifies its row")
        rows = "\n".join(qualifiers[0]).lower()
        self.assertIn(
            "which groups",
            rows,
            "the qualifier table names which install, which interpreter and which "
            "forks. A bump outside the default groups is absent from the "
            "environment in the same way and with nothing to show for it, so it "
            "belongs in the same table rather than in prose beside it",
        )

    def test_the_default_is_measured_rather_than_assumed_either_way(self):
        """Both wrong readings are live: that `dev` needs a flag, and that no group does."""
        phase5 = self.material(5)
        self.assertIn(
            "default-groups",
            phase5,
            "Phase 5 must name what the plain command actually installs. Without "
            "it, `--group dev` reads as necessary where it is a no-op and as "
            "sufficient where the group is called something else",
        )

    def test_the_installed_set_is_reconciled_against_the_packages_that_moved(self):
        """Phase 1 derived the set; the environment will say what it has.

        Not `uv pip list` on its own: § Phase 5 has printed the installed
        versions since 0.20.0, to answer *which interpreter*, and a guard
        asserting the command passes without the reconciliation ever being
        written. Mutation-checked, and it did — the same shape as the 0.24.0
        guard satisfied by a narrative quoting the string it looked for.

        What is new is where the filter comes from. A hand-typed list of names
        re-introduces the defect one layer up, because the names would be read
        off the PR title, which a grouped bump does not carry.
        """
        self.assertRegex(
            self.reachable(5),
            r"uv pip list[^\n]*Phase 1 named",
            "nothing checks the bumped packages against what was installed. That "
            "is the one test a wrong dependency group cannot pass, and the set to "
            "check against has to be the one Phase 1 derived",
        )


class TestPhase4DoesNotIsolateAGateThatNeedsTheProject(SkillHarness):
    """`--no-project` was written for ruff and applied to every gate.

    A type checker denied the project's dependencies degrades every expression
    from them to `Any` under `ignore_missing_imports`, and `warn_return_any` —
    implied by `strict = true` — then fires on code that is fine. `gate_diff.py`
    reports the difference faithfully; the difference is the environment.

    The repo this came from documents the trap in its own pre-commit config, as
    the reason its mypy hook is local rather than `mirrors-mypy`. Phase 4 walked
    into it anyway.

    Measured while fixing it, and not in the hand-back: `--no-project` is not
    isolation. With a `.venv` beside the working directory the `--with` overlay
    is layered on it and the project's dependencies are importable; with none
    they are absent. Same command, uv 0.12.5.
    """

    def test_the_flag_is_qualified_rather_than_given_for_every_gate(self):
        phase4 = self.material(4)
        self.assertIn(
            "--no-project",
            phase4,
            "Phase 4's recipe is built on the flag; the guard below is about "
            "whether the exception to it is stated",
        )
        self.assertRegex(
            phase4.lower(),
            r"--no-project[^.]{0,200}(wrong|right for)",
            "Phase 4 gives `--no-project` three times and never says which gates "
            "it is wrong for, so the recipe reads as universal",
        )

    def test_the_symptom_is_named_so_a_false_difference_is_recognisable(self):
        phase4 = self.material(4)
        self.assertIn(
            "warn_return_any",
            phase4,
            "the failure is a difference that looks real. Naming the mechanism is "
            "what lets a reader tell it from a behaviour change",
        )

    def test_the_flag_is_not_described_as_isolation_it_does_not_give(self):
        self.assertIn(
            ".venv",
            self.material(4),
            "`--no-project` layers on a `.venv` when it finds one, so what the "
            "flag means depends on a directory. A phase that runs three of these "
            "and compares them has to say so",
        )


class TestExposureIsEstablishedRatherThanAssumed(SkillHarness):
    """Phase 2 answered "is this repo in scope" with one grep over repo config.

    That works for a rule or a flag. Two ordinary cases fall outside it, and both
    return a confident `inert here` nobody established.

    **A changelog entry naming a dependency.** `rumdl` 0.2.60 ships
    `deps: update h2 to 0.4.16` under **Fixed**, with no `Security` heading —
    RUSTSEC-2026-0258. The crate is Rust inside a Python wheel, so `pip-audit`
    is clean under both `-s pypi` and `-s osv`, correctly, and the repo's config
    has never heard of `h2`. Reading the wheel's PEP 770 SBOM answers it:
    CycloneDX 1.5, 178 components, no `h2` — while `tokio` is there, so the
    document is the real shipped set. `Cargo.lock` says the opposite, because it
    carries `[dev-dependencies]` and `jsonschema` is one.

    **A rule the repo disables.** `rumdl` 0.2.59 fixed a destructive `MD013`
    autofix in a repo running `--fix` on every Markdown commit. `disable =
    ["MD013"]` is a claim about a file; `rumdl check README.md` clean against
    `--no-config` finding 32 is a claim about the tool.
    """

    def test_phase_2_says_the_grep_cannot_answer_a_vendored_dependency(self):
        self.assertRegex(
            self.material(2).lower(),
            r"grep[^.]{0,400}(cannot|only when)",
            "Phase 2 called the scope test `one grep`, which is false for an "
            "advisory against a dependency vendored out of another ecosystem — "
            "the case where every Python-side scanner is correctly clean",
        )

    def test_the_shipped_set_is_read_from_the_wheel_not_the_vendored_lockfile(self):
        phase2 = self.material(2)
        self.assertIn(
            "sboms/",
            phase2,
            "the shipped set is in the wheel's own SBOM; nothing else in reach "
            "distinguishes a vendored crate that is compiled in from one that is not",
        )
        self.assertIn(
            "dev-dependencies",
            phase2,
            "a reader who reaches for Cargo.lock instead finds the crate and calls "
            "it exposure — it records dev-dependencies, which are not in the binary",
        )

    def test_a_wheel_with_no_sbom_is_underivable_rather_than_clean(self):
        self.assertRegex(
            self.material(2).lower(),
            r"no sbom[^.]{0,200}underivable|underivable[^.]{0,200}sbom",
            "PEP 770 coverage is partial, so a missing SBOM says nothing about a "
            "missing crate. Collapsing it into `clean` builds the unverified "
            "verifier this plugin exists to argue against",
        )

    def test_a_disabled_rule_is_proven_by_running_the_tool_both_ways(self):
        phase2 = self.material(2)
        self.assertIn(
            "--no-config",
            phase2,
            "an `inert here` resting on a config line is an assertion about the "
            "file while the verdict is about the tool — the same gap Phase 6 "
            "closed for a red check by attributing it",
        )

    COUNTS: ClassVar[dict[str, int]] = {
        "one": 1,
        "two": 2,
        "three": 3,
        "four": 4,
        "five": 5,
        "six": 6,
    }

    def _scope_table(self) -> list[str]:
        for table in tables(dict(self.phases)[2]):
            if any("Why the config cannot answer it" in row for row in table):
                return [row for row in table if row.startswith("| ") and "---" not in row]
        self.fail("Phase 2 has no scope-test table")

    def test_the_counted_sentence_matches_the_table_it_counts(self):
        """CONTRIBUTING's own trap: *"The rule named two of the four until
        0.29.0."* A counted list stops covering what it was written for the
        moment a row is added, and the sentence keeps reading as complete.

        0.36.0 added the third row — an entry naming a **file type** or a
        **document shape** rather than a setting, where no config key exists to
        grep and "no config line matches" reads as `inert here`. Found by a run
        that improvised `git grep` over `*.md` and a count of `.rs` files.
        """
        rows = len(self._scope_table()) - 1  # the header is a row too
        found = re.search(
            r"\b(one|two|three|four|five|six) cases? fall outside", dict(self.phases)[2]
        )
        self.assertIsNotNone(found, "Phase 2 no longer counts the cases the grep cannot answer")
        assert found is not None
        self.assertEqual(
            self.COUNTS[found.group(1)],
            rows,
            f"the sentence says {found.group(1)} cases and the table has {rows} rows",
        )

    def test_the_third_case_greps_the_tree_rather_than_the_config(self):
        """The affected path is a file type and a document shape, so exposure is
        how many files carry it — and zero is a finding like any other."""
        phase2 = self.material(2)
        self.assertRegex(
            phase2,
            r"git grep -lE .*\*\.md.*\n.*git ls-files",
            "Phase 2 must give the content greps, not only say to grep content",
        )
        self.assertRegex(
            phase2,
            r"(?is)`1` is a real zero; `128` is `underivable`|exits `1` on\s*\n?no match and `128`",
            "and it must separate no-match from could-not-run, or the third row "
            "rebuilds the very confusion it was added to remove",
        )

    def test_the_third_case_does_not_pipe_the_count(self):
        """`git grep … | wc -l` prints 0 at exit 0 whether it matched nothing or
        could not run, which turns `underivable` into `inert here`. The class-wide
        pipe guard already forbids it; this says the prose warns about it too,
        because the reader is the one who will reach for `wc`."""
        self.assertIn(
            "do not pipe these into `wc`",
            dict(self.phases)[2],
            "the reader has to be told why the obvious shortening is wrong",
        )


class TestPhase0ClearsAStaleWorktreeRegistration(SkillHarness):
    """`$SCRATCH` lives under `$TMPDIR`, so it disappears between audits.

    The worktrees go; their registrations in `.git/worktrees` do not. Git then
    refuses the **fetch** — `refusing to fetch into branch 'refs/heads/pr-<N>'
    checked out at '<a path that no longer exists>'` — and Phase 0 dies one
    command before either `worktree add`.

    That matters because the stale-worktree paragraph further down is keyed to
    `git worktree add` refusing, so the phase's own remedy is unreachable from
    the failure that actually fires. Measured on git 2.55.0: with the
    registration stale the fetch exits 128; `git worktree prune` first makes the
    fetch, and both adds, exit 0, and `prune` on clean state is a no-op.

    Ordering is the whole content of the fix, so the guard checks position and
    not merely presence — `prune` after the fetch would be green on a
    substring test and useless in the run.
    """

    def _phase0(self) -> str:
        return self.shell[0]

    def test_phase_0_prunes_before_it_fetches(self):
        shell = self._phase0()
        self.assertIn("git worktree prune", shell, "Phase 0 must clear stale registrations")
        prune = shell.index("git worktree prune")
        fetch = shell.index('git fetch origin "pull/')
        self.assertLess(
            prune,
            fetch,
            "prune must run BEFORE the fetch: the fetch is what a stale registration "
            "refuses, and pruning afterwards never runs",
        )

    def test_the_failure_it_answers_is_named(self):
        """A message naming a directory that is not there reads like a
        permissions or ref problem, and the reader has no reason to connect it
        to worktrees at all."""
        body = dict(self.phases)[0]
        self.assertIn(
            "refusing to fetch into branch",
            body,
            "Phase 0 must quote the error, or the remedy is unfindable from the symptom",
        )


class TestPhase2CanReachAChangelogAtAll(SkillHarness):
    """ "Read the changelog for every version in the gap" had no method.

    Nothing in the plugin said how to find the project's repo, how to name the
    release tag, or what to do when there is no release — so every run
    improvised, and a run that improvises "no changelog found" reports an
    absence of evidence as evidence of absence.

    Measured 2026-08-30 on the three tools in `fpga-board-sim` #365: `ruff`'s
    release tag is `0.16.4` (and `v0.16.4` is *release not found*), `rumdl`'s is
    `v0.2.58`, and `python/mypy` publishes **zero** releases while tagging
    `v2.3.1` — with a `CHANGELOG.md` that has no `2.3.1` section, because it
    writes entries per minor release. Only the commit range answers that one.
    """

    def _ladder(self) -> str:
        """What Phase 2 *runs* to find the repo and the tag, not what it says.

        This used to return the bash block whose heredoc re-implemented the
        resolution in six lines. 0.36.0 deleted that block: it was a second
        implementation of what `changelog.py` already did, and it carried the
        same `"github.com/" in url` substring test — so fixing the flaw in one
        copy would have left it live in the other. Re-anchored to `reachable`,
        which follows the script, rather than re-anchored to nothing.
        """
        code = self.reachable(2)
        self.assertIn(
            "changelog.py",
            "\n".join(block for _, section in self._handoffs(2) for block in bash_blocks(section)),
            "Phase 2 documents no way to find the project's repository",
        )
        return code

    def test_the_tag_is_matched_against_the_release_list_not_constructed(self):
        """Guessing the prefix returns "release not found", which reads exactly
        like "this version has no notes"."""
        code = self._ladder()
        self.assertIn("tag_name", code, "the tag comes from the release list")
        self.assertRegex(
            code,
            r"for candidate in \(version, f'v\{version\}'\)",
            "both spellings must be matched against what the project actually "
            "published, rather than one of them assumed",
        )

    def test_the_repo_url_is_matched_on_its_host_not_by_substring(self):
        """`project_urls` is written by the package author, who is exactly the
        party this plugin exists to not trust.

        `"github.com/" in url` resolved `https://evil.invalid/github.com/a/b` to
        `a/b`, pointing the whole phase at a repository the package chooses —
        tidy notes, no unreconciled fixes, a clean currency row. It shipped in
        the prose from 0.33.0 and in this script's first cut, and CodeQL caught
        it as `py/incomplete-url-substring-sanitization`.
        """
        code = self._ladder()
        self.assertRegex(
            code,
            r"hostname not in \('github\.com', 'www\.github\.com'\)",
            "the host must be compared, never searched for",
        )
        self.assertNotRegex(
            code,
            r"'github\.com/' in url",
            "the substring test is the defect, not a second line of defence",
        )

    def test_a_failed_lookup_is_not_read_as_an_absent_release(self):
        """The same distinction Phase 0 draws for every derived output, at the
        point where the two look identical: an empty answer.

        The bash block used to carry this as a `|| { ...; exit 2; }` and a
        sentence. The script carries it as a type: `_gh` returns `None` on
        failure and `""` on a call that succeeded with no rows, and the release
        list goes through the wrapper that exits on `None`. Same property, and
        the guard follows it rather than following the sentence that described
        it -- `python/mypy` really does publish zero releases, so `""` here has
        to stay a real answer.
        """
        code = self._ladder()
        # DOTALL on both: these span a function body, and `assertRegex` compiles
        # a bare string with no flags.
        self.assertRegex(
            code,
            re.compile(r"def releases\(slug: str\).*?_gh_hard\(", re.DOTALL),
            "the release list must go through the wrapper that exits on failure",
        )
        self.assertRegex(
            code,
            re.compile(r"def _gh_hard\(.*?if out is None:\s+fail\(", re.DOTALL),
            "and that wrapper has to actually exit, not return an empty string",
        )

    def _uv_lock_phase2(self) -> str:
        """Just `uv-lock.md`'s Phase 2, never the whole of `material(2)`.

        `actions.md` § Phase 2 carries its own `compare` call for the tag-line
        question, so a guard reading every reference at once is satisfied by
        that one and says nothing about this ladder — the trap round 8 named:
        anchor a prose guard to the subsection it is about.
        """
        for name, section in self._handoffs(2):
            if name == "uv-lock.md":
                return section
        self.fail("Phase 2 no longer hands off to uv-lock.md")

    def _source_table(self) -> list[str]:
        """The table that says what the three sources are, not the section.

        Anchored here because the section discusses release notes in four places,
        so a guard scanning the whole of it stayed green when the table's own row
        was renamed — mutation-checked, and that is the only place a reader looks
        to learn what the three are.
        """
        for table in tables(self._uv_lock_phase2()):
            if any("runs out" in row for row in table):
                return table
        self.fail("uv-lock.md § Phase 2 has no source table")

    def test_all_three_sources_are_named_in_the_table(self):
        """The reader has to know what the three are, whoever fetches them."""
        rows = "\n".join(self._source_table())
        for source in ("release notes", "changelog", "commit range"):
            self.assertIn(source, rows, f"the source table no longer names {source}")

    def test_all_three_sources_are_actually_reached(self):
        """And the mechanism has to exist, not merely be described.

        Read from `reachable`, so mechanising the ladder into `changelog.py`
        moved the assertion rather than retiring it — which is what happened in
        0.36.0, and what this method's harness docstring says to expect.
        """
        code = self.reachable(2)
        for call in ("/releases", "/contents", "/compare/"):
            self.assertIn(call, code, f"nothing Phase 2 runs asks for {call}")

    def test_the_report_says_which_sources_answered(self):
        """ "No changelog" is not a finding; "the prose named one, the range
        carried five" is."""
        self.assertRegex(
            self._uv_lock_phase2(),
            r"[Rr]eport which sources? answered|which rung answered|[Ss]ay which rung",
            "Phase 2 must report which sources answered, as Phase 5 reports which install ran",
        )


class TestARungThatAnsweredCanStillBeIncomplete(SkillHarness):
    """#94. The ladder's stopping rule was *"did source N produce text?"*; the
    phase's question is *"what behavior changed across this range?"*

    Those diverge whenever a project generates release notes from a **subset** of
    its commits. Measured on `rumdl` v0.2.60…v0.2.62: rung 1 answers for both
    versions, rung 2 answers for both, and five `fix(…)` commits — two of them
    `stop …` entries in a tool the audited repo runs as `--fix` on every Markdown
    commit — never entered the audit. **Nothing failed**, which is why no exit
    status anywhere could reach it, and why this is worse than #91's absence.

    The guards below are on `uv-lock.md` § Phase 2 alone. `actions.md` § Phase 2
    carries its own `compare` call for the tag-line question, and a guard reading
    every reference at once is satisfied by that one while saying nothing about
    this.
    """

    def _section(self) -> str:
        for name, section in self._handoffs(2):
            if name == "uv-lock.md":
                return section
        self.fail("Phase 2 no longer hands off to uv-lock.md")

    def test_the_ladder_has_an_exit_that_is_incompleteness_and_not_absence(self):
        """Every exit condition used to be an absence — the project publishes
        none, the patch has no section, the tags are missing — so a source that
        returned real content ended the ladder. The reconciliation is the one
        with no exit condition, and the prose has to say so."""
        self.assertRegex(
            self._section(),
            r"(?is)\|[^|\n]*\bagainst each other\b[^|\n]*\|[^|\n]*\|[^|\n]*\bnever\b",
            "the source table needs a row for reconciling the three, whose "
            "'where it runs out' is never — otherwise every exit is an absence "
            "again and a source that answered ends the read",
        )

    def test_the_range_is_read_whatever_the_write_mode_judgement_says(self):
        """`--write-mode` escalates a finding; it must not gate the call that
        finds one. Gating the fetch asks the auditor to be right about write mode
        before it has the evidence, and a wrong guess restores the silence."""
        self.assertRegex(
            self._section(),
            r"(?is)changes nothing about what is looked for|"
            r"the range is fetched either way|never whether it is \*?looked for",
            "Phase 2 must say the compare range is read regardless of write mode",
        )

    def test_the_write_mode_flag_is_documented_where_it_is_passed(self):
        self.assertIn("--write-mode", self.reachable(2))
        self.assertRegex(
            self._section(),
            r"--fix.*--write.*-i|--fix`, `--write` or `-i`",
            "the reader has to know which repo condition sets the flag",
        )

    def test_the_classifier_is_named_the_way_the_source_is(self):
        """A count of fixes means something different depending on who did the
        labelling, so the output says which ran. `python/mypy` v2.3.0…v2.3.1
        carries four fixes and no conventional commits; a filter keyed on `fix(`
        called it clean, which is this plugin's own failure class rebuilt inside
        the tool written to remove it."""
        self.assertRegex(
            self._section(),
            r"(?is)says which classifier ran|which classifier",
            "the prose must say that the script reports which classifier it used",
        )
        self.assertIn(
            "classifier",
            self.reachable(2),
            "and the script has to print it, or it lives only where the reader "
            "of the output never sees it",
        )

    def test_the_worked_example_is_the_bump_it_was_found_in(self):
        """Kept concrete and kept checkable: `integration/` holds the live half,
        and goes red when rumdl changes how it generates notes."""
        section = self._section()
        self.assertIn("v0.2.60…v0.2.62", section)
        self.assertRegex(
            section,
            r"18 commits.*five of them|five of them `fix",
            "the example has to carry the counts, or it is an anecdote",
        )

    def test_the_evidence_file_is_pointed_at_for_the_security_read(self):
        """No count substitutes for seeing a `Security` heading: a privately
        disclosed fix ships with no CVE and every scanner reports clean."""
        self.assertRegex(
            self._section(),
            r"(?is)`\$SCRATCH/changelog-.*`.*\n?.*Security",
            "Phase 2 must name the file it wrote and say to read it for Security",
        )


class TestAPlaceholderForARepositoryPathIsDerived(SkillHarness):
    """#101. `git show "pr-<N>:.github/workflows/<ci>.yml"` never said how to
    learn `<ci>`, so every run invented a way to list the directory or guessed a
    filename — and the guess is quiet in the direction that matters. A repo whose
    workflow is `tests.yml` makes `ci.yml` exit non-zero and at least loud; a repo
    with **several** workflows has no single right answer, and picking one
    silently narrows the gate list that Phase 4 and Phase 5 both consume.

    The same gap appeared three times under two spellings — Phase 0's `<ci>`,
    Phase 6's `<changed>`, `actions.md` § Phase 5's `<changed>` — which is also
    why 0.36.0 made them one token: a reader who solves it in Phase 0 could not
    see that Phase 6 was asking the same question.

    **Narrow on purpose.** #101 also proposed the general form, that no
    placeholder anywhere is left underived. Measured across both references and
    `SKILL.md`, 37 placeholders appear in a fence and never in their phase's
    prose — `<owner>`, `<N>`, `<sha>` and the rest, all of them answered by
    Phase 0's outputs table or by context. A guard with 37 exceptions is a guard
    that gets weakened until it discriminates nothing, so this one is about the
    class that actually bit: a placeholder standing for a **path in the
    repository**, which cannot be answered from context because only the tree
    knows.
    """

    # `<tok>` sitting inside a repository path: `.github/workflows/<workflow>`,
    # `pr-<N>:<workflow>`, `--workflow <workflow>`. `<N>` is the audited PR and
    # comes from the argument, so it is never the token in question.
    PATH_TOKEN = re.compile(r"<(?!N>)([a-z][a-z0-9_-]*)>")
    # Something in the same block that asks the tree what is there.
    DERIVES = re.compile(r"git ls-tree --name-only|git diff --name-only|git ls-files")

    def _blocks_naming_a_workflow(self) -> list[tuple[str, int, str]]:
        """Every block that names a workflow, however it spells the path.

        `<workflow>` is in the selector because Phase 6 reads
        `git show "pr-<N>:<workflow>"` — the placeholder stands for the whole
        path there, so a selector keyed on `workflows/` skipped the block
        entirely and the derivation guard passed on a phase it never looked at.
        Mutation-checked by deleting Phase 6's derivation, which went green.
        """
        found = [
            (file, number, code)
            for file, number, code in _every_block()
            if "workflows/" in code or "--workflow " in code or "<workflow>" in code
        ]
        self.assertTrue(found, "no block reads a workflow any more")
        return found

    def test_every_block_that_names_a_workflow_derives_the_list_first(self):
        for file, number, code in self._blocks_naming_a_workflow():
            self.assertRegex(
                code,
                self.DERIVES,
                f"{file} Phase {number} names a workflow file without asking the "
                f"tree which workflows exist. A guessed name either fails loudly "
                f"or — worse — succeeds and narrows the gate list to one",
            )

    def test_the_placeholder_is_one_token_across_every_phase_that_asks(self):
        """Two spellings for one derivation is how the second one stops looking
        like the first."""
        tokens = set()
        for _, _, code in self._blocks_naming_a_workflow():
            tokens |= {t for t in self.PATH_TOKEN.findall(code) if "workflow" in t or t == "ci"}
        self.assertEqual(
            tokens,
            {"workflow"},
            "the workflow placeholder must be spelled the same everywhere it is "
            "asked for, or the same question reads as three different ones",
        )

    def test_phase_0_derives_the_list_at_both_refs(self):
        """A gate on only one side of the bump is itself a finding, and Phase 0
        already says to diff the two lists — which needs two lists."""
        block = next(
            code
            for _, number, code in self._blocks_naming_a_workflow()
            if number == 0 and "ls-tree" in code
        )
        # Asserted on the `ls-tree` line, not on the ref appearing anywhere: the
        # block also does `git show "$BASE_SHA:.github/workflows/<workflow>"`,
        # which satisfied a looser assertion while the base *listing* was gone —
        # so the guard passed on exactly the half-derived state it exists for.
        for ref in ("pr-<N>", r"\$BASE_SHA"):
            self.assertRegex(
                block,
                rf'git ls-tree --name-only "{ref}:\.github/workflows/"',
                f"Phase 0 does not list the workflows at {ref}, so it cannot diff "
                f"the two lists — and a gate on one side of the bump is a finding",
            )

    def test_the_derivation_is_loud_when_it_cannot_run(self):
        """`ls-tree` on a missing directory exits 128 and says so, rather than
        printing nothing at exit 0 — measured on git 2.55.0. The prose has to
        carry that, because "no workflows" and "could not read" are the two
        answers the gate list must not confuse."""
        self.assertRegex(
            self.material(0),
            r"(?is)ls-tree[^.]*?exits \*\*128\*\*|exits \*\*128\*\* and says so",
            "Phase 0 must say that a failed listing is loud, not empty",
        )


class TestNoExecutePhaseBuildsTheAuditedProject(SkillHarness):
    """`--no-execute` says Phases 0-3 and 6-7 are network reads. Two of them were not.

    Until 0.34.0 `references/uv-lock.md` § Phase 3 gave one command —
    `uv run --with pip-audit pip-audit --skip-editable` — and § Phase 2's config
    differential gave `uv run <tool> check`. `uv run` syncs the project before it
    runs anything, so both installed the audited tree editable and built any sdist
    in its resolution. Measured on uv 0.12.8 against a project whose `setup.py`
    writes a file when executed: plain `uv run` produces that file and a `.venv`;
    `uv run --no-project` produces neither, and `uv export` never materialises an
    environment at all.

    So the mode that exists for a PR you have no reason to trust ran that PR's
    build code — with no `MAY_EXECUTE` gate and no banner, both of which Phases 4
    and 5 carry.

    What this detects is narrower than "executes something": it is *builds or
    installs the audited project*. Phase 4 runs the repo's own gates and is
    supposed to, under `MAY_EXECUTE`; it stays clean here because it already
    spells every invocation `uv run --no-project`. The pattern is also the set of
    commands this plugin actually uses, not a proof that no other command builds
    a project — a phase in this set that gains a new tool has to be added to it,
    which is what the `--no-execute` paragraph now tells the reader.
    """

    NO_EXECUTE = (0, 1, 2, 3, 6, 7)

    # `uv run --no-project` supplies the tool from PyPI and never touches the
    # audited project, which is why Phase 2's differential and Phase 4's
    # gate_diff invocations are spelled that way. Bare `uv run` is the defect.
    EXECUTES = re.compile(
        r"\buv run\b(?!\s+--no-project\b)|\buv sync\b|\bpip install\b|\bpre-commit run\b"
    )

    @staticmethod
    def _uncommented(shell: str) -> str:
        """Drop whole-line `#` comments before matching.

        `reachable` strips comments out of *scripts* for exactly this reason, and
        bash fences need the same treatment: the first version of this guard fired
        on a comment in `uv-lock.md` § Phase 2 that warns against the very command
        it forbids — the failure the harness docstring above already names.

        Whole-line only. A trailing `#` can sit inside a quoted string (`--jq
        '"#\\(.number)"'`), and cutting there would corrupt the line rather than
        clean it. A trailing comment that names a build command is possible and
        would be missed; a whole-line explanation is what actually happens.
        """
        return "\n".join(line for line in shell.splitlines() if not line.lstrip().startswith("#"))

    def test_no_no_execute_phase_builds_the_audited_project(self):
        for number in self.NO_EXECUTE:
            code = self._uncommented(self.reachable(number, printed=False))
            hits = sorted(set(self.EXECUTES.findall(code)))
            self.assertEqual(
                hits,
                [],
                f"Phase {number} runs under --no-execute, which promises a network "
                f"read; {hits} builds and installs the audited tree",
            )

    def test_the_pattern_still_discriminates(self):
        """Anti-vacuity, pinned to literals rather than to the document.

        A pattern that matches nothing satisfies the guard above in silence. The
        strings below are the two forms that actually shipped and the three the
        fix replaced them with, so a refactor that stops matching fails here
        rather than going green on a Phase 3 that had regained `uv run`.
        """
        for bad in (
            "uv run --with pip-audit pip-audit --skip-editable",
            "uv run <tool> check --no-config <the same file>",
            "uv sync --locked --no-build --no-install-project",
        ):
            self.assertRegex(bad, self.EXECUTES, f"{bad!r} builds the project and must match")
        for ok in (
            "uv run --no-project --with ruff==<locked> ruff format .",
            "uv export --frozen --format requirements.txt --no-emit-project",
            'uvx pip-audit -r "$SCRATCH/pr-<N>-requirements.txt" --no-deps --disable-pip',
        ):
            self.assertNotRegex(ok, self.EXECUTES, f"{ok!r} does not touch the project")

    def test_the_pattern_still_finds_a_real_violation_in_the_document(self):
        """Phase 5 installs frozen and says so; if it stops matching, so has the pattern."""
        self.assertRegex(
            self._uncommented(self.reachable(5, printed=False)),
            self.EXECUTES,
            "Phase 5 is documented as the phase that installs the PR; a pattern that "
            "no longer matches it cannot be trusted over Phases 0-3",
        )

    def test_phase_3_names_the_tree_it_reads(self):
        """An unstated tree audits the pre-bump environment and reports clean."""
        self.assertIn(
            "$SCRATCH/pr-<N>",
            self.material(3),
            "Phase 3 must name the tree it audits; run in the user's checkout it "
            "reports on the currently installed set, which is not the one under audit",
        )


class TestTheDeviationHandBackCarriesItsEvidence(SkillHarness):
    """A `plugin defect` row that never ran the command still reads as measured.

    The same claim arrived on 2026-08-30 and again on 2026-09-01, from two
    different audits: that Phase 7's `git worktree remove` fails because Phase 5's
    `uv sync` leaves a `.venv/`. Both stated a mechanism and neither ran the plain
    command. Both were wrong — `remove` gates on `git status --porcelain`, which
    omits *ignored* files, and uv writes a `.gitignore` containing `*` inside
    `.venv/`, so the plain form exits 0. The second row labelled *itself* unproven
    and was filed as a probable defect anyway.

    Consistency only, per this file's header: that Phase 8 carries the rule is
    checkable here. Whether a run follows it is behavioural, and belongs to the
    replay gate — which is where both instances above were actually caught.
    """

    def phase8(self) -> str:
        return dict(self.phases)[8]

    def test_unproven_is_one_of_the_classes(self):
        """Three classes leave an inferred cause nowhere to go but `plugin defect`."""
        body = self.phase8()
        for cls in ("plugin defect", "prose gap", "unproven", "correct"):
            self.assertIn(cls, body, f"the deviation classes must include {cls!r}")

    def test_a_plugin_defect_row_names_its_command_and_exit_status(self):
        self.assertRegex(
            self.phase8(),
            r"exit status",
            "a `plugin defect` row must name the command that failed and the exit "
            "status it returned, or it is asserting a cause it inferred",
        )

    def test_the_first_check_is_whether_it_happened(self):
        r"""Both halves, because an alternation is satisfied by either.

        The first draft of this guard was `did this happen\?|search this
        session's own history` and stayed green with the question deleted — the
        rule is the question *and* where to look for the answer, and a guard that
        accepts half of it does not hold the rule.
        """
        body = self.phase8()
        self.assertIn(
            "did this happen?",
            body,
            "the cheapest verification is whether the command was ever issued, not "
            "whether the mechanism is right",
        )
        self.assertIn(
            "search this session's own history",
            body,
            "the question needs the place to look, or it is advice rather than a check",
        )

    def test_an_unproven_row_is_verified_before_it_is_filed(self):
        self.assertRegex(
            self.phase8(),
            r"question, not a ticket",
            "an unproven row that reaches the issue tracker unverified is how the "
            "same wrong cause was filed twice",
        )
