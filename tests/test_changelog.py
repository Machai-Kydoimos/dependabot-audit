"""Regression tests for changelog.py.

No network: `_gh` is the single seam every GitHub call goes through, so the fakes
below drive the real tag matching, section extraction, classification and
reconciliation, and only the subprocess is replaced.

**Every fixture the reconciliation is judged on is recorded from the live API,
not written to fit the rule.** That is the trap this file exists downstream of: a
reconciliation rule tested against changelogs invented from the rule can only
agree with itself. The subjects, release bodies and changelog sections below are
verbatim from

    gh api repos/rvben/rumdl/compare/v0.2.60...v0.2.62 --jq '.commits[].commit.message'
    gh api repos/rvben/rumdl/releases/tags/v0.2.61 --jq .body
    gh api repos/rvben/rumdl/contents/CHANGELOG.md?ref=v0.2.62 -H 'Accept: application/vnd.github.raw'
    gh api repos/python/mypy/compare/v2.3.0...v2.3.1 --jq '.commits[].commit.message'

recorded 2026-09-01. The live half of the same claims is in
`integration/test_live_changelog_sources.py`, which goes red when the world moves
-- these stay green, because they are about this script's reading of what the
world said that day.

**Four fixtures are synthetic, and say so in their names**: `example/wall`,
`example/backport`, the `astral-sh/ruff` stub, and `ROWS`. They test structure
rather than judgement -- the cap, the ranking, the tag prefix, an empty window --
where real data would be arbitrary and the property under test is not about the
world at all. Keeping the two kinds apart is the point: an invented changelog may
never decide whether the reconciliation is right.

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
from typing import Any, ClassVar
from unittest import mock

sys.path.insert(
    0, str(pathlib.Path(__file__).resolve().parent.parent / "skills/dependabot-audit/scripts")
)

from changelog import (
    DESTRUCTIVE,
    SHOWN,
    _gh,
    _gh_hard,
    bump_body,
    candidates,
    cli,
    described,
    edited_since_published,
    gap,
    github_slug,
    headings,
    is_dependency_bump,
    labelled,
    main,
    match_tag,
    normalise,
    rank,
    reconciled,
    resolve_repo,
    section_for,
    valid_slug,
    version_headings,
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
#
# RUMDL_NOTES_61 is now a historical record rather than a current reading, and
# that is deliberate. The project rewrote v0.2.61's release body on 2026-09-05 --
# ten days after cutting the tag -- backfilling the five `### Fixed` entries this
# range carries. Fetch it live today and you get the fixes; what is recorded here
# is what the API returned when #94 was filed, which is the state the ladder's
# defect was measured in. Do NOT refresh it to match the live body: that would
# delete the only case in this suite where rungs 1 and 2 both answer and both
# omit, and it is evidence that a release body is mutable (#131).
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
# Full 40-character hashes, as the API actually emits them. A first version
# abbreviated the URLs, which `normalise` happens to strip either way -- so the
# test passed while the fixture had stopped being what the world sends, and a
# future change to that regex would have been checked against a shortened form
# nobody publishes.
RUMDL_NOTES_59 = (
    "\n### Fixed\n\n"
    "- **MD033**: ignore escaped HTML tag openers "
    "([eaa4075](https://github.com/rvben/rumdl/commit/eaa4075d665f8174256ddbeb21ecb8d64f34525a))\n"
    "- **config**: match absolute patterns through symlinks "
    "([5dd6158](https://github.com/rvben/rumdl/commit/5dd615823eb3128009dd0534829f18e96a73a2c6))\n"
    "- **MD013**: stop reflow from joining a setext heading into its underline "
    "([9ec9e17](https://github.com/rvben/rumdl/commit/9ec9e17458f45c6e621de3352a678870c71441e3))\n"
)
RUMDL_NOTES_60 = (
    "\n### Fixed\n\n- **deps**: update h2 to 0.4.16 "
    "([a650302](https://github.com/rvben/rumdl/commit/a6503022a5b0268138fbec2068d8e9a7abd27e64))\n"
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

# --- recorded: rvben/rumdl v0.2.75...v0.2.76, the compare API's subjects -----
#
# Fetched 2026-09-25 (#169). `chore(deps): refresh Rust dependencies` moved 28
# crates in Cargo.lock -- rustls 0.23.38 -> 0.23.45 among them, the fix for
# RUSTSEC-2026-0285 -- and the release notes do not name it. The conventional
# classifier read only fix types, so it never reached the output or the evidence
# file either. The commit has no body.

RUMDL_75_76 = [
    "chore(deps): refresh Rust dependencies",
    "docs: redirect legacy /docs/rules/<rule> URLs to rule pages",
    "test(cli): show full command output when stdin exclude JSON fails to parse",
    "fix(discovery): attribute empty runs to .markdownlintignore separately",
    "fix(cli): name every ignore file --respect-gitignore controls",
    "fix(lint-context): record only CommonMark headings as headings",
    "chore: bump version to v0.2.76",
    "feat(encoding): lint files with non-UTF8 chars",
    "update rumdl schema",
    "feat(MD094): report invalid UTF-8 instead of refusing the file",
    "fix(MD092): follow the invocation's rule selection",
    "fix(cli): report a skipped file where the run reports findings",
    "fix(MD013): recognize mkdocstrings blocks whose identifier has no dot",
    "fix(MD013): keep every block of consecutive mkdocstrings blocks out of reflow",
    "fix(MD013): leave definition lists as written when reflowing",
    "fix(MD094): report invalid UTF-8 under --only-code-block-tools",
    "chore(changelog): list every change in the 0.2.76 section",
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
        edited: dict[str, str] | None = None,
        files_at_head: dict[str, str] | None = None,
        asset_uploads: dict[str, str] | None = None,
    ) -> None:
        self.slug = slug
        self.releases = releases or []
        # What the same paths hold on the default branch. A generated changelog
        # is rewritten in full at every release, so this is a different document
        # from `files` even though nothing was hand-edited. Defaults to `files`.
        self._files_at_head = files_at_head
        # tag -> the `updated_at` a rewritten body carries. Absent means the
        # release still reads as it was published.
        self.edited = edited or {}
        # tag -> when its assets were uploaded. `updated_at` follows an upload, so
        # a tag here moves both stamps together, the way GitHub does.
        self.asset_uploads = asset_uploads or {}
        self.tags = tags or [tag for tag, _ in (releases or [])]
        self.files = files or {}
        self.commits = commits or []

    @property
    def files_at_head(self) -> dict[str, str]:
        return self._files_at_head if self._files_at_head is not None else self.files


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
            published = "2026-08-26T00:00:00Z"
            return "\n".join(
                json.dumps(
                    {
                        "tag": tag,
                        "body": body,
                        "at": published,
                        "published": published,
                        "updated": repo.edited.get(tag, repo.asset_uploads.get(tag, published)),
                        "assets": 14 if tag in repo.asset_uploads else 0,
                        "assets_updated": repo.asset_uploads.get(tag),
                    }
                )
                for tag, body in repo.releases
            )
        if "/git/ref/tags/" in joined:
            return "{}" if joined.rsplit("/", 1)[-1] in repo.tags else None
        if "/compare/" in joined:
            return "\n".join(json.dumps(m) for m in repo.commits)
        # No `?ref=` means the default branch, which is a different document.
        if "/contents?" in joined or "/contents " in joined or joined.endswith("/contents"):
            at_head = "?ref=" not in joined
            return "\n".join(repo.files_at_head if at_head else repo.files)
        if "/contents/" in joined:
            name = joined.split("/contents/")[1].split("?")[0].split()[0]
            source = repo.files if "?ref=" in joined else repo.files_at_head
            return source.get(name)
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


class TestARungThatAnsweredCanAlsoBeRewritten(ChangelogHarness):
    """A release body is mutable, and `.body` alone cannot say it changed.

    `rvben/rumdl` backfilled a whole `### Fixed` section into v0.2.61 on
    2026-09-05, ten days after cutting the tag. The API says so in `updated_at`
    and in nothing else: `published_at` does not move, the body comes back
    well-formed, and `gh` exits 0.

    This matters more than a stale quote. **The founding case for this script
    now reconciles.** Run it against the live v0.2.60...v0.2.62 today and rung 1
    names all five fixes, so the run exits `0` -- the same range that exited `1`
    when #94 was filed, changed by an edit upstream rather than by anything here.
    The fixtures in this file still carry the notes as published, which is what
    keeps the case testable; the marker is what tells a live run that its green
    is not the green it looks like.
    """

    def rewritten(self) -> Repo:
        repo = self.rumdl_61_62()
        repo.edited = {"v0.2.61": "2026-09-05T07:05:04Z"}
        return repo

    def test_an_untouched_release_says_nothing(self):
        """The marker has to be quiet on the ordinary case or it is noise."""
        _, out, evidence = self.run_main(self.rumdl_61_62(), "--from", "0.2.60", "--to", "0.2.62")
        self.assertNotIn("EDITED", out)
        self.assertNotIn("EDITED", evidence)

    def test_a_rewritten_release_is_named_in_both_places(self):
        """The terminal summary and the evidence file, because they are read by
        different people at different times -- and the evidence file is the one
        that outlives the run."""
        _, out, evidence = self.run_main(self.rewritten(), "--from", "0.2.60", "--to", "0.2.62")
        self.assertIn("v0.2.61: EDITED 2026-09-05T07:05:04Z", out)
        self.assertIn("and not by an asset upload", out)
        self.assertIn("EDITED 2026-09-05T07:05:04Z", evidence)
        self.assertIn("may not be the text that went out with the tag", evidence)

    def test_only_the_rewritten_one_is_marked(self):
        _, _, evidence = self.run_main(self.rewritten(), "--from", "0.2.60", "--to", "0.2.62")
        marked = [ln for ln in evidence.splitlines() if "EDITED" in ln]
        self.assertEqual(len(marked), 1, f"one release was edited, {len(marked)} marked: {marked}")

    def test_the_marker_does_not_change_the_verdict(self):
        """An edit is information, not a finding. The exit code answers one
        question -- did the prose name every fix -- and an edited body that names
        them still names them."""
        plain = self.run_main(self.rumdl_61_62(), "--from", "0.2.60", "--to", "0.2.62")[0]
        rewritten = self.run_main(self.rewritten(), "--from", "0.2.60", "--to", "0.2.62")[0]
        self.assertEqual(plain, rewritten)

    def test_a_missing_stamp_is_unknown_and_not_clean(self):
        """The reason the comparison is in Python and not in the `--jq`. jq
        answers `false` for `null > "2026-.."`, so a response that stopped
        carrying `updated_at` would read as *nothing was ever edited* -- this
        repo's own failure class, inside the check written to catch it."""
        self.assertEqual(
            edited_since_published({"published": "", "updated": ""}),
            "edit status unknown -- the release carried no timestamps",
        )
        self.assertIsNone(
            edited_since_published(
                {"published": "2026-08-26T19:24:23Z", "updated": "2026-08-26T19:24:23Z"}
            )
        )

    def test_an_asset_upload_is_not_an_edit(self):
        """Round twenty-one, 2026-09-19, real stamps. rumdl v0.2.73 was published
        17:36:49 and its assets uploaded 19:07:29; `updated_at` is 19:07:30. The
        body is byte-identical to its changelog section at the tag. 0.44.0 marked
        it EDITED and told the reader the text was not what shipped."""
        self.assertIsNone(
            edited_since_published(
                {
                    "published": "2026-09-11T17:36:49Z",
                    "updated": "2026-09-11T19:07:30Z",
                    "assets": 14,
                    "assets_updated": "2026-09-11T19:07:29Z",
                }
            )
        )

    def test_an_edit_long_after_the_assets_is_still_an_edit(self):
        """rumdl v0.2.61, real stamps: assets went up with the release, the body
        was rewritten 9.5 days later. The asset list must not excuse that."""
        mark = edited_since_published(
            {
                "published": "2026-08-26T19:24:23Z",
                "updated": "2026-09-05T07:05:04Z",
                "assets": 14,
                "assets_updated": "2026-08-26T19:24:22Z",
            }
        )
        self.assertIsNotNone(mark)
        self.assertIn("EDITED 2026-09-05T07:05:04Z", mark or "")

    def test_a_change_with_no_assets_to_explain_it_is_marked(self):
        """actions/checkout publishes no assets; v6.0.2 changed twelve days on."""
        mark = edited_since_published(
            {
                "published": "2026-01-09T19:53:28Z",
                "updated": "2026-01-22T16:41:24Z",
                "assets": 0,
                "assets_updated": None,
            }
        )
        self.assertIn("EDITED", mark or "")

    def test_a_missing_asset_list_is_unknown_not_clean(self):
        """Without the list, an upload cannot be ruled in or out. Saying *edited*
        would be the 0.44.0 false positive; saying nothing would be the silent
        zero. Neither."""
        self.assertEqual(
            edited_since_published(
                {"published": "2026-09-11T17:36:49Z", "updated": "2026-09-11T19:07:30Z"}
            ),
            "edit status unknown -- the release carried no asset list",
        )

    def test_the_whole_run_is_quiet_about_an_asset_upload(self):
        repo = self.rumdl_61_62()
        repo.asset_uploads = {"v0.2.62": "2026-08-27T15:00:00Z"}
        _, out, evidence = self.run_main(repo, "--from", "0.2.60", "--to", "0.2.62")
        self.assertNotIn("EDITED", out)
        self.assertNotIn("EDITED", evidence)

    def test_created_at_is_not_what_it_compares_against(self):
        """`at` falls back to `created_at`, which on an untouched release is
        normally *earlier* than `updated_at` -- rumdl v0.2.61 was created
        19:09:35 and published 19:24:23. Comparing against the fallback would
        mark almost every release as edited."""
        self.assertIsNone(
            edited_since_published(
                {
                    "at": "2026-08-26T19:09:35Z",
                    "published": "2026-08-26T19:24:23Z",
                    "updated": "2026-08-26T19:24:23Z",
                }
            )
        )


