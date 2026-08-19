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
import unittest

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


if __name__ == "__main__":
    unittest.main()
