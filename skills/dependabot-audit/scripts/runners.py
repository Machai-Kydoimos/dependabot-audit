#!/usr/bin/env python3
"""Phase 4's Rows 1 and 3 for GitHub Actions: what starts each workflow, and which
runner every job lands on, at a ref.

**Row 1 asks which triggers this repo uses**, because a release that restricts a
trigger is inert where no workflow has it. Until 0.57.0 it was two greps: one
alternation of three event names, `pull_request_target|workflow_run|release`, and
one for a `tags:` key within two lines of `push:`. Both were closed lists, and both
missed what they were built to find (#172):

  - setup-uv v10.2.0 stops saving its cache in merge queues. A merge queue fires
    `merge_group`, which was in neither grep, so the run answered it by hand.
  - A `push:` with no `branches:` or `tags:` runs on every tag push too, and a
    `tags:` below a `branches:` list is more than two lines down. Across 304
    workflows from 22 repositories, 36 run on tag pushes. The grep missed 17 of
    them, in six repositories, pydantic's and pytest's CI among them.

So every workflow's `on:` is listed instead, with every event and the filters
GitHub applies. A `push` says which refs it runs on: `every branch and tag`,
branches only, or the tag filters as written. The reader matches the notes
against that list, which cannot leave an event out. A file this cannot read is
named as such, and its triggers are unknown, not empty. Row 1 sets no exit
status. The status below is Row 3's.

**Row 3.** `actions.md` § Phase 4 reads an environment variable's silence as `inert here` only
if every runner is GitHub-hosted: a runner, or a repository setting, can set a
variable no grep of the tree reaches, and a self-hosted runner is the one that
would (#162). Row 3 was `git grep -nE '^[[:space:]]*runs-on:'`, and a grep line
answers that question only for a literal label. On `fpga-board-sim` #436 it printed
`ci.yml:96: runs-on: ${{ matrix.os }}` among 13 lines, and the run resolved it by
hand. Nothing said to, and nothing said what an unresolved one is worth (#166).

Measured over 132 workflow files in nine repositories (564 jobs, 491 `runs-on:`
lines, 2026-09-25), an expression is common enough that reading one by eye is the
normal case:

    297  a literal label
    159  a ternary on the repository's own identity --
           ${{ github.repository_owner == 'astral-sh'
               && 'github-ubuntu-24.04-x86_64-4' || 'ubuntu-latest' }}
     27  the job's own matrix: ${{ matrix.os }}, ${{ matrix.platform.runner }}
      3  a reusable workflow's input: ${{ inputs.runner }}
     78  no `runs-on:` -- the job calls a reusable workflow

This script resolves each job's `runs-on:` to the set of labels it can take:

  - a literal, a flow or block list, or a `group:`/`labels:` mapping;
  - `matrix.<key>` and `matrix.<key>.<field>` from the job's `strategy.matrix`,
    `include` entries too (`exclude` only removes combinations, so ignoring it can
    over-report a label but never hide one);
  - `github.repository` and `github.repository_owner`, from `--repo`;
  - `==`, `!=`, `&&`, `||`, `!`, parentheses and string literals, with GitHub's
    semantics: `&&` and `||` return an operand, and `==` ignores case.

Anything else -- `inputs.*`, `vars.*`, a function call, a matrix built by
`fromJSON` -- leaves the job `underivable`, and a job that calls a reusable workflow
in another repository runs wherever that workflow says, which the tree does not
hold. A label is GitHub-hosted only if it is one of GitHub's own: `ubuntu-*`,
`windows-*` and `macos-*` in the forms GitHub publishes, and `ubuntu-slim`. A
larger runner an organisation named `github-ubuntu-24.04-x86_64-4` may well be
GitHub's hardware, but its name is the organisation's, so the label cannot say --
it reads as `not a standard GitHub-hosted label`, never as hosted.

The workflow files are read with a parser for the YAML that workflows are written
in: block and flow collections, quoted and plain scalars, and block scalars (every
`run: |`). An anchor, an alias, a tag or a merge key makes the file `unreadable`,
never guessed at. Checked against PyYAML on the same 132 files: the `runs-on:`,
`strategy:` and `uses:` of all 564 jobs agree. The first run found one file
unreadable -- cli/cli's generated `dependabot-triage.lock.yml`, whose `\\"` escapes
inside a double-quoted `run:` made a later ` #` look like a comment.

Until 0.57.0 a scalar had to start on its key's line and, if quoted, end there,
so 9 of psf/black's 13 workflows were unreadable -- `if:` with its expression on
the lines below -- and so were `action.yml` files in setup-uv and
download-artifact (#175). Those lines are folded as YAML folds them now, and
the check was rerun on 337 files, 304 workflows from 22 repositories plus 33
`action.yml` files, against PyYAML's `BaseLoader`: every document agrees, except
block scalars, which this keeps raw. Refused on purpose where PyYAML reads on: a
quoted scalar whose next line sits at its key's own indent, which reads as the
next key.

Exit status: 0 = every job's runner resolved, and to a GitHub-hosted label.
1 = at least one job's runner is not a standard GitHub-hosted label, or could not
be resolved from the tree -- the output names which, and why. 2 = could not run.

    runners.py --ref pr-<N> --repo OWNER/NAME [--workflows DIR]

Requires Python 3.11+ and `git`.
"""

