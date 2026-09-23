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

    python3 setext.py [--ref <tree-ish>] [<pathspec> ...]   # default '*.md'

With `--ref` it reads the blobs at that tree-ish — Phase 2 passes `pr-<N>` —
and without it the tracked files of the directory it runs in. Phase 2 reaches
the PR at a ref because the worktree exists only where Phase 4 or 5 will run:
under `--no-execute` there is no `$SCRATCH/pr-<N>` to run in, and the checkout
you are in is not the PR. A pathspec is a shell-style glob matched against the
whole path, or a literal path or directory; git's pathspec magic is not read.

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

import fnmatch
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


def _matches(path: str, pathspecs: list[str]) -> bool:
    """A glob against the whole path, as git's default pathspec reads `*.md`, or
    a literal path or directory prefix."""
    for spec in pathspecs:
        if any(c in spec for c in "*?["):
            if fnmatch.fnmatchcase(path, spec):
                return True
        elif path == spec or path.startswith(spec.rstrip("/") + "/"):
            return True
    return False


def _at_ref(ref: str, pathspecs: list[str]) -> list[tuple[str, str]]:
    """(path, text) for each blob at `ref` the pathspecs select."""
    listed = subprocess.run(  # noqa: S603
        ["git", "ls-tree", "-r", "-z", ref],  # noqa: S607
        capture_output=True,
        check=False,
    )
    if listed.returncode:
        underivable(listed.stderr.decode("utf-8", "replace").strip() or f"git ls-tree {ref} failed")
    blobs: list[tuple[str, str]] = []
    for entry in listed.stdout.split(b"\0"):
        meta, _, raw = entry.partition(b"\t")
        fields = meta.split()
        # A submodule is a `commit` entry, not a file in this tree.
        if len(fields) == 3 and fields[1] == b"blob":
            path = raw.decode("utf-8", "surrogateescape")
            if _matches(path, pathspecs):
                blobs.append((path, fields[2].decode("ascii")))
    if not blobs:
        return []
    read = subprocess.run(
        ["git", "cat-file", "--batch"],  # noqa: S607
        input="".join(f"{oid}\n" for _, oid in blobs).encode("ascii"),
        capture_output=True,
        check=False,
    )
    if read.returncode:
        underivable(read.stderr.decode("utf-8", "replace").strip() or "git cat-file failed")
    out, pos, found = read.stdout, 0, []
    for path, _oid in blobs:
        end = out.find(b"\n", pos)
        header = out[pos:end].split() if end >= 0 else []
        if len(header) != 3 or header[1] != b"blob":
            underivable(f"could not read {path} at {ref}")
        start = end + 1
        size = int(header[2])
        found.append((path, out[start : start + size].decode("utf-8", "replace")))
        pos = start + size + 1  # the object's trailing newline
    return found


def _in_worktree(pathspecs: list[str]) -> list[tuple[str, str]]:
    """(path, text) for each tracked file the pathspecs select, read from disk."""
    listed = subprocess.run(  # noqa: S603
        ["git", "ls-files", "-z", "--", *pathspecs],  # noqa: S607
        capture_output=True,
        check=False,
    )
    if listed.returncode:
        underivable(listed.stderr.decode("utf-8", "replace").strip() or "git ls-files failed")
    found = []
    for path in (p for p in listed.stdout.decode("utf-8", "surrogateescape").split("\0") if p):
        try:
            with open(path, encoding="utf-8", errors="replace") as handle:
                found.append((path, handle.read()))
        except OSError as exc:
            underivable(f"could not read {path}: {exc.strerror}")
    return found


def main(argv: list[str]) -> int:
    ref = None
    if argv[:1] == ["--ref"]:
        if len(argv) < 2:
            underivable("--ref needs a tree-ish")
        ref, argv = argv[1], argv[2:]
    pathspecs = argv or ["*.md"]
    files_read = _at_ref(ref, pathspecs) if ref else _in_worktree(pathspecs)
    prefix = f"{ref}:" if ref else ""
    hits = files = 0
    for path, text in files_read:
        found = headings(text)
        for number, heading, underline in found:
            print(f"{prefix}{path}:{number}: {heading} / {underline}")
        hits += len(found)
        files += bool(found)
    print(
        f"{hits} setext underline(s) under a paragraph line, "
        f"in {files} of {len(files_read)} file(s) matching {' '.join(pathspecs)}"
        + (f" at {ref}" if ref else "")
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
