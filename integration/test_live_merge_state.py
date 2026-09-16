"""The standing guard on a closed enum that this plugin classifies exhaustively.

`ci_state.py` reads `mergeStateStatus` and has to answer "does this block the
merge?" for every value GitHub can return. It used to answer with an allowlist of
three — `BLOCKED`, `DIRTY`, `DRAFT` — and everything else fell through to
*mergeable*. `BEHIND` was in the fallthrough, so the ordinary state of a bot PR on
a repo that requires up-to-date branches read as a clear merge, and with zero
required contexts it printed "this repo enforces nothing" about a repo that was
refusing the merge at that moment. Found by auditing #118.

The fix was to classify **exhaustively**, so an unrecognised value is underivable
rather than clear. That converts the failure from a false claim into silence — but
silence is still wrong, and nothing local can tell the difference between "this
enum has eight values" and "this enum had eight values when that was written".

This is the half that can only be asked of GitHub. It is the type/field-shape
tripwire proposed in #118, scoped to the one enum a wrong answer is load-bearing
for, rather than to the 1.55 MB published schema.

It earned its place on the first run: `DRAFT` is **deprecated** in this enum in
favour of `PullRequest.isDraft`, which `ci_state.py` was not reading. That is the
predictive half working as #118 argued it would — introspection reports a
deprecation while the value still answers, where a functional test cannot notice
until it has already stopped.

    RUN_NETWORK_TESTS=1 python3 -m unittest discover -s integration -v

Deliberately not in the hermetic suite: it needs api.github.com, and the hermetic
suite's value is that it runs offline and free on every commit. It is a tripwire
and not a gate — GitHub adding an enum value is not a reason to refuse a merge,
it is a reason to open an issue.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import unittest
from typing import Any, ClassVar

sys.path.insert(
    0, str(pathlib.Path(__file__).resolve().parent.parent / "skills/dependabot-audit/scripts")
)

from ci_state import BLOCKING, CLASSIFIED, MERGEABLE

live = unittest.skipUnless(
    os.environ.get("RUN_NETWORK_TESTS"),
    "set RUN_NETWORK_TESTS=1; this queries api.github.com",
)


def introspect(type_name: str, jq: str) -> Any:
    """One `gh api graphql` introspection call, with its exit status checked.

    Unpiped, per CONTRIBUTING's standing trap: `gh api ... | jq` reports jq's
    status, so a rate limit arrives as an empty result at exit 0 — which here
    would read as "the enum has no values", and an empty set passes a subset
    assertion trivially. That is the #118 failure shape exactly, inside the test
    written to catch it, so the check is a skip and never a pass.

    `jq` must be given a filter that collects — `[...]` — because a bare
    `.[]` filter emits **one value per line** and is not a JSON document. Reading
    it with `json.loads` raises on the second line, which is at least loud; the
    quieter version of the same mistake is reading only the first.
    """
    query = f'{{ __type(name:"{type_name}") {{ enumValues(includeDeprecated:true) '
    query += "{ name isDeprecated deprecationReason } } }"
    done = subprocess.run(
        ["gh", "api", "graphql", "-f", f"query={query}", "--jq", jq],
        capture_output=True,
        text=True,
        check=False,
    )
    if done.returncode != 0:
        raise unittest.SkipTest(f"introspecting {type_name} failed: {done.stderr.strip()}")
    if not done.stdout.strip():
        raise unittest.SkipTest(f"introspecting {type_name} returned no output")
    return json.loads(done.stdout)


@live
class TestTheMergeStateEnumHasNotGrown(unittest.TestCase):
    """What `ci_state.py` classifies, against what GitHub actually returns.

    Measured 2026-09-16: eight values, none deprecated. `UNKNOWN` is the one the
    script deliberately puts in neither set — "not established" is a third answer,
    not a third category of merge state.
    """

    values: ClassVar[frozenset[str]]

    UNCLASSIFIED = frozenset({"UNKNOWN"})

    # Deprecations this plugin has read and handled. `DRAFT` was found by this
    # test on its first run: GitHub deprecated it in favour of
    # `PullRequest.isDraft`, with an announced removal of 2021-01-01 UTC that has
    # not happened. `ci_state.py` now reads both, so the value going away is
    # covered — the acknowledgement is here so the test keeps failing on a *new*
    # deprecation instead of staying red on this one and being tuned out.
    ACKNOWLEDGED_DEPRECATIONS = frozenset({"DRAFT"})

    @classmethod
    def setUpClass(cls):
        cls.values = frozenset(introspect("MergeStateStatus", "[.data.__type.enumValues[].name]"))
        if not cls.values:
            # An empty set satisfies every subset assertion below, so it has to
            # be a skip. This is the test's own version of the bug it guards.
            raise unittest.SkipTest("MergeStateStatus introspected to an empty enum")

    def test_every_value_github_returns_is_handled(self):
        """The load-bearing direction. A value here and not in the script is one
        the script would route to underivable — safe, but silently so, and the
        report would stop being able to say whether the merge is gated."""
        self.assertEqual(
            self.values - CLASSIFIED - self.UNCLASSIFIED,
            frozenset(),
            "GitHub returns a mergeStateStatus this plugin does not classify; "
            "ci_state.py's BLOCKING/MERGEABLE sets and SKILL.md's Phase 6 table "
            "both need a row for it",
        )

    def test_nothing_classified_has_been_removed(self):
        """The other direction, and the cheaper one to be wrong in: a set naming
        a value GitHub no longer returns is dead code that still reads as
        coverage."""
        self.assertEqual(
            CLASSIFIED - self.values,
            frozenset(),
            "ci_state.py classifies a mergeStateStatus GitHub no longer returns",
        )

    def test_behind_is_still_a_value_and_still_blocking(self):
        """The specific regression. If GitHub ever stops returning `BEHIND` the
        handling is harmless, but the SKILL.md prose explaining it becomes a
        paragraph about something that cannot happen."""
        self.assertIn("BEHIND", self.values)
        self.assertIn("BEHIND", BLOCKING)
        self.assertNotIn("BEHIND", MERGEABLE)

    def test_nothing_the_script_classifies_is_deprecated(self):
        """The predictive half, and the reason introspection rather than a diff:
        GitHub deprecates before removing, so this is a warning window instead of
        a surprise."""
        rows = introspect(
            "MergeStateStatus",
            "[.data.__type.enumValues[] | select(.isDeprecated) | {name, why: .deprecationReason}]",
        )
        flagged = [
            r
            for r in rows
            if r["name"] in CLASSIFIED and r["name"] not in self.ACKNOWLEDGED_DEPRECATIONS
        ]
        self.assertEqual(
            flagged,
            [],
            f"newly deprecated mergeStateStatus value(s) this plugin reads: {flagged} — "
            "read the replacement field alongside it before the removal lands, then "
            "add it to ACKNOWLEDGED_DEPRECATIONS",
        )

    def test_the_acknowledged_deprecations_are_still_deprecated(self):
        """The other direction, and the one that rots quietly: an acknowledgement
        for a value GitHub un-deprecated, or removed, is a silenced alarm nobody
        will re-arm. Measured against the live enum rather than remembered."""
        rows = introspect(
            "MergeStateStatus",
            "[.data.__type.enumValues[] | select(.isDeprecated) | .name]",
        )
        stale = self.ACKNOWLEDGED_DEPRECATIONS - set(rows)
        self.assertEqual(
            stale,
            frozenset(),
            f"acknowledged as deprecated but GitHub no longer says so: {stale}",
        )


if __name__ == "__main__":
    unittest.main()
