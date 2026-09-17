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


def gh_text(path: str, *, accept: str) -> str:
    """One `gh api` call for a raw file, with its exit status actually checked.

    The raw media type rather than `--jq .content` + `base64 -d`: above 1 MiB the
    contents endpoint answers `200` with `content: ""` and `encoding: "none"`,
    and that idiom decodes the empty string to an empty file at exit 0 (#127).
    `CHANGELOG.md` is well under the boundary here; the form is the one the
    plugin documents, so this exercises it rather than a second idiom.
    """
    done = subprocess.run(
        ["gh", "api", path, "-H", f"Accept: {accept}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if done.returncode != 0:
        raise unittest.SkipTest(f"gh api {path} failed ({done.returncode}): {done.stderr.strip()}")
    return done.stdout


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

        `rumdl` v0.2.60...v0.2.62 is the case #94 was filed from, and it has already
        moved once. **Release bodies for published tags are not immutable** -- this
        file asserted that they were "immutable in practice" until 2026-09-17, when
        the assertion below went red: the project backfilled a `### Fixed` section
        into v0.2.61 on 2026-09-05, ten days after cutting the tag, and the API says
        so only in `updated_at` (#131).

    **Nor is this project's changelog fixed, and that correction is why this
    docstring was rewritten twice.** `rumdl` *generates* `CHANGELOG.md` from
    conventional commits and rebuilds it in full at every release, so its `0.2.61`
    entry carries no fixes at refs v0.2.62..v0.2.65 and five at v0.2.66 and later.
    No blob changed; the file is regenerated. That is `rumdl`'s tooling and not a
    property of changelogs -- a hand-maintained one is appended to, and its old
    sections are stable across refs.

    **And on this project the two prose rungs are not independent.** The release
    workflow builds the body with `scripts/extract-changelog.sh`, an awk slice of
    `CHANGELOG.md`; the backfilled v0.2.61 body is byte-identical to that file's
    section at v0.2.73, which `test_the_two_prose_rungs_are_the_same_text` pins. So
    their disagreement below is one source read at two times. Elsewhere they may be
    genuinely two sources -- which is why the references say to check rather than to
    assume. What holds regardless is only that rung 3 cannot be rewritten without
    rewriting history.

        Note what that costs a live run: the range now **reconciles**, exit `0`,
        because rung 1 names all five fixes. The founding case for the script passes
        the script. `tests/test_changelog.py` still carries the notes as published,
        which is what keeps the original case testable.

        These are also the live half of `tests/test_changelog.py`'s recorded
        fixtures. Those stay green by construction; these say whether they still
        describe the world.
    """

    def test_the_release_notes_were_rewritten_after_the_tag_was_cut(self):
        """The mechanism, not the symptom. `published_at` never moves; this is
        the only field that says the body you just read is not the announcement,
        and no `.body` read can reach it."""
        stamps = gh_json(
            "repos/rvben/rumdl/releases/tags/v0.2.61",
            r'"\(.published_at)\t\(.updated_at)" | @json',
        )
        published, updated = stamps.split("\t")
        self.assertEqual(published, "2026-08-26T19:24:23Z", "the tag was cut at a different time")
        self.assertGreater(
            updated,
            published,
            "rumdl v0.2.61 no longer reads as edited -- if the body was restored, "
            "the disagreement below is gone and the example needs re-measuring",
        )

    def test_the_release_notes_for_the_audited_version_now_document_the_fixes(self):
        # `.body | @json`, not `.body`: a release body is multi-line, and the
        # plain filter hands back raw text this helper cannot parse. The same
        # escaping `changelog.py` needs to keep one commit message on one line.
        body = gh_json("repos/rvben/rumdl/releases/tags/v0.2.61", ".body | @json") or ""
        notes = body.split("## Downloads")[0]
        self.assertIn("### Added", notes, "the notes are not the shape the example describes")
        self.assertIn(
            "### Fixed",
            notes,
            "the backfilled fixes are gone again -- rung 1 and rung 2 no longer "
            "disagree, so the worked example is back to its 2026-08 shape",
        )
        self.assertIn(
            "stop rewriting Rust source when formatting doc comments",
            notes,
            "the destructive fix the example is built around is not in the notes",
        )

    def _section_61(self, ref: str) -> str:
        text = gh_text(
            f"repos/rvben/rumdl/contents/CHANGELOG.md?ref={ref}",
            accept="application/vnd.github.raw",
        )
        start = text.index("## [0.2.61]")
        return text[start : text.index("## [0.2.60]", start)]

    def test_the_changelog_at_the_proposed_tag_omits_them(self):
        """What an auditor of the 0.2.60 -> 0.2.62 bump reads, because the
        procedure reads the changelog at the version being proposed."""
        section = self._section_61("v0.2.62")
        self.assertIn("### Added", section)
        self.assertNotIn(
            "### Fixed",
            section,
            "a blob at a tag cannot change, so this one can only fail if the "
            "project rewrote history",
        )

    def test_the_same_section_at_a_later_tag_carries_them(self):
        """The correction that matters: a *generated* changelog is rewritten in
        full at every release, so the entry for one version is a function of the
        ref. Same file, same version, different answer -- and the later answer is
        the more complete one. This is why `changelog.py` reads both refs."""
        self.assertIn("### Fixed", self._section_61("v0.2.66"))
        self.assertIn("stop rewriting Rust source", self._section_61("v0.2.66"))
        self.assertNotIn(
            "### Fixed",
            self._section_61("v0.2.65"),
            "v0.2.66 is the release that regenerated the changelog; if v0.2.65 "
            "now carries the fixes too, the boundary moved",
        )

    def test_the_two_prose_rungs_are_the_same_text(self):
        """Rung 1 is produced *from* rung 2 here -- the release workflow slices
        `CHANGELOG.md` with awk -- so the ladder's cross-check is not one. If
        this stops holding, the project changed how it builds release bodies and
        the independence caveat in the references needs re-measuring."""
        body = gh_json("repos/rvben/rumdl/releases/tags/v0.2.61", ".body | @json") or ""
        notes = [ln for ln in body.split("## Downloads")[0].splitlines() if ln.strip()]
        section = [ln for ln in self._section_61("v0.2.73").splitlines() if ln.strip()][1:]
        self.assertEqual(
            notes,
            section,
            "the release body is no longer a verbatim slice of the changelog",
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
