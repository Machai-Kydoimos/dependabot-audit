#!/usr/bin/env python3
"""Phase 2's rule check for `uv.lock`: does this repo's config run the rule an entry names?

A config line is a claim about a *file*, and the verdict is about the *tool*. When a
changelog entry names a rule this repo disables or never enables, this runs the tool
to settle it instead of reading the config. Until 0.61.0 it was about 9 KB of prose in
`references/uv-lock.md` and 2 KB in `SKILL.md`, and it ran in `$SCRATCH/pr-<N>`, a
worktree that exists only where Phase 4 or 5 runs. Under `--no-execute` it never ran,
and the runs read the config instead (#181). Replayed on 0.60.0, `fpga-board-sim`
#438 said *"The MD013 entries are config-off, but no named run backs that up"*, and
#451 said UP052 *"shouldn't switch on. That's a reading of the config, never
confirmed by running ruff with the rule named."*

What this does instead:

  1. reads the tool's version from `uv.lock` at `--base`, never at the PR's ref. The
     check stays inside `--no-execute` because the tool is the one this repo runs
     today, from PyPI. A version read from the PR's lockfile would let an untrusted
     PR choose what runs here;
  2. writes every regular file at `--ref` under `<scratch>/<ref>-rulecheck` with
     `git cat-file`, as `pipaudit.py` writes its manifests. A symlink or a submodule
     is named and skipped, and a path that climbs out is refused. The files are the
     tool's input, and neither tool executes them;
  3. asks the tool's own `rule` command whether the rule exists at that version, so
     a code added or renamed later is refused by name instead of read as silence;
  4. runs the tool over the tree under this config twice, as it is and with the
     rule named, and counts the rule by the code in its JSON:

       this config   this config, nothing named     the tree
       forced on     this config, the rule named    the tree

     Where the forced run reports the rule, the tree is the control: the rule fires
     here, under this config, and this config's own run either reports it (live) or
     does not (inert, earned). No input is needed;
  5. only where the tree carries none of the rule, which is every rule a gate that
     fixes on each commit runs, runs an input it fires on:

       control       no config, the rule named      the input
       config state  this config, nothing named     the input, at the tree's root
                     this config, the rule named    the same

     The control proves the instrument fires. The config state answers the
     verdict's question, whether this config runs the rule, and its named run proves
     the input was read under this config at all. The input is the run's, if it gave
     one; otherwise the first example the tool fires on, from the rule's own page:
     ruff's `rule --output-format json`, or rumdl's `docs/<rule>.md` at the tag of
     the version this runs. The first replay pair on 0.61.0's first cut, which asked
     for an input on every rule, never called this script, and the second said why:
     *"Needs each fix's own test input, which I didn't fetch."* On #438's ten rumdl
     rules the pages answered nine; MD065's *"Incorrect"* example is a setext
     heading, which no rule about horizontal rules fires on.

The traps the prose carried, each measured on ruff 0.16.7 and rumdl 0.2.72:

  - **a silent named run and a broken one print the same.** rumdl warns on stderr
    about a rule it does not know and exits 0. ruff exits 0 on a preview rule selected
    without `--preview`, saying only that it "has no effect". So nothing reads as
    inert until the rule has fired, on the tree or on the input, and a rule ruff's
    `rule` command calls preview gets `--preview` in every run that names it;
  - **text output is not a count.** Under `preview = true` ruff names a rule rather
    than coding it, and on #451 `grep -c UP052` counted ruff's own fixture `UP052.py`
    by its filename: 45, where the rule fired 0 times (#197). The JSON's code field
    cannot be read that way;
  - **dropping the config falls back to the tool's defaults**, which leave out most
    rules. Under an allow-list, a run with the config and one without both go
    silent. So every run that asks about the rule names it;
  - **an isolated run is not the gate's file set.** `--isolated` drops the config's
    `exclude` too, so the exposure run the prose prescribed counted files the gate
    never reads. ruff's `--select` on the command line beats the config's `ignore`
    and keeps its `exclude` and `per-file-ignores`. rumdl's `--enable` beats its
    `disable`, and `--extend-enable` does not. So the forced run keeps the config;
  - **a rumdl config can run a command.** It can name one as a code-block tool, and
    `rumdl check` runs it, with `--enable` too: a planted `sh -c` wrote its marker.
    `--no-config` and `--no-code-block-tools` (rumdl 0.2.65 on) stop it. Every rumdl
    run that reads the tree's config passes the flag, and a rumdl that refuses it
    gets no run that reads this config;
  - **clean is not read.** On a tree with no Markdown, rumdl says "No markdown files
    found to check." and exits 0. Each tool's own count of the files it read is
    printed: ruff's `--show-files`, rumdl's summary line.

Nothing here passes `--fix`, and every run passes `--no-cache`, so nothing is written
into the tree or to a `cache-dir` the config names. `uvx --no-config` keeps the
tree's `[tool.uv]` settings out of the fetch. Each run starts in the scratch
directory with an empty `XDG_CONFIG_HOME`, so a user-level config cannot stand in
for the repo's.

Exit status:
  0 = inert here, and earned: the rule fired, on the tree or on the input, and this
      config does not run it. Forced on, the output counts what it would report.
  1 = live here: this config runs the rule. The output counts what it reports.
  2 = underivable: it could not run, the rule fired nowhere, or the tree carries
      none of it and no input was given, so silence here means nothing.

With several `--check`s, 1 outranks 2 and 2 outranks 0, and each rule's own line
says which it is.

    rulecheck.py --scratch DIR --ref pr-<N> --base <merge base> --tool ruff|rumdl \\
        --check <RULE>[=<an input it fires on>] [--check ...]

Requires Python 3.11+ (tomllib), `git`, `uv`, `gh` for rumdl's pages, and network
access: `uvx`, PyPI, and GitHub.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import NoReturn

TIMEOUT = 300
TOOLS = ("ruff", "rumdl")
CODE = re.compile(r"^[A-Za-z]+[0-9]+$")
VERSION = re.compile(r"^[0-9][0-9A-Za-z.+!-]*$")
# Where each tool's JSON puts the rule and the file.
FIELDS = {"ruff": ("code", "filename"), "rumdl": ("rule", "file")}
# rumdl's summary line: `... in 36/41 files (13ms)` or `... in 41 files (27ms)`.
SUMMARY = re.compile(r"\bin (?:(\d+)/)?(\d+) files?\b")
NO_MARKDOWN = "No markdown files found"
ANSI = re.compile(r"\x1b\[[0-9;]*m")
NO_CODE_BLOCK_TOOLS = "--no-code-block-tools"
MAX_SHOWN = 8
# Where each tool documents a rule, and the fences its examples are written in. ruff
# carries the page in the binary (`ruff rule --output-format json`); rumdl keeps it in
# its repository, read here at the tag of the version this runs.
LANGS = {"ruff": {"python", "py"}, "rumdl": {"markdown", "md"}}
SUFFIX = {"ruff": ".py", "rumdl": ".md"}
RUMDL_DOCS = "repos/rvben/rumdl/contents/docs/{page}.md?ref=v{version}"
MAX_EXAMPLES = 20
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})[ \t]*([\w+-]*)")

LIVE, INERT, UNDERIVABLE = "LIVE HERE", "INERT HERE", "UNDERIVABLE"


def fail(what: str) -> NoReturn:
    """Exit 2. Reserved for "could not run", never for "ran and found something"."""
    print(f"error: {what}", file=sys.stderr)
    raise SystemExit(2)


def first_line(text: str) -> str:
    for line in text.splitlines():
        if line.strip():
            return line.strip()[:240]
    return ""


def run(
    argv: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    stdin: bytes | None = None,
) -> subprocess.CompletedProcess[bytes]:
    """Run a command; bytes out, so a file is written back exactly as git holds it."""
    try:
        return subprocess.run(  # noqa: S603
            argv,
            cwd=cwd,
            env=env,
            input=stdin,
            capture_output=True,
            check=False,
            timeout=TIMEOUT,
        )
    except FileNotFoundError:
        fail(f"`{argv[0]}` is not on PATH")
    except subprocess.TimeoutExpired:
        fail(f"`{' '.join(argv[:3])}` exceeded {TIMEOUT}s")


def text_of(proc: subprocess.CompletedProcess[bytes], stream: str = "stdout") -> str:
    return (proc.stdout if stream == "stdout" else proc.stderr).decode("utf-8", "replace")


def normalise(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def fences(text: str, langs: set[str]) -> list[str]:
    """The bodies of the fenced blocks in `text` whose info string is one of `langs`.

    A block closes on a fence of the same character, at least as long, as CommonMark
    reads it, so a page that shows a fence inside an example keeps the example whole.
    """
    found: list[str] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        opened = FENCE.match(lines[i])
        if opened and opened.group(2).lower() in langs:
            mark, body = opened.group(1), []
            close = re.compile(rf"^ {{0,3}}{re.escape(mark[0])}{{{len(mark)},}}[ \t]*$")
            i += 1
            while i < len(lines) and not close.match(lines[i]):
                body.append(lines[i])
                i += 1
            found.append("\n".join(body) + "\n")
        i += 1
    return found


def pinned(base: str, tool: str) -> str:
    """The one version of `tool` that `uv.lock` pins from a registry at `base`."""
    proc = run(["git", "show", f"{base}:uv.lock"])
    if proc.returncode != 0:
        fail(f"cannot read uv.lock at {base}: {first_line(text_of(proc, 'stderr'))}")
    try:
        data = tomllib.loads(text_of(proc))
    except tomllib.TOMLDecodeError as exc:
        fail(f"uv.lock at {base} is not TOML: {exc}")
    packages = data.get("package")
    if not isinstance(packages, list):
        fail(f"uv.lock at {base} has no [[package]] entries")
    versions = sorted(
        {
            str(p["version"])
            for p in packages
            if isinstance(p, dict)
            and normalise(str(p.get("name", ""))) == tool
            and isinstance(p.get("source"), dict)
            and "registry" in p["source"]
            and "version" in p
        }
    )
    if not versions:
        fail(f"uv.lock at {base[:12]} pins no {tool} from a registry, so this repo does not run it")
    if len(versions) > 1:
        fail(f"uv.lock at {base[:12]} pins {tool} at {', '.join(versions)}; this runs one version")
    if not VERSION.match(versions[0]):
        fail(f"uv.lock at {base[:12]} pins {tool} at {versions[0]!r}, which is not a version")
    return versions[0]


def materialise(ref: str, dest: Path) -> tuple[int, list[str]]:
    """Write every regular file at `ref` under `dest`. (written, skipped)

    One `git cat-file --batch` for all of them. A symlink or a submodule is skipped
    and named: following one would read something other than what the ref holds, and
    a path that is not a plain relative one is refused rather than joined onto `dest`.
    """
    listing = run(["git", "ls-tree", "-r", "-z", "--full-tree", ref])
    if listing.returncode != 0:
        fail(f"cannot list the tree at {ref}: {first_line(text_of(listing, 'stderr'))}")
    wanted: list[tuple[str, str]] = []
    skipped: list[str] = []
    for entry in text_of(listing).split("\0"):
        meta, _, path = entry.partition("\t")
        if not path:
            continue
        pure = PurePosixPath(path)
        mode, kind, sha = [*meta.split(), "", "", ""][:3]
        if kind != "blob" or mode not in ("100644", "100755"):
            skipped.append(f"{path} (mode {mode}, not a regular file)")
        elif pure.is_absolute() or any(part in ("", ".", "..") for part in pure.parts):
            skipped.append(f"{path} (not a plain relative path)")
        else:
            wanted.append((path, sha))
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    if not wanted:
        return 0, skipped
    batch = run(
        ["git", "cat-file", "--batch"], stdin="".join(f"{sha}\n" for _, sha in wanted).encode()
    )
    if batch.returncode != 0:
        fail(f"cannot read the blobs at {ref}: {first_line(text_of(batch, 'stderr'))}")
    data, pos = batch.stdout, 0
    for path, sha in wanted:
        end = data.find(b"\n", pos)
        header = data[pos:end].decode("utf-8", "replace").split() if end >= 0 else []
        if len(header) != 3 or header[0] != sha or header[1] != "blob":
            fail(f"git cat-file did not return {path} at {ref} ({' '.join(header) or 'nothing'})")
        size = int(header[2])
        target = dest.joinpath(*PurePosixPath(path).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data[end + 1 : end + 1 + size])
        pos = end + 1 + size + 1
    return len(wanted), skipped


@dataclass
class Count:
    """What one run reported for one rule. `why` is set when it could not be read."""

    hits: int = 0
    files: set[str] = field(default_factory=set)
    why: str = ""
    said: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.why


@dataclass
class Rule:
    """What the tool's own `rule` command says about a code; `why` is set if it refused."""

    code: str = ""
    preview: bool = False
    name: str = ""
    explanation: str = ""
    why: str = ""


