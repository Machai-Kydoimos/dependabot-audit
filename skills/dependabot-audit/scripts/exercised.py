#!/usr/bin/env python3
"""Phase 6's second question, mechanised: did a run on this commit exercise the change?

A green check proves a job passed. Whether that job ran anything this PR changed
is a different question, and an actions or lockfile bump routinely passes the
first while failing the second. Until 0.57.0 the procedure answered it by
**prediction**: a loose grep for install steps, then `sed -n '/^on:/,/^[a-z]/p'`
over each workflow it named, read for a `pull_request` trigger. Nothing in it
mentioned `paths:` or `paths-ignore:` (#176), and across the workflows of 12
repositories **25 of 52** `pull_request` triggers carry one. `fpga-board-sim`'s
own `install-docs.yml` does. A workflow filtered that way never runs on a PR
that changes only `uv.lock`, and the trigger read says it would.

So this **observes** instead. The runs on the PR's head commit are the runs that
happened, and each job's steps carry their own conclusions:

  1. `pulls/<N>/files` -- what changed, and for a workflow, what the patch adds:
     the `uses:` lines, which name the actions bumped, and any other value set;
  2. `actions/runs?head_sha=` -- every run on this commit, whatever the event;
  3. the workflow files at that commit, one GraphQL read, through `runners.py`'s
     YAML reader -- which jobs hold a step that exercises each changed file;
  4. `actions/runs/<id>/jobs` for the runs that matter -- whether that job ran,
     and whether the step inside it did. A job-level `if:` skips the job; a
     step-level one skips only the step, and `success` on the job hides it.

A step that exercises a changed file is: for `uv.lock` or `pyproject.toml`, a
`run:` with `uv sync` or `uv run`; for `.pre-commit-config.yaml`, `pre-commit
run` or `prek run`, or their actions; for a workflow file, a `uses:` of an action
the patch bumps, or a step whose own keys, `with:` or `env:` carry a value the
patch sets -- Renovate's `version: "0.12.18"` under ruff's setup-uv steps bumps
no action (ruff#28880). A workflow change no step accounts for, like a job-level
`env:`, can only show that the file ran: that is its own state, `file ran`, and
not exercise. A workflow called from another runs as `caller / callee` under the
caller's path, and is followed there, `./` and `$/` alike.

Steps are matched against the job's step list by the name GitHub shows: the
step's own `name:`, else `Run <uses>` or `Run <first line of run>`. A step whose
name cannot be matched falls back to its job's conclusion, and says so.

A workflow holding such a step that did not run on this commit is listed with its
triggers, from `runners.py`: that is where a path filter or a missing
`pull_request` shows why. A status from outside Actions is listed too;
`pre-commit.ci` counts for `.pre-commit-config.yaml`, anything else is named and
not read.

Exit status: 0 = every changed file was exercised by a step that ran to a
conclusion on this commit. 1 = at least one was not, is still running, only
shows its file ran, has no rule here, or could not be matched -- or `--head-sha`
is not the PR's head -- and the output names which, and why. That is a finding
for the CI row: green, and not green because of this change. 2 = could not run.

    exercised.py --owner OWNER --name NAME --number N --head-sha SHA

Requires Python 3.11+ and `gh`. Reads nothing locally: the runs are GitHub's.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from typing import Any, NoReturn

import runners

TIMEOUT = 120

WORKFLOWS = ".github/workflows"

Rule = tuple[str, re.Pattern[str], tuple[str, ...]]


def _rule(commands: list[tuple[str, tuple[str, ...]]], actions: tuple[str, ...] = ()) -> Rule:
    """(how it reads in the output, `run:` pattern, `uses:` prefixes) for a step that
    exercises a file. The label and the pattern come from one list, so the output
    can never name a command the match does not look for."""
    label = " or ".join(f"`{tool} {verb}`" for tool, verbs in commands for verb in verbs)
    pattern = "|".join(
        rf"\b{re.escape(tool)}\s+(?:{'|'.join(verbs)})\b" for tool, verbs in commands
    )
    return (
        label + (", or their action" if actions else ""),
        re.compile(pattern),
        tuple(f"{action}@" for action in actions),
    )


# What a step shows when it exercises a changed file this plugin audits.
UV_INSTALLS = _rule([("uv", ("sync", "run"))])
RULES: dict[str, Rule] = {
    "uv.lock": UV_INSTALLS,
    "pyproject.toml": UV_INSTALLS,
    ".pre-commit-config.yaml": _rule(
        [("pre-commit", ("run",)), ("prek", ("run",))], ("pre-commit/action", "j178/prek-action")
    ),
}

# What a changed file's row collects, in the order it prints.
KINDS = ("evidence", "file", "skipped", "pending", "unmatched", "silent")

# How many lines of each kind are printed per changed file.
SHOWN_EVIDENCE = 5
SHOWN_OTHER = 20

# Concluded, and therefore ran its commands. `cancelled` and `skipped` did not
# finish them, and a job waiting on approval has not started.
RAN = frozenset({"success", "failure"})

TREE_QUERY = """
query($owner:String!, $name:String!, $expr:String!) {
  repository(owner:$owner, name:$name) {
    object(expression:$expr) {
      ... on Tree { entries { name type object { ... on Blob { text isBinary } } } }
    }
  }
}
"""


def fail(what: str) -> NoReturn:
    """Exit 2. Reserved for "could not run", never for "ran and found something"."""
    print(f"error: {what}", file=sys.stderr)
    raise SystemExit(2)


def _gh(args: list[str]) -> str:
    """`gh` stdout, or exit 2 naming the call. `gh` writes an error body to stdout
    and still exits non-zero, so the status is the signal."""
    try:
        proc = subprocess.run(  # noqa: S603
            ["gh", *args],  # noqa: S607
            capture_output=True,
            text=True,
            check=False,
            timeout=TIMEOUT,
        )
    except FileNotFoundError:
        fail("`gh` is not on PATH; this question is answered entirely from GitHub")
    except subprocess.TimeoutExpired:
        fail(f"`gh {' '.join(args[:2])}` exceeded {TIMEOUT}s")
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        fail(f"`gh {' '.join(args[:2])}` failed: {detail[0] if detail else 'no output'}")
    return proc.stdout


def _lines(args: list[str]) -> list[Any]:
    """A `--jq` filter emitting one JSON value per line. Empty is a real answer."""
    out = _gh(args).strip()
    try:
        return [json.loads(line) for line in out.splitlines() if line.strip()]
    except json.JSONDecodeError as exc:
        fail(f"`gh {' '.join(args[:2])}` returned unparseable JSON lines: {exc}")


# --- what GitHub holds for this commit ----------------------------------------


def changed_files(owner: str, name: str, number: int) -> list[dict[str, Any]]:
    return _lines([
        "api", f"repos/{owner}/{name}/pulls/{number}/files?per_page=100", "--paginate",
        "--jq", ".[] | {filename, status, patch}",
    ])  # fmt: skip


def head_of(owner: str, name: str, number: int) -> str:
    """The PR's head commit as GitHub has it now -- merged or not."""
    return _gh(["api", f"repos/{owner}/{name}/pulls/{number}", "--jq", ".head.sha"]).strip()


