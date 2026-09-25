"""pipaudit.py against real `uv` and `pip-audit`, in both directions (#165).

The hermetic suite fakes both tools, so it can only agree with what this repo
believes they do. This builds a repository with every shape that broke the prose
block `pipaudit.py` replaced, locks it with the real `uv`, and runs the script the
way Phase 3 does -- from the repository, naming refs:

  - a uv workspace whose root depends on a member, so the export used to carry
    `-e ./packages/member-a` and `pip-audit` refused the whole file at exit 1;
  - an extra on the root and on a member, which `--all-groups` alone never exported;
  - a `tool.uv.sources` path dependency and a git source, both unhashable;
  - a member whose build backend writes a file when imported, and must never run;
  - `jinja2` moved into 2.11.3, which carries published advisories -- and a control
    branch moving it out again, which must come back clean.

    RUN_NETWORK_TESTS=1 python3 -m unittest discover -s integration -v

Needs `git`, `uv` and the network: `uv lock` resolves from PyPI and clones one git
source, and `uvx` fetches pip-audit. The advisories on jinja2 2.11.3 are published
and permanent, so the finding does not drift; the clean control could, the day an
advisory is filed against a package in it, and would then say so by name.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest

SCRIPT = (
    pathlib.Path(__file__).resolve().parent.parent / "skills/dependabot-audit/scripts/pipaudit.py"
)

live = unittest.skipUnless(
    os.environ.get("RUN_NETWORK_TESTS") and shutil.which("uv") and shutil.which("git"),
    "set RUN_NETWORK_TESTS=1, with uv and git on PATH; this locks against PyPI",
)

ROOT = """\
[project]
name = "app"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = ["member-a", "member-c", "localdep", "iniconfig", "jinja2=={jinja}"]

[project.optional-dependencies]
docs = ["pyparsing"]

[tool.uv.sources]
member-a = {{ workspace = true }}
member-c = {{ workspace = true }}
localdep = {{ path = "vendor/localdep" }}
iniconfig = {{ git = "https://github.com/pytest-dev/iniconfig", tag = "v2.0.0" }}

[tool.uv.workspace]
members = ["packages/*"]
"""

MEMBER_A = """\
[project]
name = "member-a"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = ["idna"]

[project.optional-dependencies]
extra1 = ["six"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
"""

MEMBER_C = """\
[project]
name = "member-c"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = []

[build-system]
requires = []
build-backend = "tripwire"
backend-path = ["."]
"""

LOCALDEP = """\
[project]
name = "localdep"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = ["packaging"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
"""


def sh(*argv: str, cwd: pathlib.Path) -> str:
    done = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, check=False)
    if done.returncode != 0:
        raise AssertionError(f"{' '.join(argv)} exited {done.returncode}: {done.stderr}")
    return done.stdout


@live
class TestPipauditAgainstTheRealTools(unittest.TestCase):
    repo: pathlib.Path
    tripwire: pathlib.Path
    base: str
    tmp: tempfile.TemporaryDirectory[str]

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        top = pathlib.Path(cls.tmp.name)
        cls.repo = top / "repo"
        cls.tripwire = top / "TRIPWIRE-FIRED"
        files = {
            "packages/member-a/pyproject.toml": MEMBER_A,
            "packages/member-a/src/member_a/__init__.py": "",
            "packages/member-c/pyproject.toml": MEMBER_C,
            "packages/member-c/tripwire.py": textwrap.dedent(f"""\
                import pathlib
                pathlib.Path({str(cls.tripwire)!r}).write_text("the PR's code ran")
                """),
            "vendor/localdep/pyproject.toml": LOCALDEP,
            "vendor/localdep/src/localdep/__init__.py": "",
            "src/app/__init__.py": "",
        }
        for path, text in files.items():
            target = cls.repo / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        sh("git", "init", "-q", "-b", "main", cwd=cls.repo)
        who = ("-c", "user.email=t@t", "-c", "user.name=t")
        for branch, jinja in (("main", "3.1.6"), ("pr-1", "2.11.3"), ("pr-2", "3.1.6")):
            if branch != "main":
                start = "main" if branch == "pr-1" else "pr-1"
                sh("git", "checkout", "-q", "-b", branch, start, cwd=cls.repo)
            (cls.repo / "pyproject.toml").write_text(ROOT.format(jinja=jinja), encoding="utf-8")
            sh("uv", "lock", "-q", cwd=cls.repo)
            sh("git", "add", "-A", cwd=cls.repo)
            sh("git", *who, "commit", "-q", "-m", f"jinja2 {jinja}", cwd=cls.repo)
        cls.base = sh("git", "rev-parse", "main", cwd=cls.repo).strip()
        # The checkout is left on pr-2 -- neither of the refs pr-1 is audited against.
        cls.tripwire.unlink(missing_ok=True)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def audit(self, ref: str, base: str) -> tuple[int, str]:
        """(exit status, output); the export's text is kept in `self.export`."""
        with tempfile.TemporaryDirectory() as scratch:
            done = subprocess.run(
                [sys.executable, str(SCRIPT), "--scratch", scratch, "--ref", ref, "--base", base],
                cwd=self.repo,
                capture_output=True,
                text=True,
                check=False,
            )
            exported = pathlib.Path(scratch, f"{ref}-requirements.txt")
            self.export = exported.read_text(encoding="utf-8") if exported.is_file() else ""
        return done.returncode, done.stdout + done.stderr

    def test_a_pr_moving_into_a_vulnerable_version_is_found_and_tagged(self):
        code, out = self.audit("pr-1", self.base)
        self.assertEqual(code, 1, out)
        self.assertIn("jinja2==2.11.3   in the export", out)
        self.assertRegex(out, r"jinja2==2\.11\.3 .*<- a version this PR introduces")
        self.assertIn("RESULT: FOUND", out)

    def test_the_extras_are_audited_and_the_unhashable_are_named(self):
        _, out = self.audit("pr-1", self.base)
        self.assertNotIn("UNDERIVABLE", out, out)
        # `pyparsing` is the root's `docs` extra, `six` member-a's `extra1`, and
        # `packaging` reaches the tree only through the path dependency.
        for pin in ("pyparsing==", "six==", "packaging==", "idna=="):
            self.assertIn(pin, self.export)
        self.assertNotRegex(self.export, r"(?m)^(?:-e |\./|\S+ @ git\+)")
        for named in ("member-a (editable)", "localdep (directory)", "iniconfig (git)"):
            self.assertIn(named, out)
        self.assertNotIn("NOT AUDITED", out)

    def test_the_prs_build_backend_never_runs(self):
        self.audit("pr-1", self.base)
        self.assertFalse(self.tripwire.exists(), "member-c's backend was imported")

    def test_a_pr_moving_out_of_it_comes_back_clean(self):
        """The control, the other direction: the same tree with 3.1.6 again."""
        code, out = self.audit("pr-2", "pr-1")
        self.assertEqual(code, 0, out)
        self.assertIn("jinja2==3.1.6   in the export", out)
        self.assertIn("RESULT: CLEAN", out)


if __name__ == "__main__":
    unittest.main()
