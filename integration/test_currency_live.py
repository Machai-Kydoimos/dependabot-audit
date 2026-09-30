"""currency.py against the registries, on the releases behind #187.

The hermetic suite answers from recordings, so it can only agree with what this
repo believes PyPI and GitHub serve, and with what it believes the bot reads. These
read the published ones:

  - rumdl 0.2.77 is dated by its last-listed file, the sdist uploaded three days
    after its wheels, and that is why Dependabot proposed 0.2.76 on
    `fpga-board-sim` #443;
  - astral-sh/setup-uv v10.2.0 is dated by its release's `published_at`;
  - across `fpga-board-sim`'s 19 uv PRs up to #444, no newer release was outside
    the 3-day window by the bot's date when the PR opened. That is the rule's own
    evidence, re-derived, and it fails if either the dating or the bot changes.

    RUN_NETWORK_TESTS=1 python3 -m unittest discover -s integration -v

Needs the network, and `gh` for the GitHub reads. Upload times and PR opening times
do not change once written.
"""

from __future__ import annotations

import datetime
import json
import os
import pathlib
import re
import subprocess
import sys
import unittest

sys.path.insert(
    0, str(pathlib.Path(__file__).resolve().parent.parent / "skills/dependabot-audit/scripts")
)

import audit
import currency

live = unittest.skipUnless(
    os.environ.get("RUN_NETWORK_TESTS"),
    "set RUN_NETWORK_TESTS=1; this reads PyPI's JSON API and GitHub's releases",
)

# `fpga-board-sim`'s Dependabot uv PRs up to #444, as 2026-09-30 found them.
FBS_UV_PRS = (94, 96, 100, 134, 136, 177, 225, 283, 334, 355, 359, 364, 365, 384, 424, 437,
              438, 443, 444)  # fmt: skip


@live
class TestTheBotsDates(unittest.TestCase):
    def test_rumdl_0_2_77_is_dated_by_its_sdist(self) -> None:
        release = next(r for r in currency.pypi_releases("rumdl") if r.version == "0.2.77")
        self.assertEqual(currency.at(release.dated), "2026-09-26T14:54:23Z")
        self.assertEqual(currency.at(release.first), "2026-09-23T13:25:41Z")

    def test_setup_uv_v10_2_0_is_dated_by_its_release(self) -> None:
        found = dict(
            (release.version, currency.at(release.dated))
            for release, _ in currency.github_releases("astral-sh/setup-uv")
        )
        self.assertEqual(found.get("v10.2.0"), "2026-09-21T13:15:15Z")


@live
class TestTheRuleExplainsTheBot(unittest.TestCase):
    def test_no_newer_release_was_outside_the_window_when_a_pr_opened(self) -> None:
        window = datetime.timedelta(days=currency.DEFAULT_DAYS)
        passed_over = []
        for number in FBS_UV_PRS:
            raw = subprocess.run(
                ["gh", "api", f"repos/Machai-Kydoimos/fpga-board-sim/pulls/{number}",
                 "--jq", "[.created_at, .body, .title] | @json"],
                capture_output=True, text=True, check=True, timeout=120,
            ).stdout  # fmt: skip
            stamp, body, title = json.loads(raw)
            opened = currency.when(stamp)
            assert opened is not None
            moves = re.findall(r"Updates `([^`]+)` from \S+ to (\S+)", body or "")
            moves += re.findall(r"Bump (\S+) from \S+ to (\S+)", title)
            for name, proposed in {(n, v.rstrip(".")) for n, v in moves}:
                key = audit._version_key(proposed)
                for release in currency.pypi_releases(name):
                    newer = audit._version_key(release.version) > key
                    existed = release.first is not None and release.first <= opened
                    if (
                        newer
                        and existed
                        and currency.label(release.dated, opened, window) == "outside"
                    ):
                        passed_over.append(f"#{number} {name} {release.version}")
        self.assertEqual(passed_over, [], "the bot proposed older than the rule says it could")
