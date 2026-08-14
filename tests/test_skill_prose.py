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
MADE_BY_WORKTREE = re.compile(r"git worktree add\s+\"?(\$SCRATCH/[A-Za-z0-9_.<>-]+)")

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

    def test_phase_6_reads_the_required_contexts_from_phase_0(self):
        """The structural half: the list has to come from somewhere derived."""
        self.assertIn(
            "$SCRATCH/required.txt",
            self.shell[6],
            "Phase 6 must read the required contexts Phase 0 derived, not a typed list",
        )
        self.assertIn(
            "$SCRATCH/required.txt",
            self.shell[0],
            "Phase 0 must write the required contexts somewhere Phase 6 can read",
        )


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
