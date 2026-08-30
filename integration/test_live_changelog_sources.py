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