class TestAGeneratedChangelogIsRewrittenAtEveryRelease(ChangelogHarness):
    """#131, second half. The entry for a version is a function of the ref.

    `rumdl` generates `CHANGELOG.md` from conventional commits with `vership`,
    and the generator was dropping `fix` types. When it was fixed, the v0.2.66
    release regenerated the whole file: the `0.2.61` entry carries no fixes at
    refs v0.2.62..v0.2.65 and five at v0.2.66 and later. Nothing was hand-edited
    and no history was rewritten -- each blob is exactly what it always was.

    So reading the changelog once, at the proposed tag, is a choice that can
    return the less complete answer. The script reads both refs and says which
    versions differ, because "the project documented nothing here" and "the
    project had not documented it yet when this tag was cut" are different
    findings and only one of them is about the bump.
    """

    REGENERATED: ClassVar[str] = """\
# Changelog

## [Unreleased]

## [0.2.62](https://github.com/rvben/rumdl/compare/v0.2.61...v0.2.62) - 2026-08-27

### Added

- **flavor**: add support for Markdown with Gherkin (MDG) ([db62377](https://github.com/rvben/rumdl/commit/db62377aa7e63865f682bf16d790c6ff5eb40b31))

## [0.2.61](https://github.com/rvben/rumdl/compare/v0.2.60...v0.2.61) - 2026-08-26

### Added

- **cli**: add `--stdin-batch` for NUL-framed multi-document linting and `--stdin-batch-closed-world` for supplied-document-only link resolution

### Fixed

- **cli**: stop rewriting Rust source when formatting doc comments
- **lint-context**: stop reading a lazy continuation as a setext underline

## [0.2.60](https://github.com/rvben/rumdl/compare/v0.2.59...v0.2.60) - 2026-08-22

### Fixed

- **deps**: update h2 to 0.4.16
"""

    def regenerated(self) -> Repo:
        repo = self.rumdl_61_62()
        repo._files_at_head = {"CHANGELOG.md": self.REGENERATED, "Cargo.toml": ""}
        return repo

    def test_an_unchanged_changelog_says_nothing(self):
        """Quiet on the ordinary project, where both reads are the same file."""
        _, out, evidence = self.run_main(self.rumdl_61_62(), "--from", "0.2.60", "--to", "0.2.62")
        self.assertNotIn("DIFFERS", out)
        self.assertNotIn("default branch", evidence)

    def test_a_regenerated_section_is_named_in_the_summary(self):
        _, out, _ = self.run_main(self.regenerated(), "--from", "0.2.60", "--to", "0.2.62")
        self.assertIn("v0.2.61: the section at the default branch DIFFERS", out)
        self.assertNotIn("v0.2.62: the section at the default branch DIFFERS", out)

    def test_the_fuller_text_reaches_the_evidence_file(self):
        """Naming the difference and not carrying it would leave the reader to
        fetch it by hand -- which is the improvisation this repo keeps finding."""
        _, _, evidence = self.run_main(self.regenerated(), "--from", "0.2.60", "--to", "0.2.62")
        self.assertIn("rung 2 at the default branch", evidence)
        self.assertIn("stop rewriting Rust source when formatting doc comments", evidence)
        self.assertIn("a function of the ref you read it at", evidence)

    def test_the_later_read_counts_as_prose_for_the_reconciliation(self):
        """The question is whether the project's prose names the fixes, not
        whether the tag's own snapshot did. Two of the range's five fixes appear
        only in the regenerated text, so the unreconciled list must shrink."""
        before = self.run_main(self.rumdl_61_62(), "--from", "0.2.60", "--to", "0.2.62")[1]
        after = self.run_main(self.regenerated(), "--from", "0.2.60", "--to", "0.2.62")[1]
        self.assertIn("UNRECONCILED: 5 of 5 fix commit(s)", before)
        self.assertIn("UNRECONCILED: 3 of 5 fix commit(s)", after)
        self.assertNotIn("stop rewriting Rust source", after.split("UNRECONCILED")[1])

    def test_a_project_with_no_changelog_at_head_still_reports_the_tag_read(self):
        """The second read must not be able to erase the first. A file deleted
        on the default branch would otherwise turn a found section into none."""
        repo = self.rumdl_61_62()
        repo._files_at_head = {"Cargo.toml": ""}
        status, out, _ = self.run_main(repo, "--from", "0.2.60", "--to", "0.2.62")
        self.assertIn("rung 2 -- CHANGELOG.md: 2 section(s)", out)
        self.assertNotIn("DIFFERS", out)
        self.assertEqual(
            status, self.run_main(self.rumdl_61_62(), "--from", "0.2.60", "--to", "0.2.62")[0]
        )