class Tool:
    """One tool at one version, run through `uvx` from the scratch directory."""

    def __init__(self, name: str, version: str, cwd: Path, home: Path) -> None:
        self.name, self.version, self.cwd = name, version, cwd
        self.env = {**os.environ, "XDG_CONFIG_HOME": str(home), "NO_COLOR": "1"}
        self.exe = ["uvx", "--quiet", "--no-config", f"{name}@{version}"]
        self.code_key, self.file_key = FIELDS[name]

    def call(self, *args: str) -> subprocess.CompletedProcess[bytes]:
        return run([*self.exe, *args], cwd=self.cwd, env=self.env)

    def check(self, *args: str) -> list[str]:
        """`check` with what every run here carries: no cache, no fix, no colour."""
        if self.name == "ruff":
            return ["check", "--no-cache", *args]
        return ["check", "--no-cache", "--color", "never", *args]

    def isolated(self) -> list[str]:
        return ["--isolated"] if self.name == "ruff" else ["--no-config"]

    def configured(self) -> list[str]:
        """Flags for a run that reads the tree's config."""
        return [] if self.name == "ruff" else [NO_CODE_BLOCK_TOOLS]

    def named(self, code: str, preview: bool) -> list[str]:
        if self.name == "ruff":
            return [*(["--preview"] if preview else []), "--select", code]
        return ["--enable", code]

    def json(self) -> list[str]:
        return ["--output-format", "json"]

    def count(self, code: str, args: list[str]) -> Count:
        proc = self.call(*self.check(*args, *self.json()))
        # rumdl colours its config warnings even under `--color never` and NO_COLOR.
        err = ANSI.sub("", text_of(proc, "stderr"))
        said = [ln.strip()[:240] for ln in err.splitlines() if "warning" in ln.lower()][:3]
        if proc.returncode not in (0, 1):
            return Count(why=first_line(err) or f"exit {proc.returncode}", said=said)
        try:
            found = json.loads(text_of(proc) or "[]")
        except json.JSONDecodeError as exc:
            return Count(why=f"its JSON did not parse: {exc}", said=said)
        if not isinstance(found, list):
            return Count(why="its JSON is not a list of findings", said=said)
        mine = [f for f in found if isinstance(f, dict) and f.get(self.code_key) == code]
        files = {str(f.get(self.file_key, "")) for f in mine}
        if NO_MARKDOWN in err:
            said.insert(0, first_line(err[err.index(NO_MARKDOWN) :]))
        return Count(hits=len(mine), files=files, said=said)

    def files_read(self, tree: Path) -> tuple[int | None, str]:
        """How many files this config's run reads over the tree, by the tool's own count."""
        if self.name == "ruff":
            proc = self.call(*self.check("--show-files", str(tree)))
            if proc.returncode != 0:
                why = first_line(text_of(proc, "stderr")) or f"exit {proc.returncode}"
                return None, f"ruff --show-files could not list this config's files: {why}"
            return len([ln for ln in text_of(proc).splitlines() if ln.strip()]), ""
        proc = self.call(*self.check(*self.configured(), str(tree)))
        out, err = ANSI.sub("", text_of(proc)), ANSI.sub("", text_of(proc, "stderr"))
        if NO_MARKDOWN in out + err:
            return 0, ""
        if proc.returncode not in (0, 1):
            why = first_line(err) or f"exit {proc.returncode}"
            return None, f"rumdl could not run over the tree with this config: {why}"
        lines = [m for ln in (out + err).splitlines() if (m := SUMMARY.search(ln))]
        if not lines:
            return None, "rumdl printed no summary line, so how many files it read is not known"
        return int(lines[-1].group(2)), ""

    def rule(self, code: str) -> Rule:
        """The code as this version names it, from the tool's own `rule` command."""
        flag = "--output-format" if self.name == "ruff" else "-o"
        proc = self.call("rule", code, flag, "json")
        err = text_of(proc, "stderr")
        if proc.returncode != 0:
            said = first_line(err) or first_line(text_of(proc)) or f"exit {proc.returncode}"
            return Rule(
                why=(
                    f"{self.name} {self.version}, the version this repo runs today, has no rule "
                    f"{code} ({said}). Whether the PR's version runs it under this config is "
                    "Phase 4's question"
                )
            )
        try:
            info = json.loads(text_of(proc))
        except json.JSONDecodeError:
            return Rule(why=f"`{self.name} rule {code}` did not print JSON")
        if isinstance(info, list) and info:
            info = info[0]
        if not isinstance(info, dict) or not isinstance(info.get("code"), str):
            return Rule(why=f"`{self.name} rule {code}` named no code")
        return Rule(
            code=info["code"],
            preview=bool(info.get("preview")),
            name=str(info.get("name", "")),
            explanation=str(info.get("explanation") or ""),
        )

    def examples(self, rule: Rule) -> tuple[list[str], str]:
        """(the examples the tool documents for this rule, where they came from)."""
        if self.name == "ruff":
            where = f"ruff {self.version}'s own page for {rule.code}"
            return fences(rule.explanation, LANGS["ruff"])[:MAX_EXAMPLES], where
        page = rule.code.lower()
        where = f"rumdl's docs/{page}.md at v{self.version}"
        url = RUMDL_DOCS.format(page=page, version=self.version)
        proc = run(["gh", "api", "-H", "Accept: application/vnd.github.raw", url])
        if proc.returncode != 0:
            return [], f"{where}, which did not come back ({first_line(text_of(proc, 'stderr'))})"
        return fences(text_of(proc), LANGS["rumdl"])[:MAX_EXAMPLES], where