def runs_at(owner: str, name: str, sha: str) -> list[dict[str, Any]]:
    return _lines([
        "api", f"repos/{owner}/{name}/actions/runs?head_sha={sha}&per_page=100", "--paginate",
        "--jq", ".workflow_runs[] | {id, name, path, event, status, conclusion}",
    ])  # fmt: skip


def jobs_of(owner: str, name: str, run_id: int) -> list[dict[str, Any]]:
    return _lines([
        "api", f"repos/{owner}/{name}/actions/runs/{run_id}/jobs?per_page=100", "--paginate",
        "--jq", ".jobs[] | {name, status, conclusion, "
        "steps: [(.steps // [])[] | {name, conclusion}]}",
    ])  # fmt: skip


def statuses_at(owner: str, name: str, sha: str) -> list[dict[str, Any]]:
    return _lines([
        "api", f"repos/{owner}/{name}/commits/{sha}/status",
        "--jq", ".statuses[] | {context, state}",
    ])  # fmt: skip


def workflow_texts(owner: str, name: str, sha: str) -> dict[str, str | None]:
    """Every workflow file at the commit, path -> text (None where unreadable)."""
    out = _gh([
        "api", "graphql", "-f", f"query={TREE_QUERY}",
        "-F", f"owner={owner}", "-F", f"name={name}", "-F", f"expr={sha}:{WORKFLOWS}",
    ])  # fmt: skip
    try:
        data = json.loads(out)
    except json.JSONDecodeError as exc:
        fail(f"the workflow tree read returned unparseable JSON: {exc}")
    repo = (data.get("data") or {}).get("repository")
    if repo is None:
        fail(f"{owner}/{name} is not readable")
    tree = repo.get("object") or {}
    found: dict[str, str | None] = {}
    for entry in tree.get("entries") or []:
        path = f"{WORKFLOWS}/{entry.get('name')}"
        if entry.get("type") != "blob" or not re.search(r"\.ya?ml$", path):
            continue
        blob = entry.get("object") or {}
        found[path] = None if blob.get("isBinary") else blob.get("text")
    return found