# `pre-commit/pre-commit` CHANGELOG.md at v4.6.2, first 21 lines, verbatim. Setext:
# every version is a line of text over a rule of `=`, subsections are ATX `###`.
PRECOMMIT_CHANGELOG = """\
4.6.2 - 2026-08-10
==================

### Fixes
- Fix `language: node` hooks that contain `"scripts": {"build": ...}` with
  npm 11.x.
    - Regressed in 4.6.1.
    - #3737 issue by @mheiges.
    - #3743 PR by @asottile.

4.6.1 - 2026-07-21
==================

### Fixes
- Install `language: node` hooks via `git`.
    - Fixes npm 12.x compatibility
    - #3719 PR by @asottile.
    - #3517 issue by @ojob.
"""

# `pytest-dev/pytest` CHANGELOG.rst at 8.4.2, all 230 bytes, verbatim. It is the
# name every changelog matcher looks for, and it is a signpost.
PYTEST_STUB = """\
=========
Changelog
=========

The pytest CHANGELOG is located `here <https://docs.pytest.org/en/stable/changelog.html>`__.

The source document can be found at: https://github.com/pytest-dev/pytest/blob/main/doc/en/changelog.rst
"""

# The shape of the real `doc/en/changelog.rst`: RST, versions over `=`,
# subsections over `-`. Abridged; the headings are as pytest writes them.
PYTEST_REAL = """\
Changelog
=========

pytest 8.4.2 (2025-09-03)
=========================

Bug fixes
---------

- `#13312 <https://github.com/pytest-dev/pytest/issues/13312>`_: Fixed a possible ``KeyError`` crash on PyPy.

pytest 8.4.1 (2025-06-17)
=========================

Bug fixes
---------

- `#13461 <https://github.com/pytest-dev/pytest/issues/13461>`_: Corrected ``_pytest.terminal.TerminalReporter.isatty``.
"""