def counted(c: Count, total: int | None = None, *, tree: bool = False) -> str:
    """One run's cell: `fires: N` or `silent` on the control, `N in M of K` on the tree."""
    if not c.ok:
        return c.why if c.why.startswith("not run") else f"could not run: {c.why}"
    shown = "?" if total is None else str(total)
    if not tree:
        return f"fires: {c.hits} finding(s)" if c.hits else "silent"
    if not c.hits:
        return f"0 in {shown} file(s)"
    return f"{c.hits} in {len(c.files)} of {shown} file(s)"


def reading(
    code: str,
    configured: Count,
    forced: Count,
    total: int | None,
    why_total: str,
    given: bool,
    control: Count,
    state: Count,
    vouch: Count,
    hint: str,
    tried: str = "",
) -> tuple[str, str]:
    """(LIVE | INERT | UNDERIVABLE, the sentence): the tree first, then the input."""
    if total is None:
        return UNDERIVABLE, why_total
    if total == 0:
        return UNDERIVABLE, "this config's run read no files, so its silence is not clean"
    for label, c in (("this config", configured), ("forced on", forced)):
        if not c.ok:
            return UNDERIVABLE, f"the {label} run could not run: {c.why}"
    if configured.hits:
        return LIVE, (
            f"this config runs {code}: it reports {configured.hits} in "
            f"{len(configured.files)} of {total} file(s)"
        )
    read = given and control.hits > 0 and state.ok and vouch.ok and vouch.hits > 0
    if forced.hits:
        # The tree is the control: the rule fires on it under this config when named.
        if read and state.hits:
            return LIVE, (
                f"this config runs {code} at the tree's root, and not over the "
                f"{len(forced.files)} file(s) that carry it: a config below the root, or "
                "per-file settings, differ"
            )
        return INERT, (
            f"this config does not run {code}. Forced on, it reports {forced.hits} in "
            f"{len(forced.files)} of {total} file(s)"
        )
    # The tree carries none of it, so only an input can say what this config does.
    if not given:
        return UNDERIVABLE, (
            f"forced on, the tree carries none of {code}{tried}, so whether this config "
            f"runs it takes an input it fires on: --check {code}=<file>"
        )
    if not control.ok:
        return UNDERIVABLE, f"the control could not run: {control.why}"
    if not control.hits:
        return UNDERIVABLE, f"the control did not fire, so silence here means nothing: {hint}"
    if not read:
        return UNDERIVABLE, (
            f"whether this config runs {code} is not read: named, it does not fire on the "
            f"input under this config ({vouch.why or 'silent'}), and the tree carries none"
        )
    if state.hits:
        return LIVE, f"this config runs {code}; the tree carries none of it today"
    return INERT, (
        f"this config does not run {code}, and forced on the tree carries none of it either"
    )


