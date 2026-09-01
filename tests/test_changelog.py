"""Regression tests for changelog.py.

No network: `_gh` is the single seam every GitHub call goes through, so the fakes
below drive the real tag matching, section extraction, classification and
reconciliation, and only the subprocess is replaced.

**Every fixture here is recorded from the live API, not written to fit the rule.**
That is the trap this file exists downstream of: a reconciliation rule tested
against changelogs invented from the rule can only agree with itself. The
subjects, release bodies and changelog sections below are verbatim from

    gh api repos/rvben/rumdl/compare/v0.2.60...v0.2.62 --jq '.commits[].commit.message'
    gh api repos/rvben/rumdl/releases/tags/v0.2.61 --jq .body
    gh api repos/rvben/rumdl/contents/CHANGELOG.md?ref=v0.2.62 -H 'Accept: application/vnd.github.raw'
    gh api repos/python/mypy/compare/v2.3.0...v2.3.1 --jq '.commits[].commit.message'

recorded 2026-09-01. The live half of the same claims is in
`integration/test_live_changelog_sources.py`, which goes red when the world moves
-- these stay green, because they are about this script's reading of what the
world said that day.

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import contextlib
import io
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
from typing import Any
from unittest import mock

sys.path.insert(
    0, str(pathlib.Path(__file__).resolve().parent.parent / "skills/dependabot-audit/scripts")
)

from changelog import (
    DESTRUCTIVE,
    SHOWN,
    _gh,
    _gh_hard,
    candidates,
    cli,
    described,
    github_slug,
    labelled,
    main,
    match_tag,
    normalise,
    rank,
    reconciled,
    resolve_repo,
    section_for,
    valid_slug,
)

# --- recorded: rvben/rumdl, the bump behind #94 ------------------------------

RUMDL_61_62 = [
    "feat(cli): add multi-document stdin batches",
    "docs(cli): document stdin batch protocol",
    "fix(MD057): respect closed-world self-reference policy",
    "test(cli): add stdin batch performance smoke test",
    "docs(changelog): record stdin batch support",
    "fix(cli): resolve canonical stdin batch target paths",
    "test(lint-context): use native canonical path fixtures",
    "fix(cli): report document-level fixes as fixed",
    "fix(cli): stop rewriting Rust source when formatting doc comments",
    "fix(lint-context): stop reading a lazy continuation as a setext underline",
    "chore: bump version to v0.2.61",
    "feat(flavor): add support for Markdown with Gherkin (MDG)",
    "docs(mdg): correct the tag-line rationale and the flavor selection note",
    "ci(windows): name cargo-binstall in the scoped mise installs",
    "ci: pin the mise version at every mise-action call site",
    "ci: track the mise version each workflow passes to mise-action",
    "ci(deps): move upd to v0.8.2 and align the last mise pin",
    "chore: bump version to v0.2.62",
]

# The whole of what rung 1 says for each. Additive, both of them.
RUMDL_NOTES_61 = (
    "\n### Added\n\n- **cli**: add `--stdin-batch` for NUL-framed multi-document "
    "linting and `--stdin-batch-closed-world` for supplied-document-only link "
    "resolution\n\n\n## Downloads\n\n| File | Platform | Checksum |\n"
)
RUMDL_NOTES_62 = (
    "\n### Added\n\n- **flavor**: add support for Markdown with Gherkin (MDG) "
    "([db62377](https://github.com/rvben/rumdl/commit/db62377aa7e63865f682bf16d790c6ff5eb40b31))"
    "\n\n\n## Downloads\n\n| File | Platform | Checksum |\n"
)

# Rung 2, verbatim -- including the compare link whose target carries the
# *previous* version, which is what broke the first `section_for`.
RUMDL_CHANGELOG = """\
# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

## [0.2.62](https://github.com/rvben/rumdl/compare/v0.2.61...v0.2.62) - 2026-08-27

### Added

