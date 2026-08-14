#!/usr/bin/env python3
"""Differential gate runner: what does a version bump change about this repo?

Phase 4 asks whether a bump changes what the repo's gates *accept*. The obvious
way to answer that is to read the changelog for added rules and reason about
whether the repo's config is an allow-list or a disable-list. Both steps are
predictions, and both are where the answer goes wrong.

This measures instead. It runs the same gate once per version in a disposable
worktree and compares what each run *did to the files*, not what it printed.

Three findings drove that choice, all observed rather than assumed:

  * Exit codes are not enough. ruff 0.15.22 and 0.16.2 both exit 0 on a repo
    that is already compliant, while 0.16 formats 33 more files than 0.15 --
    the gate stays green and the scope moves underneath it.
  * Output text is not comparable across versions. 0.15 prints
    "Would reformat: mod.py"; 0.16 prints an annotated diff. The renderer
    changed, so a line-set diff reports everything as different.
  * What the tool *touches* is stable and comparable. {mod.py} vs
    {doc.md, mod.py} is the finding, in any output format.

So: run each version in write mode, snapshot the files it changed and their
contents, and report the delta between runs.

    only in the newer run        widened scope, or a rule that now fires
    only in the older run        narrowed scope
    both, different content      the fix itself behaves differently

The last is the one no vulnerability feed reports: a formatter that used to
delete something and no longer does, or vice versa, in a mode many repos run
automatically on every commit.

Usage:
    gate_diff.py --tree DIR --run LABEL CMD [--run LABEL CMD ...] [--json]

The first --run is the baseline; every later one is compared against it. Give
the tool's *write* mode where it has one (`ruff format .`, not
`ruff format --check .`) -- the whole measurement is what it does to the tree.
For a gate with no write mode (a type checker, a test suite) the tree delta is
empty and only the exit code and output are available; that is a weaker
measurement and is labelled as such.

Exit status: 0 = the runs agree, 1 = they differ, 2 = could not run.
Requires Python 3.11+ and a clean git worktree. No network of its own.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, NoReturn

DEFAULT_TIMEOUT = 900


def fail(what: str) -> NoReturn:
    """Exit 2 — could not run. Never 1, which means the runs disagreed."""
    print(f"error: {what}", file=sys.stderr)
    raise SystemExit(2)


def _git(tree: Path, *args: str) -> str:
    # S603/S607: a fixed argv with `git` resolved from PATH, the only sane way to
    # invoke it. Each directive has to sit on the line its own diagnostic is
    # reported on — S603 on the call, S607 on the argv — because `ruff check
    # --fix` deletes any code that is not reported exactly there. Grouping them
    # on one line looks tidier and silently loses one of them.
    proc = subprocess.run(  # noqa: S603
        ["git", "-C", str(tree), *args],  # noqa: S607
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if proc.returncode != 0:
        fail(f"git {' '.join(args)} failed in {tree}: {proc.stderr.strip()}")
    return proc.stdout


def require_clean_worktree(tree: Path) -> None:
    """Refuse to touch a tree that has anything to lose.

    Every run mutates the tree and is undone with `reset --hard` + `clean -fd`.
    That restore is only safe if the tree started with nothing uncommitted and
    nothing untracked, so this is the guard that makes the whole approach safe
    to point at a real checkout by mistake. It is also the same check Phase 5
    already makes before reusing a worktree.
    """
    if not (tree / ".git").exists():
        fail(f"{tree} is not a git worktree (no .git); refusing to run")
    dirty = _git(tree, "status", "--porcelain").strip()
    if dirty:
        fail(
            f"{tree} has uncommitted or untracked files; refusing to run.\n"
            "       Every run is undone with `git reset --hard && git clean -fd`,\n"
            "       which would discard them. Use the Phase 5 worktree, or commit\n"
            "       and re-run."
        )


def snapshot_changes(tree: Path) -> dict[str, str]:
    """What the tool just did: changed path -> sha256 of its new content.

    Keyed on content rather than on the diff text so two runs are comparable
    even when they touch the same file in different ways. A deleted file maps to
    a sentinel rather than being dropped, since deleting a file is exactly the
    kind of fix-mode behaviour worth catching.
    """
    changes: dict[str, str] = {}
    for line in _git(tree, "status", "--porcelain", "-z").split("\0"):
        if not line:
            continue
        # porcelain -z: XY then a space then the path, no quoting or renames to
        # unpick for our purposes (a rename shows as delete + add).
        path = line[3:]
        if not path:
            continue
        blob = tree / path
        if not blob.exists():
            changes[path] = "<deleted>"
        elif blob.is_file():
            changes[path] = hashlib.sha256(blob.read_bytes()).hexdigest()
    return changes


def restore(tree: Path) -> None:
    """Undo everything the last run did, including anything it staged.

    `reset --hard` rather than `checkout -- .`: the latter restores the worktree
    *from the index*, so a gate that stages its own edits survives it untouched
    and run two inherits run one's work. `pre-commit` stages directly, and it is
    among the likeliest gate commands this tool is handed.

    `clean -fd` is still needed on top, for untracked files, which `reset --hard`
    leaves alone. Deliberately without `-x`: ignored paths carry state between
    runs — a `.venv` a gate builds holds one version's tool — but
    `require_clean_worktree` gates on `git status --porcelain`, which does not
    list ignored files, so `-x` would delete a `.env` or a virtualenv the guard
    never warned about. Losing something unannounced is the worse failure here.
    """
    _git(tree, "reset", "--hard", "-q")
    _git(tree, "clean", "-fdq")


def run_gate(tree: Path, label: str, command: str, timeout: int) -> dict[str, Any]:
    """One version's run: what it did, what it said, and how it exited."""
    try:
        # S602: `shell=True` is the feature. The command is a gate invocation the
        # operator wrote, not user data — it needs a shell for the redirects and
        # pipes real gate commands contain.
        proc = subprocess.run(  # noqa: S602
            command,
            shell=True,
            cwd=tree,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
            check=False,
        )
        out, code = (proc.stdout or "") + (proc.stderr or ""), proc.returncode
    except subprocess.TimeoutExpired:
        restore(tree)
        fail(f"run '{label}' exceeded {timeout}s: {command}")

    changed = snapshot_changes(tree)
    restore(tree)
    return {"label": label, "command": command, "exit": code, "changed": changed, "output": out}


