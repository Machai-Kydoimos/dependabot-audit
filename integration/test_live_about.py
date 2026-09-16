"""The scope statement that lives outside the repository, and therefore outside the gate.

This plugin says which ecosystems it covers in four places. Three are files, and
are therefore reachable by a hermetic test:

    SKILL.md frontmatter `description:`   — and this is the one that *routes the skill*
    .claude-plugin/plugin.json            `description`
    README.md                             the ecosystem table

The fourth is the **GitHub repo About**, which is repository metadata rather than
a file. No test in `tests/` can see it, nothing in `ci.yml` can fail on it, and
so it drifted: until 2026-09-16 it read *"Covers uv.lock and GitHub Actions"*,
omitting `pre-commit` — which has a 307-line reference, a 546-line
`scripts/precommit.py`, seven test files touching it, its own
`integration/test_precommit_replay.py`, and a README row arguing it is the one
that pays. Three sources said one thing, the fourth said another, and the only
reason the discrepancy survived is that nothing could look at it.

That is worse than an ordinary stale sentence, because `SKILL.md`'s `description`
is what decides whether the skill is invoked at all. The About and the router
disagreeing is two different answers to "what does this plugin do".

**Anchored to the classifier, not to the prose.** The obvious version of this
check compares one description against another, which only proves two sentences
were written by the same person. Instead the ecosystem list is read from
`discover.py`'s `_ecosystem()` — the function that actually decides which Phase 1
method a PR gets. A fourth ecosystem cannot be added without `ALIASES` below
going incomplete, which fails loudly rather than passing in silence.

    RUN_NETWORK_TESTS=1 python3 -m unittest discover -s integration -v

The live half is a tripwire and not a gate, like everything else here: an About
edited in the GitHub UI is a reason to open an issue, not to refuse a merge. The
hermetic half needs no network and runs whenever this directory does.
"""

from __future__ import annotations

import ast
import json
import os
import pathlib
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "skills/dependabot-audit"

live = unittest.skipUnless(
    os.environ.get("RUN_NETWORK_TESTS"),
    "set RUN_NETWORK_TESTS=1; this queries api.github.com",
)

# What `_ecosystem()` returns when it has *not* identified a supported ecosystem.
# Named rather than inferred: if a refactor stops returning these, the set below
# would silently gain two members that are not ecosystems at all.
SENTINELS = frozenset({"unsupported", "unknown"})

# identifier -> the spellings that count as naming it in prose. The identifiers
# are what `discover.py` returns; the spellings are what a human writes, and the
# two differ on purpose (`github-actions` is written "GitHub Actions"). Matched
# case-insensitively against whitespace-collapsed text.
ALIASES = {
    "uv.lock": ("uv.lock",),
    "github-actions": ("github actions",),
    "pre-commit": ("pre-commit",),
}


