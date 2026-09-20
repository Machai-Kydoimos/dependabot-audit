"""Regression tests for setext.py.

No network. The unit cases hand text to `headings()`; the CLI cases run the
script in a throwaway git repository, because two of its three exit codes are
claims about git — a failed `git ls-files` is `128`, and a tree with nothing
tracked is a real zero.

**The expected answers here are not this file's reading of CommonMark.** Each
was measured with markdown-it-py 4.2.0 in CommonMark mode, and `headings()` was
cross-checked against it over three corpora while the script was being written
(#141): the CommonMark 0.31.2 spec's 655 examples (23 setext headings in scope,
all 23 found), `fpga-board-sim` #437's 41 Markdown files and rumdl's 144 at
013621e (no headings in scope, none found). **No misses anywhere**, which is the
direction that would read as `inert here`. The 14 over-counts were all the line
above being a list item, a quote, indented code, a lazy continuation or a link
definition — the cases the script's docstring says it counts.

CONTRIBUTING's rule is that a fixture built from the rule can only ever agree
with it, and the rule here is a claim about what CommonMark does. That is why the
cross-check is recorded above rather than asserted below: markdown-it-py is not
in this suite's environment, which is stdlib-only on four interpreters.

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import contextlib
import io
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "skills/dependabot-audit/scripts"
sys.path.insert(0, str(SCRIPTS))

from setext import cli, headings  # noqa: E402


class TestWhatCountsAsAHeading(unittest.TestCase):
    """The line above is what separates a heading from a thematic break."""

    def lines(self, text: str) -> list[int]:
        return [number for number, _, _ in headings(text)]

    def test_an_underline_under_a_paragraph_line_is_a_heading(self) -> None:
        """Including the two shapes Phase 2's scans could not see until 0.48.0:
        a single `-`, which CommonMark reads as an `<h2>`, and a CRLF line, whose
        `\\r` `[[:blank:]]*$` does not match."""
        for text in (
            "Title\n===\n",
            "Title\n---\n",
            "Title\n-\n",
            "Title\n=\n",
            "Title\n   ---\n",
            "Title\n---   \n",
            "Title\r\n---\r\n",
            "--\n---\n",
            "#hashtag\n---\n",
        ):
            with self.subTest(text=text):
                self.assertEqual(self.lines(text), [2], f"{text!r} is a setext heading")

    def test_a_break_is_not_a_heading(self) -> None:
        """Under a blank line, an ATX heading or another break, `---` is a break."""
        for text in (
            "Title\n\n---\n",
            "# Title\n---\n",
            "#\n---\n",
            "***\n---\n",
            "- - -\n---\n",
            "Title\n    ---\n",
            "Title\n\t---\n",
        ):
            with self.subTest(text=text):
                self.assertEqual(self.lines(text), [], f"{text!r} carries no setext heading")

    def test_an_underline_already_counted_is_not_the_line_above_the_next(self) -> None:
        """`Title` / `---` / `---` is one heading and then a break, not two."""
        self.assertEqual(self.lines("Title\n---\n---\n"), [2])

    def test_fenced_code_is_skipped(self) -> None:
        """A `---` between two YAML documents in a fenced example is not a heading.

        Only a fence of the same character and at least the opening length closes
        one, so a ``` inside a ```` block does not end it, and an unclosed fence
        runs to the end of the file as CommonMark reads it.
        """
        for text in (
            "```yaml\nkey: v\n---\n```\n",
            "~~~\nkey: v\n---\n~~~\n",
            "````\n```\nkey: v\n---\n````\n",
            "```\n~~~\nkey\n---\n```\n",
            "```\nkey\n---\n",
        ):
            with self.subTest(text=text):
                self.assertEqual(self.lines(text), [], "fenced code carries no headings")

    def test_a_backtick_in_the_info_string_is_not_a_fence(self) -> None:
        """```` ``` a`b ```` cannot open a fence, so it is a paragraph line."""
        self.assertEqual(self.lines("``` a`b\n---\n"), [2])

    def test_headings_count_again_once_the_fence_closes(self) -> None:
        self.assertEqual(self.lines("```\nx\n```\nTitle\n---\n"), [5])

    def test_front_matter_is_skipped_and_only_when_it_closes(self) -> None:
        """A closing `---` under `title: x` is front matter, not an `<h2>`.

        Unclosed, the opening `---` is an ordinary thematic break and nothing is
        skipped — otherwise a first line of `---` would hide every heading below
        it, which is the one way this could miss one.
        """
        self.assertEqual(self.lines("---\ntitle: x\n---\n\nText.\n"), [])
        self.assertEqual(self.lines("---\ntitle: x\n---\nTitle\n===\n"), [5])
        self.assertEqual(self.lines("---\nTitle\n===\n"), [3])

    def test_it_counts_the_shapes_commonmark_reads_as_something_else(self) -> None:
        """Measured: CommonMark makes both of these a thematic break.

        The line above is a list item or a quote, and a `git grep` narrowing
        cannot see either. Counting them over-counts, which reads as exposure and
        sends the auditor to look; missing one reads as `inert here`.
        """
        self.assertEqual(self.lines("- item\n---\n"), [2])
        self.assertEqual(self.lines("> quote\n---\n"), [2])

    def test_the_shapes_the_grep_narrowings_cover_are_left_to_them(self) -> None:
        """A quoted underline and one indented past three spaces are out of scope."""
        self.assertEqual(self.lines("> Title\n> ---\n"), [])
        self.assertEqual(self.lines("- item\n\n    Title\n    ---\n"), [])

    def test_the_heading_text_and_underline_come_back_with_the_line(self) -> None:
        self.assertEqual(headings("Intro\n\nTitle\n=====\n"), [(4, "Title", "=====")])


