#!/usr/bin/env python3
"""Phase 7's tidy-up: remove what Phase 0 created, and report what dirtied it.

Phase 0 registers two worktrees and a `pr-<N>` branch in the **user's** repo, and
they accumulate one set per PR audited. Phase 7 is the only phase every audit
reaches -- `--no-execute` skips Phase 5, and Phase 1's gate stops before it, which
is the path where the audit was most right to stop and the one that used to litter
every time.

Until 0.35.0 this was three commands in the prose:

    git worktree remove "$SCRATCH/pr-<N>"
    git worktree remove "$SCRATCH/base-<N>"
    git branch -D "pr-<N>"

and the prose said nothing about what does or does not dirty a worktree. Two
different audits (2026-08-30 and 2026-09-01) therefore reasoned it out and reached
the same wrong answer -- that `uv sync`'s `.venv/` makes the plain `remove` refuse.
It does not: `remove` gates on `git status --porcelain`, which omits *ignored*
files, and uv, pytest, ruff and mypy each write a `.gitignore` containing `*`
inside their own directory. Measured on git 2.55.0 and uv 0.12.8 in a worktree of a
repo with no `.gitignore` at all, the plain form exits 0.

The refusal that *is* real was missed by both: Phase 5 runs the repo's own gates in
`$SCRATCH/pr-<N>`, and where those are fix-mode -- `pre-commit` stages its own
edits, as `gate_diff.py`'s `restore()` already records -- tracked files end up
modified or staged, and `remove` exits 128. Phase 4 mutates `base-<N>` and
`gate_diff.py` restores it after every run; nothing restored `pr-<N>`.

**So the residue is written out before the tree goes.** `--force` alone would
discard evidence the audit produced and has not yet reported, which is the failure
`restore()` reasons about when it declines `-x`. Writing first is what makes the
removal safe: `$SCRATCH` outlives the worktrees, so the diff outlives the tree that
held it. A gate that rewrote tracked files at the proposed version is the strongest
row Phase 5 can produce, and it was being thrown away either way -- forced away, or
left behind with the litter.

Not archival: `$SCRATCH` lives under `$TMPDIR` and a reboot takes it. The file is
for the report being written now.

Usage:
    cleanup.py --scratch DIR --pr N [--repo DIR]

Exit status: 0 = removed, nothing to report. 1 = removed, and residue was found
(a Phase 5 finding, not a cleanup failure). 2 = could not run.
Requires Python 3.11+. No network.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import NoReturn

# Exactly what Phase 0 creates, and nothing else. `tip-<N>` exists only when
# Phase 0 found the base rewritten; an actions bump creates no worktree at all
# and leaves only the branch. Discovering which are present beats being told:
# the caller cannot get it wrong, and the actions case needs no flag.
WORKTREES = ("pr-{n}", "base-{n}", "tip-{n}")


def fail(what: str) -> NoReturn:
    """Exit 2 -- could not run. Never 1, which means residue was found."""
    print(f"error: {what}", file=sys.stderr)
    raise SystemExit(2)


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run git and hand back the result. Callers decide what a failure means.

    Unlike `gate_diff.py`'s `_git`, this one does not raise on a non-zero exit:
    half the calls here are expected to fail sometimes. `worktree remove` on a
    path that is already gone exits 0 (measured for #90), but `branch -D` on a
    branch that was never created exits 1, and that is a no-op rather than an
    error -- an actions bump reaches Phase 7 with no branch of its own.
    """
    return subprocess.run(  # noqa: S603
        ["git", "-C", str(cwd), *args],  # noqa: S607
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def porcelain(tree: Path) -> str:
    """What git considers dirty here: modified, staged, or untracked-and-not-ignored.

    The same command `git worktree remove` itself gates on, which is why this is
    the right question to ask before removing. Ignored files -- a `.venv`, a tool
    cache -- are absent from it, and are also absent from what `remove` refuses on.
    """
    return _git(tree, "status", "--porcelain").stdout.strip()


def write_residue(tree: Path, scratch: Path, status: str) -> Path:
    """Save what dirtied the tree, outside the tree, before it is removed.

    Per-tree rather than one file: `pr-<N>` dirty is Phase 5's gates, `base-<N>`
    dirty means `gate_diff.py`'s restore did not hold, and those are different
    findings that must not land in one buffer under one heading.

    Tracked changes get their diff, because *what* the gate rewrote is the finding.
    Untracked paths are listed and not dumped -- a stray build artefact is signal
    that it happened, not content worth carrying.
    """
    out = scratch / f"residue-{tree.name}.diff"
    diff = _git(tree, "diff", "HEAD").stdout
    body = [
        f"# residue in {tree}",
        "# Written by Phase 7 before removing the worktree. Not archival:",
        "# $SCRATCH lives under $TMPDIR and a reboot takes it.",
        "",
        "=== git status --porcelain ===",
        status,
        "",
        "=== git diff HEAD (tracked changes) ===",
        diff.rstrip() or "(none -- the residue is untracked files only)",
        "",
    ]
    out.write_text("\n".join(body), encoding="utf-8")
    return out


def counts(status: str) -> tuple[int, int]:
    """(tracked changes, untracked paths) from porcelain's two-column codes.

    `??` is untracked; everything else is a tracked path the gate touched. The
    split matters to the report: a gate that rewrote tracked files is a behaviour
    finding, while untracked residue is usually just a cache the repo does not
    ignore.
    """
    lines = [ln for ln in status.splitlines() if ln.strip()]
    untracked = sum(1 for ln in lines if ln.startswith("??"))
    return len(lines) - untracked, untracked


def remove_worktree(repo: Path, tree: Path, scratch: Path) -> tuple[bool, Path | None]:
    """Remove one worktree, saving its residue first. (removed, residue path)."""
    if not tree.exists():
        # Still worth calling: the registration can outlive the directory, and
        # `remove` on a missing path clears it and exits 0.
        _git(repo, "worktree", "remove", str(tree))
        return True, None

    status = porcelain(tree)
    residue = write_residue(tree, scratch, status) if status else None

    result = _git(repo, "worktree", "remove", str(tree))
    if result.returncode != 0:
        # The only expected failure is the dirty tree whose residue is now saved,
        # so forcing here loses nothing that is not already written down. Anything
        # else -- a locked worktree, a permission error -- still fails loudly below.
        result = _git(repo, "worktree", "remove", "--force", str(tree))
    return result.returncode == 0, residue


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scratch", required=True, help="$SCRATCH from the Phase 0 handoff")
    parser.add_argument("--pr", required=True, help="the PR number under audit")
    parser.add_argument("--repo", default=".", help="the user's repo (default: cwd)")
    args = parser.parse_args()

    repo = Path(args.repo).resolve()
    scratch = Path(args.scratch)
    if not (repo / ".git").exists():
        fail(f"{repo} is not a git repository; refusing to run")
    if not scratch.is_dir():
        fail(f"{scratch} does not exist -- re-derive $SCRATCH, or Phase 0 never ran")

    # A previous run's registration can point at a path a tmp sweep deleted, and
    # a stale one makes `remove` on a live path report about the wrong thing.
    _git(repo, "worktree", "prune")

    removed: list[str] = []
    residues: list[tuple[str, Path, tuple[int, int]]] = []
    stuck: list[str] = []

    for pattern in WORKTREES:
        tree = scratch / pattern.format(n=args.pr)
        existed = tree.exists()
        status = porcelain(tree) if existed else ""
        ok, residue = remove_worktree(repo, tree, scratch)
        if not ok:
            stuck.append(str(tree))
            continue
        if existed:
            removed.append(tree.name)
        if residue:
            residues.append((tree.name, residue, counts(status)))

    branch = f"pr-{args.pr}"
    if _git(repo, "branch", "-D", branch).returncode == 0:
        removed.append(f"branch {branch}")

    print(f"removed: {', '.join(removed) if removed else 'nothing -- already clean'}")
    if stuck:
        fail("could not remove " + ", ".join(stuck) + "; they are still registered")

    if not residues:
        print("no residue: every worktree was clean when it was removed.")
        return 0

    print()
    for name, path, (tracked, untracked) in residues:
        print(f"RESIDUE in {name}: {tracked} tracked file(s) changed, {untracked} untracked")
        print(f"  saved to {path}")
    print()
    print("This is a Phase 5 finding, not a cleanup failure. A gate that rewrote")
    print("tracked files at the proposed version is what Phase 4 exists to catch,")
    print("reached from the other direction -- report it, with the file above.")
    return 1


def cli() -> NoReturn:
    """Entry point. Anything unforeseen becomes exit 2, never exit 1.

    Exit 1 here means residue was found -- a finding for the report. An unhandled
    exception exits 1 too, so without this a crash would be read as a gate having
    rewritten the tree. Set `DEPENDABOT_AUDIT_DEBUG` to re-raise.
    """
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as exc:
        if os.environ.get("DEPENDABOT_AUDIT_DEBUG"):
            raise
        fail(
            f"unexpected {type(exc).__name__}: {exc}\n"
            "       This is a bug, not a finding. Set DEPENDABOT_AUDIT_DEBUG=1 "
            "for the traceback."
        )


if __name__ == "__main__":
    cli()