# --- which steps exercise a change --------------------------------------------


def bumped_actions(patch: str | None) -> list[str]:
    """What an added `uses:` line pins: `owner/repo`, `owner/repo/path`, or a
    `docker://` image. A local action carries no `@`, so it is never one."""
    found = []
    for line in (patch or "").splitlines():
        hit = re.match(r"^\+\s*-?\s*uses:\s*['\"]?([^@\s'\"]+)@", line)
        if hit and hit.group(1) not in found:
            found.append(hit.group(1))
    return found


def changed_values(patch: str | None) -> list[tuple[str, str]]:
    """Every `key: value` an added line sets, other than a `uses:` -- Renovate's
    `version: "0.12.18"` under a setup-uv step, which bumps no action (ruff#28880)."""
    found = []
    for line in (patch or "").splitlines():
        # A key starts with a word character, so a comment line never matches.
        hit = re.match(r"^\+\s*-?\s*([A-Za-z0-9_.-]+):\s*(.+?)\s*$", line)
        if not hit or hit.group(1) == "uses":
            continue
        value = re.sub(r"\s+#.*$", "", hit.group(2)).strip().strip("'\"")
        if value and (hit.group(1), value) not in found:
            found.append((hit.group(1), value))
    return found


def sets_value(step: dict[str, Any], changed: list[tuple[str, str]]) -> bool:
    """Does the step itself, its `with:` or its `env:` carry one of those values?"""
    where = [step, step.get("with") or {}, step.get("env") or {}]
    return any(
        isinstance(block, dict) and str(block.get(key, "")).strip() == value
        for key, value in changed
        for block in where
    )


def _pattern(template: str) -> str:
    """A regex for a name GitHub renders: literal text, and `.+?` per expression."""
    parts = re.split(r"\$\{\{.*?\}\}", template, flags=re.DOTALL)
    return ".+?".join(re.escape(part) for part in parts)


def step_name(step: dict[str, Any]) -> re.Pattern[str] | None:
    """The name GitHub shows for a step: its `name:`, else `Run <uses>` or `Run <first
    line of run>`. The run line is matched on its first 60 characters as a prefix,
    which holds whether or not GitHub shortens a long one."""
    name = step.get("name")
    if isinstance(name, str) and name.strip():
        return re.compile(rf"^{_pattern(name.strip())}$", re.DOTALL)
    if isinstance(step.get("uses"), str):
        return re.compile(rf"^Run {_pattern(step['uses'].strip())}$")
    run = step.get("run")
    if isinstance(run, str):
        first = next((line.strip() for line in run.splitlines() if line.strip()), "")
        if first:
            return re.compile(rf"^Run {_pattern(first[:60])}")
    return None


def exercises(
    step: dict[str, Any], changed: str, actions: list[str], values: list[tuple[str, str]]
) -> bool:
    """Does this step run what `changed` altered?"""
    uses = str(step.get("uses") or "")
    run = str(step.get("run") or "")
    if changed.startswith(f"{WORKFLOWS}/"):
        bumped = any(uses.startswith((f"{a}@", f"{a}/")) for a in actions)
        return bumped or sets_value(step, values)
    rule = RULES.get(os.path.basename(changed))
    if rule is None:
        return False
    _, pattern, prefixes = rule
    return bool(pattern.search(run)) or uses.startswith(prefixes)


def job_name(display: str, caller: str | None = None) -> re.Pattern[str]:
    """How GitHub names a job's runs: its `name:` or id, a matrix suffix optional,
    and `caller / callee` for a job inside a called workflow."""
    own = rf"{_pattern(display)}(?: \(.*\))?"
    if caller is not None:
        return re.compile(rf"^{_pattern(caller)}(?: \(.*\))? / {own}$", re.DOTALL)
    return re.compile(rf"^{own}$", re.DOTALL)


def _literal(display: str) -> int:
    """How much of a job's name is fixed text -- the likelier match wins."""
    return len(re.sub(r"\$\{\{.*?\}\}", "", display, flags=re.DOTALL))


