"""The premise behind Phase 2's changelog ladder: not every project releases.

`references/uv-lock.md` § Phase 2 tells the auditor to fall back from release
notes to a changelog section to a tag-to-tag commit range, and justifies the
ladder with two facts about the outside world:

1. a widely-depended-on project can publish **no GitHub releases at all**, and
2. release tags disagree about the `v` prefix, so constructing one silently
   returns "release not found" — which reads exactly like "no notes for this
   version".

Both are claims about other people's repositories and both can change without
notice. That is precisely why they belong here rather than in the hermetic
suite: if `python/mypy` starts cutting releases, the prose should be revisited,
not quietly left asserting something that stopped being true.

    RUN_NETWORK_TESTS=1 python3 -m unittest discover -s integration -v
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import unittest
from typing import Any

live = unittest.skipUnless(
    os.environ.get("RUN_NETWORK_TESTS"),
    "set RUN_NETWORK_TESTS=1; this queries api.github.com",
)


def gh_json(path: str, jq: str) -> Any:
    """One `gh api` call, unpiped, with its exit status actually checked.

    CONTRIBUTING's standing trap: `gh api ... | jq` reports jq's status, so an
    auth failure or a rate limit arrives as an empty result at exit 0 — which
    here would look like "this project publishes no releases", the very fact
    under test.
    """
    done = subprocess.run(
        ["gh", "api", path, "--jq", jq],
        capture_output=True,
        text=True,
        check=False,
    )
    if done.returncode != 0:
        raise unittest.SkipTest(f"gh api {path} failed ({done.returncode}): {done.stderr.strip()}")
    return json.loads(done.stdout or "null")


@live
class TestNotEveryProjectPublishesReleases(unittest.TestCase):
    def test_mypy_still_publishes_no_github_releases(self):
        """The worked example in the reference. If this starts failing, mypy has
        begun releasing and the ladder's rung-3 example needs a new subject."""
        count = gh_json("repos/python/mypy/releases", "length")
        self.assertEqual(
            count,
            0,
            "python/mypy now publishes releases — Phase 2's 'zero releases, patch "
            "documented only in commits' example is stale",
        )

    def test_mypy_still_tags_what_it_does_not_release(self):
        """The ladder only works because the tags exist to compare between."""
        tags = gh_json("repos/python/mypy/tags", "[.[].name]")
        self.assertIn("v2.3.1", tags, "the tag the reference compares from is gone")

    def test_the_commit_range_still_answers_for_a_patch_release(self):
        n = gh_json("repos/python/mypy/compare/v2.3.0...v2.3.1", ".commits | length")
        self.assertIsInstance(n, int)
        self.assertGreater(n, 0, "rung 3 returned nothing for a release known to have content")


@live
class TestReleaseTagsDisagreeAboutThePrefix(unittest.TestCase):
    """Why the reference matches the tag instead of building it."""

    def test_ruff_releases_without_a_v_prefix(self):
        tags = gh_json("repos/astral-sh/ruff/releases?per_page=100", "[.[].tag_name]")
        self.assertIn("0.16.4", tags)
        self.assertNotIn("v0.16.4", tags, "ruff has started prefixing; the example needs updating")

    def test_rumdl_releases_with_one(self):
        tags = gh_json("repos/rvben/rumdl/releases?per_page=100", "[.[].tag_name]")
        self.assertIn("v0.2.58", tags)


@live
class TestARungThatAnsweredCanStillBeIncomplete(unittest.TestCase):
    """The premise behind Phase 2's reconciliation, and behind `changelog.py`.

    The ladder's rungs are not a fallback chain. Prose is what a project chose to
    say; the commit range is what actually landed, and the two can disagree while
    every rung returns real, well-formed, correctly-authored content. No exit
    status anywhere can reach that.

    `rumdl` v0.2.60...v0.2.62 is the case #94 was filed from, and it is durable:
    release bodies for published tags are immutable in practice, the tag pair is
    fixed, and the assertions below are the ones the rule rests on. If any of
    them goes red, the project changed how it generates release notes and the
    reference's worked example needs a new subject -- which is exactly what this
    directory is for.

    These are also the live half of `tests/test_changelog.py`'s recorded
    fixtures. Those stay green by construction; these say whether they still
    describe the world.
    """

    def test_the_release_notes_for_the_audited_version_document_no_fixes(self):
        # `.body | @json`, not `.body`: a release body is multi-line, and the
        # plain filter hands back raw text this helper cannot parse. The same
        # escaping `changelog.py` needs to keep one commit message on one line.
        body = gh_json("repos/rvben/rumdl/releases/tags/v0.2.61", ".body | @json") or ""
        notes = body.split("## Downloads")[0]
        self.assertIn("### Added", notes, "the notes are not the shape the example describes")
        self.assertNotIn(
            "### Fixed",
            notes,
            "rumdl v0.2.61 now documents fixes -- the reconciliation example needs a new subject",
        )

    def test_the_same_range_carries_fix_commits(self):
        """The half no changelog can omit."""
        subjects = gh_json(
            "repos/rvben/rumdl/compare/v0.2.60...v0.2.62",
            '[.commits[].commit.message | split("\\n")[0]]',
        )
        fixes = [s for s in subjects if s.startswith("fix(")]
        self.assertGreaterEqual(
            len(fixes),
            5,
            f"the range carried 5 fix commits when #94 was filed; it now carries {len(fixes)}",
        )

    def test_two_of_them_carry_the_destructive_shape_phase_2_looks_for(self):
        """`stop rewriting Rust source when formatting doc comments` wrote
        `# [derive(Debug)]` to disk. This is the row a run honoring the ladder as
        written never reported."""
        subjects = gh_json(
            "repos/rvben/rumdl/compare/v0.2.60...v0.2.62",
            '[.commits[].commit.message | split("\\n")[0]]',
        )
        marked = [s for s in subjects if re.search(r"\b(?:stops?|no longer)\b", s, re.I)]
        self.assertGreaterEqual(len(marked), 2, f"expected the two destructive fixes, got {marked}")

    def test_the_project_does_document_fixes_when_it_has_them(self):
        """Why the obvious heuristic does not save the ladder: "does this project
        document its fixes at all?" returns a confident yes. Only the versions
        under audit had none."""
        body = gh_json("repos/rvben/rumdl/releases/tags/v0.2.59", ".body | @json") or ""
        self.assertIn("### Fixed", body.split("## Downloads")[0])

    def test_mypy_labels_none_of_its_commits(self):
        """The other classifier's premise. A filter keyed on `fix(` reports zero
        fixes for this range, which carries four -- the defect `changelog.py`
        shipped in its first version and the offline suite now pins."""
        subjects = gh_json(
            "repos/python/mypy/compare/v2.3.0...v2.3.1",
            '[.commits[].commit.message | split("\\n")[0]]',
        )
        conventional = [s for s in subjects if re.match(r"^[a-z]+(\([^)]*\))?!?:", s)]
        self.assertEqual(
            conventional, [], "python/mypy has adopted conventional commits; the example is stale"
        )
        self.assertTrue(
            [s for s in subjects if "Fix" in s],
            "the range no longer carries the fixes the unlabelled example rests on",
        )