from __future__ import annotations

import argparse
import itertools
import re
import subprocess
import sys
from collections.abc import Iterator
from typing import Any, NoReturn

TIMEOUT = 60

# GitHub's own runner labels, in the forms it publishes: the OS, a version or
# `latest`, and an architecture or size suffix. Anything else is a name someone
# chose, and a name cannot say whose hardware it is.
HOSTED = re.compile(
    r"^(?:ubuntu|windows|macos)-(?:latest|\d+(?:\.\d+)?)(?:-(?:arm|intel|large|xlarge))?$"
    r"|^ubuntu-slim$",
    re.IGNORECASE,
)

# At most this many matrix combinations are enumerated per expression. Past it
# the job is `underivable` rather than slow.
COMBINATIONS = 4096


class Unreadable(Exception):
    """The YAML uses a construct this parser does not read. Never guessed at."""


class Underivable(Exception):
    """A runner that cannot be resolved from the tree -- the reason is the message."""


def fail(what: str) -> NoReturn:
    """Exit 2. Reserved for "could not run", never for "ran and found something"."""
    print(f"error: {what}", file=sys.stderr)
    raise SystemExit(2)


# --- a parser for the YAML workflows are written in ---------------------------


def _strip_comment(line: str) -> str:
    """Drop a `#` comment that is not inside quotes; keep indentation.

    A double-quoted string escapes with a backslash, so `\\"` does not close it --
    cli/cli's generated `*.lock.yml` carries `run: "... rm -f \\"$PKGS\\" ..."`, and
    reading that as a close made a later ` #` look like a comment.
    """
    quote = ""
    escaped = False
    for i, ch in enumerate(line):
        if quote:
            if escaped:
                escaped = False
            elif quote == '"' and ch == "\\":
                escaped = True
            elif ch == quote:
                quote = ""
        elif ch in "'\"":
            quote = ch
        elif ch == "#" and (i == 0 or line[i - 1] in " \t"):
            return line[:i].rstrip()
    return line.rstrip()


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "0": "\0", "a": "\a", "b": "\b", "e": "\x1b"}


def _unescape(text: str) -> str:
    """A double-quoted scalar's escapes; one this does not name stands for itself."""
    return re.sub(r"\\(.)", lambda m: _ESCAPES.get(m.group(1), m.group(1)), text, flags=re.DOTALL)


def _scalar(text: str) -> Any:
    text = text.strip()
    if not text:
        return None
    if text[0] in "&*!" or text.startswith("<<"):
        raise Unreadable(f"anchor, alias, tag or merge key: {text[:40]}")
    if text[0] == '"':
        if not text.endswith('"') or len(text) < 2:
            raise Unreadable(f"unterminated string: {text[:40]}")
        return _unescape(text[1:-1])
    if text[0] == "'":
        if not text.endswith("'") or len(text) < 2:
            raise Unreadable(f"unterminated string: {text[:40]}")
        return text[1:-1].replace("''", "'")
    return text


def _flow(text: str) -> Any:
    """Parse one flow collection (`[...]` or `{...}`) that makes up the whole of `text`."""
    value, rest = _flow_value(text.strip())
    if rest.strip():
        raise Unreadable(f"text after a flow collection: {rest[:40]}")
    return value