def jobs_in(path: str, texts: dict[str, str | None]) -> tuple[list[dict[str, Any]], str]:
    """Every job the workflow at `path` runs: its API name pattern, the name shown,
    its steps, and the file those steps are written in. A job that calls a workflow
    in this tree is expanded into that workflow's jobs, which is how a change to a
    called workflow is seen running under its caller's path. The second value is
    why the file could not be read, or ""."""
    text = texts.get(path)
    if text is None:
        return [], "not in the tree at this commit, or not text"
    try:
        document = runners.load(text)
    except runners.Unreadable as exc:
        return [], f"unreadable here -- {exc}"
    found: list[dict[str, Any]] = []
    for job_id, spec in runners.jobs(document):
        display = str(spec.get("name") or job_id)
        called = str(spec.get("uses") or "")
        # `./` and `$/` both name a workflow in this repository; ruff's cargo-dist
        # release.yml calls five with `$/`, and each is in the tree read above.
        if called.startswith(("./", "$/")) and called[2:].split("@")[0] in texts:
            callee_path = called[2:].split("@")[0]
            try:
                callee = runners.load(texts.get(callee_path) or "")
            except runners.Unreadable:
                callee = None
            for inner_id, inner in runners.jobs(callee):
                inner_display = str(inner.get("name") or inner_id)
                found.append({
                    "pattern": job_name(inner_display, display),
                    "shown": f"{display} / {inner_display}",
                    "steps": [s for s in inner.get("steps") or [] if isinstance(s, dict)],
                    "source": callee_path,
                    "literal": _literal(display) + _literal(inner_display),
                })  # fmt: skip
            continue
        found.append({
            "pattern": job_name(display),
            "shown": display,
            "steps": [s for s in spec.get("steps") or [] if isinstance(s, dict)],
            "source": path,
            "literal": _literal(display),
        })  # fmt: skip
    return found, ""


def relevant_steps(
    job: dict[str, Any], changed: str, actions: list[str], values: list[tuple[str, str]]
) -> list[dict[str, Any]]:
    """The job's steps that exercise `changed` -- none for another file's job."""
    if changed.startswith(f"{WORKFLOWS}/") and job["source"] != changed:
        return []
    return [s for s in job["steps"] if exercises(s, changed, actions, values)]


def wanted_jobs(
    found: list[dict[str, Any]],
    changed: str,
    actions: list[str],
    values: list[tuple[str, str]],
    file_level: bool,
) -> list[tuple[dict[str, Any], list[dict[str, Any]]]]:
    """(job, the steps to find in it) for every job that bears on `changed`. At file
    level -- a workflow change no step of it accounts for, like a job-level `env:`
    -- that is every job the file defines, with no step to look for."""
    if file_level:
        return [(job, []) for job in found if job["source"] == changed]
    pairs = [(job, relevant_steps(job, changed, actions, values)) for job in found]
    return [(job, steps) for job, steps in pairs if steps]


def at_file_level(
    changed: str,
    actions: list[str],
    values: list[tuple[str, str]],
    defined: dict[str, tuple[list[dict[str, Any]], str]],
) -> bool:
    """A workflow change that no step of that workflow, wherever it runs, accounts for."""
    if not changed.startswith(f"{WORKFLOWS}/"):
        return False
    return not any(
        relevant_steps(job, changed, actions, values)
        for found, _ in defined.values()
        for job in found
    )


def best_match(name: str, defined: list[dict[str, Any]]) -> dict[str, Any] | str | None:
    """The workflow job an API job ran, or "ambiguous" when two fit equally well."""
    fits = [job for job in defined if job["pattern"].match(name)]
    if not fits:
        return None
    top = max(job["literal"] for job in fits)
    best = [job for job in fits if job["literal"] == top]
    return best[0] if len(best) == 1 else "ambiguous"