# `python/mypy`'s shape: versions at `##`, Python in the examples. A `#` comment
# inside a fence is not a heading, and until #133 it ended the section.
MYPY_FENCED = """\
# Mypy Release Notes

## Mypy 2.0

### Allow redefinitions

```python
# mypy: allow-redefinition
def f() -> None:
    x = 1
```

### Other notable fixes

- Fix crash on a recursive alias.

## Mypy 1.20

- Earlier.
"""


class TestAChangelogIsReadInTheShapeItIsWrittenIn(unittest.TestCase):
    """#133. Three shapes rung 2 read successfully and parsed to nothing.

    Each is a file that was fetched in full, at exit 0, and then reported as
    *"no section for this version"* -- which reads exactly like *"the project
    documented nothing"*, this plugin's own failure class. Measured 2026-09-19:

    - `pre-commit/pre-commit` heads versions setext-style, `4.6.2 - 2026-08-10`
      over `===`. 72,898 bytes, zero sections found. It is the `pre-commit`
      ecosystem's own repository.
    - `pytest-dev/pytest`'s root `CHANGELOG.rst` is 230 bytes pointing at
      `doc/en/changelog.rst`, which is 500,693 bytes.
    - `python/mypy` heads versions at `##` and puts `# comment` lines inside
      code fences, which read as level-1 headings and ended sections early:
      `## Mypy 2.0` came back as 34 of its 246 lines.

    Controls measured unchanged on real files: rumdl, ruff, uv, black, pydantic.
    """

    def test_a_setext_version_heading_is_found(self):
        section = section_for(PRECOMMIT_CHANGELOG, "4.6.2")
        self.assertTrue(section.startswith("4.6.2 - 2026-08-10"))
        self.assertIn("#3743 PR by @asottile", section)

    def test_a_setext_section_stops_at_the_next_version_and_not_at_its_subsections(self):
        """`### Fixes` is level 3 and sits inside a level-1 version; it must not
        end the section, and `4.6.1` must."""
        section = section_for(PRECOMMIT_CHANGELOG, "4.6.2")
        self.assertIn("### Fixes", section)
        self.assertNotIn("4.6.1 - 2026-07-21", section)
        self.assertNotIn("Install `language: node` hooks via `git`", section)

    def test_the_later_setext_version_is_found_too(self):
        self.assertIn(
            "Install `language: node` hooks via `git`", section_for(PRECOMMIT_CHANGELOG, "4.6.1")
        )

    def test_rst_uses_the_same_two_levels(self):
        """pytest underlines versions with `=` and subsections with `-`, so a
        version section spans its `Bug fixes` and ends at the next version."""
        section = section_for(PYTEST_REAL, "8.4.2")
        self.assertIn("#13312", section)
        self.assertIn("Bug fixes", section)
        self.assertNotIn("#13461", section)

    def test_an_rst_overlined_title_is_a_heading(self):
        found = headings(PYTEST_STUB.splitlines())
        self.assertIn((1, "Changelog"), found.values())

    def test_a_comment_inside_a_fence_does_not_end_the_section(self):
        section = section_for(MYPY_FENCED, "2.0")
        self.assertIn("# mypy: allow-redefinition", section)
        self.assertIn("Fix crash on a recursive alias", section, "the section was cut at the fence")
        self.assertNotIn("Earlier.", section)

    def test_a_thematic_break_after_a_blank_line_is_not_a_heading(self):
        """`---` after a blank line is a rule, not an underline. Treating it as
        one would turn the paragraph *above the blank* into a heading."""
        text = "## 1.0.0\n\nSome note.\n\n---\n\nMore about 1.0.0.\n\n## 0.9.0\n\n- old\n"
        section = section_for(text, "1.0.0")
        self.assertIn("More about 1.0.0.", section)
        self.assertNotIn("- old", section)

    def test_the_last_line_of_a_paragraph_is_not_a_heading(self):
        """Stricter than CommonMark on purpose: a changelog heads a version with
        one line, so text directly under other text is prose even with a rule
        beneath it."""
        text = "## 2.0.0\n\nA long note that\nwraps onto 1.9.0 here\n---\n\n- still 2.0.0\n"
        self.assertIn("- still 2.0.0", section_for(text, "2.0.0"))
        self.assertEqual(section_for(text, "1.9.0"), "")

    def test_yaml_front_matter_is_not_a_heading(self):
        text = "---\ntitle: Release notes for 3.1.0\n---\n\n## 3.1.0\n\n- real entry\n"
        found = headings(text.splitlines())
        self.assertEqual([title for _, title in found.values()], ["3.1.0"])

    def test_a_list_item_over_a_rule_is_not_a_heading(self):
        text = "## 1.2.0\n\n- item one\n---\n\n- item two\n"
        self.assertEqual(len(headings(text.splitlines())), 1)

    def test_a_signpost_carries_no_version_headings(self):
        self.assertEqual(version_headings(PYTEST_STUB), 0)
        self.assertGreater(version_headings(PYTEST_REAL), 0)
        self.assertGreater(version_headings(PRECOMMIT_CHANGELOG), 0)