- **flavor**: add support for Markdown with Gherkin (MDG) ([db62377](https://github.com/rvben/rumdl/commit/db62377aa7e63865f682bf16d790c6ff5eb40b31))

## [0.2.61](https://github.com/rvben/rumdl/compare/v0.2.60...v0.2.61) - 2026-08-26

### Added

- **cli**: add `--stdin-batch` for NUL-framed multi-document linting and `--stdin-batch-closed-world` for supplied-document-only link resolution

## [0.2.60](https://github.com/rvben/rumdl/compare/v0.2.59...v0.2.60) - 2026-08-22

### Fixed

- **deps**: update h2 to 0.4.16 ([a650302](https://github.com/rvben/rumdl/commit/a6503022a5b0268138fbec2068d8e9a7abd27e64))

## [0.2.59](https://github.com/rvben/rumdl/compare/v0.2.58...v0.2.59) - 2026-08-22

### Fixed

- **MD033**: ignore escaped HTML tag openers ([eaa4075](https://github.com/rvben/rumdl/commit/eaa4075d665f8174256ddbeb21ecb8d64f34525a))
- **config**: match absolute patterns through symlinks ([5dd6158](https://github.com/rvben/rumdl/commit/5dd615823eb3128009dd0534829f18e96a73a2c6))
- **MD013**: stop reflow from joining a setext heading into its underline ([9ec9e17](https://github.com/rvben/rumdl/commit/9ec9e17458f45c6e621de3352a678870c71441e3))

## [0.2.58](https://github.com/rvben/rumdl/compare/v0.2.57...v0.2.58) - 2026-08-19

### Added

- **wasm**: load extends chains from embedder-supplied config files ([e7c7d8f](https://github.com/rvben/rumdl/commit/e7c7d8f9fa64f1a74195f52cae9d328a8fae9389))
"""

# The positive control, and the strongest one available: 0.2.59's release notes
# name all three of its fixes, and one of them carries the destructive shape.
RUMDL_59_60 = [
    "fix(MD013): stop reflow from joining a setext heading into its underline",
    "docs: point the schema instructions at src/config/",
    "docs(scope): reject general regex ignore surfaces",
    "fix(config): match absolute patterns through symlinks",
    "fix(MD033): ignore escaped HTML tag openers",
    "chore: bump version to v0.2.59",
    "fix(deps): update h2 to 0.4.16",
    "chore: bump version to v0.2.60",
]
RUMDL_NOTES_59 = (
    "\n### Fixed\n\n"
    "- **MD033**: ignore escaped HTML tag openers ([eaa4075](https://github.com/rvben/rumdl/commit/eaa4075))\n"
    "- **config**: match absolute patterns through symlinks ([5dd6158](https://github.com/rvben/rumdl/commit/5dd6158))\n"
    "- **MD013**: stop reflow from joining a setext heading into its underline "
    "([9ec9e17](https://github.com/rvben/rumdl/commit/9ec9e17))\n"
)
RUMDL_NOTES_60 = (
    "\n### Fixed\n\n- **deps**: update h2 to 0.4.16 "
    "([a650302](https://github.com/rvben/rumdl/commit/a650302))\n"
)

# --- recorded: python/mypy, the range the reference already cites ------------
#
# Zero releases, a CHANGELOG.md with no 2.3.1 section, and six commits of which
# four are fixes -- none of them conventionally labelled. The first version of
# changelog.py reported this range as carrying no fixes at all.

MYPY_30_31 = [
    "Bump version to 2.3.1+dev",
    "Fix crash when unpacking return value from overload (#21830)",
    "[mypyc] Clear coroutine env on coroutine completion (#21734)",
    "[mypyc] Fix `default_factory` for inherited dataclass (#21785)",
    "[mypyc] Fix crash on double yielding Iterators (#21826)",
    "Bump version to 2.3.1",
]

MYPY_CHANGELOG = """\
# Mypy Release Notes

## Next Release

## Mypy 2.3

Some notes about the 2.3 feature release.

## Mypy 2.2

Older notes.
"""


class Repo:
    """One repository as the five calls in `changelog.py` see it."""

    def __init__(
        self,
        slug: str,
        releases: list[tuple[str, str]] | None = None,
        tags: list[str] | None = None,
        files: dict[str, str] | None = None,
        commits: list[str] | None = None,
    ) -> None:
        self.slug = slug
        self.releases = releases or []
        self.tags = tags or [tag for tag, _ in (releases or [])]
        self.files = files or {}
        self.commits = commits or []


def fake_gh(repo: Repo, log: list[str] | None = None) -> Any:
    """A `_gh` that answers from `repo`, and `None` for anything absent.

    `None` rather than `""` throughout, because that distinction is the thing
    under test in half these cases: a call that failed must never be readable as
    an answer that was empty.
    """

    def fake(args: list[str]) -> str | None:
        joined = " ".join(args)
        if log is not None:
            log.append(joined)
        if f"repos/{repo.slug}/releases" in joined:
            return "\n".join(
                json.dumps({"tag": tag, "body": body, "at": "2026-08-26T00:00:00Z"})
                for tag, body in repo.releases
            )
        if "/git/ref/tags/" in joined:
            return "{}" if joined.rsplit("/", 1)[-1] in repo.tags else None
        if "/compare/" in joined:
            return "\n".join(json.dumps(m) for m in repo.commits)
        if "/contents?" in joined:
            return "\n".join(repo.files)
        if "/contents/" in joined:
            name = joined.split("/contents/")[1].split("?")[0]
            return repo.files.get(name)
        return None

    return fake


class ChangelogHarness(unittest.TestCase):
    def run_main(self, repo: Repo, *argv: str) -> tuple[int, str, str]:
        """(exit status, stdout, the evidence file's text).

        The file is read before the temporary directory goes, because the
        directory is the thing under test as much as the text is: exactly one
        evidence file per run, whatever the verdict.
        """
        with tempfile.TemporaryDirectory() as scratch:
            full = ["changelog.py", "--scratch", scratch, "--repo-slug", repo.slug, *argv]
            out = io.StringIO()
            with (
                mock.patch("changelog._gh", fake_gh(repo)),
                mock.patch.object(sys, "argv", full),
                contextlib.redirect_stdout(out),
            ):
                status = main()
            # Copy the directory listing out before the context manager takes it.
            written = list(pathlib.Path(scratch).iterdir())
            self.assertEqual(len(written), 1, "exactly one evidence file per run")
            return status, out.getvalue(), written[0].read_text(encoding="utf-8")

    def rumdl_61_62(self) -> Repo:
        return Repo(
            "rvben/rumdl",
            releases=[
                ("v0.2.62", RUMDL_NOTES_62),
                ("v0.2.61", RUMDL_NOTES_61),
                ("v0.2.60", RUMDL_NOTES_60),
                ("v0.2.59", RUMDL_NOTES_59),
                ("v0.2.58", "\n### Added\n\n- **wasm**: load extends chains\n"),
            ],
            files={"CHANGELOG.md": RUMDL_CHANGELOG, "Cargo.toml": ""},
            commits=RUMDL_61_62,
        )

    def rumdl_58_60(self) -> Repo:
        repo = self.rumdl_61_62()
        repo.commits = RUMDL_59_60
        return repo

    def mypy(self) -> Repo:
        return Repo(
            "python/mypy",
            releases=[],
            tags=["v2.3.1", "v2.3.0"],
            files={"CHANGELOG.md": MYPY_CHANGELOG},
            commits=MYPY_30_31,
        )


class TestTheRungThatAnsweredIsNotTheWholeAnswer(ChangelogHarness):
    """#94, reduced to one assertion.

    Both prose rungs return real, well-formed, correctly-authored content for
    exactly the right versions, and five fixes never enter the audit. Nothing
    fails; no exit status anywhere can reach it.
    """

    def test_the_range_carries_fixes_the_prose_does_not_name(self):
        status, out, _ = self.run_main(self.rumdl_61_62(), "--from", "0.2.60", "--to", "0.2.62")
        self.assertEqual(status, 1, "a range whose prose omits five fixes is a finding")
        self.assertIn("UNRECONCILED: 5 of 5", out)

    def test_both_prose_rungs_did_answer(self):
        """The point of the case: this is not a lookup failure wearing a finding's
        clothes. Rung 1 and rung 2 both produced content for both versions."""
        _, out, _ = self.run_main(self.rumdl_61_62(), "--from", "0.2.60", "--to", "0.2.62")
        self.assertIn("rung 1 -- release notes: 2 release(s)", out)
        self.assertIn("rung 2 -- CHANGELOG.md: 2 section(s)", out)

    def test_the_two_destructive_fixes_are_marked(self):
        _, out, _ = self.run_main(self.rumdl_61_62(), "--from", "0.2.60", "--to", "0.2.62")
        for subject in (
            "stop rewriting Rust source when formatting doc comments",
            "stop reading a lazy continuation as a setext underline",
        ):
            line = next(ln for ln in out.splitlines() if subject in ln)
            self.assertIn("destructive-fix shape", line, f"{subject!r} was not marked")

    def test_write_mode_changes_the_wording_and_not_the_search(self):
        """The judgement escalates a finding; it never gates the call that finds it."""
        plain = self.run_main(self.rumdl_61_62(), "--from", "0.2.60", "--to", "0.2.62")
        armed = self.run_main(
            self.rumdl_61_62(), "--from", "0.2.60", "--to", "0.2.62", "--write-mode"
        )
        self.assertEqual(plain[0], armed[0], "the flag must not change what was found")
        self.assertIn("UNRECONCILED: 5 of 5", plain[1])
        self.assertIn("if this repo runs the tool in write mode", plain[1])
        self.assertIn("this repo runs the tool in write mode, so", armed[1])


class TestTheReconciliationCanAlsoSayYes(ChangelogHarness):
    """The anti-vacuity half. A matcher that never matches would pass every test
    above, and would be the same defect one layer down.

    rumdl v0.2.58...v0.2.60 is the strongest control available: 0.2.59's notes
    name all three of its fixes, **including the destructive-shaped one**, so a
    marker cannot smuggle a row past a matcher that is working.
    """

    def test_a_range_whose_prose_names_its_fixes_is_clean(self):
        status, out, _ = self.run_main(self.rumdl_58_60(), "--from", "0.2.58", "--to", "0.2.60")
        self.assertEqual(status, 0, out)
        self.assertIn("RECONCILED: the prose names all 4 fix commit(s)", out)

    def test_a_destructive_shape_that_is_documented_is_not_a_finding(self):
        prose = [normalise(line) for line in RUMDL_NOTES_59.splitlines() if normalise(line)]
        subject = "fix(MD013): stop reflow from joining a setext heading into its underline"
        self.assertTrue(DESTRUCTIVE.search(subject), "the fixture must carry the shape")
        parsed = described(subject)
        assert parsed is not None
        self.assertTrue(
            reconciled(parsed[1], prose),
            "a fix the changelog names is reconciled however alarming its wording",
        )

    def test_a_reworded_entry_still_reconciles(self):
        """Generated changelogs re-punctuate and re-link. `difflib` covers that
        much and no more -- see the next test for where it stops."""
        prose = [normalise("- **cli**: resolve the canonical stdin batch target paths")]
        self.assertTrue(reconciled("resolve canonical stdin batch target paths", prose))

    def test_a_different_fix_does_not_reconcile_against_a_similar_one(self):
        """The threshold has to fail somewhere, or it is not a threshold."""
        prose = [normalise("- **cli**: resolve canonical stdin batch target paths")]
        self.assertFalse(
            reconciled("stop rewriting Rust source when formatting doc comments", prose)
        )


class TestAProjectThatDoesNotLabelItsCommits(ChangelogHarness):
    """The defect this file's own first version shipped.

    Filtering on `fix(` is only honest where the project did the labelling.
    `python/mypy` v2.3.0...v2.3.1 carries four fixes and no conventional commits,
    and the first version of `changelog.py` reported it as carrying none -- an
    absence of evidence read as evidence of absence, rebuilt inside the tool
    written to remove it.
    """

    def test_mypys_four_fixes_are_not_reported_as_zero(self):
        status, out, _ = self.run_main(self.mypy(), "--from", "2.3.0", "--to", "2.3.1")
        self.assertEqual(status, 1, out)
        self.assertIn("UNRECONCILED: 4 of 4", out)
        self.assertIn("Fix crash when unpacking return value from overload", out)

    def test_the_output_says_which_classifier_ran(self):
        """The ladder's own discipline one level down: a count of fixes means
        something different depending on who classified them."""
        _, unlabelled, _ = self.run_main(self.mypy(), "--from", "2.3.0", "--to", "2.3.1")
        _, conventional, _ = self.run_main(self.rumdl_61_62(), "--from", "0.2.60", "--to", "0.2.62")
        self.assertIn("does not label its commits", unlabelled)
        self.assertIn("classifier: conventional commits", conventional)

    def test_the_two_fixtures_really_do_differ(self):
        self.assertTrue(labelled(RUMDL_61_62))
        self.assertFalse(labelled(MYPY_30_31))

    def test_release_chores_are_excluded_and_counted(self):
        """`Bump version to 2.3.1` is never a finding, and every project has two
        per release -- so the exclusion is named in the output rather than silent."""
        _, out, _ = self.run_main(self.mypy(), "--from", "2.3.0", "--to", "2.3.1")
        self.assertIn("(2 release chore(s) excluded)", out)
        self.assertNotIn("Bump version to 2.3.1", out.split("UNRECONCILED")[1])

    def test_nothing_is_filtered_when_the_project_did_not_label(self):
        """`[mypyc] Clear coroutine env on coroutine completion` reads like neither
        a fix nor a chore. It is a fix, and it is in the report because the
        unlabelled mode filters nothing."""
        kept, mode, _ = candidates(MYPY_30_31)
        self.assertEqual(mode, "unlabelled")
        self.assertIn("[mypyc] Clear coroutine env on coroutine completion (#21734)", kept)


class TestTheTagIsMatchedRatherThanConstructed(ChangelogHarness):
    """Projects disagree about the `v` prefix and change their minds mid-life.

    A guessed tag returns "not found", which reads exactly like "this version has
    no notes" -- the reference already records `ruff` releasing `0.16.4` while its
    older tags carry `v`, and `rumdl` releasing `v0.2.58`.
    """

    def test_a_prefixed_project_is_matched(self):
        _, out, _ = self.run_main(self.rumdl_61_62(), "--from", "0.2.60", "--to", "0.2.62")
        self.assertIn("range: v0.2.60...v0.2.62", out)

    def test_an_unprefixed_project_is_matched(self):
        repo = Repo(
            "astral-sh/ruff",
            releases=[("0.16.4", "### Bug fixes\n"), ("0.16.3", "### Bug fixes\n")],
            files={},
            commits=["Fix something (#1)"],
        )
        _, out, _ = self.run_main(repo, "--from", "0.16.3", "--to", "0.16.4")
        self.assertIn("range: 0.16.3...0.16.4", out)

    def test_a_shared_prefix_is_not_a_match(self):
        """`0.2.6` must not find `v0.2.61`. The equality is the guard, and the
        reference's existing tag recipe makes the same point about `grep -Fx`."""
        repo = self.rumdl_61_62()
        log: list[str] = []
        with (
            mock.patch("changelog._gh", fake_gh(repo, log)),
            tempfile.TemporaryDirectory() as scratch,
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            argv = [
                "changelog.py",
                "--scratch",
                scratch,
                "--repo-slug",
                repo.slug,
                "--from",
                "0.2.6",
                "--to",
                "0.2.62",
            ]
            with mock.patch.object(sys, "argv", argv), self.assertRaises(SystemExit) as caught:
                main()
        self.assertEqual(caught.exception.code, 2, "an unmatched version must not be guessed at")
        self.assertTrue(
            any("git/ref/tags/0.2.6" in call for call in log),
            "the fallback should have probed the git ref before giving up",
        )

    def test_a_version_with_no_release_falls_back_to_the_tag(self):
        """mypy publishes nothing and tags `v2.3.1`. Probing both spellings is
        still matching: neither is assumed, and the one that answers is used."""
        _, out, _ = self.run_main(self.mypy(), "--from", "2.3.0", "--to", "2.3.1")
        self.assertIn("range: v2.3.0...v2.3.1", out)


class TestTheChangelogSectionSurvivesItsOwnHeading(ChangelogHarness):
    """A generated changelog heads each section with a compare link carrying the
    **previous** version. The first `section_for` read the raw heading and found
    no section at all in a file that has one per release -- caught by replaying
    the script against the live repo, not by reading it."""

    def test_a_linked_heading_matches_its_own_version(self):
        found = section_for(RUMDL_CHANGELOG, "0.2.61")
        self.assertIn("[0.2.61]", found.splitlines()[0])
        self.assertIn("stdin-batch", found)

    def test_the_link_target_does_not_win(self):
        """`## [0.2.61](...compare/v0.2.60...v0.2.61)` contains `v0.2.60`. Asking
        for 0.2.60 must return 0.2.60's section, which is the one with `h2` in it."""
        found = section_for(RUMDL_CHANGELOG, "0.2.60")
        self.assertIn("[0.2.60]", found.splitlines()[0])
        self.assertIn("update h2 to 0.4.16", found)

    def test_a_heading_that_does_not_lead_with_the_version_still_matches(self):
        """`## Mypy 2.3`. Any token, not the first."""
        self.assertIn("2.3 feature release", section_for(MYPY_CHANGELOG, "2.3"))

    def test_a_version_with_no_section_returns_nothing(self):
        """mypy writes per *minor* release, so 2.3.1 has none. That is rung 2
        running out, and it must be empty rather than approximately 2.3."""
        self.assertEqual(section_for(MYPY_CHANGELOG, "2.3.1"), "")

    def test_the_section_stops_at_the_next_heading_of_its_level(self):
        """Asserted on content, not on the version string: 0.2.62's own heading
        links to `compare/v0.2.61...v0.2.62`, so `0.2.61` is legitimately inside
        its first line. That is the same conflation `section_for` guards against,
        and a test written the lazy way inherits it."""
        found = section_for(RUMDL_CHANGELOG, "0.2.62")
        self.assertIn("Gherkin", found)
        self.assertNotIn("stdin-batch", found, "0.2.61's entry leaked into 0.2.62's section")
        self.assertEqual(len([ln for ln in found.splitlines() if ln.startswith("## ")]), 1)


class TestTheMarkerRanksAndTheCapCutsTheTail(ChangelogHarness):
    """266 rows is the same failure as silence -- the reader's eye slides off it.

    Answered by ranking and a cap, never by filtering: the evidence file holds
    every row, and the rows Phase 2 came for cannot be the ones cut.
    """

    def test_ordinary_english_does_not_carry_the_destructive_shape(self):
        """Measured on ruff 0.16.2...0.16.5, where a broader first version fired
        on both of these. A marker on a fifth of the rows marks nothing."""
        for subject in (
            "[ty] Avoid composite Salsa keys for unspecialized MROs (#27592)",
            "[ty] Avoid deadlock when scheduling watch checks (#27605)",
        ):
            self.assertIsNone(DESTRUCTIVE.search(subject), subject)

    def test_the_shape_the_prose_names_still_matches(self):
        for subject in (
            "fix(cli): stop rewriting Rust source when formatting doc comments",
            "MD002 no longer removes the heading",
        ):
            self.assertIsNotNone(DESTRUCTIVE.search(subject), subject)

    def test_destructive_outranks_fix_worded_outranks_the_rest(self):
        self.assertEqual(rank("fix(cli): stop rewriting Rust source"), 0)
        self.assertEqual(rank("[mypyc] Fix crash on double yielding"), 1)
        self.assertEqual(rank("[ty] Add an opt-in unsound-return-statement lint"), 2)

    def test_nothing_marked_is_ever_cut(self):
        """A long unlabelled range with the marked row last in API order."""
        wall = [f"Add feature number {n} (#{n})" for n in range(SHOWN + 20)]
        wall.append("Stop deleting the trailing newline (#999)")
        repo = Repo(
            "example/wall",
            releases=[("v2.0.0", "### Added\n\n- nothing relevant\n"), ("v1.0.0", "")],
            files={},
            commits=wall,
        )
        status, out, evidence = self.run_main(repo, "--from", "1.0.0", "--to", "2.0.0")
        self.assertEqual(status, 1)
        shown = out.split("UNRECONCILED")[1]
        self.assertIn("Stop deleting the trailing newline", shown)
        self.assertIn("destructive-fix shape", shown.splitlines()[3])
        self.assertIn("and 21 more", shown)
        self.assertIn("Add feature number 0 (#0)", evidence)

    def test_the_evidence_file_is_never_capped(self):
        wall = [f"Add feature number {n} (#{n})" for n in range(SHOWN + 20)]
        repo = Repo(
            "example/wall",
            releases=[("v2.0.0", "### Added\n\n- nothing relevant\n"), ("v1.0.0", "")],
            files={},
            commits=wall,
        )
        _, _, evidence = self.run_main(repo, "--from", "1.0.0", "--to", "2.0.0")
        listed = [ln for ln in evidence.splitlines() if ln.startswith("- Add feature")]
        self.assertEqual(len(listed), SHOWN + 20)

    def test_the_multi_product_note_only_appears_once_rows_were_cut(self):
        """It fired on mypy's 4-of-4 when it keyed on the ratio, where the project
        ships one product and the changelog really has no section."""
        _, small, _ = self.run_main(self.mypy(), "--from", "2.3.0", "--to", "2.3.1")
        self.assertNotIn("more than one product", small)


class TestItRefusesRatherThanGuessing(ChangelogHarness):
    def _expect_exit(self, code: int, repo: Repo, *argv: str) -> str:
        err = io.StringIO()
        with tempfile.TemporaryDirectory() as scratch:
            full = ["changelog.py", "--scratch", scratch, "--repo-slug", repo.slug, *argv]
            with (
                mock.patch("changelog._gh", fake_gh(repo)),
                mock.patch.object(sys, "argv", full),
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(err),
                self.assertRaises(SystemExit) as caught,
            ):
                main()
        self.assertEqual(caught.exception.code, code, err.getvalue())
        return err.getvalue()

    def test_a_missing_scratch_directory_is_exit_2(self):
        repo = self.rumdl_61_62()
        argv = [
            "changelog.py",
            "--scratch",
            "/nonexistent/scratch",
            "--repo-slug",
            repo.slug,
            "--from",
            "0.2.60",
            "--to",
            "0.2.62",
        ]
        with (
            mock.patch("changelog._gh", fake_gh(repo)),
            mock.patch.object(sys, "argv", argv),
            contextlib.redirect_stderr(io.StringIO()),
            self.assertRaises(SystemExit) as caught,
        ):
            main()
        self.assertEqual(caught.exception.code, 2)

    def test_a_failed_release_list_is_exit_2_not_an_empty_ladder(self):
        """The distinction the whole phase turns on. A call that failed must not
        become "this project publishes no releases"."""
        repo = self.rumdl_61_62()
        with (
            mock.patch("changelog._gh", lambda args: None),
            mock.patch.object(
                sys,
                "argv",
                [
                    "changelog.py",
                    "--scratch",
                    ".",
                    "--repo-slug",
                    repo.slug,
                    "--from",
                    "0.2.60",
                    "--to",
                    "0.2.62",
                ],
            ),
            contextlib.redirect_stderr(io.StringIO()) as err,
            self.assertRaises(SystemExit) as caught,
        ):
            main()
        self.assertEqual(caught.exception.code, 2)
        self.assertIn("releases", err.getvalue())

    def test_an_unknown_version_is_exit_2(self):
        self._expect_exit(2, self.rumdl_61_62(), "--from", "0.2.60", "--to", "9.9.9")

    def test_a_crash_is_exit_2_and_never_exit_1(self):
        """Exit 1 means the prose came up short. An unhandled exception exits 1
        too, so without `cli()` a crash would be read as a project having quietly
        dropped its fixes."""
        with (
            mock.patch("changelog.main", side_effect=RuntimeError("boom")),
            contextlib.redirect_stderr(io.StringIO()) as err,
            self.assertRaises(SystemExit) as caught,
        ):
            cli()
        self.assertEqual(caught.exception.code, 2)
        self.assertIn("This is a bug, not a finding", err.getvalue())


class TestThePackageCannotChooseWhichRepositoryAnswersForIt(unittest.TestCase):
    """`project_urls` is written by the package author.

    That is the party this whole plugin exists to not trust, and until 0.36.0
    both the prose (since 0.33.0) and this script's first cut resolved the
    repository with `if "github.com/" in url` — an unanchored substring test.

    A package that wants a clean Phase 2 row can supply
    `https://evil.invalid/github.com/attacker/lookalike` and have the audit read
    *that* repository's release notes: tidy, additive, no unreconciled fixes.
    The reconciliation is a verdict input Phase 7 reads, so this is worse after
    #94 than it was before — the feature made the target worth attacking.

    Reported by CodeQL as `py/incomplete-url-substring-sanitization`, high
    severity, on the PR that mechanised the ladder. The prose copy was found only
    because the script copy was flagged.
    """

    def test_a_lookalike_host_is_not_github(self):
        for url in (
            "https://evil.example.invalid/github.com/attacker/lookalike",
            "https://example.invalid/?q=github.com/attacker/repo",
            "https://github.com.attacker.invalid/github.com/a/b",
            "https://notgithub.com/a/b",
        ):
            self.assertIsNone(github_slug(url), f"{url} resolved to a repository")

    def test_a_path_cannot_walk_out_of_the_repos_endpoint(self):
        """The slug is interpolated into `gh api repos/<slug>/...`."""
        for url in (
            "https://github.com/../../users/octocat",
            "https://github.com/./../org/repo",
        ):
            slug = github_slug(url)
            if slug is not None:
                self.assertNotIn("..", slug, f"{url} produced a traversing slug")

    def test_a_non_http_scheme_is_rejected(self):
        """Not redundant with the host check, though it looks it.

        `javascript:alert(1)//github.com/a/b` parses with no hostname, so the
        host check alone would already reject it. **`//github.com/attacker/repo`
        does not** — a protocol-relative URL parses with hostname `github.com`
        and resolves without this check, which is why the mutation that deleted
        it first went green. Pinned by the second case here.
        """
        for url in ("javascript:alert(1)//github.com/a/b", "file:///github.com/a/b", ""):
            self.assertIsNone(github_slug(url))
        self.assertIsNone(
            github_slug("//github.com/attacker/repo"),
            "a protocol-relative URL parses with hostname github.com and must "
            "still be rejected: the scheme is what says this is a real link",
        )

    def test_the_real_forms_still_resolve(self):
        """Erring toward rejection would be its own defect: an unresolved repo
        sends Phase 2 back to having no method at all."""
        for url, want in (
            ("https://github.com/rvben/rumdl", "rvben/rumdl"),
            ("https://github.com/rvben/rumdl.git", "rvben/rumdl"),
            ("https://www.github.com/astral-sh/ruff/issues", "astral-sh/ruff"),
            ("http://github.com/python/mypy", "python/mypy"),
            ("https://github.com/psf/requests/", "psf/requests"),
        ):
            self.assertEqual(github_slug(url), want, url)

    def test_the_metadata_is_read_through_the_same_check(self):
        """The guard belongs on the resolution, not beside it — the flaw was in
        `resolve_repo`'s loop, so a helper nothing calls fixes nothing."""
        payload = {
            "info": {
                "project_urls": {
                    "Homepage": "https://evil.example.invalid/github.com/attacker/lookalike",
                    "Source": "https://github.com/rvben/rumdl",
                }
            }
        }
        with mock.patch("changelog.urllib.request.urlopen") as opened:
            opened.return_value.__enter__.return_value = io.StringIO(json.dumps(payload))
            self.assertEqual(resolve_repo("rumdl"), "rvben/rumdl")

    def test_a_package_naming_only_a_lookalike_fails_rather_than_guessing(self):
        payload = {"info": {"project_urls": {"Homepage": "https://evil.invalid/github.com/a/b"}}}
        with (
            mock.patch("changelog.urllib.request.urlopen") as opened,
            contextlib.redirect_stderr(io.StringIO()) as err,
            self.assertRaises(SystemExit) as caught,
        ):
            opened.return_value.__enter__.return_value = io.StringIO(json.dumps(payload))
            resolve_repo("malicious")
        self.assertEqual(caught.exception.code, 2)
        self.assertIn("names no GitHub repository", err.getvalue())

    def test_the_auditors_own_slug_is_validated_too(self):
        """`--repo-slug` reaches the same API path. Checked even though the
        auditor types it: a typo that silently answers about something else is
        the failure this phase is about."""
        for good in ("rvben/rumdl", "astral-sh/ruff", "a/b"):
            self.assertTrue(valid_slug(good), good)
        for bad in ("../..", "a/b/c", "rvben", "", "a/../b", "./x"):
            self.assertFalse(valid_slug(bad), bad)

    def test_the_slug_check_is_wired_into_the_run(self):
        """A validator nothing calls validates nothing.

        Mutation-checked: deleting the call in `main()` left the unit test above
        green, because it exercises the function and not the path. This drives
        the whole entry point.
        """
        with tempfile.TemporaryDirectory() as scratch:
            argv = [
                "changelog.py",
                "--scratch",
                scratch,
                "--repo-slug",
                "../..",
                "--from",
                "1.0.0",
                "--to",
                "2.0.0",
            ]
            with (
                mock.patch("changelog._gh", lambda args: None),
                mock.patch.object(sys, "argv", argv),
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()) as err,
                self.assertRaises(SystemExit) as caught,
            ):
                main()
        self.assertEqual(caught.exception.code, 2)
        self.assertIn("is not an owner/repo pair", err.getvalue())


class TestAFailedCallIsNotAnEmptyAnswer(unittest.TestCase):
    """The seam's own contract, tested one layer below the seam.

    Every other case here replaces `_gh` wholesale, so nothing in them reaches
    the line that decides what a failure *is* -- a mutation turning its `None`
    into `""` left all thirty-four green. That distinction is load-bearing in two
    places: `match_tag` reads a non-`None` probe as "this tag exists" and would
    hand back a constructed tag, and `_gh_hard` reads `None` as "could not run"
    and would otherwise let a failed release list become "this project publishes
    no releases" -- the ladder's own failure mode, inside the tool written to
    remove it.

    So this patches `subprocess.run` instead, and asserts the invariant the
    callers rest on: `None` if and only if the call failed.
    """

    def _run(self, code: int, stdout: str) -> Any:
        return mock.patch(
            "changelog.subprocess.run",
            return_value=subprocess.CompletedProcess([], code, stdout=stdout, stderr=""),
        )

    def test_a_non_zero_exit_is_a_failure_even_when_it_printed_a_body(self):
        """`gh` writes an API error body to stdout and still exits non-zero, so
        the exit code is the signal and the body is the explanation."""
        with self._run(1, '{"message":"Not Found","status":"404"}'):
            self.assertIsNone(_gh(["api", "repos/x/y/releases"]))

    def test_a_zero_exit_with_no_output_is_a_real_and_empty_answer(self):
        """`python/mypy` publishes no releases. That is content, not a failure."""
        with self._run(0, ""):
            self.assertEqual(_gh(["api", "repos/python/mypy/releases"]), "")

    def test_the_hard_wrapper_stops_on_the_failure_and_passes_the_emptiness_through(self):
        with self._run(1, ""), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                _gh_hard(["api", "repos/x/y/releases"])
            self.assertEqual(caught.exception.code, 2)
        with self._run(0, ""):
            self.assertEqual(_gh_hard(["api", "repos/python/mypy/releases"]), "")

    def test_a_failed_probe_does_not_become_a_tag_that_exists(self):
        """`match_tag`'s fallback probes both spellings. If a failure read as
        success it would return the first thing it tried, which is exactly the
        constructed tag the reference forbids."""
        with self._run(1, '{"message":"Not Found"}'):
            self.assertIsNone(match_tag("9.9.9", [], "rvben/rumdl"))


class TestTheEvidenceOutlivesTheTerminal(ChangelogHarness):
    def test_the_prose_rungs_are_saved_for_the_security_read(self):
        """No count in the output substitutes for reading the notes: a privately
        disclosed fix ships with no CVE and every scanner reports clean."""
        _, out, evidence = self.run_main(self.rumdl_58_60(), "--from", "0.2.58", "--to", "0.2.60")
        self.assertIn("evidence saved to", out)
        self.assertIn("update h2 to 0.4.16", evidence)
        self.assertIn("Security", evidence)

    def test_a_clean_run_still_writes_the_file(self):
        status, _, evidence = self.run_main(
            self.rumdl_58_60(), "--from", "0.2.58", "--to", "0.2.60"
        )
        self.assertEqual(status, 0)
        self.assertIn("rung 1 -- release notes", evidence)


if __name__ == "__main__":
    unittest.main()