def compare(base: dict[str, Any], other: dict[str, Any]) -> dict[str, Any]:
    """The three ways a bump can move a gate."""
    a, b = base["changed"], other["changed"]
    return {
        "base": base["label"],
        "other": other["label"],
        "only_in_other": sorted(set(b) - set(a)),
        "only_in_base": sorted(set(a) - set(b)),
        "different_result": sorted(p for p in set(a) & set(b) if a[p] != b[p]),
        "exit_changed": base["exit"] != other["exit"],
        "base_exit": base["exit"],
        "other_exit": other["exit"],
    }


def render(report: dict[str, Any]) -> None:
    print(f"tree: {report['tree']}\n")
    for run in report["runs"]:
        print(f"  {run['label']:<12} exit {run['exit']:<3} touched {len(run['changed'])} file(s)")
        print(f"  {'':<12} {run['command']}")
    print()

    for cmp_ in report["comparisons"]:
        head = f"=== {cmp_['other']} vs {cmp_['base']}"
        print(head)
        if cmp_["exit_changed"]:
            print(
                f"  EXIT   {cmp_['base_exit']} -> {cmp_['other_exit']}  (the gate's answer changed)"
            )
        for path in cmp_["only_in_other"]:
            print(
                f"  +      {path}   acted on by {cmp_['other']} only — widened scope or a new rule"
            )
        for path in cmp_["only_in_base"]:
            print(f"  -      {path}   acted on by {cmp_['base']} only — narrowed scope")
        for path in cmp_["different_result"]:
            print(f"  ~      {path}   both act, different result — the fix itself changed")
        if not any((cmp_["only_in_other"], cmp_["only_in_base"], cmp_["different_result"])):
            print("  no difference in what the gate did to the tree")
            if not cmp_["exit_changed"]:
                print("  (and the exit code is unchanged)")
        print()

    if report["no_write_mode"]:
        print("NOTE: no run changed any file. If these gates have a write mode, this")
        print("      measured the wrong thing — re-run with it (`ruff format .`, not")
        print("      `--check`). If they genuinely have none (a type checker, a test")
        print("      suite), exit code is the only signal here, and it is the weaker one.\n")

    print("RESULT:", "GATES AGREE" if report["agree"] else "GATES DIFFER")
    print("This is the mechanical half of Phase 4. Whether a difference matters")
    print("is a judgment about this repo, and belongs in the report.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tree", required=True, help="clean git worktree to run in")
    parser.add_argument(
        "--run",
        nargs=2,
        action="append",
        metavar=("LABEL", "COMMAND"),
        required=True,
        help="a labelled gate invocation; the first is the baseline",
    )
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if len(args.run) < 2:
        fail("need at least two --run invocations to compare")

    tree = Path(args.tree).resolve()
    require_clean_worktree(tree)

    runs = [run_gate(tree, label, cmd, args.timeout) for label, cmd in args.run]
    comparisons = [compare(runs[0], other) for other in runs[1:]]

    report: dict[str, Any] = {
        "tree": str(tree),
        "runs": runs,
        "comparisons": comparisons,
        "no_write_mode": all(not r["changed"] for r in runs),
        "agree": all(
            not c["only_in_other"]
            and not c["only_in_base"]
            and not c["different_result"]
            and not c["exit_changed"]
            for c in comparisons
        ),
    }

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        render(report)
    return 0 if report["agree"] else 1


if __name__ == "__main__":
    sys.exit(main())