def control_hint(tool: Tool, code: str, control: Count) -> str:
    said = " ".join(control.said)
    if "Unknown rule" in said:
        return f"{tool.name} does not know {code} ({said})"
    if "has no effect" in said:
        return f"{tool.name} did not run {code} ({said})"
    if NO_MARKDOWN in said:
        return "rumdl read no Markdown: give the control a .md name"
    return (
        f"the input carries no {code} violation that {tool.name} {tool.version} reports. "
        "The fix's own test has one"
    )


def parse_checks(raw: list[str]) -> list[tuple[str, Path | None]]:
    checks: list[tuple[str, Path | None]] = []
    for item in raw:
        code, sep, path = item.partition("=")
        code = code.strip()
        if not CODE.match(code) or (sep and not path.strip()):
            fail(
                "--check takes <RULE> or <RULE>=<file>, as MD013 or "
                f"MD013=$SCRATCH/md013.md; got {item!r}"
            )
        control = Path(path.strip()) if sep else None
        if control is not None and (not control.is_file() or control.stat().st_size == 0):
            fail(f"the input for {code} is not a non-empty file: {control}")
        checks.append((code.upper(), control))
    if len({code for code, _ in checks}) != len(checks):
        fail("each rule takes one --check")
    return checks


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 2's rule check, over the PR at its ref")
    parser.add_argument("--scratch", required=True, help="$SCRATCH from the Phase 0 handoff")
    parser.add_argument("--ref", required=True, help="the PR's ref, pr-<N>")
    parser.add_argument("--base", required=True, help="$BASE_SHA: uv.lock's pin is read here")
    parser.add_argument("--tool", required=True, choices=TOOLS)
    parser.add_argument(
        "--check",
        required=True,
        action="append",
        metavar="RULE[=FILE]",
        help="a rule; and, where the tree carries none of it, an input it fires on",
    )
    args = parser.parse_args()

    scratch = Path(args.scratch).resolve()
    if not scratch.is_dir():
        fail(f"{scratch} does not exist -- re-derive $SCRATCH, or Phase 0 never ran")
    checks = parse_checks(args.check)
    version = pinned(args.base, args.tool)

    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", args.ref)
    tree = scratch / f"{stem}-rulecheck"
    controls = scratch / f"{stem}-rulecheck-controls"
    home = scratch / f"{stem}-rulecheck-xdg"
    written, skipped = materialise(args.ref, tree)
    for where in (controls, home):
        if where.exists():
            shutil.rmtree(where)
        where.mkdir(parents=True)

    tool = Tool(args.tool, version, scratch, home)
    said = tool.call("--version")
    if said.returncode != 0 or version not in text_of(said).split():
        why = first_line(text_of(said, "stderr")) or first_line(text_of(said))
        fail(f"`{' '.join(tool.exe)} --version` did not answer {version}: {why}")

    head = run(["git", "rev-parse", "--short=12", args.ref])
    print(
        f"rulecheck: {args.tool} {version}, the version uv.lock pins at the base "
        f"{args.base[:12]} (never the PR's)"
    )
    print(f"  tree: {args.ref} at {text_of(head).strip() or '?'}, {written} regular file(s)")
    print(f"        -> {tree}")
    for line in skipped[:MAX_SHOWN]:
        print(f"  not written: {line}")
    if len(skipped) > MAX_SHOWN:
        print(f"  not written: ... and {len(skipped) - MAX_SHOWN} more")

    refused = ""
    if args.tool == "rumdl":
        helped = tool.call("check", "--help")
        if NO_CODE_BLOCK_TOOLS not in text_of(helped):
            refused = (
                f"rumdl {version} has no {NO_CODE_BLOCK_TOOLS} (it arrived in 0.2.65), and "
                "a rumdl config can name any command as a code-block tool, so no run here "
                "reads this tree's config"
            )
            print(f"  {refused}")
        else:
            print(f"  every run that reads this tree's config passes {NO_CODE_BLOCK_TOOLS}")
    total, why_total = (None, refused) if refused else tool.files_read(tree)
    if total is not None:
        print(f"  this config reads {total} file(s) over the tree")
    elif not refused:
        print(f"  {why_total}")

    results: list[tuple[str, str]] = []
    for code, source in checks:
        print()
        rule = tool.rule(code)
        if rule.why:
            print(f"{code}: {UNDERIVABLE} -- {rule.why}")
            results.append((UNDERIVABLE, code))
            continue
        canonical, preview, name = rule.code, rule.preview, rule.name
        named = tool.named(canonical, preview)
        off = Count(why="not run: no run here reads this tree's config")
        if refused:
            configured = forced = off
        else:
            configured = tool.count(canonical, [*tool.configured(), str(tree)])
            forced = tool.count(canonical, [*tool.configured(), *named, str(tree)])
        first = state = vouch = Count(why="not run: no input given")
        here = controls / canonical
        here.mkdir(parents=True, exist_ok=True)
        control: Path | None = None
        origin = tried = ""
        if source is not None:
            control = here / source.name
            shutil.copyfile(source, control)
            first = tool.count(canonical, [*tool.isolated(), *named, str(control)])
            origin = "the input given"
        elif not refused and configured.ok and forced.ok and not forced.hits:
            # The tree carries none of it, so it cannot be the control: try what the
            # tool documents for the rule, and keep the first example it fires on.
            examples, page = tool.examples(rule)
            for number, body in enumerate(examples, start=1):
                candidate = here / f"example-{number}{SUFFIX[tool.name]}"
                candidate.write_text(body, encoding="utf-8")
                fired = tool.count(canonical, [*tool.isolated(), *named, str(candidate)])
                if fired.ok and fired.hits:
                    control, first = candidate, fired
                    origin = f"example {number} of {len(examples)} in {page}"
                    break
            else:
                tried = (
                    f", and none of the {len(examples)} example(s) in {page} fires"
                    if examples
                    else f", and {page} shows no example"
                )
        if control is not None:
            state = vouch = off
            if not refused:
                # At the tree's root, so this config is the one that reads it; written
                # only after the tree runs above, and removed before the next rule's.
                rooted = tree / f"rulecheck-control-{canonical}{control.suffix}"
                while rooted.exists():
                    rooted = rooted.with_name(f"_{rooted.name}")
                shutil.copyfile(control, rooted)
                try:
                    state = tool.count(canonical, [*tool.configured(), str(rooted)])
                    vouch = tool.count(canonical, [*tool.configured(), *named, str(rooted)])
                finally:
                    rooted.unlink()

        verdict, sentence = reading(
            canonical,
            configured,
            forced,
            total,
            why_total,
            control is not None,
            first,
            state,
            vouch,
            control_hint(tool, canonical, first),
            tried,
        )
        label = f"{canonical} ({name})" if name else canonical
        flags = " ".join(named)
        alone = " ".join([*tool.isolated(), *named])
        print(f"{label}{', preview' if preview else ''}")
        rows = [
            ("this config", "this config", "the tree", counted(configured, total, tree=True)),
            ("forced on", f"this config, {flags}", "the tree", counted(forced, total, tree=True)),
        ]
        runs = [configured, forced]
        if control is not None:
            rows += [
                ("control", alone, "the input", counted(first)),
                ("config state", "this config", "the input", counted(state)),
                ("", f"this config, {flags}", "the input", counted(vouch)),
            ]
            runs += [first, state, vouch]
        wide = max(len(how) for _, how, _, _ in rows)
        for run_name, how, what, result in rows:
            print(f"  {run_name:<13} {how:<{wide}}  {what:<10} {result}")
        if origin:
            print(f"  the input: {origin}")
        heard: list[str] = []
        for c in runs:
            for line in c.said:
                line = line.replace(str(tree), "<tree>").replace(str(controls), "<controls>")
                if line not in heard:
                    heard.append(line)
                    print(f"  {args.tool} said: {line}")
        print(f"{canonical}: {verdict} -- {sentence}")
        results.append((verdict, canonical))

    print()
    for verdict in (LIVE, UNDERIVABLE, INERT):
        codes = [c for v, c in results if v == verdict]
        if codes:
            print(f"RESULT: {verdict} -- {', '.join(codes)}")
    verdicts = {v for v, _ in results}
    return 1 if LIVE in verdicts else 2 if UNDERIVABLE in verdicts else 0


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