def _flow_value(text: str) -> tuple[Any, str]:
    text = text.lstrip()
    if text.startswith("["):
        items: list[Any] = []
        text = text[1:].lstrip()
        while not text.startswith("]"):
            item, text = _flow_value(text)
            items.append(item)
            text = text.lstrip()
            if text.startswith(","):
                text = text[1:].lstrip()
            elif not text.startswith("]"):
                raise Unreadable(f"unclosed flow sequence near: {text[:40]}")
        return items, text[1:]
    if text.startswith("{"):
        mapping: dict[str, Any] = {}
        text = text[1:].lstrip()
        while not text.startswith("}"):
            key, text = _flow_value(text)
            text = text.lstrip()
            if not text.startswith(":"):
                raise Unreadable(f"flow mapping key without a value: {text[:40]}")
            value, text = _flow_value(text[1:])
            mapping[str(key)] = value
            text = text.lstrip()
            if text.startswith(","):
                text = text[1:].lstrip()
            elif not text.startswith("}"):
                raise Unreadable(f"unclosed flow mapping near: {text[:40]}")
        return mapping, text[1:]
    if text[:1] in "'\"":
        quote = text[0]
        end = 1
        while True:
            end = text.find(quote, end)
            if end < 0:
                raise Unreadable(f"unterminated string: {text[:40]}")
            if quote == "'" and text[end + 1 : end + 2] == "'":
                end += 2
                continue
            if quote == '"' and (len(text[:end]) - len(text[:end].rstrip("\\"))) % 2:
                end += 1
                continue
            break
        return _scalar(text[: end + 1]), text[end + 1 :]
    # A plain scalar in flow context ends at `,` `]` `}` or `: ` -- but `${{ }}`
    # holds braces of its own, so an expression is taken whole.
    out = []
    depth = 0
    i = 0
    while i < len(text):
        if text.startswith("${{", i):
            depth += 1
            out.append("${{")
            i += 3
            continue
        if depth and text.startswith("}}", i):
            depth -= 1
            out.append("}}")
            i += 2
            continue
        ch = text[i]
        if not depth and (ch in ",]}" or (ch == ":" and text[i + 1 : i + 2] in (" ", ""))):
            break
        out.append(ch)
        i += 1
    return _scalar("".join(out)), text[i:]


def _balanced(text: str) -> bool:
    """Do the flow brackets in `text` close, ignoring quotes and `${{ }}`?"""
    depth = 0
    quote = ""
    i = 0
    while i < len(text):
        ch = text[i]
        if quote:
            if quote == '"' and ch == "\\":
                i += 1
            elif ch == quote:
                quote = ""
        elif ch in "'\"":
            quote = ch
        elif text.startswith("${{", i):
            i = text.find("}}", i)
            if i < 0:
                return False
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
        i += 1
    return depth <= 0


def _split_key(text: str) -> tuple[str, str] | None:
    """`key: rest` -> (key, rest), or None when the text is not a mapping entry."""
    if text[:1] in "'\"":
        quote = text[0]
        end = text.find(quote, 1)
        if end > 0 and text[end + 1 : end + 2] == ":":
            return text[1:end], text[end + 2 :]
        return None
    found = re.match(r"^([^\s:#{}\[\],][^:#]*?|[^\s:#{}\[\],]):(?:\s+|$)(.*)$", text)
    if not found:
        return None
    return found.group(1).strip(), found.group(2)


class _Lines:
    """The document's lines, comments stripped, blanks kept for block scalars."""

    def __init__(self, text: str) -> None:
        self.raw = text.splitlines()
        self.clean = [_strip_comment(line) for line in self.raw]

    def next_content(self, i: int) -> int:
        while i < len(self.clean) and not self.clean[i].strip():
            i += 1
        return i


def load(text: str) -> Any:
    """The document as dicts, lists and strings -- or `Unreadable`."""
    if "\t" in "".join(line[: _indent(line) + 1] for line in text.splitlines()):
        raise Unreadable("tab indentation")
    lines = _Lines(text)
    start = lines.next_content(0)
    if start < len(lines.clean) and lines.clean[start].strip() == "---":
        start = lines.next_content(start + 1)
    if start >= len(lines.clean):
        return None
    value, end = _block(lines, start, _indent(lines.clean[start]))
    end = lines.next_content(end)
    if end < len(lines.clean):
        raise Unreadable(f"line {end + 1} does not fit the structure above it")
    return value