def verdict_for(
    changed: str,
    actions: list[str],
    values: list[tuple[str, str]],
    runs: list[dict[str, Any]],
    jobs: dict[int, list[dict[str, Any]]],
    texts: dict[str, str | None],
    defined: dict[str, tuple[list[dict[str, Any]], str]],
) -> dict[str, Any]:
    """What ran on this commit that exercises `changed`, and what did not."""
    row: dict[str, Any] = {key: [] for key in KINDS}
    file_level = at_file_level(changed, actions, values, defined)
    for path in sorted(defined):
        found, why = defined[path]
        if why:
            if not changed.startswith(f"{WORKFLOWS}/") or path == changed:
                row["unmatched"].append(f"{path}: {why}")
            continue
        pairs = wanted_jobs(found, changed, actions, values, file_level)
        wanted = [job for job, _ in pairs]
        if not wanted:
            continue
        mine = [r for r in runs if r.get("path") == path]
        if not mine:
            try:
                on = runners.triggers(runners.load(texts.get(path) or ""))
                started = ", ".join(runners.describe(e, spec) for e, spec in on.items())
            except runners.Unreadable:
                started = "unreadable here"
            row["silent"].append(
                f"{path} -- no run on this commit; it starts on: {started or 'nothing'}"
            )
            continue
        seen: set[str] = set()
        for run in mine:
            if run.get("status") != "completed":
                row["pending"].append(f"{path} ({run.get('event')}) is {run.get('status')}")
                continue
            for job in jobs.get(int(run["id"]), []):
                name = str(job.get("name") or "")
                match = best_match(name, found)
                if match == "ambiguous":
                    row["unmatched"].append(f"{os.path.basename(path)}  {name} -- fits two jobs")
                    continue
                if not isinstance(match, dict) or match not in wanted:
                    continue
                seen.add(match["shown"])
                steps = next(st for jb, st in pairs if jb is match)
                where = f"{os.path.basename(path)}  {name}"
                if job.get("conclusion") not in RAN:
                    row["skipped"].append(
                        f"{where} -- job {job.get('conclusion') or job.get('status')}"
                    )
                    continue
                named = [n for n in (step_name(s) for s in steps) if n is not None]
                # Anchored at `Run ` or the step's own name, so a `Post Run ...`
                # cleanup step never stands in for the step itself.
                hits = [
                    s
                    for s in job.get("steps") or []
                    if any(n.match(str(s.get("name") or "")) for n in named)
                ]
                if not steps:
                    row["file"].append(f"{where} -- job {job['conclusion']}")
                elif not hits:
                    row["evidence"].append(
                        f"{where} -- job {job['conclusion']}; its step could not be matched "
                        "by name, so this rests on the job"
                    )
                elif any(s.get("conclusion") in RAN for s in hits):
                    ran = next(s for s in hits if s.get("conclusion") in RAN)
                    row["evidence"].append(f"{where} -- step `{ran['name']}` {ran['conclusion']}")
                else:
                    row["skipped"].append(
                        f"{where} -- job {job['conclusion']}, but step "
                        f"`{hits[0]['name']}` {hits[0].get('conclusion')}"
                    )
        for job in wanted:
            if job["shown"] not in seen and not row["pending"]:
                row["skipped"].append(
                    f"{os.path.basename(path)}  {job['shown']} -- no run of this job"
                )
    return row


def state_of(row: dict[str, Any]) -> str:
    if row["evidence"]:
        return "exercised"
    if row["pending"]:
        return "pending"
    if row["file"]:
        # The workflow ran, and no step of it sets what changed, so whether the
        # changed line did anything is not something the runs can say.
        return "file ran"
    if row["unmatched"] and not row["skipped"]:
        return "underivable"
    return "not exercised"