class TestAStubIsFollowedOnceAndNeverReportedAsNone(ChangelogHarness):
    """The other half of #133. A matched name is not a changelog."""

    def pytest_repo(self, *, real_at: str | None = "doc/en/changelog.rst") -> Repo:
        files = {"CHANGELOG.rst": PYTEST_STUB}
        if real_at:
            files[real_at] = PYTEST_REAL
        return Repo(
            "pytest-dev/pytest",
            releases=[("8.4.2", "notes"), ("8.4.1", "older notes")],
            files=files,
            commits=["Fix KeyError on PyPy (#13566)"],
        )

    def test_the_pointer_is_followed_to_the_real_file(self):
        _, out, evidence = self.run_main(self.pytest_repo(), "--from", "8.4.1", "--to", "8.4.2")
        self.assertIn("doc/en/changelog.rst (via the pointer in CHANGELOG.rst)", out)
        self.assertIn("1 section(s)", out)
        self.assertIn("#13312", evidence)

    def test_the_pointer_is_fetched_at_the_ref_being_read_not_the_one_in_its_url(self):
        """pytest's URL says `blob/main/`. Following that from a tag read would
        answer a question about a different commit."""
        log: list[str] = []
        repo = self.pytest_repo()
        with mock.patch("changelog._gh", fake_gh(repo, log)):
            from changelog import changelog_at

            changelog_at("pytest-dev/pytest", "8.4.2")
        followed = [call for call in log if "doc/en/changelog.rst" in call]
        self.assertTrue(followed, "the pointer was never followed")
        self.assertTrue(all("?ref=8.4.2" in call for call in followed), followed)

    def test_a_stub_with_nowhere_to_go_says_so(self):
        """What must never happen is the stub reading as "no changelog"."""
        _, out, _ = self.run_main(
            self.pytest_repo(real_at=None), "--from", "8.4.1", "--to", "8.4.2"
        )
        self.assertIn("carries no version headings at all", out)
        self.assertIn("it is not 'none'", out)
        self.assertNotIn("0 section(s)", out)

    def test_a_pointer_to_another_signpost_is_not_taken(self):
        """Following a pointer to a file that is no more a changelog than the stub
        would name it in the output as though one had been found. Stay on the
        original, and say it is a signpost."""
        repo = self.pytest_repo(real_at=None)
        repo.files["doc/en/changelog.rst"] = "Changelog\n=========\n\nSee the website.\n"
        _, out, _ = self.run_main(repo, "--from", "8.4.1", "--to", "8.4.2")
        self.assertNotIn("via the pointer", out)
        self.assertIn("rung 2 -- CHANGELOG.rst carries no version headings at all", out)

    def test_a_pointer_into_another_repository_is_not_followed(self):
        stub = PYTEST_STUB.replace("pytest-dev/pytest/blob", "someone-else/fork/blob")
        repo = self.pytest_repo()
        repo.files["CHANGELOG.rst"] = stub
        _, out, _ = self.run_main(repo, "--from", "8.4.1", "--to", "8.4.2")
        self.assertNotIn("via the pointer", out)

    def test_a_real_changelog_is_never_redirected(self):
        """Only a file with no version headings of its own is followed out of.
        A real changelog that happens to mention a docs path stays the answer."""
        real_with_mention = PRECOMMIT_CHANGELOG + "\nSee also docs/changelog.md for history.\n"
        repo = Repo(
            "pre-commit/pre-commit",
            releases=[("v4.6.2", "n"), ("v4.6.1", "n")],
            files={"CHANGELOG.md": real_with_mention, "docs/changelog.md": PYTEST_REAL},
            commits=["Merge pull request #3743 from pre-commit/npm-build-scripts-11-x"],
        )
        _, out, _ = self.run_main(repo, "--from", "4.6.1", "--to", "4.6.2")
        self.assertIn("rung 2 -- CHANGELOG.md: 1 section(s)", out)
        self.assertNotIn("via the pointer", out)


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
        self.assertEqual(rank("Update Rust crate bstr to v1.13.1 (#28628)"), 2)
        self.assertEqual(rank("[ty] Add an opt-in unsound-return-statement lint"), 3)

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