def _block(lines: _Lines, i: int, indent: int) -> tuple[Any, int]:
    text = lines.clean[i][indent:]
    if text == "-" or text.startswith("- "):
        return _sequence(lines, i, indent)
    return _mapping(lines, i, indent)


def _node(lines: _Lines, i: int, parent: int) -> tuple[Any, int]:
    """A value that starts on a line of its own, below a key or a dash at `parent`.

    A collection, or a scalar: `if:` with its expression on the next line, or a
    `description:` whose quoted text does. Both are ordinary YAML, and reading
    every such line as a mapping entry made 9 of psf/black's 13 workflows
    unreadable (#175).
    """
    column = _indent(lines.clean[i])
    text = lines.clean[i][column:]
    if text == "-" or text.startswith("- "):
        return _block(lines, i, column)
    if _split_key(text) and not text.startswith(("[", "{")):
        return _block(lines, i, column)
    return _value(lines, i, text, parent)


def _sequence(lines: _Lines, i: int, indent: int) -> tuple[list[Any], int]:
    items: list[Any] = []
    while True:
        i = lines.next_content(i)
        if i >= len(lines.clean) or _indent(lines.clean[i]) != indent:
            return items, i
        text = lines.clean[i][indent:]
        if not (text == "-" or text.startswith("- ")):
            return items, i
        rest = text[1:].lstrip(" ")
        column = indent + (len(text) - len(rest))
        if not rest:
            nxt = lines.next_content(i + 1)
            if nxt < len(lines.clean) and _indent(lines.clean[nxt]) > indent:
                value, i = _node(lines, nxt, indent)
            else:
                value, i = None, i + 1
        elif _split_key(rest) and not rest.startswith(("[", "{")):
            # `- key: value` opens a mapping whose keys sit at the column after `- `.
            lines.clean[i] = " " * column + rest
            value, i = _mapping(lines, i, column)
        else:
            value, i = _value(lines, i, rest, indent)
        items.append(value)


def _mapping(lines: _Lines, i: int, indent: int) -> tuple[dict[str, Any], int]:
    mapping: dict[str, Any] = {}
    while True:
        i = lines.next_content(i)
        if i >= len(lines.clean) or _indent(lines.clean[i]) != indent:
            return mapping, i
        text = lines.clean[i][indent:]
        if text == "-" or text.startswith("- "):
            return mapping, i
        split = _split_key(text)
        if split is None:
            raise Unreadable(f"line {i + 1} is not a `key: value`: {text[:40]}")
        key, rest = split
        if key.startswith(("&", "*", "!", "<<")):
            raise Unreadable(f"anchor, alias, tag or merge key: {key[:40]}")
        if rest.strip():
            value, i = _value(lines, i, rest, indent)
        else:
            nxt = lines.next_content(i + 1)
            if nxt < len(lines.clean) and (
                _indent(lines.clean[nxt]) > indent
                or (
                    _indent(lines.clean[nxt]) == indent
                    and re.match(r"-(?: |$)", lines.clean[nxt][indent:])
                )
            ):
                value, i = _node(lines, nxt, indent)
            else:
                value, i = None, i + 1
        mapping[key] = value


