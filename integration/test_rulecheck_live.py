"""rulecheck.py with real ruff and rumdl, over a repository planted for it (#181).

The hermetic suite fakes both tools, so it can only agree with what this repo
believes they do. These run the versions `fpga-board-sim` pinned when #438 opened,
ruff 0.16.7 and rumdl 0.2.72, over a repository with a base commit and a PR commit
whose lockfile pins other versions, and check what was measured before the script
was written:

  - rumdl under `disable = ["MD013"]`: the config does not run it, and forced on it
    reports the long lines it spares;
  - a `.rumdl.toml` that names `sh -c` as a code-block tool. `rumdl check` runs it
    unless told not to, so the marker it would write must not exist afterwards;
  - ruff under an allow-list `select` with no `N`, `preview = true` and
    `explicit-preview-rules`: N802 is not run, PLW1514 is, and UP052 does not exist
    yet at 0.16.7.

    RUN_NETWORK_TESTS=1 python3 -m unittest discover -s integration -v

Both releases are historical and immutable on PyPI, so this does not drift.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from typing import ClassVar

SCRIPT = (
    pathlib.Path(__file__).resolve().parent.parent / "skills/dependabot-audit/scripts/rulecheck.py"
)

live = unittest.skipUnless(
    os.environ.get("RUN_NETWORK_TESTS") and shutil.which("uvx") and shutil.which("git"),
    "set RUN_NETWORK_TESTS=1, with uvx and git on PATH; this fetches ruff and rumdl from PyPI",
)

LOCK = """\
version = 1
requires-python = ">=3.10"

[[package]]
name = "ruff"
version = "{ruff}"
source = {{ registry = "https://pypi.org/simple" }}

[[package]]
name = "rumdl"
version = "{rumdl}"
source = {{ registry = "https://pypi.org/simple" }}
"""

PYPROJECT = """\
[project]
name = "planted"
version = "0"

[tool.ruff.lint]
preview = true
explicit-preview-rules = true
select = ["E", "F", "I", "PLW1514"]

