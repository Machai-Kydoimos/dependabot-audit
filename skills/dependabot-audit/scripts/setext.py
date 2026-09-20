#!/usr/bin/env python3
"""Phase 2's top-level narrowing: the setext underlines `git grep` cannot tell apart.

Phase 2's shape scan matches every line shaped like a setext underline, and a
`---` thematic break has exactly that shape. What separates the two is the line
*above*: under a paragraph line, `---` makes a heading, and under a blank line it
is a break. `git grep` reads one line at a time, so Phase 2's other narrowings
cover the shapes a line carries on its own, a `>` prefix or an indent, and stop
there. On `fpga-board-sim` #437 the scan listed 187 lines, every one of them the
top-level shape. Round twenty-five had to write its own awk to read the line
above (#141).

This reads it:

    python3 setext.py [<pathspec> ...]      # default '*.md', tracked files

It prints `path:line: <heading> / <underline>` for each underline that sits under
a paragraph line, then one summary line, and exits the way `git grep` does:

    0    found at least one
    1    found none, a real zero
    128  could not list or read the tree, underivable

What counts was measured against markdown-it-py's CommonMark mode:

- **An underline** is `=` or `-`, one or more, indented at most three spaces.
  `Foo` over `-` is an `<h2>`; over `    ---` (four spaces) it is paragraph text.
- **The line above** must be non-blank, and neither an ATX heading nor a thematic
  break (`# Foo` over `---` is a heading, then a break). An underline this has
  already counted does not count as the line above the next one.
- **Fenced code is skipped**, backtick or tilde. Only a fence of the same
  character, at least as long, closes it. An unclosed fence runs to the end of
  the file, as CommonMark reads it. A front-matter block opening on line 1 is
  skipped too, through the next `---` or `...`.

Any other non-blank line above counts. That includes a list item, a quoted
line and indented code, under which CommonMark reads `---` as a break, and a
lazy continuation line, under which it reads `-` as an empty list item. So a
line listed here may not be a heading after all, and every one is worth reading.
It errs that way on purpose. A line it passes over is in fenced code or front
matter, or it sits under a blank line, a heading or a break.

Requires Python 3.11+. No network. Standard library only.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from typing import NoReturn

UNDERLINE = re.compile(r" {0,3}(?:=+|-+)[ \t]*")
ATX = re.compile(r" {0,3}#{1,6}(?:[ \t]|$)")
BREAK = re.compile(r" {0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*")
FENCE = re.compile(r" {0,3}(`{3,}|~{3,})(.*)")


def underivable(what: str) -> NoReturn:
    """Exit 128, `git grep`'s code for could-not-run. Never 1, a real zero."""
    print(f"error: {what}", file=sys.stderr)
    raise SystemExit(128)


def front_matter_end(lines: list[str]) -> int:
    """The index of the first line after a front-matter block, or 0 for none.

    Unclosed, it is not front matter, and nothing is skipped.
    """
    if not lines or lines[0].rstrip() != "---":
        return 0
    for i in range(1, len(lines)):
        if lines[i].rstrip() in ("---", "..."):
            return i + 1
    return 0


def headings(text: str) -> list[tuple[int, str, str]]:
    """(1-based line of the underline, the line above it, the underline).

    `splitlines()` also splits on the `\\r` of a CRLF file, which Phase 2's
    `git grep` scans had to be taught separately.
    """
    lines = text.splitlines()
    found: list[tuple[int, str, str]] = []
    fence = ""  # the opening fence, while inside one
    above: str | None = None  # the line above, while it could be a paragraph line
    for index in range(front_matter_end(lines), len(lines)):
        line = lines[index]
        delimiter = FENCE.fullmatch(line)
        if fence:
            if (
                delimiter
                and delimiter.group(1)[0] == fence[0]
                and len(delimiter.group(1)) >= len(fence)
                and not delimiter.group(2).strip()
            ):
                fence = ""
            above = None
            continue
        # A backtick info string cannot hold a backtick; if it does, the line is
        # inline code in a paragraph, not a fence.
        if delimiter and not (delimiter.group(1)[0] == "`" and "`" in delimiter.group(2)):
            fence, above = delimiter.group(1), None
            continue
        if above is not None and UNDERLINE.fullmatch(line):
            found.append((index + 1, above.strip(), line.strip()))
            above = None
            continue
        paragraph = line.strip() and not ATX.match(line) and not BREAK.fullmatch(line)
        above = line if paragraph else None
    return found


def main(argv: list[str]) -> int:
    pathspecs = argv or ["*.md"]
    listed = subprocess.run(  # noqa: S603
        ["git", "ls-files", "-z", "--", *pathspecs],  # noqa: S607
        capture_output=True,
        check=False,
    )
    if listed.returncode:
        underivable(listed.stderr.decode("utf-8", "replace").strip() or "git ls-files failed")
    paths = [p for p in listed.stdout.decode("utf-8", "surrogateescape").split("\0") if p]
    hits = files = 0
    for path in paths:
        try:
            with open(path, encoding="utf-8", errors="replace") as handle:
                text = handle.read()
        except OSError as exc:
            underivable(f"could not read {path}: {exc.strerror}")
        found = headings(text)
        for number, heading, underline in found:
            print(f"{path}:{number}: {heading} / {underline}")
        hits += len(found)
        files += bool(found)
    print(
        f"{hits} setext underline(s) under a paragraph line, "
        f"in {files} of {len(paths)} file(s) matching {' '.join(pathspecs)}"
    )
    return 0 if hits else 1


def cli() -> NoReturn:
    """Entry point. Anything unforeseen becomes 128, never 1.

    Exit 1 is a real zero, and an unhandled exception exits 1 too, so without
    this a crash would read as a clean tree. Set `DEPENDABOT_AUDIT_DEBUG` to
    re-raise.
    """
    try:
        sys.exit(main(sys.argv[1:]))
    except SystemExit:
        raise
    except Exception as exc:
        if os.environ.get("DEPENDABOT_AUDIT_DEBUG"):
            raise
        underivable(f"unexpected {type(exc).__name__}: {exc} (a bug, not a finding)")


if __name__ == "__main__":
    cli()
