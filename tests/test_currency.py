"""Regression tests for currency.py -- Phase 2's gap, dated the way the bot dates it.

No network and no git: `_gh`, `_git` and `_json_url` are the seams, and the fakes
behind them answer from recorded data. Each case is a way the gap question went
wrong or could go wrong, measured on a real PR (#187):

- two replays of `fpga-board-sim` #436 took different rows on one gap;
- `audit.py`'s first-file date calls the bot behind on #443, where it was waiting;
- a configured cooldown, a Renovate PR or a moving major tag, where the 3-day
  default or a plain version comparison would answer wrongly.

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import contextlib
import io
import pathlib
import subprocess
import sys
import unittest
from typing import Any
from unittest import mock

sys.path.insert(
    0, str(pathlib.Path(__file__).resolve().parent.parent / "skills/dependabot-audit/scripts")
)

import currency
from currency import main

# pypi.org's JSON API for rumdl and ruff, recorded 2026-09-30: every file of each
# version, in the order the API lists them. The bot reads the last one (#187).
RUMDL_FILES = {
    "0.2.75": [
        {
            "filename": "rumdl-0.2.75-py3-none-macosx_10_12_x86_64.whl",
            "upload_time_iso_8601": "2026-09-20T20:06:26.082069Z",
            "yanked": False,
        },
        {
            "filename": "rumdl-0.2.75-py3-none-macosx_11_0_arm64.whl",
            "upload_time_iso_8601": "2026-09-20T20:06:18.619049Z",
            "yanked": False,
        },
        {
            "filename": "rumdl-0.2.75-py3-none-manylinux_2_28_aarch64.whl",
            "upload_time_iso_8601": "2026-09-20T20:06:21.561516Z",
            "yanked": False,
        },
        {
            "filename": "rumdl-0.2.75-py3-none-manylinux_2_28_x86_64.whl",
            "upload_time_iso_8601": "2026-09-20T20:06:30.971342Z",
            "yanked": False,
        },
        {
            "filename": "rumdl-0.2.75-py3-none-musllinux_1_2_aarch64.whl",
            "upload_time_iso_8601": "2026-09-20T20:06:23.932826Z",
            "yanked": False,
        },
        {
            "filename": "rumdl-0.2.75-py3-none-musllinux_1_2_x86_64.whl",
            "upload_time_iso_8601": "2026-09-20T20:06:33.316069Z",
            "yanked": False,
        },
        {
            "filename": "rumdl-0.2.75-py3-none-win_amd64.whl",
            "upload_time_iso_8601": "2026-09-20T20:06:28.613245Z",
            "yanked": False,
        },
        {
            "filename": "rumdl-0.2.75.tar.gz",
            "upload_time_iso_8601": "2026-09-20T20:06:35.700686Z",
            "yanked": False,
        },
    ],
    "0.2.76": [
        {
            "filename": "rumdl-0.2.76-py3-none-macosx_10_12_x86_64.whl",
            "upload_time_iso_8601": "2026-09-23T08:25:13.057029Z",
            "yanked": False,
        },
        {
            "filename": "rumdl-0.2.76-py3-none-macosx_11_0_arm64.whl",
            "upload_time_iso_8601": "2026-09-23T08:25:07.425988Z",
            "yanked": False,
        },
        {
            "filename": "rumdl-0.2.76-py3-none-manylinux_2_28_aarch64.whl",
            "upload_time_iso_8601": "2026-09-23T08:25:09.350623Z",
            "yanked": False,
        },
        {
            "filename": "rumdl-0.2.76-py3-none-manylinux_2_28_x86_64.whl",
            "upload_time_iso_8601": "2026-09-23T08:25:16.724888Z",
            "yanked": False,
        },
        {
            "filename": "rumdl-0.2.76-py3-none-musllinux_1_2_aarch64.whl",
            "upload_time_iso_8601": "2026-09-23T08:25:11.141877Z",
            "yanked": False,
        },
        {
            "filename": "rumdl-0.2.76-py3-none-musllinux_1_2_x86_64.whl",
            "upload_time_iso_8601": "2026-09-23T08:25:18.638558Z",
            "yanked": False,
        },
        {
            "filename": "rumdl-0.2.76-py3-none-win_amd64.whl",
            "upload_time_iso_8601": "2026-09-23T08:25:14.780148Z",
            "yanked": False,
        },
        {
            "filename": "rumdl-0.2.76.tar.gz",
            "upload_time_iso_8601": "2026-09-23T01:24:12.485459Z",
            "yanked": False,
        },
    ],
    "0.2.77": [
        {
            "filename": "rumdl-0.2.77-py3-none-macosx_10_12_x86_64.whl",
            "upload_time_iso_8601": "2026-09-23T13:25:47.365791Z",
            "yanked": False,
        },
        {
            "filename": "rumdl-0.2.77-py3-none-macosx_11_0_arm64.whl",
            "upload_time_iso_8601": "2026-09-23T13:25:41.335279Z",
            "yanked": False,
        },
        {
            "filename": "rumdl-0.2.77-py3-none-manylinux_2_28_aarch64.whl",
            "upload_time_iso_8601": "2026-09-23T13:25:43.728000Z",
            "yanked": False,
        },
        {
            "filename": "rumdl-0.2.77-py3-none-manylinux_2_28_x86_64.whl",
            "upload_time_iso_8601": "2026-09-23T13:25:50.740023Z",
            "yanked": False,
        },
        {
            "filename": "rumdl-0.2.77-py3-none-musllinux_1_2_aarch64.whl",
            "upload_time_iso_8601": "2026-09-23T13:25:45.589954Z",
            "yanked": False,
        },
        {
            "filename": "rumdl-0.2.77-py3-none-musllinux_1_2_x86_64.whl",
            "upload_time_iso_8601": "2026-09-23T13:25:52.284568Z",
            "yanked": False,
        },
        {
            "filename": "rumdl-0.2.77-py3-none-win_amd64.whl",
            "upload_time_iso_8601": "2026-09-23T13:25:48.960844Z",
            "yanked": False,
        },
        {
            "filename": "rumdl-0.2.77.tar.gz",
            "upload_time_iso_8601": "2026-09-26T14:54:23.375878Z",
            "yanked": False,
        },
    ],
    "0.2.78": [
        {
            "filename": "rumdl-0.2.78-py3-none-macosx_10_12_x86_64.whl",
            "upload_time_iso_8601": "2026-09-29T15:28:36.873067Z",
            "yanked": False,
        },
        {
            "filename": "rumdl-0.2.78-py3-none-macosx_11_0_arm64.whl",
            "upload_time_iso_8601": "2026-09-29T15:28:29.599897Z",
            "yanked": False,
        },
        {
            "filename": "rumdl-0.2.78-py3-none-manylinux_2_28_aarch64.whl",
            "upload_time_iso_8601": "2026-09-29T15:28:32.561418Z",
            "yanked": False,
        },
        {
            "filename": "rumdl-0.2.78-py3-none-manylinux_2_28_x86_64.whl",
            "upload_time_iso_8601": "2026-09-29T15:28:41.760970Z",
            "yanked": False,
        },
        {
            "filename": "rumdl-0.2.78-py3-none-musllinux_1_2_aarch64.whl",
            "upload_time_iso_8601": "2026-09-29T15:28:34.591934Z",
            "yanked": False,
        },
        {
            "filename": "rumdl-0.2.78-py3-none-musllinux_1_2_x86_64.whl",
            "upload_time_iso_8601": "2026-09-29T15:28:44.238686Z",
            "yanked": False,
        },
        {
            "filename": "rumdl-0.2.78-py3-none-win_amd64.whl",
            "upload_time_iso_8601": "2026-09-29T15:28:39.280167Z",
            "yanked": False,
        },
        {
            "filename": "rumdl-0.2.78.tar.gz",
            "upload_time_iso_8601": "2026-09-29T15:28:46.705837Z",
            "yanked": False,
        },
    ],
}
RUFF_FILES = {
    "0.16.8": [
        {
            "filename": "ruff-0.16.8-py3-none-linux_armv6l.whl",
            "upload_time_iso_8601": "2026-09-16T15:53:57.605047Z",
            "yanked": False,
        },
        {
            "filename": "ruff-0.16.8-py3-none-macosx_10_12_x86_64.whl",
            "upload_time_iso_8601": "2026-09-16T15:54:01.140172Z",
            "yanked": False,
        },
        {
            "filename": "ruff-0.16.8-py3-none-macosx_11_0_arm64.whl",
            "upload_time_iso_8601": "2026-09-16T15:54:03.998574Z",
            "yanked": False,
        },
        {
            "filename": "ruff-0.16.8-py3-none-manylinux_2_17_aarch64.manylinux2014_aarch64.whl",
            "upload_time_iso_8601": "2026-09-16T15:54:06.804373Z",
            "yanked": False,
        },
        {
            "filename": "ruff-0.16.8-py3-none-manylinux_2_17_armv7l.manylinux2014_armv7l.whl",
            "upload_time_iso_8601": "2026-09-16T15:54:09.552612Z",
            "yanked": False,
        },
        {
            "filename": "ruff-0.16.8-py3-none-manylinux_2_17_i686.manylinux2014_i686.whl",
            "upload_time_iso_8601": "2026-09-16T15:54:12.152853Z",
            "yanked": False,
        },
        {
            "filename": "ruff-0.16.8-py3-none-manylinux_2_17_ppc64le.manylinux2014_ppc64le.whl",
            "upload_time_iso_8601": "2026-09-16T15:54:15.489463Z",
            "yanked": False,
        },
        {
            "filename": "ruff-0.16.8-py3-none-manylinux_2_17_s390x.manylinux2014_s390x.whl",
            "upload_time_iso_8601": "2026-09-16T15:54:18.160442Z",
            "yanked": False,
        },
        {
            "filename": "ruff-0.16.8-py3-none-manylinux_2_17_x86_64.manylinux2014_x86_64.whl",
            "upload_time_iso_8601": "2026-09-16T15:54:20.743932Z",
            "yanked": False,
        },
        {
            "filename": "ruff-0.16.8-py3-none-manylinux_2_31_riscv64.whl",
            "upload_time_iso_8601": "2026-09-16T15:54:23.497281Z",
            "yanked": False,
        },
        {
            "filename": "ruff-0.16.8-py3-none-musllinux_1_2_aarch64.whl",
            "upload_time_iso_8601": "2026-09-16T15:54:26.185214Z",
            "yanked": False,
        },
        {
            "filename": "ruff-0.16.8-py3-none-musllinux_1_2_armv7l.whl",
            "upload_time_iso_8601": "2026-09-16T15:54:29.278735Z",
            "yanked": False,
        },
        {
            "filename": "ruff-0.16.8-py3-none-musllinux_1_2_i686.whl",
            "upload_time_iso_8601": "2026-09-16T15:54:32.036786Z",
            "yanked": False,
        },
        {
            "filename": "ruff-0.16.8-py3-none-musllinux_1_2_x86_64.whl",
            "upload_time_iso_8601": "2026-09-16T15:54:34.838921Z",
            "yanked": False,
        },
        {
            "filename": "ruff-0.16.8-py3-none-win32.whl",
            "upload_time_iso_8601": "2026-09-16T15:54:37.470525Z",
            "yanked": False,
        },
        {
            "filename": "ruff-0.16.8-py3-none-win_amd64.whl",
            "upload_time_iso_8601": "2026-09-16T15:54:40.488357Z",
            "yanked": False,
        },
        {
            "filename": "ruff-0.16.8-py3-none-win_arm64.whl",
            "upload_time_iso_8601": "2026-09-16T15:54:43.332603Z",
            "yanked": False,
        },
        {
            "filename": "ruff-0.16.8.tar.gz",
            "upload_time_iso_8601": "2026-09-16T15:54:46.688031Z",
            "yanked": False,
        },
    ],
    "0.16.9": [
        {
            "filename": "ruff-0.16.9-py3-none-linux_armv6l.whl",
            "upload_time_iso_8601": "2026-09-24T20:37:13.045587Z",
            "yanked": False,
        },
        {
            "filename": "ruff-0.16.9-py3-none-macosx_10_12_x86_64.whl",
            "upload_time_iso_8601": "2026-09-24T20:37:16.049486Z",
            "yanked": False,
        },
        {
            "filename": "ruff-0.16.9-py3-none-macosx_11_0_arm64.whl",
            "upload_time_iso_8601": "2026-09-24T20:37:17.957358Z",
            "yanked": False,
        },
        {
            "filename": "ruff-0.16.9-py3-none-manylinux_2_17_aarch64.manylinux2014_aarch64.whl",
            "upload_time_iso_8601": "2026-09-24T20:37:19.942251Z",
            "yanked": False,
        },
        {
            "filename": "ruff-0.16.9-py3-none-manylinux_2_17_armv7l.manylinux2014_armv7l.whl",
            "upload_time_iso_8601": "2026-09-24T20:37:21.872940Z",
            "yanked": False,
        },
        {
            "filename": "ruff-0.16.9-py3-none-manylinux_2_17_i686.manylinux2014_i686.whl",
            "upload_time_iso_8601": "2026-09-24T20:37:24.229986Z",
            "yanked": False,
        },
        {
            "filename": "ruff-0.16.9-py3-none-manylinux_2_17_ppc64le.manylinux2014_ppc64le.whl",
            "upload_time_iso_8601": "2026-09-24T20:37:26.307580Z",
            "yanked": False,
        },
        {
            "filename": "ruff-0.16.9-py3-none-manylinux_2_17_s390x.manylinux2014_s390x.whl",
            "upload_time_iso_8601": "2026-09-24T20:37:28.350957Z",
            "yanked": False,
        },
        {
            "filename": "ruff-0.16.9-py3-none-manylinux_2_17_x86_64.manylinux2014_x86_64.whl",
            "upload_time_iso_8601": "2026-09-24T20:37:30.624820Z",
            "yanked": False,
        },
        {
            "filename": "ruff-0.16.9-py3-none-manylinux_2_31_riscv64.whl",
            "upload_time_iso_8601": "2026-09-24T20:37:32.439274Z",
            "yanked": False,
        },
        {
            "filename": "ruff-0.16.9-py3-none-musllinux_1_2_aarch64.whl",
            "upload_time_iso_8601": "2026-09-24T20:37:34.581301Z",
            "yanked": False,
        },
        {
            "filename": "ruff-0.16.9-py3-none-musllinux_1_2_armv7l.whl",
            "upload_time_iso_8601": "2026-09-24T20:37:36.796345Z",
            "yanked": False,
        },
        {
            "filename": "ruff-0.16.9-py3-none-musllinux_1_2_i686.whl",
            "upload_time_iso_8601": "2026-09-24T20:37:38.857223Z",
            "yanked": False,
        },
        {
            "filename": "ruff-0.16.9-py3-none-musllinux_1_2_x86_64.whl",
            "upload_time_iso_8601": "2026-09-24T20:37:41.042165Z",
            "yanked": False,
        },
        {
            "filename": "ruff-0.16.9-py3-none-win32.whl",
            "upload_time_iso_8601": "2026-09-24T20:37:43.025323Z",
            "yanked": False,
        },
        {
            "filename": "ruff-0.16.9-py3-none-win_amd64.whl",
            "upload_time_iso_8601": "2026-09-24T20:37:44.944561Z",
            "yanked": False,
        },
        {
            "filename": "ruff-0.16.9-py3-none-win_arm64.whl",
            "upload_time_iso_8601": "2026-09-24T20:37:46.882829Z",
            "yanked": False,
        },
        {
            "filename": "ruff-0.16.9.tar.gz",
            "upload_time_iso_8601": "2026-09-24T20:37:49.416437Z",
            "yanked": False,
        },
    ],
}

# `fpga-board-sim`'s .github/dependabot.yml, recorded 2026-09-30 at main (20e39a9).
FBS_DEPENDABOT = (
    'version: 2\nupdates:\n  - package-ecosystem: "github-actions"\n    directory: "/"\n'
    '    schedule:\n      interval: "weekly"\n  - package-ecosystem: "uv"\n    directory: "/"\n'
    '    schedule:\n      interval: "weekly"\n    groups:\n      python-deps:\n'
    '        patterns: ["*"]\n        update-types: ["minor", "patch"]\n'
)

# astral-sh/setup-uv's v10 releases, `published_at` as the API gave it on 2026-09-30.
SETUP_UV = [
    ("v10.2.0", "2026-09-21T13:15:15Z"),
    ("v10.1.0", "2026-09-10T19:20:03Z"),
    ("v10.0.1", "2026-08-14T08:55:32Z"),
    ("v10.0.0", "2026-08-12T12:58:19Z"),
]
# #436's ci.yml pin, before and after, recorded from its diff.
PIN_BEFORE = (
    "      - uses: astral-sh/setup-uv@20cfd1bf945f4377ade1205e4dbc17946fc9a30d  # v10.0.1\n"
)
PIN_AFTER = "      - uses: astral-sh/setup-uv@bec219d24cd3e171d82865faccec33120bb574f4  # v10.1.0\n"


def uv_lock(**versions: str) -> str:
    blocks = [
        f'[[package]]\nname = "{name}"\nversion = "{version}"\n'
        'source = { registry = "https://pypi.org/simple" }\n'
        for name, version in versions.items()
    ]
    return "version = 1\n\n" + "\n".join(blocks)


class World:
    """GitHub, PyPI and one repository's refs -- all in memory."""

    def __init__(self, created: str, author: str = "dependabot[bot]") -> None:
        self.pr = (created, author)
        self.files: dict[tuple[str, str], str] = {}
        self.releases: dict[str, list[tuple[str, str]]] = {}
        self.pypi: dict[str, dict[str, list[dict[str, Any]]]] = {}
        self.changed_precommit = False

    def gh(self, args: list[str]) -> str | None:
        joined = " ".join(args)
        if "/pulls/" in joined:
            return None if self.pr[0] == "" else f"{self.pr[0]}\t{self.pr[1]}\n"
        for slug, rows in self.releases.items():
            if f"repos/{slug}/releases" in joined:
                return "".join(f"{tag}\t{stamp}\n" for tag, stamp in rows)
        return None

    def git(self, args: list[str]) -> tuple[int, str]:
        if args[0] == "show":
            ref, _, path = args[1].partition(":")
            text = self.files.get((ref, path))
            return (0, text) if text is not None else (128, "")
        if args[0] == "ls-tree":
            ref, _, directory = args[-1].partition(":")
            names = sorted(
                path.rsplit("/", 1)[1]
                for (at, path) in self.files
                if at == ref and path.startswith(directory + "/")
            )
            return (0, "\n".join(names) + "\n") if names else (128, "")
        if args[0] == "diff":
            return (1 if self.changed_precommit else 0), ""
        return 128, ""

    def json_url(self, url: str) -> Any:
        name = url.rstrip("/").split("/")[-2]
        if name not in self.pypi:
            raise currency.Unreadable(f"{url}: HTTP 404")
        return {"releases": self.pypi[name]}