def _value(lines: _Lines, i: int, rest: str, indent: int) -> tuple[Any, int]:
    """The value that starts on line `i` after a key or a dash."""
    rest = rest.strip()
    if re.match(r"^[|>][-+0-9]*$", rest):
        # A block scalar: every deeper or blank line after it, opaque.
        body = []
        j = i + 1
        while j < len(lines.raw) and (not lines.raw[j].strip() or _indent(lines.raw[j]) > indent):
            body.append(lines.raw[j])
            j += 1
        return "\n".join(body), j
    if rest.startswith(("[", "{")):
        text, j = rest, i + 1
        while not _balanced(text):
            if j >= len(lines.clean):
                raise Unreadable(f"flow collection opened on line {i + 1} never closes")
            text += " " + lines.clean[j].strip()
            j += 1
        return _flow(text), j
    if rest[:1] in "'\"":
        return _quoted(lines, i, len(lines.clean[i]) - len(rest), indent)
    # A plain scalar may continue on deeper lines, folded as YAML folds them: a line
    # break reads as a space, and each blank line between two as a newline.
    j = i + 1
    folded = rest
    while True:
        nxt = lines.next_content(j)
        if nxt >= len(lines.clean) or _indent(lines.clean[nxt]) <= indent:
            break
        # A comment line ends a plain scalar, and a deeper line after it is not
        # YAML. Stopping here leaves that line to fail the structure, not be read.
        if any(lines.raw[k].strip() for k in range(j, nxt)):
            break
        blanks = nxt - j
        folded += ("\n" * blanks or " ") + lines.clean[nxt].strip()
        j = nxt + 1
    return _scalar(folded), j


def _quoted(lines: _Lines, i: int, column: int, indent: int) -> tuple[str, int]:
    """A quoted scalar that opens at `column` on line `i`, however many lines it takes.

    Read from the raw lines: inside the quotes a `#` is text, never a comment.
    """
    quote = lines.raw[i][column]
    pieces: list[str] = []
    j, start = i, column + 1
    while True:
        line = lines.raw[j]
        if j > i and line.strip() and _indent(line) <= indent:
            raise Unreadable(f"a quoted scalar opened on line {i + 1} never closes")
        end = _closing(line, start, quote)
        if end is not None:
            pieces.append(line[start:end])
            after = line[end + 1 :].strip()
            if after and not after.startswith("#"):
                raise Unreadable(f"text after a quoted scalar on line {j + 1}: {after[:40]}")
            break
        pieces.append(line[start:])
        j, start = j + 1, 0
        if j >= len(lines.raw):
            raise Unreadable(f"a quoted scalar opened on line {i + 1} never closes")
    text = _fold_lines(pieces, quote)
    return (_unescape(text) if quote == '"' else text.replace("''", "'")), j + 1


def _closing(line: str, start: int, quote: str) -> int | None:
    """Where the quote closes on this line, or None when it runs on to the next."""
    k = start
    while k < len(line):
        if quote == '"' and line[k] == "\\":
            k += 2
            continue
        if line[k] == quote:
            if quote == "'" and line[k + 1 : k + 2] == "'":
                k += 2
                continue
            return k
        k += 1
    return None


def _fold_lines(pieces: list[str], quote: str) -> str:
    """A quoted scalar's lines joined as YAML joins them.

    A line break is a space and each blank line a newline; the whitespace around a
    break goes. In double quotes, a backslash at the end of a line escapes the
    break, so the lines join with nothing between them.
    """
    if len(pieces) == 1:
        return pieces[0]
    out = pieces[0].rstrip(" \t")
    n = 1
    while n < len(pieces):
        blanks = 0
        while n < len(pieces) - 1 and not pieces[n].strip(" \t"):
            blanks += 1
            n += 1
        text = pieces[n].lstrip(" \t")
        if n < len(pieces) - 1:
            text = text.rstrip(" \t")
        trailing = len(out) - len(out.rstrip("\\"))
        if quote == '"' and trailing % 2:
            out = out[:-1] + "\n" * blanks + text
        else:
            out += ("\n" * blanks or " ") + text
        n += 1
    return out


# --- GitHub's expression language, the part runners are written in ----------

_TOKEN = re.compile(
    r"\s*(?:(?P<str>'(?:[^']|'')*')|(?P<op>==|!=|&&|\|\||!|\(|\))"
    r"|(?P<ident>[A-Za-z_][A-Za-z0-9_-]*(?:\.[A-Za-z_*][A-Za-z0-9_-]*)*)"
    r"|(?P<num>-?\d+(?:\.\d+)?)|(?P<other>\S))"
)


def _tokens(expression: str) -> list[tuple[str, str]]:
    out = []
    for match in _TOKEN.finditer(expression):
        kind = match.lastgroup or "other"
        text = match.group(kind)
        if kind == "other":
            raise Underivable(f"`{expression.strip()}` uses `{text}`, which is not resolved here")
        out.append((kind, text))
    return out