def classified_ecosystems() -> frozenset[str]:
    """Every ecosystem `discover.py` can classify, read from the function itself.

    Parsed rather than imported: this needs the literals the classifier can
    return, not its behaviour on some fixture, and a fixture-driven version would
    only find the ecosystems whose probes someone remembered to write — which is
    the failure this whole file exists to catch, one level along.
    """
    tree = ast.parse((PLUGIN / "scripts/discover.py").read_text(encoding="utf-8"))
    fn = next(
        n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_ecosystem"
    )
    found = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Tuple):
            first = node.value.elts[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                found.add(first.value)
    return frozenset(found)


def unnamed(description: str) -> set[str]:
    """Which supported ecosystems this description fails to name."""
    flat = " ".join(description.split()).lower()
    return {
        eco
        for eco, spellings in ALIASES.items()
        if not any(spelling in flat for spelling in spellings)
    }


def about() -> str:
    """The live GitHub About, unpiped, with its exit status actually checked.

    No `-R`: `gh` resolves the repository from the checkout's remote, so this
    carries no hardcoded `owner/name` to go stale in a fork. CONTRIBUTING's
    standing trap applies — `gh ... | jq` reports jq's status, so an auth failure
    would arrive as an empty description at exit 0, which is indistinguishable
    from the very drift under test.
    """
    done = subprocess.run(
        ["gh", "repo", "view", "--json", "description"],
        capture_output=True,
        text=True,
        check=False,
        cwd=ROOT,
    )
    if done.returncode != 0:
        raise unittest.SkipTest(f"gh repo view failed ({done.returncode}): {done.stderr.strip()}")
    return json.loads(done.stdout or "{}").get("description") or ""


class TestTheEcosystemListIsReadFromTheClassifier(unittest.TestCase):
    """`ALIASES` is hand-written, so something has to keep it honest."""

    def test_every_classified_ecosystem_has_a_prose_spelling(self):
        supported = classified_ecosystems() - SENTINELS
        self.assertEqual(
            supported,
            set(ALIASES),
            "`discover.py` classifies an ecosystem this file cannot check for. Add "
            "its prose spelling to ALIASES — and then say it in all four scope "
            "statements, the About included",
        )

    def test_the_sentinels_are_still_what_the_classifier_returns(self):
        """Without this, a rename turns 'unsupported' into a fourth ecosystem."""
        self.assertLessEqual(
            SENTINELS,
            classified_ecosystems(),
            "`_ecosystem()` no longer returns the not-identified sentinels this "
            "file subtracts, so the supported set above is wrong in a direction "
            "that reads as coverage",
        )


class TestTheScopeStatementsInTheRepositoryAgree(unittest.TestCase):
    """Checked here beside the fourth, because the point is the four agreeing
    rather than any one of them being right.

    **The README is deliberately not among them**, and this was measured rather
    than assumed. `unnamed()` is a substring search, and the README names
    `uv.lock` 5 times, `github actions` twice and `pre-commit` 9 times while
    discussing all three at length. Delete **every** row of its ecosystem table
    and `unnamed()` still reports nothing missing — so the check would pass no
    matter what that table said. That is the "a guard that matches anything
    anywhere stops discriminating" failure this repo's own prose harness warns
    about, and adding it would look like coverage while being none.
    """

    def test_the_plugin_manifest_names_every_ecosystem(self):
        description = json.loads((ROOT / ".claude-plugin/plugin.json").read_text(encoding="utf-8"))[
            "description"
        ]
        self.assertEqual(set(), unnamed(description), "plugin.json omits an ecosystem")

    def test_the_skill_description_names_every_ecosystem(self):
        """The one that routes the skill, so an omission here is not cosmetic:
        it decides whether the audit is offered for that ecosystem at all."""
        text = (PLUGIN / "SKILL.md").read_text(encoding="utf-8")
        _, _, rest = text.partition("description:")
        description, _, _ = rest.partition("\ndisallowed-tools:")
        self.assertTrue(description.strip(), "SKILL.md frontmatter has no description")
        self.assertEqual(
            set(),
            unnamed(description),
            "SKILL.md's description omits an ecosystem the plugin classifies. That "
            "is what decides when this skill is invoked",
        )

    def test_the_checker_fails_on_the_drift_that_actually_happened(self):
        """The negative control, and it is the load-bearing test here.

        A checker that has only ever seen agreeing descriptions reports green
        forever and nobody notices it stopped discriminating. The fixture is the
        real About as it read until 2026-09-16, so this asserts the check would
        have caught the thing it was written for — not merely that it runs.
        """
        drifted = (
            "Claude Code plugin: audit a Dependabot or Renovate PR and report an "
            "evidence-backed merge recommendation. Covers uv.lock and GitHub "
            "Actions. Never merges."
        )
        self.assertEqual(
            {"pre-commit"},
            unnamed(drifted),
            "the checker does not notice the omission this file was written for",
        )
        self.assertEqual(
            {"uv.lock", "github-actions", "pre-commit"},
            unnamed("audits dependency bumps"),
            "a description naming no ecosystem at all must fail for all three",
        )


@live
class TestTheAboutAgreesWithTheClassifier(unittest.TestCase):
    def test_the_about_names_every_ecosystem_the_plugin_classifies(self):
        text = about()
        self.assertTrue(
            text,
            "the GitHub About is empty. Three files say what this plugin covers "
            "and the fourth says nothing",
        )
        self.assertEqual(
            set(),
            unnamed(text),
            f"the GitHub About omits an ecosystem this plugin classifies.\n"
            f"  About: {text!r}\n"
            f"It is the only one of the four scope statements that is not a file, "
            f"so no commit can fix it: use `gh repo edit --description`",
        )


if __name__ == "__main__":
    unittest.main()