class Harness(unittest.TestCase):
    def run_main(self, world: World, number: int = 443, *extra: str) -> tuple[int, str]:
        argv = [
            "currency.py", "--owner", "Machai-Kydoimos", "--name", "fpga-board-sim",
            "--number", str(number), "--ref", f"pr-{number}", "--base", "base",
            "--created-at", world.pr[0], *extra,
        ]  # fmt: skip
        out = io.StringIO()
        with (
            mock.patch("currency._gh", world.gh),
            mock.patch("currency._git", world.git),
            mock.patch("currency._json_url", world.json_url),
            mock.patch.object(sys, "argv", argv),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            try:
                code: int | str | None = main()
            except SystemExit as exc:
                code = exc.code
        return int(code or 0), out.getvalue()

    def pr_443(self, author: str = "dependabot[bot]") -> World:
        """ruff 0.16.8 -> 0.16.9 and rumdl 0.2.75 -> 0.2.76, opened 2026-09-28T13:10:04Z."""
        world = World("2026-09-28T13:10:04Z", author)
        world.files[("base", "uv.lock")] = uv_lock(ruff="0.16.8", rumdl="0.2.75")
        world.files[("pr-443", "uv.lock")] = uv_lock(ruff="0.16.9", rumdl="0.2.76")
        world.files[("base", ".github/dependabot.yml")] = FBS_DEPENDABOT
        world.pypi = {"rumdl": RUMDL_FILES, "ruff": RUFF_FILES}
        return world

    def pr_436(self) -> World:
        world = World("2026-09-14T13:07:45Z")
        world.files[("base", ".github/workflows/ci.yml")] = PIN_BEFORE
        world.files[("pr-436", ".github/workflows/ci.yml")] = PIN_AFTER
        world.files[("base", ".github/dependabot.yml")] = FBS_DEPENDABOT
        world.releases["astral-sh/setup-uv"] = SETUP_UV
        return world

    def pr_442(self) -> World:
        world = World("2026-09-28T13:07:43Z")
        world.files[("base", ".github/workflows/ci.yml")] = PIN_AFTER
        world.files[("pr-442", ".github/workflows/ci.yml")] = PIN_AFTER.replace(
            "bec219d24cd3e171d82865faccec33120bb574f4  # v10.1.0",
            "c18668ad3cf93ea998bef934396af7bb5c839dc7  # v10.2.0",
        )
        world.files[("base", ".github/dependabot.yml")] = FBS_DEPENDABOT
        world.releases["astral-sh/setup-uv"] = SETUP_UV
        return world


class TestTheBotsDateIsTheLastFileListed(Harness):
    def test_rumdl_0_2_77_was_inside_the_window_on_443(self):
        """Its wheels landed 2026-09-23T13:25Z and its sdist, listed last,
        2026-09-26T14:54Z. By the first file the bot was behind; it was waiting,
        and it proposed 0.2.76."""
        code, out = self.run_main(self.pr_443())
        self.assertEqual(code, 1, out)
        line = next(ln for ln in out.splitlines() if ln.strip().startswith("0.2.77"))
        self.assertIn("2026-09-26T14:54:23Z", line)
        self.assertIn("inside the window when the PR opened: the bot was waiting", line)
        self.assertIn("its first file landed 2026-09-23T13:25:41Z", out)
        self.assertNotIn("passed it over", out)

    def test_it_is_the_last_listed_file_and_not_the_latest_upload(self):
        """rumdl 0.2.76's sdist is listed last and was uploaded first, at 01:24:12;
        its last wheel landed at 08:25:18. The bot reads the listed one. #438's
        moment, a PR taking rumdl 0.2.74 -> 0.2.75."""
        world = self.pr_443()
        world.pr = ("2026-09-21T13:11:46Z", "dependabot[bot]")
        world.files[("base", "uv.lock")] = uv_lock(ruff="0.16.9", rumdl="0.2.74")
        world.files[("pr-443", "uv.lock")] = uv_lock(ruff="0.16.9", rumdl="0.2.75")
        _, out = self.run_main(world)
        line = next(ln for ln in out.splitlines() if ln.strip().startswith("0.2.76"))
        self.assertIn("2026-09-23T01:24:12Z", line)
        self.assertNotIn("08:25:18", out)


class TestTheRowIsTheTables(Harness):
    def test_releases_the_bot_never_weighed_offer_no_follow_up(self):
        """#436: v10.2.0 came a week after the PR opened. The 0.56.0 replay said
        merge as-is; the 0.57.0 replay said merge, then follow up."""
        code, out = self.run_main(self.pr_436(), 436)
        self.assertEqual(code, 1, out)
        self.assertIn("after the PR opened: the bot never weighed it", out)
        self.assertIn(
            '"A gap exists inside the cooldown window" -> merge as-is, and no follow-up', out
        )

    def test_a_release_the_bot_passed_over_selects_the_follow_up_row(self):
        world = self.pr_443()
        world.pr = ("2026-10-09T00:00:00Z", "dependabot[bot]")
        code, out = self.run_main(world)
        self.assertEqual(code, 1, out)
        self.assertIn("outside the window when the PR opened: the bot passed it over", out)
        self.assertIn(
            '"A gap exists, outside the cooldown" -> merge as-is, then follow up, to 0.2.78', out
        )

    def test_the_target_is_the_newest_release_the_bot_passed_over(self):
        """On 2026-10-01T00:00Z, 0.2.77 was over three days old and 0.2.78 was not."""
        world = self.pr_443()
        world.pr = ("2026-10-01T00:00:00Z", "dependabot[bot]")
        _, out = self.run_main(world)
        self.assertIn("follow up, to 0.2.77", out)

    def test_a_yanked_release_is_not_a_candidate(self):
        """The bot filters yanked versions before it looks at dates."""
        world = self.pr_443()
        world.pypi = {
            "ruff": RUFF_FILES,
            "rumdl": {
                v: [dict(f, yanked=(v == "0.2.78")) for f in files]
                for v, files in RUMDL_FILES.items()
            },
        }
        _, out = self.run_main(world)
        self.assertIn("rumdl 0.2.75 -> 0.2.76: 1 newer release(s)", out)
        self.assertIn("yanked, so not a candidate: 0.2.78", out)

    def test_nothing_newer_is_current(self):
        """#442 proposes v10.2.0, the newest release."""
        code, out = self.run_main(self.pr_442(), 442)
        self.assertEqual(code, 0, out)
        self.assertIn("RESULT: CURRENT", out)


class TestTheDefaultWindowIsNeverAssumed(Harness):
    def test_a_default_days_block_sets_the_window(self):
        """cli/cli sets `cooldown: default-days: 3` and nothing else (#13996)."""
        world = self.pr_443()
        world.files[("base", ".github/dependabot.yml")] = FBS_DEPENDABOT + (
            "    cooldown:\n      default-days: 7\n"
        )
        _, out = self.run_main(world)
        self.assertIn("window: `cooldown: default-days: 7`", out)
        self.assertIn("boundary 2026-09-21T13:10:04Z", out)

    def test_a_richer_cooldown_block_makes_the_labels_underivable(self):
        world = self.pr_443()
        world.files[("base", ".github/dependabot.yml")] = FBS_DEPENDABOT + (
            "    cooldown:\n      default-days: 7\n      semver-major-days: 30\n"
        )
        code, out = self.run_main(world)
        self.assertEqual(code, 1, out)
        self.assertIn("its `cooldown:` block sets more than `default-days`", out)
        self.assertIn("window underivable, so not placed", out)
        self.assertIn("underivable: a release above could not be placed", out)

    def test_the_config_is_read_at_the_base_and_not_the_prs_ref(self):
        """The PR's own tree cannot choose the window it is judged by."""
        world = self.pr_443()
        world.files[("pr-443", ".github/dependabot.yml")] = FBS_DEPENDABOT + (
            "    cooldown:\n      default-days: 30\n"
        )
        _, out = self.run_main(world)
        self.assertIn("Dependabot's default", out)

    def test_a_renovate_pr_is_not_given_dependabots_window(self):
        code, out = self.run_main(self.pr_443("renovate[bot]"))
        self.assertEqual(code, 1, out)
        self.assertIn("a Renovate PR, and its `minimumReleaseAge` is not computed here", out)
        self.assertNotIn("the bot was waiting", out)

    def test_an_ecosystem_the_config_does_not_list_is_underivable(self):
        world = self.pr_443()
        world.files[("base", ".github/dependabot.yml")] = FBS_DEPENDABOT.split(
            '  - package-ecosystem: "uv"'
        )[0]
        _, out = self.run_main(world)
        self.assertIn("no `updates` entry for uv or pip", out)


class TestAnActionIsDatedByItsRelease(Harness):
    def test_a_moving_major_is_a_gap_only_above_its_major(self):
        """`nickg/setup-nvc@<sha>  # v1` picks up v1.x by itself."""
        world = World("2026-09-28T13:07:43Z")
        sha_a, sha_b = "a" * 40, "b" * 40
        world.files[("base", ".github/workflows/ci.yml")] = f"  - uses: o/nvc@{sha_a}  # v1\n"
        world.files[("pr-9", ".github/workflows/ci.yml")] = f"  - uses: o/nvc@{sha_b}  # v1\n"
        world.files[("base", ".github/dependabot.yml")] = FBS_DEPENDABOT
        world.releases["o/nvc"] = [
            ("v2.0.0", "2026-09-01T00:00:00Z"),
            ("v1.3.0", "2026-08-01T00:00:00Z"),
            ("v1.2.0", "2026-07-01T00:00:00Z"),
        ]
        _, out = self.run_main(world, 9)
        self.assertIn("o/nvc v1 -> v1: 1 newer release(s)", out)
        self.assertIn("v2.0.0", out)
        self.assertNotIn("v1.3.0", out)

    def test_a_subpath_action_is_its_repository(self):
        """`actions.md`: `github/codeql-action/init@...` is how that action is pinned."""
        found = currency.USES.match(
            "      - uses: github/codeql-action/init@" + "c" * 40 + "  # v4.31.0"
        )
        assert found is not None
        self.assertEqual(found.group("slug"), "github/codeql-action")
        self.assertEqual(
            currency.version_of((found.group("ref"), found.group("comment"))), "v4.31.0"
        )

    def test_a_failed_release_read_is_underivable(self):
        world = self.pr_436()
        del world.releases["astral-sh/setup-uv"]
        code, out = self.run_main(world, 436)
        self.assertEqual(code, 1, out)
        self.assertIn("UNDERIVABLE -- gh api repos/astral-sh/setup-uv/releases failed", out)

    def test_gh_is_read_by_its_status_not_its_stdout(self):
        """`gh` writes a 404's body to stdout, so a capture holds
        `{"message":"Not Found"}` and looks like an answer (#39's shape)."""
        failed = subprocess.CompletedProcess(["gh"], 1, stdout='{"message":"Not Found"}', stderr="")
        with mock.patch("currency.subprocess.run", return_value=failed):
            self.assertIsNone(currency._gh(["api", "repos/git/git/releases"]))

    def test_an_action_with_no_releases_is_underivable(self):
        world = self.pr_436()
        world.releases["astral-sh/setup-uv"] = []
        code, out = self.run_main(world, 436)
        self.assertEqual(code, 1, out)
        self.assertIn("UNDERIVABLE -- the action publishes no releases", out)


class TestTheDefaultBranchSaysWhatLanded(Harness):
    def test_the_default_branch_pin_is_printed(self):
        """#179: both #438 replays improvised `git show origin/main:uv.lock | grep`."""
        world = self.pr_443()
        world.files[("origin/main", "uv.lock")] = uv_lock(ruff="0.16.8", rumdl="0.2.78")
        _, out = self.run_main(world, 443, "--default", "origin/main")
        self.assertIn("origin/main pins 0.2.78 now", out)
        self.assertIn("origin/main pins 0.16.8 now", out)

    def test_an_actions_default_pin_is_printed(self):
        world = self.pr_436()
        world.files[("origin/main", ".github/workflows/ci.yml")] = PIN_AFTER
        _, out = self.run_main(world, 436, "--default", "origin/main")
        self.assertIn("origin/main pins v10.1.0 now", out)

    def test_without_default_nothing_is_claimed_about_it(self):
        _, out = self.run_main(self.pr_443())
        self.assertNotIn("pins", out)


class TestItSaysWhatToReadNext(Harness):
    def test_the_changelog_ranges_are_the_adopted_ones_and_the_gaps(self):
        _, out = self.run_main(self.pr_443())
        self.assertIn(
            'changelog.py ranges, adopted and gap: "ruff 0.16.8 0.16.9" '
            '"rumdl 0.2.75 0.2.76" "rumdl 0.2.76 0.2.78 --gap"',
            out,
        )

    def test_the_row_is_conditional_on_what_outranks_it(self):
        """The #438 replay under 0.59.0's first cut quoted `gap inside the cooldown
        window` for rumdl and dropped it from the follow-up, over 37 fix-mode lines."""
        _, out = self.run_main(self.pr_443())
        self.assertIn(
            "row, unless the gap carries a Security entry or a fix-mode fix: "
            '"A gap exists inside the cooldown window"',
            out,
        )

    def test_the_rows_that_outrank_it_are_named(self):
        _, out = self.run_main(self.pr_443())
        self.assertIn("A `Security` entry or a fix-mode fix in a gap takes its own row", out)

    def test_a_pre_commit_bump_is_left_to_its_reference(self):
        world = self.pr_436()
        world.changed_precommit = True
        _, out = self.run_main(world, 436)
        self.assertIn("its hooks are not dated here", out)
        self.assertIn("no `updates` entry for pre-commit", out)


class TestWhatCannotBeReadIsNotCurrent(Harness):
    def test_an_unreadable_pr_exits_2(self):
        world = self.pr_443()
        world.pr = ("", "")
        code, _ = self.run_main(world)
        self.assertEqual(code, 2)

    def test_a_handoff_from_another_moment_exits_2(self):
        """A stale phase0.env would label every release against the wrong opening."""
        world = self.pr_443()
        code, _ = self.run_main(world, 443, "--created-at", "2026-09-21T13:11:46Z")
        self.assertEqual(code, 2)

    def test_a_package_pypi_cannot_serve_is_underivable(self):
        world = self.pr_443()
        del world.pypi["rumdl"]
        code, out = self.run_main(world)
        self.assertEqual(code, 1, out)
        self.assertIn("rumdl 0.2.75 -> 0.2.76: UNDERIVABLE", out)

    def test_a_private_registry_is_not_read_as_pypi(self):
        world = self.pr_443()
        for ref in ("base", "pr-443"):
            world.files[(ref, "uv.lock")] = world.files[(ref, "uv.lock")].replace(
                "https://pypi.org/simple", "https://pypi.example.invalid/simple"
            )
        code, out = self.run_main(world)
        self.assertEqual(code, 1, out)
        self.assertIn("reads pypi.org's JSON API only", out)


if __name__ == "__main__":
    unittest.main()