class TestTheExitCodesAreGitGreps(unittest.TestCase):
    """`0` found, `1` a real zero, `128` underivable — the codes Phase 2 reads."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = pathlib.Path(self.tmp.name)
        self.git("init", "-q")

    def git(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(self.repo), *args],
            capture_output=True,
            text=True,
            check=True,
        )

    def write(self, name: str, text: str, *, add: bool = True) -> None:
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        if add:
            self.git("add", "--", name)

    def run_script(
        self, *args: str, cwd: pathlib.Path | None = None
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPTS / "setext.py"), *args],
            capture_output=True,
            text=True,
            cwd=str(cwd or self.repo),
            check=False,
        )

    def test_a_heading_exits_0_and_names_the_file_and_line(self) -> None:
        self.write("README.md", "Intro\n\nTitle\n---\n")
        done = self.run_script()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("README.md:4: Title / ---", done.stdout)
        self.assertIn("1 setext underline(s)", done.stdout)
        self.assertIn("in 1 of 1 file(s)", done.stdout)

    def test_none_exits_1_and_says_how_many_files_it_read(self) -> None:
        """A zero over 40 files and a zero over none are different answers, and
        `git grep`'s silent `1` cannot tell them apart."""
        self.write("README.md", "Text.\n\n---\n")
        self.write("docs/guide.md", "# Guide\n\nText.\n")
        done = self.run_script()
        self.assertEqual(done.returncode, 1, done.stderr)
        self.assertIn("in 0 of 2 file(s)", done.stdout)

    def test_no_markdown_at_all_is_still_a_real_zero(self) -> None:
        self.write("main.py", "x = 1\n")
        done = self.run_script()
        self.assertEqual(done.returncode, 1, done.stderr)
        self.assertIn("in 0 of 0 file(s)", done.stdout)

    def test_outside_a_repository_it_is_underivable(self) -> None:
        with tempfile.TemporaryDirectory() as outside:
            done = self.run_script(cwd=pathlib.Path(outside))
        self.assertEqual(done.returncode, 128, done.stdout)
        self.assertIn("error:", done.stderr)

    def test_a_tracked_file_missing_from_the_tree_is_underivable(self) -> None:
        """Not a zero: the file that would have carried a heading was not read."""
        self.write("README.md", "Title\n---\n")
        (self.repo / "README.md").unlink()
        done = self.run_script()
        self.assertEqual(done.returncode, 128, done.stdout)
        self.assertIn("could not read README.md", done.stderr)

    def test_an_untracked_file_is_not_read(self) -> None:
        """`git grep` reads tracked files, and so does this."""
        self.write("README.md", "Title\n---\n", add=False)
        done = self.run_script()
        self.assertEqual(done.returncode, 1, done.stderr)

    def test_a_pathspec_argument_is_passed_through(self) -> None:
        self.write("notes.markdown", "Title\n---\n")
        self.assertEqual(self.run_script().returncode, 1)
        done = self.run_script("*.markdown")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("notes.markdown:2:", done.stdout)

    def test_a_path_with_a_space_survives_the_nul_split(self) -> None:
        self.write("my docs/read me.md", "Title\n---\n")
        done = self.run_script()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("my docs/read me.md:2:", done.stdout)

    def test_bytes_that_are_not_utf8_do_not_stop_the_scan(self) -> None:
        (self.repo / "raw.md").write_bytes(b"Caf\xe9\n---\n")
        self.git("add", "--", "raw.md")
        done = self.run_script()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("raw.md:2:", done.stdout)


class TestACrashIsUnderivableAndNotAZero(unittest.TestCase):
    """Exit 1 means no heading. An unhandled exception exits 1 too, so `cli()`
    turns anything unforeseen into 128 — the same reason `cleanup.py` has one."""

    def test_an_unexpected_error_exits_128(self) -> None:
        with (
            mock.patch("setext.main", side_effect=RuntimeError("boom")),
            mock.patch.object(sys, "argv", ["setext.py"]),
            mock.patch.dict("os.environ", {}, clear=True),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()) as err,
            self.assertRaises(SystemExit) as caught,
        ):
            cli()
        self.assertEqual(caught.exception.code, 128)
        self.assertIn("a bug, not a finding", err.getvalue())


if __name__ == "__main__":
    unittest.main()