class _Expression:
    """Recursive descent over `||` < `&&` < `==`/`!=` < `!` < primary."""

    def __init__(self, text: str, names: dict[str, Any]) -> None:
        self.tokens = _tokens(text)
        self.text = text.strip()
        self.names = names
        self.at = 0

    def parse(self) -> Any:
        value = self._or()
        if self.at != len(self.tokens):
            raise Underivable(f"`{self.text}` is not an expression this resolves")
        return value

    def _peek(self) -> tuple[str, str] | None:
        return self.tokens[self.at] if self.at < len(self.tokens) else None

    def _or(self) -> Any:
        left = self._and()
        while self._peek() == ("op", "||"):
            self.at += 1
            right = self._and()
            left = left if _truthy(left) else right
        return left

    def _and(self) -> Any:
        left = self._eq()
        while self._peek() == ("op", "&&"):
            self.at += 1
            right = self._eq()
            left = right if _truthy(left) else left
        return left

    def _eq(self) -> Any:
        left = self._not()
        while self._peek() in (("op", "=="), ("op", "!=")):
            op = self.tokens[self.at][1]
            self.at += 1
            right = self._not()
            same = _fold(left) == _fold(right)
            left = same if op == "==" else not same
        return left

    def _not(self) -> Any:
        if self._peek() == ("op", "!"):
            self.at += 1
            return not _truthy(self._not())
        return self._primary()

    def _primary(self) -> Any:
        token = self._peek()
        if token is None:
            raise Underivable(f"`{self.text}` ends early")
        kind, text = token
        self.at += 1
        if kind == "str":
            return text[1:-1].replace("''", "'")
        if kind == "num":
            return float(text)
        if (kind, text) == ("op", "("):
            value = self._or()
            if self._peek() != ("op", ")"):
                raise Underivable(f"`{self.text}` has an unclosed parenthesis")
            self.at += 1
            return value
        if kind == "ident":
            if self._peek() == ("op", "("):
                raise Underivable(f"`{self.text}` calls `{text}()`, which is not resolved here")
            if text in ("true", "false"):
                return text == "true"
            if text == "null":
                return None
            if text not in self.names:
                raise Underivable(f"`{text}` is not in the tree, so `{self.text}` is underivable")
            return self.names[text]
        raise Underivable(f"`{self.text}` is not an expression this resolves")


def _truthy(value: Any) -> bool:
    return value not in (None, False, 0, 0.0, "")


def _fold(value: Any) -> Any:
    """GitHub compares strings without regard to case."""
    return value.casefold() if isinstance(value, str) else value


_EMBEDDED = re.compile(r"\$\{\{(.*?)\}\}", re.DOTALL)


def _matrix_paths(text: str) -> list[str]:
    return sorted(set(re.findall(r"\bmatrix((?:\.[A-Za-z0-9_-]+)+)", text)))


def _matrix_values(matrix: Any, path: str) -> list[Any]:
    """Every value `matrix.<path>` can take: the key's list, plus `include` entries."""
    if not isinstance(matrix, dict):
        raise Underivable("the matrix is built by an expression, not written in the file")
    head, *fields = path.strip(".").split(".")
    found: list[Any] = []
    listed = matrix.get(head)
    if isinstance(listed, str) and listed.strip().startswith("${{"):
        raise Underivable(f"`matrix.{head}` is built by an expression, not written in the file")
    if isinstance(listed, list):
        found += listed
    elif listed is not None:
        found.append(listed)
    include = matrix.get("include") or []
    if isinstance(include, str):
        # psf/black's `include: ${{ fromJson(needs.configure.outputs.include) }}`,
        # which a loop over its characters reported as a key missing from the matrix.
        raise Underivable("`matrix.include` is built by an expression, not written in the file")
    for entry in include:
        if isinstance(entry, dict) and head in entry:
            found.append(entry[head])
    values = []
    for value in found:
        for field in fields:
            if not isinstance(value, dict) or field not in value:
                raise Underivable(f"`matrix.{path.strip('.')}` does not resolve in every entry")
            value = value[field]
        values.append(value)
    if not values:
        raise Underivable(f"`matrix.{path.strip('.')}` is not in the job's matrix")
    return values


