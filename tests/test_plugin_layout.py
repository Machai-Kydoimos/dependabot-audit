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

import json
import pathlib
import re
import unittest
from typing import ClassVar

ROOT = pathlib.Path(__file__).resolve().parent.parent
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