class TestAnEmptyWindowSaysWhyItIsEmpty(unittest.TestCase):
    """Three causes, three answers -- the first version gave one.

    `gap()` returned a bare `[]` whether the target had no release, the versions
    were out of order, or the project published nothing, and `main` printed
    *"this project publishes no releases for these versions"* for all three. Two
    of those are false, and one of them is false about a project that visibly
    does publish releases.

    That is this plugin's own failure class -- an absence of evidence reported as
    evidence of absence -- inside the tool written to remove it, which is the
    second time in one sprint. The comment in `gap()` even claimed it said so.
    Found by reading the function against its own docstring, and the branch had
    no test at all.
    """

    ROWS: ClassVar[list[dict[str, str]]] = [
        {"tag": "v3.0.0", "body": "", "at": "2026-03-01T00:00:00Z"},
        {"tag": "v2.0.0", "body": "", "at": "2026-02-01T00:00:00Z"},
        {"tag": "v1.0.0", "body": "", "at": "2026-01-01T00:00:00Z"},
    ]

    def test_the_ordinary_window_carries_no_note(self):
        window, why = gap(self.ROWS, "v1.0.0", "v3.0.0")
        self.assertEqual([r["tag"] for r in window], ["v3.0.0", "v2.0.0"])
        self.assertEqual(why, "", "a window that sliced cleanly needs no explanation")

    def test_a_target_with_no_release_says_so(self):
        window, why = gap(self.ROWS, "v1.0.0", "v9.9.9")
        self.assertEqual(window, [])
        self.assertIn("no published release for v9.9.9", why)

    def test_a_start_with_no_release_gathers_the_target_and_says_so(self):
        """`python/mypy` reaches this every time it tags without releasing."""
        window, why = gap(self.ROWS, "v0.1.0", "v3.0.0")
        self.assertEqual([r["tag"] for r in window], ["v3.0.0"])
        self.assertIn("no published release for v0.1.0", why)

    def test_versions_out_of_order_are_not_reported_as_no_releases(self):
        """The false one. A downgrade or a backported patch line got told the
        project publishes nothing."""
        window, why = gap(self.ROWS, "v3.0.0", "v1.0.0")
        self.assertEqual(window, [])
        self.assertIn("downgrade or a backported line", why)
        self.assertNotIn("publishes no releases", why)

    def test_the_reason_reaches_the_terminal(self):
        """A note `main` does not print explains nothing."""
        repo = Repo(
            "example/backport",
            releases=[("v3.0.0", "### Added\n\n- three\n"), ("v1.0.0", "### Added\n\n- one\n")],
            files={},
            commits=["fix: something the notes never mention"],
        )
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as scratch:
            argv = [
                "changelog.py",
                "--scratch",
                scratch,
                "--repo-slug",
                repo.slug,
                "--from",
                "3.0.0",
                "--to",
                "1.0.0",
            ]
            with (
                mock.patch("changelog._gh", fake_gh(repo)),
                mock.patch.object(sys, "argv", argv),
                contextlib.redirect_stdout(out),
            ):
                main()
        printed = out.getvalue()
        self.assertIn("downgrade or a backported line", printed)
        self.assertNotIn("publishes no releases", printed)

    def test_the_range_is_still_read_when_the_window_cannot_be_sliced(self):
        """The prose half is the fallible half; `compare` asks about two refs and
        does not consult the release list at all."""
        repo = Repo(
            "example/backport",
            releases=[("v3.0.0", "### Added\n\n- three\n"), ("v1.0.0", "### Added\n\n- one\n")],
            files={},
            commits=["fix: something the notes never mention"],
        )
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as scratch:
            argv = [
                "changelog.py",
                "--scratch",
                scratch,
                "--repo-slug",
                repo.slug,
                "--from",
                "3.0.0",
                "--to",
                "1.0.0",
            ]
            with (
                mock.patch("changelog._gh", fake_gh(repo)),
                mock.patch.object(sys, "argv", argv),
                contextlib.redirect_stdout(out),
            ):
                status = main()
        self.assertEqual(status, 1, "the fix in the range is still a finding")
        self.assertIn("something the notes never mention", out.getvalue())


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

    def test_a_marked_row_carries_its_full_commit_message(self):
        """`commits()` fetches whole messages because *"the body is where a fix
        says what it corrupted"* — and the first version dropped every body and
        told the terminal reader to go fetch the range again. rumdl's Rust-source
        fix names `# [derive(Debug)]` only in the body.
        """
        repo = self.rumdl_61_62()
        repo.commits = [
            *RUMDL_61_62[:8],
            "fix(cli): stop rewriting Rust source when formatting doc comments\n\n"
            "`rumdl fmt lib.rs` wrote `# [derive(Debug)]` to disk and the file\n"
            "stopped being Rust.",
            *RUMDL_61_62[9:],
        ]
        _, out, evidence = self.run_main(repo, "--from", "0.2.60", "--to", "0.2.62")
        self.assertIn("## destructive-fix shape -- full commit message", evidence)
        self.assertIn("wrote `# [derive(Debug)]` to disk", evidence)
        self.assertNotIn(
            "wrote `# [derive(Debug)]` to disk",
            out,
            "the body belongs in the file; the terminal shows the ranked subjects",
        )

    def test_an_unmarked_row_does_not_carry_a_body(self):
        """Only the rows Phase 7 takes the verdict from. A body for all 266 of
        ruff's would be the wall this file exists to replace."""
        repo = self.rumdl_61_62()
        repo.commits = [
            "fix(MD057): respect closed-world self-reference policy\n\nA long body.",
            "chore: bump version to v0.2.62",
        ]
        _, _, evidence = self.run_main(repo, "--from", "0.2.60", "--to", "0.2.62")
        self.assertIn("respect closed-world self-reference policy", evidence)
        self.assertNotIn("A long body.", evidence)
        self.assertNotIn("destructive-fix shape -- full commit message", evidence)

    def test_a_clean_run_still_writes_the_file(self):
        status, _, evidence = self.run_main(
            self.rumdl_58_60(), "--from", "0.2.58", "--to", "0.2.60"
        )
        self.assertEqual(status, 0)
        self.assertIn("rung 1 -- release notes", evidence)