def resolve(value: Any, job: dict[str, Any], repo: str) -> set[str]:
    """Every label a `runs-on:` value can take for this job, or `Underivable`."""
    if isinstance(value, list):
        labels: set[str] = set()
        for item in value:
            labels |= resolve(item, job, repo)
        return labels
    if isinstance(value, dict):
        if "group" in value:
            raise Underivable(f"runner group `{value['group']}`, which the repository defines")
        return resolve(value.get("labels"), job, repo)
    if not isinstance(value, str) or not value.strip():
        raise Underivable("no `runs-on:` value")
    if "${{" not in value:
        return {value.strip()}
    owner = repo.split("/")[0]
    base = {"github.repository": repo, "github.repository_owner": owner}
    matrix = (job.get("strategy") or {}).get("matrix")
    paths = _matrix_paths(value)
    choices = [[(p, v) for v in _matrix_values(matrix, p)] for p in paths]
    if _product_size(choices) > COMBINATIONS:
        raise Underivable(f"more than {COMBINATIONS} matrix combinations")
    labels = set()
    for combination in itertools.product(*choices):
        names = dict(base)
        names.update({f"matrix{p}": v for p, v in combination})
        text = _substitute(value, names)
        labels.add(text.strip())
    return labels


def _substitute(value: str, names: dict[str, Any]) -> str:
    """`value` with every `${{ ... }}` replaced by what it evaluates to under `names`."""

    def one(match: re.Match[str]) -> str:
        return _as_text(_Expression(match.group(1), names).parse())

    return _EMBEDDED.sub(one, value)


def _product_size(choices: list[list[tuple[str, Any]]]) -> int:
    size = 1
    for options in choices:
        size *= max(len(options), 1)
    return size


def _as_text(value: Any) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def jobs(document: Any) -> Iterator[tuple[str, dict[str, Any]]]:
    if not isinstance(document, dict) or not isinstance(document.get("jobs"), dict):
        return
    for name, job in document["jobs"].items():
        if isinstance(job, dict):
            yield str(name), job


# --- what starts a workflow: Row 1 --------------------------------------------

# The filters GitHub applies to an event, in the order they are shown.
FILTERS = (
    "types",
    "branches",
    "branches-ignore",
    "tags",
    "tags-ignore",
    "paths",
    "paths-ignore",
    "workflows",
)


def triggers(document: Any) -> dict[str, Any]:
    """The workflow's `on:` as {event: its settings, or None}, in the file's order."""
    on = document.get("on") if isinstance(document, dict) else None
    if isinstance(on, str):
        return {on: None}
    if isinstance(on, list):
        return {str(event): None for event in on}
    if isinstance(on, dict):
        return {str(event): spec for event, spec in on.items()}
    return {}


