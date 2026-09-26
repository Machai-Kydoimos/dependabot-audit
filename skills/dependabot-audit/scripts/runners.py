#!/usr/bin/env python3
"""Phase 4's Row 3 for GitHub Actions: which runner every job lands on, at a ref.

`actions.md` § Phase 4 reads an environment variable's silence as `inert here` only
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


def _scalar(text: str) -> Any:
    text = text.strip()
    if not text:
        return None
    if text[0] in "&*!" or text.startswith("<<"):
        raise Unreadable(f"anchor, alias, tag or merge key: {text[:40]}")
    if text[0] == '"':
        if not text.endswith('"') or len(text) < 2:
            raise Unreadable(f"unterminated string: {text[:40]}")
        return re.sub(r"\\(.)", r"\1", text[1:-1])
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
                value, i = _block(lines, nxt, _indent(lines.clean[nxt]))
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
                value, i = _block(lines, nxt, _indent(lines.clean[nxt]))
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
    # A plain scalar may continue on deeper lines that are not entries of their own.
    j = i + 1
    parts = [rest]
    while True:
        nxt = lines.next_content(j)
        if nxt >= len(lines.clean) or _indent(lines.clean[nxt]) <= indent:
            break
        if rest[:1] in "'\"":
            raise Unreadable(f"a quoted scalar continues past line {i + 1}")
        parts.append(lines.clean[nxt].strip())
        j = nxt + 1
    return _scalar(" ".join(parts)), j


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
    for entry in matrix.get("include") or []:
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
    for path, text in files:
        try:
            document = load(text)
        except Unreadable as exc:
            counts["underivable"] += 1
            lines.append(f"  underivable  {path}: the file is unreadable here -- {exc}")
            continue
        for name, job in jobs(document):
            state, detail = classify(job, args.repo)
            counts[state] += 1
            mark = {
                "hosted": "hosted     ",
                "not hosted": "NOT HOSTED ",
                "underivable": "underivable",
            }
            lines.append(f"  {mark[state]}  {path} {name}: {detail}")

    print(f"runners at {args.ref}, {len(files)} workflow file(s), as {args.repo}:")
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
