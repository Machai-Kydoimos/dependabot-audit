"""Consistency tests for SKILL.md, the half the other suites cannot reach.

Four defects have now shipped in the prose and nowhere else: Phase 6 improvised a
check-name parse; Phase 1 referenced a branch that Phase 5 created; Phase 4 ran
against a worktree that Phase 5 created; and Phase 6 filled a placeholder in with
one specific repo's required check names. Two of them are the same
forward-reference shape. Each was found by a human reading in order, and each was
answered by editing the prose — which is the same lever that produced them.

Whether the model *follows* the phases is behavioral and belongs in
`claude plugin eval`, which is unavailable on this account. That gap stays open
and stays stated. What is checkable here is narrower and still worth having:
whether the document is consistent **with itself**.

    a phase may not consume what a later phase creates
    the required-context list must be read from a Phase 0 artifact, never typed
    every path the prose names must exist
    the frontmatter key that withholds tools must be the one that works

Every one of those corresponds to a defect that shipped.

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

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
            self.shell[6],
            "Phase 6 must ask the API which contexts are required, not derive a list",
        )

    def test_no_phase_reads_required_checks_from_branch_protection(self):
        """The `admin`-only endpoint 404s without admin, and the body is stdout.

        A bare 404 is indistinguishable from an unprotected branch, so a redirect
        of this call produces a well-formed artifact asserting the opposite of the
        truth. Verified against a repo enforcing three required checks.
        """
        for number, code in sorted(self.shell.items()):
            self.assertNotIn(
                "/protection",
                code,
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
        for number, code in sorted(self.shell.items()):
            self.assertNotIn(
                "/rules/branches",
                code,
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
            dict(self.phases)[3],
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

    PLUGIN_PATH = re.compile(r"\$\{CLAUDE_PLUGIN_ROOT\}/([A-Za-z0-9_./-]+)")
    REFERENCE = re.compile(r"references/([a-z0-9_-]+\.md)")

    def _docs(self):
        yield SKILL
        yield from sorted((PLUGIN / "references").glob("*.md"))

    def test_every_plugin_root_path_exists(self):
        for doc in self._docs():
            for rel in self.PLUGIN_PATH.findall(doc.read_text(encoding="utf-8")):
                self.assertTrue(
                    (ROOT / rel).exists(),
                    f"{doc.name} names ${{CLAUDE_PLUGIN_ROOT}}/{rel}, which does not exist",
                )

    def test_every_referenced_document_exists(self):
        for doc in self._docs():
            for name in self.REFERENCE.findall(doc.read_text(encoding="utf-8")):
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