def _listed(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return str(value)


def describe(event: str, spec: Any) -> str:
    """One event with its filters -- `push` always saying which refs it runs on."""
    spec = spec if isinstance(spec, dict) else {}
    shown = [f"{key}: {_listed(spec[key])}" for key in FILTERS if key in spec]
    if event == "push":
        # GitHub runs a push with no ref filter on branches AND tags, and one with
        # only branch filters on no tag at all.
        refs = [
            key for key in ("branches", "branches-ignore", "tags", "tags-ignore") if key in spec
        ]
        if not refs:
            shown.insert(0, "every branch and tag")
        elif not any(key.startswith("tags") for key in refs):
            shown.append("no tag pushes")
    return f"{event} [{'; '.join(shown)}]" if shown else event


def classify(job: dict[str, Any], repo: str) -> tuple[str, str]:
    """(state, detail): `hosted`, `not hosted`, or `underivable`."""
    if "runs-on" not in job:
        uses = str(job.get("uses", ""))
        if uses.startswith("./"):
            return "hosted", f"calls {uses}, whose own jobs are listed there"
        if uses:
            return "underivable", f"calls {uses}, which runs wherever that workflow says"
        return "underivable", "no `runs-on:` and no `uses:`"
    try:
        labels = resolve(job["runs-on"], job, repo)
    except Underivable as exc:
        return "underivable", str(exc)
    shown = ", ".join(sorted(labels))
    if "self-hosted" in {label.lower() for label in labels}:
        return "not hosted", f"{shown} -- self-hosted"
    odd = sorted(label for label in labels if not HOSTED.match(label))
    if odd:
        return "not hosted", f"{shown} -- not a standard GitHub-hosted label: {', '.join(odd)}"
    return "hosted", shown


# --- the tree at a ref --------------------------------------------------------


def _git(args: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(  # noqa: S603
            ["git", *args],  # noqa: S607
            capture_output=True,
            text=True,
            check=False,
            timeout=TIMEOUT,
        )
    except FileNotFoundError:
        fail("`git` is not on PATH")
    except subprocess.TimeoutExpired:
        fail(f"`git {' '.join(args[:2])}` exceeded {TIMEOUT}s")


def workflows(ref: str, directory: str) -> list[tuple[str, str]]:
    listing = _git(["ls-tree", "--name-only", f"{ref}:{directory}"])
    if listing.returncode != 0:
        fail(f"cannot list {directory} at {ref}: {listing.stderr.strip()[:200]}")
    found = []
    for name in listing.stdout.splitlines():
        if not re.search(r"\.ya?ml$", name):
            continue
        shown = _git(["show", f"{ref}:{directory}/{name}"])
        if shown.returncode != 0:
            fail(f"cannot read {directory}/{name} at {ref}: {shown.stderr.strip()[:200]}")
        found.append((f"{directory}/{name}", shown.stdout))
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description="Which runner every job lands on, at a ref")
    parser.add_argument("--ref", required=True, help="the tree to read, pr-<N>")
    parser.add_argument("--repo", required=True, metavar="OWNER/NAME", help="$OWNER/$NAME")
    parser.add_argument("--workflows", default=".github/workflows", metavar="DIR")
    args = parser.parse_args()
    if not re.fullmatch(r"[^/\s]+/[^/\s]+", args.repo):
        fail(f"--repo takes OWNER/NAME, got {args.repo!r}")

    files = workflows(args.ref, args.workflows.strip("/"))
    counts = {"hosted": 0, "not hosted": 0, "underivable": 0}
    lines: list[str] = []
    started: list[str] = []
    events: dict[str, int] = {}
    unread = 0
    for path, text in files:
        try:
            document = load(text)
        except Unreadable as exc:
            counts["underivable"] += 1
            unread += 1
            lines.append(f"  underivable  {path}: the file is unreadable here -- {exc}")
            started.append(f"  {path}: unreadable here, so its triggers are unknown -- not none")
            continue
        on = triggers(document)
        for event in on:
            events[event] = events.get(event, 0) + 1
        shown = ", ".join(describe(event, spec) for event, spec in on.items())
        started.append(f"  {path}: {shown or 'no `on:` -- nothing starts it'}")
        for name, job in jobs(document):
            state, detail = classify(job, args.repo)
            counts[state] += 1
            mark = {
                "hosted": "hosted     ",
                "not hosted": "NOT HOSTED ",
                "underivable": "underivable",
            }
            lines.append(f"  {mark[state]}  {path} {name}: {detail}")

    print(f"triggers at {args.ref} (Row 1), {len(files)} workflow file(s):")
    for line in started:
        print(line)
    index = ", ".join(f"{event} ({n})" for event, n in sorted(events.items()))
    print(f"events: {index or 'none'}")
    if unread:
        print(
            f"  {unread} file(s) unreadable, so an event missing from this index may still "
            "start one of them."
        )
    print()
    print(f"runners at {args.ref} (Row 3), as {args.repo}:")
    for line in lines:
        print(line)
    total = sum(counts.values())
    print()
    if not total:
        print("RESULT: NO JOBS -- nothing here runs, so no runner can set anything.")
        return 0
    if counts["hosted"] == total:
        print(f"RESULT: HOSTED -- every one of {total} job(s) runs on a GitHub-hosted label.")
        return 0
    print(
        f"RESULT: NOT ALL HOSTED -- {counts['not hosted']} job(s) on another runner, "
        f"{counts['underivable']} unresolved, of {total}. A runner or a repository setting can "
        "set what no grep of the tree reaches, so an environment variable's silence is not "
        "`inert here` for these."
    )
    return 1


def cli() -> NoReturn:
    """Entry point. Anything unforeseen becomes exit 2, never exit 1."""
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as exc:
        fail(f"unexpected {type(exc).__name__}: {exc} -- a bug, not a finding")


if __name__ == "__main__":
    cli()