if __name__ == "__main__":
    unittest.main()


class TestADependencyBumpIsReadWhateverItsType(ChangelogHarness):
    """#169: a crate compiled into a wheel is Phase 2's first scope row, and the
    bump that moves it was invisible to this script in both modes.

    Conventional mode read only fix types, so `chore(deps)` never became a
    candidate -- not cut, absent, from the evidence file too. Unlabelled mode
    listed it, in the tier the 40-row cut drops, and then said *"nothing marked
    was cut"*: true of its own marker, and read as true of everything.
    """

    def rumdl_75_76(self) -> Repo:
        return Repo(
            "rvben/rumdl",
            releases=[
                (
                    "v0.2.76",
                    "\n### Fixed\n\n- **cli**: report a skipped file where the "
                    "run reports findings\n",
                ),
                ("v0.2.75", ""),
            ],
            files={"Cargo.toml": ""},
            commits=RUMDL_75_76,
        )

    @staticmethod
    def wall(*tail: str) -> Repo:
        rows = [f"Add feature number {n} (#{n})" for n in range(SHOWN + 20)]
        return Repo(
            "example/wall",
            releases=[("v2.0.0", "### Added\n\n- nothing relevant\n"), ("v1.0.0", "")],
            files={},
            commits=[*rows, *tail],
        )

    def test_the_conventional_classifier_keeps_a_chore_deps_bump(self):
        kept, mode, _ = candidates(RUMDL_75_76)
        self.assertEqual(mode, "conventional")
        self.assertIn("chore(deps): refresh Rust dependencies", kept)
        # The control: the filter still filters. A docs commit is not a candidate.
        self.assertNotIn("docs: redirect legacy /docs/rules/<rule> URLs to rule pages", kept)

    def test_it_reaches_the_screen_and_the_evidence_file_marked(self):
        status, out, evidence = self.run_main(
            self.rumdl_75_76(), "--from", "0.2.75", "--to", "0.2.76"
        )
        self.assertEqual(status, 1)
        shown = out.split("UNRECONCILED")[1]
        row = next(ln for ln in shown.splitlines() if "refresh Rust dependencies" in ln)
        self.assertIn("dependency bump", row)
        self.assertIn("- chore(deps): refresh Rust dependencies", evidence)
        flat = " ".join(out.split())
        self.assertRegex(flat, r"classifier: conventional commits.*dependency bumps")

    def test_a_bump_the_project_labelled_ci_stays_out(self):
        """The label is the project's word that nothing ships from it. Measured on
        rumdl v0.2.61...v0.2.62, where counting it would add a CI tool's bump to a
        range whose five fix commits are the finding."""
        kept, _, _ = candidates(RUMDL_61_62)
        self.assertNotIn("ci(deps): move upd to v0.8.2 and align the last mise pin", kept)
        self.assertTrue(
            is_dependency_bump("ci(deps): move upd to v0.8.2 and align the last mise pin")
        )

    def test_the_bot_and_maintainer_shapes_are_recognised(self):
        for subject in (
            "Update Rust crate bstr to v1.13.1 (#28628)",
            "Update dependency pyright to v1.1.413 (#28626)",
            "build(deps): bump h2 from 0.4.15 to 0.4.16",
            "Bump serde from 1.0.1 to 1.0.2",
            "chore(deps): lock file maintenance",
            "fix(deps): update h2 to 0.4.16",
            "chore(deps): refresh Rust dependencies",
            "Update Cargo.lock",
        ):
            self.assertTrue(is_dependency_bump(subject), subject)
        # Measured on ruff 0.16.7...0.16.8: two [ty] rows name dependencies and
        # bump none. A tier that fires on them ranks prose over the crate bumps.
        for subject in (
            "[ty] Resolve dependencies within correlated inference alternatives (#28252)",
            "[ty] Share strings in dependency metadata (#28141)",
            "docs: redirect legacy /docs/rules/<rule> URLs to rule pages",
            "Add feature number 3 (#3)",
        ):
            self.assertFalse(is_dependency_bump(subject), subject)

    def test_the_ruff_shape_puts_the_crate_bumps_on_screen(self):
        """Sixty tail rows in API order before two crate bumps -- the #438 shape,
        where `Update Rust crate bstr` and `uuid` fell among the 32 cut."""
        repo = self.wall(
            "Update Rust crate bstr to v1.13.1 (#28628)",
            "Update Rust crate uuid to v1.24.1 (#28629)",
        )
        _, out, _ = self.run_main(repo, "--from", "1.0.0", "--to", "2.0.0")
        shown = out.split("UNRECONCILED")[1].split("... and")[0]
        self.assertIn("Update Rust crate bstr", shown)
        self.assertIn("Update Rust crate uuid", shown)

    def test_the_cut_line_counts_what_it_cut_by_tier(self):
        rows = [f"Update Rust crate c{n} to v1.0.{n} (#{n})" for n in range(SHOWN + 5)]
        repo = Repo(
            "example/crates",
            releases=[("v2.0.0", "### Added\n\n- nothing relevant\n"), ("v1.0.0", "")],
            files={},
            commits=[
                *rows,
                "Add feature one (#1)",
                "Add feature two (#2)",
                "Add feature three (#3)",
            ],
        )
        _, out, _ = self.run_main(repo, "--from", "1.0.0", "--to", "2.0.0")
        flat = " ".join(out.split())
        self.assertNotIn("nothing marked was cut", flat)
        self.assertIn("Cut from this list: 5 dependency bump(s), 3 other.", flat)

    def test_nothing_is_said_about_a_cut_that_did_not_happen(self):
        _, out, _ = self.run_main(self.rumdl_75_76(), "--from", "0.2.75", "--to", "0.2.76")
        self.assertNotIn("Cut from this list", out)


