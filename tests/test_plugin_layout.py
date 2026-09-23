"""Packaging tests: what the plugin ships has to be reachable.

Skills and commands are not in separate namespaces. Both are addressed through
the `Skill` tool as `<plugin>:<name>`, so a command whose basename equals a
skill's directory name claims the same address — and the command wins. The skill
does not lose a race; it becomes unaddressable.

That shipped, and it shipped from 0.2.1 to 0.22.1. `commands/dependabot-audit.md`
existed only to say "invoke the `dependabot-audit` skill", which resolved back to
the command — a delegation loop closing on itself. Measured on Claude Code
2.1.235: the one listed entry for `dependabot-audit:dependabot-audit` carried the
*command's* description, and a real audit was handed the 1527-character command
body with `$1` expanded to the PR number, never `SKILL.md`.

The cost was not cosmetic. `SKILL.md`'s `disallowed-tools: Edit, Write,
NotebookEdit` never loaded, so the read-only contract that the command file
itself called "the whole point" was never applied by the harness at all. The
audit stayed read-only because the model chose to, and got the procedure by
reading `SKILL.md` off disk by hand.

No test looked inside `commands/` for those twenty versions, which is how a file
whose entire purpose was delegation survived as the thing that broke delegation.

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import ast
import json
import pathlib
import re
import sys
import unittest
from typing import ClassVar

ROOT = pathlib.Path(__file__).resolve().parent.parent
SKILLS = ROOT / "skills/dependabot-audit"
MANIFEST = ROOT / ".claude-plugin/plugin.json"


def plugin_name() -> str:
    manifest: dict[str, str] = json.loads(MANIFEST.read_text(encoding="utf-8"))
    return manifest["name"]


def command_names() -> set[str]:
    """Basenames under `commands/` — what `/<name>` resolves to."""
    return {path.stem for path in (ROOT / "commands").glob("*.md")}


def skill_dirs() -> list[pathlib.Path]:
    skills = ROOT / "skills"
    return sorted(p for p in skills.iterdir() if (p / "SKILL.md").is_file())


def frontmatter(skill_md: pathlib.Path) -> dict[str, str]:
    """Stdlib only — no yaml. The frontmatter here is flat `key: value`."""
    text = skill_md.read_text(encoding="utf-8")
    _, _, rest = text.partition("---\n")
    block, _, _ = rest.partition("\n---")
    return {
        key.strip(): value.strip()
        for key, _, value in (line.partition(":") for line in block.splitlines())
        if key.strip() and not key.startswith(" ")
    }


class TestNothingShadowsASkill(unittest.TestCase):
    def test_a_skill_is_addressed_by_its_directory_name(self):
        """The guard below compares directory names, so this is load-bearing."""
        for skill in skill_dirs():
            self.assertEqual(
                skill.name,
                frontmatter(skill / "SKILL.md").get("name"),
                f"skills/{skill.name}/ is addressed by its `name:`, not its path,"
                " so the collision guard would be comparing the wrong string",
            )

    def test_no_command_claims_a_skill_name(self):
        clash = sorted(command_names() & {skill.name for skill in skill_dirs()})
        self.assertEqual(
            [],
            clash,
            "these commands claim the same `<plugin>:<name>` address as the skill"
            f" of that name, and the command wins, leaving the skill unloadable: {clash}",
        )

    def test_no_command_claims_the_plugin_name(self):
        """The collision that bit, in the shape it arrives in.

        A single-procedure plugin names its skill after itself, and `/<plugin>`
        is the obvious thing to type — so a command named after the plugin is the
        collision already made, whether or not the skill exists yet.
        """
        self.assertNotIn(
            plugin_name(),
            command_names(),
            f"commands/{plugin_name()}.md shadows the plugin's own skill address",
        )


class TestEveryScriptRunsOnABareInterpreter(unittest.TestCase):
    """The scripts run as `python3 "$SCRIPT"` inside the **audited** repository.

    Nothing installs anything there, so an import outside the standard library is
    a crash on a machine this suite never runs on. CONTRIBUTING has carried that
    rule since 0.2.0 by listing the scripts it applied to, and the list went stale
    twice: it named two of four until 0.29.0, and four of seven until 0.48.0. A
    rule stated as a list stops covering what it was written for the moment a file
    is added, which is the same defect as a counted sentence in `SKILL.md`. So the
    directory is the list now, and this reads it.
    """

    def test_no_script_imports_anything_outside_the_standard_library(self):
        scripts = sorted((ROOT / "skills/dependabot-audit/scripts").glob("*.py"))
        self.assertTrue(scripts, "no scripts found — has the directory moved?")
        for script in scripts:
            for node in ast.walk(ast.parse(script.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    modules = [node.module or ""] if node.level == 0 else []
                else:
                    continue
                for module in modules:
                    root = module.split(".")[0]
                    self.assertIn(
                        root,
                        sys.stdlib_module_names,
                        f"{script.name} imports {module!r}, which the audited "
                        f"repository's interpreter will not have",
                    )


class TestTheChangelogIndexIsComplete(unittest.TestCase):
    """A `## [x.y.z]` heading with no link reference renders as literal text.

    Markdown resolves `[0.34.0]` against a definition at the bottom of the file;
    without one the brackets stay on the page and the compare link is gone. It is
    silent in the only place it matters — the rendered view — and it had been
    silent for two releases: 0.34.0 and 0.35.0 both shipped headings with no
    definition, and `[Unreleased]` still compared from v0.33.0, so the "what has
    landed since the last release" link spanned three of them.

    Both directions, because a definition left behind after a heading is renamed
    is the same defect pointing the other way.
    """

    text: ClassVar[str]

    VERSION_HEADING = re.compile(r"^## \[(\d+\.\d+\.\d+)\]", re.MULTILINE)
    VERSION_DEF = re.compile(r"^\[(\d+\.\d+\.\d+)\]:", re.MULTILINE)

    @classmethod
    def setUpClass(cls):
        cls.text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")

    def test_every_version_heading_has_a_link_reference(self):
        headings = set(self.VERSION_HEADING.findall(self.text))
        defined = set(self.VERSION_DEF.findall(self.text))
        self.assertEqual(
            sorted(headings - defined),
            [],
            "these versions have a heading and no link definition, so they render "
            "as literal bracketed text with no compare link",
        )

    def test_no_link_reference_outlives_its_heading(self):
        headings = set(self.VERSION_HEADING.findall(self.text))
        defined = set(self.VERSION_DEF.findall(self.text))
        self.assertEqual(sorted(defined - headings), [], "these link definitions name no section")

    def test_unreleased_compares_from_the_newest_release(self):
        """It is the link a reader follows to see what has landed since the last
        release. Left stale it spans several, and says the opposite of that."""
        newest = max(
            self.VERSION_HEADING.findall(self.text), key=lambda v: [int(p) for p in v.split(".")]
        )
        # Compiled with re.M explicitly: `assertRegex` compiles a bare string
        # without flags, so `^`/`$` would only ever match the whole document.
        wanted = re.compile(
            rf"^\[Unreleased\]: \S+/compare/v{re.escape(newest)}\.\.\.HEAD$", re.MULTILINE
        )
        self.assertRegex(
            "\n".join(ln for ln in self.text.splitlines() if ln.startswith("[Unreleased]:")),
            wanted,
            f"[Unreleased] must compare from v{newest}, the newest release in this file",
        )

    def test_the_newest_heading_matches_the_shipped_version(self):
        """The manifest is what the harness installs; the changelog is what the
        reader is told was installed."""
        newest = max(
            self.VERSION_HEADING.findall(self.text), key=lambda v: [int(p) for p in v.split(".")]
        )
        manifest = json.loads((ROOT / ".claude-plugin/plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(
            manifest["version"],
            newest,
            "plugin.json and CHANGELOG.md disagree about which version this is",
        )


if __name__ == "__main__":
    unittest.main()


class TestTheShippedDescriptionMatchesTheSkill(unittest.TestCase):
    """Three files state this plugin's scope, and two of them are not prose.

    `plugin.json` and `marketplace.json` carry the description a user reads in
    `/plugin` before installing anything, and `SKILL.md`'s frontmatter carries
    the one the model reads when deciding whether the skill applies. Nothing
    connected them.

    0.38.0 added `pre-commit` as a covered ecosystem and updated `SKILL.md`,
    every phase's method table, the boundary sentence, both scripts' refusal
    messages and the README -- and left both manifests saying "Verifies uv.lock
    and GitHub Actions end to end". The whole suite passed, because the manifests
    were read for their `name` and never for what they claim.

    That is the worst place for the claim to rot: it is the only one a user sees
    *before* installing, and it understates the plugin rather than overstating
    it, so nobody reports it.
    """

    MARKETPLACE: ClassVar[pathlib.Path] = ROOT / ".claude-plugin/marketplace.json"
    SKILL: ClassVar[pathlib.Path] = ROOT / "skills/dependabot-audit/SKILL.md"

    # Every ecosystem name that must appear wherever the scope is stated. Adding
    # one here is the whole cost of adding an ecosystem to this check.
    COVERED: ClassVar[tuple[str, ...]] = ("uv.lock", "GitHub Actions", "pre-commit")

    def _manifest_descriptions(self) -> dict[str, str]:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        market = json.loads(self.MARKETPLACE.read_text(encoding="utf-8"))
        entries = [p for p in market["plugins"] if p["name"] == manifest["name"]]
        self.assertEqual(len(entries), 1, "the marketplace lists this plugin once")
        return {
            "plugin.json": manifest["description"],
            "marketplace.json": entries[0]["description"],
        }

    def test_the_two_manifests_agree_word_for_word(self):
        """They are copies, and a copy edited in one place is the ordinary way
        this drifts. Neither is derived from the other at build time."""
        got = self._manifest_descriptions()
        self.assertEqual(
            got["plugin.json"],
            got["marketplace.json"],
            "the installed description and the one shown before installing differ",
        )

    def test_every_covered_ecosystem_is_named_where_scope_is_stated(self):
        stated = self._manifest_descriptions()
        stated["SKILL.md frontmatter"] = self.SKILL.read_text(encoding="utf-8").split("---")[1]
        for where, text in stated.items():
            for ecosystem in self.COVERED:
                with self.subTest(where=where, ecosystem=ecosystem):
                    self.assertIn(
                        ecosystem,
                        text,
                        f"{where} states this plugin's scope and does not name "
                        f"`{ecosystem}`, which every phase has a method for",
                    )


class TestTheCorpusIsFrozen(unittest.TestCase):
    """The procedure does not grow. Measured, 2026-09-21, and then stopped.

    Six weeks of sprints took the shipped documents from 57 KB to 125 KB of
    `SKILL.md` and 32 KB to 139 KB of references — **2.7x** — across the same
    nine phases and one added ecosystem. The text grew; the product did not.

    That growth is not incidental to the defect rate, it is the mechanism. Each
    fix wrote a paragraph explaining what it had measured; that paragraph is new
    unaudited surface carrying new falsifiable claims, and the next replay round
    audits it. Two of 0.53.0's five findings traced by `git log -S` to `883441e`
    — shipped **one day** earlier. Meanwhile the documents are ~30% of every
    run's tokens, so the growth is also a per-audit cost paid forever.

    The budget below is `v0.53.0`'s size. Nothing here says the numbers are
    right; they say the size is now a **decision** rather than a side effect.
    Raising one is a one-line diff in this file, visible in review, in the same
    commit as the growth it permits — which is all this guard is for.

    The rule it enforces, from CONTRIBUTING: **a measurement's reasoning belongs
    in the CHANGELOG entry and the commit body; the procedure carries the
    command and one line of why.** 0.54.0 was written under it — three
    measurements supplied and four unchecked statuses fixed, paid for by moving
    0.53.0's rationale out of `actions.md` and `SKILL.md` into the entries that
    already carried it.
    """

    # v0.55.0, 2026-09-23 — lower again. 0.55.0 added a frontmatter hook, three
    # commands and two preambles, and paid for them by moving version history into
    # the CHANGELOG. Lower these when prose comes out; raise one only
    # deliberately, and say in the same commit what was bought with it.
    BUDGET: ClassVar[dict[str, int]] = {"SKILL.md": 124_416, "references": 139_137}

    def test_the_skill_does_not_grow(self) -> None:
        size = (SKILLS / "SKILL.md").stat().st_size
        self.assertLessEqual(
            size,
            self.BUDGET["SKILL.md"],
            f"SKILL.md is {size} bytes against a budget of {self.BUDGET['SKILL.md']}. "
            f"It is loaded on every audit of every ecosystem, so every byte is paid "
            f"for on every run. Put the reasoning in the CHANGELOG entry and keep the "
            f"command; if the procedure genuinely needs the line, take one out or "
            f"raise the number here and say what it bought",
        )

    def test_the_references_do_not_grow(self) -> None:
        sizes = {p.name: p.stat().st_size for p in sorted((SKILLS / "references").glob("*.md"))}
        total = sum(sizes.values())
        self.assertLessEqual(
            total,
            self.BUDGET["references"],
            f"the references total {total} bytes against a budget of "
            f"{self.BUDGET['references']} — {sizes}. Budgeted together rather than "
            f"per file, because moving a paragraph between them is not growth and "
            f"an ecosystem's method may legitimately need a line the others do not",
        )

    def test_the_budget_is_not_slack(self) -> None:
        """A budget far above the corpus is a guard that cannot fire.

        This is the anti-vacuity half: the numbers were set *at* the measured
        size, and a later edit that shrinks the corpus should lower them rather
        than bank the difference as room to grow back into.
        """
        skill = (SKILLS / "SKILL.md").stat().st_size
        refs = sum(p.stat().st_size for p in (SKILLS / "references").glob("*.md"))
        for name, actual, budget in (
            ("SKILL.md", skill, self.BUDGET["SKILL.md"]),
            ("references", refs, self.BUDGET["references"]),
        ):
            with self.subTest(name=name):
                self.assertGreater(
                    actual,
                    budget * 0.97,
                    f"{name} is {budget - actual} bytes under its budget of {budget}. "
                    f"Headroom is how a ratchet stops ratcheting — lower the number in "
                    f"BUDGET to what the corpus now measures",
                )