def main() -> int:
    parser = argparse.ArgumentParser(description="Did a run on this commit exercise the change?")
    parser.add_argument("--owner", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--number", required=True, type=int)
    parser.add_argument("--head-sha", required=True, help="$HEAD_SHA, all 40 characters")
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{40}", args.head_sha):
        fail(f"--head-sha takes the full 40-character SHA, got {args.head_sha!r}")

    files = changed_files(args.owner, args.name, args.number)
    if not files:
        fail(f"{args.owner}/{args.name}#{args.number} lists no changed files")
    # Every row below is about `--head-sha`. A PR that moved, or a SHA copied
    # wrong, has no runs and no workflow tree, and read quietly that is "nothing
    # ran" -- the finding, reached from a typo.
    moved = head_of(args.owner, args.name, args.number) != args.head_sha
    runs = runs_at(args.owner, args.name, args.head_sha)
    texts = workflow_texts(args.owner, args.name, args.head_sha)
    defined = {path: jobs_in(path, texts) for path in texts}

    # Each changed file, the actions its patch bumps, and whether this has a rule.
    changes = []
    for entry in files:
        changed = str(entry.get("filename") or "")
        is_workflow = changed.startswith(f"{WORKFLOWS}/")
        actions = bumped_actions(entry.get("patch")) if is_workflow else []
        values = changed_values(entry.get("patch")) if is_workflow else []
        ruled = is_workflow or os.path.basename(changed) in RULES
        changes.append((changed, actions, values, ruled))

    # Jobs are fetched only for runs whose workflow holds a job that matters.
    needed = {
        path
        for path, (found, _) in defined.items()
        for changed, actions, values, ruled in changes
        if ruled
        and wanted_jobs(
            found, changed, actions, values, at_file_level(changed, actions, values, defined)
        )
    }
    jobs = {
        int(r["id"]): jobs_of(args.owner, args.name, int(r["id"]))
        for r in runs
        if r.get("path") in needed and r.get("status") == "completed"
    }
    outside = [s for s in statuses_at(args.owner, args.name, args.head_sha) if s.get("context")]

    if moved:
        print(f"!! --head-sha {args.head_sha[:9]} IS NOT THIS PR'S HEAD. The PR moved, or the")
        print("   SHA is wrong; every row below describes that commit, not the PR.\n")
    print(f"runs on {args.head_sha[:9]} (this PR's head): {len(runs)}")
    if not texts:
        print(f"  no {WORKFLOWS}/ at this commit, so no workflow can have run the change")
    for run in runs:
        listed = f"  {len(jobs[int(run['id'])])} job(s)" if int(run["id"]) in jobs else ""
        state = run.get("conclusion") or run.get("status")
        print(f"  {run.get('path')}  {run.get('event')}  {state}{listed}")
    for status in outside:
        print(f"  outside Actions: {status['context']}  {status.get('state')}")

    states: dict[str, str] = {}
    for changed, actions, values, ruled in changes:
        row: dict[str, Any] = (
            verdict_for(changed, actions, values, runs, jobs, texts, defined)
            if ruled
            else {key: [] for key in KINDS}
        )
        if os.path.basename(changed) == ".pre-commit-config.yaml":
            for status in outside:
                if str(status["context"]).startswith("pre-commit.ci"):
                    row["evidence"].append(
                        f"{status['context']} -- status {status.get('state')}, outside Actions"
                    )
        state = state_of(row) if ruled else "no rule"
        states[changed] = state
        if actions or values:
            named = [*actions, *(f"{k}: {v}" for k, v in values)]
            how = f"changes {', '.join(named[:4])}" + (
                f" and {len(named) - 4} more" if len(named) > 4 else ""
            )
        elif changed.startswith(f"{WORKFLOWS}/"):
            how = "a workflow file, and no line of it a step sets"
        elif ruled:
            how = f"installed from by {RULES[os.path.basename(changed)][0]}"
        else:
            how = "no rule for this file here, so read the jobs above"
        print(f"\n{changed} -- {how}: {state.upper()}")
        for label, key in (
            ("EXERCISED", "evidence"),
            ("FILE RAN ", "file"),
            ("NOT RUN  ", "skipped"),
            ("PENDING  ", "pending"),
            ("UNREAD   ", "unmatched"),
            ("NO RUN   ", "silent"),
        ):
            # The evidence is capped because one line settles it: #438 lists 30 jobs,
            # every one `uv sync`. What did NOT run is the finding, so it is capped
            # far later, and never silently.
            cap = SHOWN_EVIDENCE if key == "evidence" else SHOWN_OTHER
            for line in row[key][:cap]:
                print(f"  {label}  {line}")
            if len(row[key]) > cap:
                print(f"  {label}  ... and {len(row[key]) - cap} more like these")

    print()
    counts = {s: sum(1 for v in states.values() if v == s) for s in set(states.values())}
    total = len(states)
    if moved:
        print(f"RESULT: UNDERIVABLE -- --head-sha is not the head of #{args.number}.")
        return 1
    if counts.get("exercised") == total:
        print(f"RESULT: EXERCISED -- every one of {total} changed file(s) ran on this commit.")
        return 0
    parts = ", ".join(f"{n} {s}" for s, n in sorted(counts.items()))
    print(
        f"RESULT: NOT ALL EXERCISED -- {parts}, of {total} changed file(s). For each file "
        "not exercised, no green on this commit speaks for it."
    )
    return 1


def cli() -> NoReturn:
    """Entry point. Anything unforeseen becomes exit 2, never exit 1."""
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as exc:
        if os.environ.get("DEPENDABOT_AUDIT_DEBUG"):
            raise
        fail(f"unexpected {type(exc).__name__}: {exc} -- a bug, not a finding")


if __name__ == "__main__":
    cli()