# Recorded from astral-sh/ruff 0.16.7...0.16.8, the squash commit for #28628 --
# Renovate's whole PR description, as the compare API returns the message.
RENOVATE_BSTR = """\
Update Rust crate bstr to v1.13.1 (#28628)

This PR contains the following updates:

| Package | Type | Update | Change |
|---|---|---|---|
| [bstr](https://redirect.github.com/BurntSushi/bstr) |
workspace.dependencies | patch | `1.13.0` → `1.13.1` |

---

### Release Notes

<details>
<summary>BurntSushi/bstr (bstr)</summary>

###
[`v1.13.1`](https://redirect.github.com/BurntSushi/bstr/compare/1.13.0...1.13.1)

</details>

---

### Configuration

📅 **Schedule**: (UTC)

- Branch creation
  - "before 4am on Wednesday"

♻ **Rebasing**: Whenever PR becomes conflicted, or you tick the
rebase/retry checkbox.

---

This PR was generated by [Mend Renovate](https://mend.io/renovate/).
"""


class TestABumpsBodyIsItsEvidenceNotItsBoilerplate(unittest.TestCase):
    """The #438 replay under 0.56.0's branch: the ruff evidence file grew 23%
    (16.2 KB to 19.9 KB) on two bump bodies, and the run had already called it
    too large to read whole. Half of each body is Renovate's schedule and rebase
    settings, which say nothing about what moved."""

    def test_what_moved_and_its_notes_stay(self):
        body = bump_body(RENOVATE_BSTR)
        self.assertIn("`1.13.0` → `1.13.1`", body)
        self.assertIn("### Release Notes", body)

    def test_the_bots_settings_go(self):
        body = bump_body(RENOVATE_BSTR)
        self.assertNotIn("Configuration", body)
        self.assertNotIn("Mend Renovate", body)
        self.assertNotIn("Rebasing", body)

    def test_a_long_body_is_capped_and_says_so(self):
        body = bump_body("Update everything\n\n" + "\n".join(f"- crate {n}" for n in range(200)))
        self.assertLessEqual(len(body.splitlines()), 41)
        self.assertRegex(body, r"\[\.\.\. \d+ more line\(s\) in the commit\]")
