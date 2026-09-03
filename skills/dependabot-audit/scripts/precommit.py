#!/usr/bin/env python3
"""Phases 1-4 for a `pre-commit` bump: what a `rev:` actually pins, and what moved.

A hook `rev:` is a git ref on **someone else's repository**, and that is the whole
difficulty. It is not a hash, so there is no artifact to verify; and it is not the
tool's version either, so a per-package currency check answers a question nobody
asked. Three things have to be derived before any other phase has an input:

  pin          what the `rev:` resolves to, and whether it is immutable. A 40-hex
               SHA is; a tag is not, and `pre-commit autoupdate` writes tags. Same
               question `references/actions.md` asks of a `uses:` pin
  requirement  the tool version the hook repo *installs* at that rev, read from
               its packaging metadata. `mirrors-mypy` pins `mypy==2.3.1` in
               `setup.py`; `ruff-pre-commit` pins `ruff==0.16.5` in
               `pyproject.toml`. THIS is the dependency, and it is what Phases 2
               and 3 query -- which puts them back on covered ground
  hooks        `.pre-commit-hooks.yaml` at both revs, diffed field by field

**The third is the one that earns this script.** Measured on `ruff-pre-commit`
v0.16.2 -> v0.16.5: `ruff-format`'s `types_or` gained `markdown`, so the hook began
rewriting every Markdown file in the repository. `ruff` itself did not change in
any way a changelog reports, and no per-package view of `ruff` could have surfaced
it -- the defect lived entirely in the wrapper. That bump went red in CI on this
repo, and the cause was one word in a list.

Not a lockfile procedure with the names changed. There is no artifact hash here
and this script does not pretend otherwise: `pin` reports immutability, never
integrity. Phase 1's boundary language applies to the difference, and
`references/pre-commit.md` says which claims survive it.

Exit status: 0 = nothing to report, 1 = a behavioural field moved or the rev and
the requirement disagree, 2 = could not run. A cosmetic field moving is reported
and is not a finding; a mutable pin is reported and is not one either, because
every `pre-commit` pin is a tag and a signal that fires on every run is one
nobody reads.

    precommit.py --repo OWNER/NAME --from REV --to REV [--hook ID]... [--json]

Requires Python 3.11+ (tomllib), `gh`, and network access.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
import tomllib
from typing import Any, NoReturn

TIMEOUT = 60

SHA = re.compile(r"^[0-9a-f]{40}$")

# A tag shaped like a dotted version, which is a tag making a claim about one.
VERSIONISH = re.compile(r"^\d+(\.\d+)+")


def _pyproject(text: str) -> list[str]:
    return [str(d) for d in (tomllib.loads(text).get("project") or {}).get("dependencies") or []]


# Where a hook repo records the tool version it installs, **in the order tried**,
# and the order is load-bearing: a repo carrying both gets the modern one. Both
# shapes are live -- `ruff-pre-commit` ships the first, every `pre-commit/
# mirrors-*` repo ships the second.
#
# A tuple of (filename, parser) rather than a tuple of names beside a hardcoded
# sequence of reads. The earlier version was the latter, and flipping the
# constant changed nothing: it looked like it drove the lookup and only fed an
# error message. A mutation run is what showed that, by passing.
PACKAGING: tuple[tuple[str, Any], ...] = ()

HOOKS_FILE = ".pre-commit-hooks.yaml"

# Fields that change **what runs**. Everything else is presentation.
#
# An unknown field counts as behavioural, deliberately: `pre-commit` gains keys,
# and a new one that selects files or alters the command must not arrive as
# cosmetic because this list predates it. The safe direction is the noisy one.
BEHAVIOURAL = frozenset({
    "entry", "args", "language", "language_version", "files", "exclude",
    "types", "types_or", "exclude_types", "additional_dependencies",
    "pass_filenames", "always_run", "require_serial", "stages", "verbose",
})  # fmt: skip

COSMETIC = frozenset({"id", "name", "description", "alias", "minimum_pre_commit_version"})


class Unparsed(Exception):
    """The hooks file used YAML this parser does not cover.

    Raised rather than guessed. A parser that skips what it does not recognise
    reports "no fields changed" about a file it did not read, which is the
    unverified-verifier failure this plugin exists to argue against -- one level
    down, in a parser rather than a procedure.
    """


def fail(what: str) -> NoReturn:
    """Exit 2. Reserved for "could not run", never for "ran and found something"."""
    print(f"error: {what}", file=sys.stderr)
    raise SystemExit(2)


def _gh(args: list[str]) -> tuple[int, str]:
    """Run `gh`; return (exit code, stdout). The code is the signal.

    `gh` writes an API error body to **stdout** and exits non-zero, so a capture
    that reads stdout alone succeeds and holds an error document. Every caller
    checks the code.
    """
    try:
        proc = subprocess.run(  # noqa: S603
            ["gh", *args],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
            check=False,
        )
    except FileNotFoundError:
        fail("`gh` is not on PATH")
    except subprocess.TimeoutExpired:
        return 1, ""
    return proc.returncode, proc.stdout


def read_at(repo: str, path: str, rev: str) -> str | None:
    """One file's bytes at one rev, or None when it is not there.

    `Accept: application/vnd.github.raw` rather than the base64 `content` field:
    a file over the contents endpoint's inline limit returns *no* `content` at
    all, and a decoder handed the empty string produces the empty file rather
    than an error.
    """
    code, out = _gh([
        "api", f"repos/{repo}/contents/{path}?ref={rev}",
        "-H", "Accept: application/vnd.github.raw",
    ])  # fmt: skip
    return None if code else out


def resolve(repo: str, rev: str) -> dict[str, Any]:
    """What a `rev:` points at, and whether anyone can move it.

    Three states. `immutable` is a 40-hex SHA written into the config, which is
    what `pre-commit autoupdate --freeze` produces; `mutable` is a tag or branch,
    which is what it writes by default and what every ordinary config carries;
    `underivable` is a ref that would not resolve, which is a finding of its own
    -- a pin naming nothing is not a pin.
    """
    code, out = _gh(["api", f"repos/{repo}/commits/{rev}", "--jq", ".sha"])
    sha = out.strip()
    if code or not SHA.match(sha):
        return {"rev": rev, "sha": "", "pin": "underivable",
                "why": f"`{rev}` did not resolve to a commit on {repo}"}  # fmt: skip
    if SHA.match(rev):
        return {"rev": rev, "sha": sha, "pin": "immutable",
                "why": "the config pins a commit SHA, which nobody can repoint"}  # fmt: skip
    return {"rev": rev, "sha": sha, "pin": "mutable",
            "why": f"`{rev}` is a name, and a name can be repointed at another commit"}  # fmt: skip


def requirement(
    repo: str, rev: str, hooks: dict[str, dict[str, str]] | None = None
) -> dict[str, Any]:
    """The tool version the hook repo installs at this rev.

    Read from packaging metadata and never from the tag. They agree by convention
    and the convention is not enforced: the tag is what the config says, the
    requirement is what `pre-commit` will install, and a Phase 2 currency check
    run against the wrong one of those is answering about a different artifact.
    """
    for name, parse in PACKAGING:
        text = read_at(repo, name, rev)
        if text is None:
            continue
        try:
            found = parse(text)
        except (SyntaxError, tomllib.TOMLDecodeError) as exc:
            return {"source": name, "specs": [], "state": "underivable",
                    "why": f"{name} did not parse: {exc}"}  # fmt: skip
        if found:
            return _specs(name, found)

    # Last, and it is a different kind of answer. A node mirror carries no Python
    # packaging at all and pins its tool in the hook's own
    # `additional_dependencies`, npm-style. Deriving it is right; calling it
    # covered is not, which is what `language` is reported for.
    if hooks is not None:
        pinned = [
            item
            for hook in hooks.values()
            for item in _flow(hook.get("additional_dependencies", ""))
        ]
        if pinned:
            return _specs(f"{HOOKS_FILE}:additional_dependencies", pinned)

    return {
        "source": "", "specs": [], "state": "underivable",
        "why": f"no {' or '.join(n for n, _ in PACKAGING)} at {rev} declared a dependency, and no "
               "hook pinned one in `additional_dependencies` — the tool version this "
               "hook installs was not established, so a currency or advisory check "
               "would be about the tag alone",
    }  # fmt: skip


def _install_requires(source: str) -> list[str] | None:
    """`install_requires=[...]` out of a `setup.py`, by AST and not by regex.

    Every `pre-commit/mirrors-*` repo is this file and nothing else, so the
    keyword is reachable without executing anything. A regex over the same text
    matches the string in a comment as readily as the argument.
    """
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        for kw in node.keywords:
            if kw.arg != "install_requires" or not isinstance(kw.value, ast.List):
                continue
            out = [e.value for e in kw.value.elts if isinstance(e, ast.Constant)]
            if len(out) != len(kw.value.elts):
                return None  # a computed entry: not established, not empty
            return [str(v) for v in out]
    return None


PACKAGING = (("pyproject.toml", _pyproject), ("setup.py", _install_requires))


def _flow(value: str) -> list[str]:
    """The entries of an inline `[a, b]`, or nothing. Never a partial read."""
    inner = value.strip()
    if not (inner.startswith("[") and inner.endswith("]")):
        return []
    return [_scalar(item) for item in inner[1:-1].split(",") if item.strip()]


def _specs(source: str, raw: list[str]) -> dict[str, Any]:
    """Split `name==version` requirements, keeping anything that is not one.

    A requirement pinned with `>=` or a marker is reported as `loose` rather than
    dropped: it means the installed version is not decided by this rev at all,
    which changes what Phase 2 can claim and is the opposite of a missing answer.
    """
    specs = []
    for item in raw:
        # `==` is PyPI's and `@` is npm's. Both are read; only the first is on
        # ground this plugin covers, which is why `language` travels with them.
        got = re.match(r"^\s*(@?[A-Za-z0-9._/-]+?)\s*(?:==|@)\s*([^\s;]+)\s*$", item)
        specs.append(
            {"raw": item, "name": got.group(1), "version": got.group(2)}
            if got
            else {"raw": item, "name": "", "version": ""}
        )
    exact = [s for s in specs if s["version"]]
    return {
        "source": source,
        "specs": specs,
        "state": "derived" if exact else "loose",
        "why": (
            f"{source} pins " + ", ".join(f"{s['name']} {s['version']}" for s in exact)
            if exact
            else f"{source} declares {raw}, none of them an `==` pin — the version "
            "installed is not decided by this rev"
        ),
    }


def parse_hooks(text: str) -> dict[str, dict[str, str]]:
    """`.pre-commit-hooks.yaml` -> {hook id: {field: normalised value}}.

    A deliberately small grammar, and it **raises** on anything outside it rather
    than skipping. Covers what the real files use, verified against four live
    mirrors that disagree on every cosmetic detail:

        - id: x            two-space continuation      (ruff-pre-commit)
        -   id: x          four-space continuation     (mirrors-mypy)
        'types_or': [a]    quoted keys                 (mirrors-mypy, mirrors-prettier)
        description:       a folded scalar on the      (black-pre-commit-mirror)
          "..."           following line

    Values are kept as **strings**, normalised for whitespace and one layer of
    matching outer quotes, and never interpreted. Comparing two of them answers
    "did this field move", which is the whole question -- and it cannot misread a
    flow sequence, because it never reads one.
    """
    items: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    column: int | None = None
    pending: str | None = None
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.split(" #")[0].rstrip() if " #" in raw else raw.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        body = line.lstrip()
        if body.startswith("- "):
            after = body.lstrip("- ")
            # Where the item's *content* starts, which is the column its later
            # fields line up on. `- id:` and `-   id:` both occur in the wild and
            # the difference is only how far this is from the dash.
            column = indent + len(body) - len(after)
            current, pending = {}, None
            items.append(current)
        elif current is None or column is None:
            raise Unparsed(f"line {number}: content before any `- ` item")
        elif indent > column:
            if not pending:
                raise Unparsed(f"line {number}: indented under `{body[:24]}` with no folded key")
            current[pending] = _scalar(f"{current[pending]} {body}")
            continue
        elif indent < column:
            raise Unparsed(f"line {number}: dedent to {indent} inside an item opening at {column}")
        else:
            after = body
        key, value = _field(after, number)
        current[key] = value
        pending = key if not value else None

    hooks: dict[str, dict[str, str]] = {}
    for number, item in enumerate(items, 1):
        if "id" not in item:
            raise Unparsed(f"item {number} has no `id`, so nothing can be compared against it")
        hooks[item["id"]] = item
    return hooks


def _field(text: str, number: int) -> tuple[str, str]:
    key, sep, value = text.partition(":")
    if not sep:
        raise Unparsed(f"line {number}: `{text}` is not `key: value`")
    return _scalar(key), _scalar(value)


def _scalar(text: str) -> str:
    """Collapse whitespace, then strip one layer of matching outer quotes.

    The quote strip is what stops `name: mypy` -> `name: 'mypy'` being reported as
    a behaviour change; `mirrors-mypy` made exactly that edit between v1.18.2 and
    v2.3.1. One layer only, and only when both ends match, so a value that is
    genuinely quoted-inside-quoted survives visibly.
    """
    out = " ".join(text.split())
    if len(out) > 1 and out[0] == out[-1] and out[0] in "\"'":
        out = out[1:-1]
    return out


def compare(before: dict[str, dict[str, str]], after: dict[str, dict[str, str]],
            wanted: list[str]) -> list[dict[str, Any]]:  # fmt: skip
    """Field-level differences per hook, ranked by whether they change what runs."""
    ids = [h for h in dict.fromkeys([*before, *after]) if not wanted or h in wanted]
    rows = []
    for hook in ids:
        old, new = before.get(hook), after.get(hook)
        if old is None or new is None:
            rows.append({
                "hook": hook, "field": "", "before": "", "after": "",
                "kind": "behavioural",
                "note": "the hook was added" if old is None else "THE HOOK WAS REMOVED",
            })  # fmt: skip
            continue
        for field in dict.fromkeys([*old, *new]):
            was, now = old.get(field, ""), new.get(field, "")
            if was == now:
                continue
            rows.append({
                "hook": hook, "field": field, "before": was, "after": now,
                "kind": "cosmetic" if field in COSMETIC else "behavioural",
                "note": "",
            })  # fmt: skip
    return rows


def audit(repo: str, old_rev: str, new_rev: str, wanted: list[str]) -> dict[str, Any]:
    report: dict[str, Any] = {
        "repo": repo,
        "from": resolve(repo, old_rev),
        "to": resolve(repo, new_rev),
        "hooks": [],
        "hooks_state": "derived",
        "why": "",
        "language": "",
    }
    # Hooks first, because the requirement may have to come out of them: a node
    # mirror carries no Python packaging and pins its tool in the hook itself.
    parsed: dict[str, dict[str, dict[str, str]]] = {}
    texts = {}
    for end, rev in (("from", old_rev), ("to", new_rev)):
        got = read_at(repo, HOOKS_FILE, rev)
        if got is None:
            report["hooks_state"] = "underivable"
            report["why"] = f"no {HOOKS_FILE} at {rev} — nothing to compare"
            break
        texts[end] = got
    if report["hooks_state"] == "derived":
        try:
            parsed = {end: parse_hooks(text) for end, text in texts.items()}
            report["hooks"] = compare(parsed["from"], parsed["to"], wanted)
        except Unparsed as exc:
            # The raw text still reaches the reader. A refusal that also withholds
            # the evidence turns a degraded answer into no answer.
            parsed = {}
            report["hooks_state"] = "underivable"
            report["why"] = f"{HOOKS_FILE} used YAML this parser does not cover — {exc}"
            report["raw"] = texts

    for end, rev in (("from", old_rev), ("to", new_rev)):
        report[f"requirement_{end}"] = requirement(repo, rev, parsed.get(end))
    # Which registry the requirement lives in, and therefore whether Phases 2 and
    # 3 are on covered ground for it. `language: python` is PyPI, which
    # `audit.py` and `changelog.py` verify end to end; anything else is the
    # boundary again, one layer in, and has to be reported as one.
    languages = {h.get("language", "") for h in (parsed.get("to") or {}).values()}
    report["language"] = ", ".join(sorted(x for x in languages if x))
    return report


def findings(report: dict[str, Any]) -> list[str]:
    """What makes this exit 1. Deliberately short, and deliberately not "changed"."""
    out = [
        f"{r['hook']}.{r['field'] or '(hook)'}: {r['before'] or '(absent)'} -> "
        f"{r['after'] or '(absent)'}"
        for r in report["hooks"]
        if r["kind"] == "behavioural"
    ]
    # The tag and the packaging pin are two different claims about one bump, and
    # nothing enforces that they agree. Where they disagree, what `pre-commit`
    # installs is not what the config appears to say.
    for end in ("from", "to"):
        # `removeprefix` and not `lstrip("v")`, which strips every leading `v`.
        rev = report[end]["rev"].removeprefix("v")
        exact = [s for s in report[f"requirement_{end}"]["specs"] if s["version"]]
        # Only where the tag *claims* a version. A branch name, a SHA pin, or a
        # mirror whose tags do not track the tool at all would otherwise disagree
        # on every bump, and a check that fires every time is one nobody reads.
        # The failure this catches is narrower and real: a tag that looks like it
        # names the version while the repo installs a different one, which a
        # reader takes at face value because it looks authoritative.
        if exact and VERSIONISH.match(rev) and not any(s["version"] == rev for s in exact):
            out.append(
                f"rev `{report[end]['rev']}` does not match what it installs "
                f"({', '.join(s['name'] + ' ' + s['version'] for s in exact)})"
            )
    return out


def render(report: dict[str, Any]) -> None:
    print(f"hook repo: {report['repo']}")
    for end in ("from", "to"):
        p = report[end]
        print(f"  {end:<5} {p['rev']:<22} {p['sha'][:9] or '(unresolved)'}  {p['pin'].upper()}")
        print(f"        {p['why']}")
    print()
    for end in ("from", "to"):
        r = report[f"requirement_{end}"]
        print(f"  installs at {end:<5} [{r['state']}] {r['why']}")
    if report["language"]:
        covered = report["language"] == "python"
        where = "PyPI, which Phases 2 and 3 verify end to end" if covered else "NOT PyPI"
        print(f"\n  hook language: {report['language']} — {where}")
        if not covered:
            print("     The requirement above is derived and its registry is not one this")
            print("     plugin covers. Report that boundary rather than querying it by hand:")
            print("     an improvised recipe returns a clean answer, which is the failure")
            print("     Phase 1's boundary language exists for.")
    if report["from"]["pin"] == "mutable" or report["to"]["pin"] == "mutable":
        print("\n  -- The pin is a name, not a SHA. Report that what was audited is what")
        print("     runs today; it is NOT a Hold on this bump, which did not make it so.")
        print("     `pre-commit autoupdate --freeze` writes the SHA form.")

    print(f"\n=== hook definitions: {report['hooks_state'].upper()}")
    if report["hooks_state"] != "derived":
        print(f"    {report['why']}")
        print("    UNDERIVABLE, not 'nothing changed'. The raw file is in --json;")
        print("    read it rather than reporting an unmade comparison as clean.")
    elif not report["hooks"]:
        print("    no field moved on any hook compared")
    else:
        for row in report["hooks"]:
            mark = "!!" if row["kind"] == "behavioural" else "--"
            if row["note"]:
                print(f"  {mark} {row['hook']}: {row['note']}")
                continue
            print(f"  {mark} {row['hook']}.{row['field']}  [{row['kind']}]")
            print(f"       before: {row['before'] or '(absent)'}")
            print(f"       after:  {row['after'] or '(absent)'}")
        print("\n  A behavioural field decides WHICH FILES the hook touches or WHAT it")
        print("  runs on them. Measure the blast radius in the audited repo — the hook")
        print("  says what it now selects, not how much of this repo that is.")

    found = findings(report)
    print(f"\nRESULT: {'NEEDS REVIEW' if found else 'CLEAN'} — {len(found)} finding(s)")
    for line in found:
        print(f"  - {line}")
    print("This is Phases 1 and 4 for the wrapper. The tool it pins is a separate")
    print("audit: hand the requirement above to Phase 2 and Phase 3.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, help="OWNER/NAME of the hook repository")
    parser.add_argument("--from", dest="old", required=True, help="the rev being replaced")
    parser.add_argument("--to", dest="new", required=True, help="the rev proposed")
    parser.add_argument(
        "--hook",
        action="append",
        default=[],
        help="hook id to compare; repeatable. Default: every hook in the file",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if args.repo.count("/") != 1 or not all(args.repo.split("/")):
        fail(f"--repo must be OWNER/NAME, got `{args.repo}`")
    report = audit(args.repo, args.old, args.new, args.hook)
    for end in ("from", "to"):
        if report[end]["pin"] == "underivable":
            fail(report[end]["why"])
    if args.json:
        report["findings"] = findings(report)
        print(json.dumps(report, indent=2))
    else:
        render(report)
    return 1 if findings(report) else 0


def cli() -> NoReturn:
    """Entry point. Anything unforeseen becomes exit 2, never exit 1.

    Exit 1 means a field moved. An unhandled exception exits 1 too, so without
    this a crash reports as a behaviour change on someone else's repository.
    """
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:
        fail(f"{type(exc).__name__}: {exc}")


if __name__ == "__main__":
    cli()