[tool.rumdl]
disable = ["MD013"]
"""

LONG = "This line is deliberately much longer than eighty characters, so MD013 fires on it."


def git(repo: pathlib.Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )
    return done.stdout.strip()


@live
class TestTheRuleCheckLive(unittest.TestCase):
    tmp: ClassVar[tempfile.TemporaryDirectory[str]]
    repo: ClassVar[pathlib.Path]
    scratch: ClassVar[pathlib.Path]
    inputs: ClassVar[pathlib.Path]
    marker: ClassVar[pathlib.Path]
    base: ClassVar[str]

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        root = pathlib.Path(cls.tmp.name)
        cls.repo, cls.scratch, cls.inputs = root / "repo", root / "scratch", root / "inputs"
        for d in (cls.repo, cls.scratch, cls.inputs):
            d.mkdir()
        cls.marker = root / "MARKER"
        repo = cls.repo
        git(repo, "init", "-q", "-b", "main")
        git(repo, "config", "user.email", "t@example.invalid")
        git(repo, "config", "user.name", "t")
        (repo / "uv.lock").write_text(LOCK.format(ruff="0.16.7", rumdl="0.2.72"))
        (repo / "pyproject.toml").write_text(PYPROJECT)
        (repo / "README.md").write_text(f"# Planted\n\n{LONG}\n")
        (repo / "app.py").write_text('def BadName() -> None:\n    open("x").close()\n')
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "base")
        cls.base = git(repo, "rev-parse", "HEAD")
        # The PR pins other versions; the check must run the base's.
        (repo / "uv.lock").write_text(LOCK.format(ruff="0.16.8", rumdl="0.2.60"))
        git(repo, "commit", "-q", "-am", "pr")
        git(repo, "branch", "pr-1")
        # A second PR whose rumdl config names a command for Python code blocks.
        (repo / ".rumdl.toml").write_text(
            '[global]\ndisable = ["MD013"]\n\n'
            "[code-block-tools]\nenabled = true\n\n"
            '[code-block-tools.languages.python]\nlint = ["marker"]\n\n'
            "[code-block-tools.tools.marker]\n"
            f'command = ["sh", "-c", "touch {cls.marker}; cat >/dev/null"]\n'
        )
        (repo / "README.md").write_text(f"# Planted\n\n```python\nprint(1)\n```\n\n{LONG}\n")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "pr with a command")
        git(repo, "branch", "pr-2")
        (cls.inputs / "md013.md").write_text(f"# Control\n\n{LONG}\n")
        (cls.inputs / "n802.py").write_text("def BadName() -> None:\n    pass\n")
        (cls.inputs / "plw1514.py").write_text('with open("x") as f:\n    pass\n')

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def check(self, ref: str, tool: str, *checks: str) -> tuple[int, str]:
        argv = [sys.executable, str(SCRIPT), "--scratch", str(self.scratch), "--ref", ref]
        argv += ["--base", self.base, "--tool", tool]
        for c in checks:
            argv += ["--check", c]
        done = subprocess.run(argv, cwd=self.repo, capture_output=True, text=True, timeout=600)
        return done.returncode, done.stdout + done.stderr

    def test_rumdl_under_a_disable_list_is_inert_and_says_what_it_spares(self) -> None:
        code, out = self.check("pr-1", "rumdl", f"MD013={self.inputs / 'md013.md'}")
        self.assertEqual(code, 0, out)
        self.assertIn("rumdl 0.2.72, the version uv.lock pins at the base", out)
        self.assertIn("MD013: INERT HERE -- this config does not run MD013", out)
        self.assertIn("Forced on, it reports 1 in 1 of 1 file(s)", out)

    def test_a_command_the_config_names_never_runs(self) -> None:
        self.marker.unlink(missing_ok=True)
        code, out = self.check("pr-2", "rumdl", f"MD013={self.inputs / 'md013.md'}")
        self.assertIn(code, (0, 1), out)
        self.assertIn("passes --no-code-block-tools", out)
        self.assertFalse(self.marker.exists(), "the config's code-block tool ran")

    def test_ruff_reads_its_allow_list_and_its_preview_rule(self) -> None:
        code, out = self.check(
            "pr-1",
            "ruff",
            f"N802={self.inputs / 'n802.py'}",
            f"PLW1514={self.inputs / 'plw1514.py'}",
            f"UP052={self.inputs / 'n802.py'}",
        )
        self.assertEqual(code, 1, out)
        self.assertIn("ruff 0.16.7, the version uv.lock pins at the base", out)
        self.assertNotIn("0.16.8", out)
        self.assertIn("N802: INERT HERE -- this config does not run N802", out)
        self.assertIn("PLW1514: LIVE HERE -- this config runs PLW1514", out)
        self.assertIn("UP052: UNDERIVABLE -- ruff 0.16.7, the version this repo runs today", out)

    def test_bare_rules_take_their_control_from_the_tree_or_the_tools_page(self) -> None:
        """The first replays declined to write an input per rule. Bare, MD013 and
        N802 are earned off the tree, which carries both; SIM117, which the tree does
        not carry, off ruff's own page for it."""
        code, out = self.check("pr-1", "rumdl", "MD013")
        self.assertEqual(code, 0, out)
        self.assertIn("MD013: INERT HERE -- this config does not run MD013. Forced on", out)
        code, out = self.check("pr-1", "ruff", "N802", "SIM117")
        self.assertEqual(code, 0, out)
        self.assertIn("N802: INERT HERE -- this config does not run N802. Forced on", out)
        self.assertIn("in ruff 0.16.7's own page for SIM117", out)
        self.assertIn("SIM117: INERT HERE -- this config does not run SIM117, and forced on", out)

    @unittest.skipUnless(shutil.which("gh"), "rumdl's pages are read from GitHub with gh")
    def test_rumdls_page_answers_a_rule_the_tree_does_not_carry(self) -> None:
        code, out = self.check("pr-1", "rumdl", "MD018")
        self.assertEqual(code, 1, out)
        self.assertIn("in rumdl's docs/md018.md at v0.2.72", out)
        self.assertIn("MD018: LIVE HERE -- this config runs MD018; the tree carries none", out)


if __name__ == "__main__":
    unittest.main()
