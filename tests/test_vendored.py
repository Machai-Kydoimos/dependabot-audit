"""Regression tests for vendored.py -- advisories on the crates a compiled wheel ships.

No network: `_http` is the one seam, and the fake behind it serves real zip files
built here by byte range, a PyPI JSON index and OSV's two endpoints. The wheels
are real archives, so `zipfile` and the range reader run as they do live.

Each case is a way the question goes wrong that was measured on a real release
(#171): an advisory fixed only above the PR and never named in its notes (ruff
0.16.9, `salsa`); a crate in `Cargo.lock` that no wheel ships (rumdl 0.2.76,
`rustls`); wheels that differ by platform (rumdl 0.2.76, 214 to 219 components);
and a release with no SBOM at all (pydantic-core 2.41.5).

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import contextlib
import io
import json
import pathlib
import re
import sys
import unittest
import zipfile
from collections.abc import Mapping
from typing import Any, ClassVar
from unittest import mock

sys.path.insert(
    0, str(pathlib.Path(__file__).resolve().parent.parent / "skills/dependabot-audit/scripts")
)

import vendored
from vendored import _key, classify, main, moved

PLATFORMS = ("manylinux_2_17_x86_64", "macosx_11_0_arm64", "win_amd64")


def crate(name: str, version: str, scope: str = "required") -> dict[str, Any]:
    return {"name": name, "version": version, "scope": scope, "purl": f"pkg:cargo/{name}@{version}"}


def wheel_bytes(package: str, version: str, components: list[dict[str, Any]] | None) -> bytes:
    """A real wheel: a module, a METADATA, and -- unless None -- a CycloneDX SBOM."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{package}/__init__.py", "x = 1\n" * 2000)
        zf.writestr(f"{package}-{version}.dist-info/METADATA", f"Name: {package}\n")
        if components is not None:
            sbom = {"bomFormat": "CycloneDX", "specVersion": "1.5", "components": components}
            zf.writestr(
                f"{package}-{version}.dist-info/sboms/{package}.cyclonedx.json", json.dumps(sbom)
            )
    return out.getvalue()


class World:
    """PyPI, the wheels on its CDN, and OSV -- all in memory."""

    def __init__(self) -> None:
        self.releases: dict[tuple[str, str], list[tuple[str, bytes]]] = {}
        self.latest: dict[str, str] = {}
        self.advisories: dict[str, list[str]] = {}  # purl -> ids
        self.records: dict[str, dict[str, Any]] = {}
        self.requests: list[str] = []
        self.ignore_range = False
        self.broken: set[str] = set()

    def release(
        self, package: str, version: str, per_platform: Mapping[str, list[dict[str, Any]] | None]
    ) -> None:
        files = []
        for platform, components in per_platform.items():
            tag = "none-any" if platform == "any" else f"abi3-{platform}"
            name = f"{package}-{version}-py3-{tag}.whl"
            files.append((name, wheel_bytes(package, version, components)))
        self.releases[(package, version)] = files

    def advisory(
        self, advisory_id: str, purl: str, kind: str = "", summary: str = "a flaw"
    ) -> None:
        self.advisories.setdefault(purl, []).append(advisory_id)
        record: dict[str, Any] = {"id": advisory_id, "summary": summary, "affected": [{}]}
        if kind:
            record["affected"][0]["database_specific"] = {"informational": kind}
        self.records[advisory_id] = record

    def http(
        self, url: str, *, data: bytes | None = None, headers: dict[str, str] | None = None
    ) -> tuple[int, dict[str, str], bytes]:
        self.requests.append(url)
        headers = headers or {}
        if url.startswith(vendored.PYPI):
            parts = url[len(vendored.PYPI) + 1 :].split("/")
            if len(parts) == 2:
                return 200, {}, json.dumps({"info": {"version": self.latest[parts[0]]}}).encode()
            files = self.releases.get((parts[0], parts[1]))
            if files is None:
                return 404, {}, b""
            urls = [
                {
                    "filename": f,
                    "url": f"https://files/{f}",
                    "size": len(b),
                    "packagetype": "bdist_wheel",
                }
                for f, b in files
            ]
            return 200, {}, json.dumps({"urls": urls}).encode()
        if url.startswith("https://files/"):
            name = url.rsplit("/", 1)[1]
            if name in self.broken:
                return 500, {}, b""
            body = next(
                b for (f, b) in (x for fs in self.releases.values() for x in fs) if f == name
            )
            wanted = re.match(r"bytes=(\d+)-(\d+)", headers.get("Range", ""))
            if self.ignore_range or not wanted:
                return 200, {}, body
            start, end = int(wanted.group(1)), int(wanted.group(2))
            return 206, {"Content-Range": f"bytes {start}-{end}/{len(body)}"}, body[start : end + 1]
        if url.endswith("/querybatch"):
            # One id per page, as OSV pages a purl with many advisories.
            queries = json.loads(data or b"{}")["queries"]
            results = []
            for q in queries:
                ids = self.advisories.get(q["package"]["purl"], [])
                page: dict[str, Any] = {"vulns": [{"id": i} for i in ids[:1]]}
                if len(ids) > 1:
                    page["next_page_token"] = "1"  # noqa: S105 -- OSV's page cursor
                results.append(page)
            return 200, {}, json.dumps({"results": results}).encode()
        if url.endswith("/query"):
            asked = json.loads(data or b"{}")
            ids = self.advisories.get(asked["package"]["purl"], [])
            at = int(asked["page_token"])
            page = {"vulns": [{"id": i} for i in ids[at : at + 1]]}
            if at + 1 < len(ids):
                page["next_page_token"] = str(at + 1)
            return 200, {}, json.dumps(page).encode()
        if "/vulns/" in url:
            return 200, {}, json.dumps(self.records[url.rsplit("/", 1)[1]]).encode()
        return 404, {}, b""


class Harness(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World()

    def run_main(self, argv: list[str]) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with (
            mock.patch("vendored._http", self.world.http),
            mock.patch.object(sys, "argv", ["vendored.py", *argv]),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            try:
                code: int | str | None = main()
            except SystemExit as exc:
                code = exc.code
        return int(code or 0), out.getvalue(), err.getvalue()

    def ruff_like(self) -> None:
        """ruff 0.16.7 and 0.16.8 ship salsa 0.28.2; 0.16.9 ships 0.28.5."""
        for version, salsa in (("0.16.7", "0.28.2"), ("0.16.8", "0.28.2"), ("0.16.9", "0.28.5")):
            shipped = [
                crate("salsa", salsa),
                crate("regex", "1.12.0"),
                crate("cc", "1.2.0", "excluded"),
            ]
            self.world.release("ruff", version, {p: shipped for p in PLATFORMS})
        self.world.latest["ruff"] = "0.16.9"
        self.world.advisory(
            "RUSTSEC-2026-0308", "pkg:cargo/salsa@0.28.2", "unsound", "Use-after-free"
        )


class TestTheKnownAnswers(Harness):
    def test_a_fix_above_the_pr_that_the_notes_never_named(self):
        """ruff 0.16.9: the #438 replay read its notes as nothing security-shaped."""
        self.ruff_like()
        code, out, _ = self.run_main(
            ["--package", "ruff", "--current", "0.16.7", "--proposed", "0.16.8"]
        )
        self.assertEqual(code, 1, out)
        self.assertIn("FIXED ABOVE THIS PR  RUSTSEC-2026-0308  salsa 0.28.2", out)
        self.assertIn("unsound: Use-after-free", out)
        self.assertIn("proposed 0.16.8: in 3 of 3 wheel(s)", out)
        self.assertIn("latest 0.16.9: not shipped, ships salsa 0.28.5", out)

    def test_a_crate_only_the_lockfile_has_is_never_queried(self):
        """rumdl 0.2.76's rustls: in `Cargo.lock`, in no wheel. The SBOM is the
        shipped set, so the advisory on it is never asked about."""
        for version in ("0.2.75", "0.2.76"):
            self.world.release("rumdl", version, {p: [crate("regex", "1.12.0")] for p in PLATFORMS})
        self.world.latest["rumdl"] = "0.2.76"
        self.world.advisory("RUSTSEC-2026-0285", "pkg:cargo/rustls@0.23.38")
        code, out, _ = self.run_main(
            ["--package", "rumdl", "--current", "0.2.75", "--proposed", "0.2.76"]
        )
        self.assertEqual(code, 0, out)
        self.assertIn("RESULT: CLEAN", out)
        self.assertNotIn("RUSTSEC-2026-0285", out)

    def test_every_page_of_advisories_is_read(self):
        """OSV pages a purl with many advisories; the second page is a real one."""
        self.ruff_like()
        self.world.advisory("RUSTSEC-2026-9999", "pkg:cargo/salsa@0.28.2", summary="second page")
        _, out, _ = self.run_main(
            ["--package", "ruff", "--current", "0.16.7", "--proposed", "0.16.8"]
        )
        self.assertIn("RUSTSEC-2026-9999", out)

    def test_a_build_time_crate_is_not_shipped(self):
        """`scope: excluded` marks `cc` and its kind; an advisory on one is not exposure."""
        self.ruff_like()
        self.world.advisory("RUSTSEC-2099-0001", "pkg:cargo/cc@1.2.0")
        _, out, _ = self.run_main(
            ["--package", "ruff", "--current", "0.16.7", "--proposed", "0.16.8"]
        )
        self.assertNotIn("RUSTSEC-2099-0001", out)


class TestEveryWheelIsRead(Harness):
    def test_a_crate_one_platform_ships_is_found_on_that_platform(self):
        """rumdl 0.2.76's wheels differ; reading the first alone misses Windows."""
        per = {
            "manylinux_2_17_x86_64": [crate("inotify", "0.11.0")],
            "macosx_11_0_arm64": [crate("fsevent-sys", "4.1.0")],
            "win_amd64": [crate("windows-sys", "0.59.0")],
        }
        self.world.release("tool", "1.0", per)
        self.world.release("tool", "1.1", per)
        self.world.latest["tool"] = "1.1"
        self.world.advisory("RUSTSEC-2099-0002", "pkg:cargo/windows-sys@0.59.0")
        code, out, _ = self.run_main(["--package", "tool", "--current", "1.0", "--proposed", "1.1"])
        self.assertEqual(code, 1, out)
        self.assertIn("STANDING  RUSTSEC-2099-0002  windows-sys 0.59.0", out)
        self.assertIn("proposed 1.1: in 1 of 3 wheel(s)", out)

    def test_the_wheel_is_read_by_range_not_downloaded(self):
        self.ruff_like()
        self.run_main(["--package", "ruff", "--current", "0.16.7", "--proposed", "0.16.8"])
        self.assertTrue(any("files/" in r for r in self.world.requests))

    def test_a_server_that_ignores_range_is_still_read(self):
        self.ruff_like()
        self.world.ignore_range = True
        code, out, _ = self.run_main(
            ["--package", "ruff", "--current", "0.16.7", "--proposed", "0.16.8"]
        )
        self.assertEqual(code, 1, out)
        self.assertIn("FIXED ABOVE THIS PR", out)

    def test_a_wheel_that_cannot_be_read_is_underivable(self):
        self.ruff_like()
        self.world.broken.add("ruff-0.16.8-py3-abi3-win_amd64.whl")
        code, out, _ = self.run_main(
            ["--package", "ruff", "--current", "0.16.7", "--proposed", "0.16.8"]
        )
        self.assertEqual(code, 1, out)
        self.assertIn(
            "UNDERIVABLE  ruff-0.16.8-py3-abi3-win_amd64.whl: range read answered HTTP 500", out
        )


class TestWhatCannotBeReadIsNotClean(Harness):
    def test_a_release_with_no_sbom_is_underivable_and_not_read_to_the_end(self):
        """pydantic-core 2.41.5: 120 wheels, none with an SBOM."""
        bare = {f"plat{i}": None for i in range(10)}
        for version in ("2.41.4", "2.41.5"):
            self.world.release("pydantic-core", version, bare)
        self.world.latest["pydantic-core"] = "2.41.5"
        code, out, _ = self.run_main(
            ["--package", "pydantic-core", "--current", "2.41.4", "--proposed", "2.41.5"]
        )
        self.assertEqual(code, 1, out)
        self.assertIn("no SBOM in the first 3 of 10 compiled wheel(s)", out)
        read = [r for r in self.world.requests if "files/" in r and "2.41.5" in r]
        self.assertLessEqual(len({r for r in read}), 3, read)

    def test_an_advisory_the_unread_pin_may_ship_is_not_called_standing(self):
        """pydantic-core 2.49.0 has SBOMs and ships `lru` with an advisory; 2.41.x
        cannot say either way, so the row is underivable, never `standing`."""
        for version in ("2.41.4", "2.41.5"):
            self.world.release("pc", version, {f"p{i}": None for i in range(4)})
        self.world.release("pc", "2.49.0", {p: [crate("lru", "0.18.0")] for p in PLATFORMS})
        self.world.latest["pc"] = "2.49.0"
        self.world.advisory("RUSTSEC-2026-0253", "pkg:cargo/lru@0.18.0", "unsound")
        _, out, _ = self.run_main(
            ["--package", "pc", "--current", "2.41.4", "--proposed", "2.41.5"]
        )
        self.assertIn("UNDERIVABLE AT THE PROPOSED PIN, AND THE LATEST SHIPS IT", out)
        self.assertIn("proposed 2.41.5: unknown -- its shipped set was not read in full", out)

    def test_a_pure_python_wheel_vendors_nothing(self):
        self.world.release("jinja2", "3.1.5", {"any": None})
        self.world.release("jinja2", "3.1.6", {"any": None})
        self.world.latest["jinja2"] = "3.1.6"
        code, out, _ = self.run_main(
            ["--package", "jinja2", "--current", "3.1.5", "--proposed", "3.1.6"]
        )
        self.assertEqual(code, 0, out)
        self.assertIn("RESULT: NOTHING VENDORED", out)

    def test_a_package_with_no_wheel_is_underivable_not_pure_python(self):
        """#188. actionlint-py publishes one sdist and no wheel, and its build fetches
        the compiled actionlint Go binary. 0.58.0 printed `NOTHING VENDORED -- none
        of 1 moved package(s) is compiled` and exited 0 on `fpga-board-sim` #444."""
        self.world.release("actionlint-py", "1.7.12.24", {})
        self.world.release("actionlint-py", "1.7.12.25", {})
        self.world.latest["actionlint-py"] = "1.7.12.25"
        code, out, _ = self.run_main(
            ["--package", "actionlint-py", "--current", "1.7.12.24", "--proposed", "1.7.12.25"]
        )
        self.assertEqual(code, 1, out)
        self.assertIn("actionlint-py 1.7.12.25: UNDERIVABLE -- no wheel", out)
        self.assertNotIn("NOTHING VENDORED", out)

    def test_a_release_the_registry_cannot_serve_is_not_nothing_vendored(self):
        """Found reading #188's path: the one moved package's release JSON fails, the
        loop prints UNDERIVABLE and never counts it read, and 0.58.0 then ended
        `RESULT: NOTHING VENDORED`, exit 0, two lines below its own UNDERIVABLE."""
        self.world.release("t", "1.1", {p: [crate("regex", "1.12.0")] for p in PLATFORMS})
        self.world.latest["t"] = "1.1"
        code, out, _ = self.run_main(["--package", "t", "--current", "1.0", "--proposed", "1.1"])
        self.assertEqual(code, 1, out)
        self.assertIn("t: UNDERIVABLE", out)
        self.assertNotIn("NOTHING VENDORED", out)

    def test_an_unmaintained_notice_alone_is_not_a_finding(self):
        """ruff ships two, `paste` and `proc-macro-error2`, both compile-time."""
        for version in ("1.0", "1.1"):
            self.world.release("t", version, {p: [crate("paste", "1.0.15")] for p in PLATFORMS})
        self.world.latest["t"] = "1.1"
        self.world.advisory("RUSTSEC-2024-0436", "pkg:cargo/paste@1.0.15", "unmaintained")
        code, out, _ = self.run_main(["--package", "t", "--current", "1.0", "--proposed", "1.1"])
        self.assertEqual(code, 0, out)
        self.assertIn(
            "unmaintained, noted and not a finding: paste 1.0.15 (RUSTSEC-2024-0436)", out
        )


class TestWhereAnAdvisoryStands(unittest.TestCase):
    def test_each_state(self):
        cases: dict[str, dict[str, bool | None]] = {
            "fixed by this PR": {"current": True, "proposed": False},
            "introduced by this PR": {"current": False, "proposed": True},
            "fixed above this PR": {"current": True, "proposed": True, "latest": False},
            "standing": {"current": True, "proposed": True, "latest": True},
            "introduced above this PR": {"current": False, "proposed": False, "latest": True},
            "underivable at the proposed pin": {"current": True, "proposed": None},
            "underivable at the current pin": {"current": None, "proposed": True},
        }
        for expected, present in cases.items():
            with self.subTest(expected=expected):
                self.assertEqual(classify(present), expected)

    def test_a_pr_can_introduce_what_the_latest_fixes(self):
        self.assertEqual(
            classify({"current": False, "proposed": True, "latest": False}),
            "introduced by this PR, fixed above this PR",
        )

    def test_a_package_the_pr_adds_has_no_current_pin_to_leave_unread(self):
        """`main()` deletes `current` for a package the base never pinned, and 0.59.0
        read the missing key as an unread one: `shipped at the proposed pin, current
        pin unread`. There is nothing to read. The PR adds the package, so whatever
        it ships, this PR introduces (#196)."""
        self.assertEqual(classify({"proposed": True}), "introduced by this PR")
        self.assertEqual(vendored.verdict({"proposed": True}), vendored.HOLD)


class TestEachAdvisoryNamesItsRow(Harness):
    """#196. Phase 7's row 2 Held on any advisory "in a version being adopted", and
    `vendored.py` had placed each one -- fixed, introduced, standing -- without saying
    what that place does to the verdict. Two live audits (this repo's #193, and
    `fpga-board-sim` #451) met a standing advisory and reached merge only by arguing
    past row 2. The row is now printed under each advisory, as `currency.py` prints
    its own, so the table is a lookup and not a reading."""

    def two_releases(self, shipped: dict[str, list[dict[str, Any]]], latest: str) -> None:
        for version, crates in shipped.items():
            self.world.release("tool", version, {p: crates for p in PLATFORMS})
        self.world.latest["tool"] = latest

    def test_a_standing_advisory_is_not_a_hold(self):
        """`fpga-board-sim` #451: ruff 0.16.9 -> 0.16.10, crossbeam-epoch 0.9.18 in
        all 17 wheels at both, 0.16.10 the latest (vendored.py, 2026-10-07)."""
        epoch = [crate("crossbeam-epoch", "0.9.18")]
        for version in ("0.16.9", "0.16.10"):
            self.world.release("ruff", version, {p: epoch for p in PLATFORMS})
        self.world.latest["ruff"] = "0.16.10"
        self.world.advisory("RUSTSEC-2026-0204", "pkg:cargo/crossbeam-epoch@0.9.18")
        code, out, _ = self.run_main(
            ["--package", "ruff", "--current", "0.16.9", "--proposed", "0.16.10"]
        )
        self.assertEqual(code, 1, out)
        self.assertIn("STANDING  RUSTSEC-2026-0204  crossbeam-epoch 0.9.18", out)
        self.assertIn('row: "standing" -> not a Hold on this bump', out)
        self.assertNotIn("-> Hold", out)

    def test_a_fix_above_names_its_follow_up(self):
        """ruff 0.16.9's salsa, seen from 0.16.7 -> 0.16.8 (#171)."""
        self.ruff_like()
        _, out, _ = self.run_main(
            ["--package", "ruff", "--current", "0.16.7", "--proposed", "0.16.8"]
        )
        self.assertIn('row: "fixed above this PR" -> merge as-is, then follow up to 0.16.9', out)

    def test_an_advisory_this_pr_introduces_is_a_hold(self):
        self.two_releases(
            {"1.0": [crate("regex", "1.12.0")], "1.1": [crate("lru", "0.18.0")]}, "1.1"
        )
        self.world.advisory("RUSTSEC-2026-0253", "pkg:cargo/lru@0.18.0", "unsound")
        _, out, _ = self.run_main(["--package", "tool", "--current", "1.0", "--proposed", "1.1"])
        self.assertIn("INTRODUCED BY THIS PR  RUSTSEC-2026-0253", out)
        self.assertIn('row: "introduced by this PR" -> Hold', out)

    def test_a_pr_that_introduces_what_the_latest_fixes_is_still_a_hold(self):
        self.two_releases(
            {
                "1.0": [crate("regex", "1.12.0")],
                "1.1": [crate("lru", "0.18.0")],
                "1.2": [crate("lru", "0.18.1")],
            },
            "1.2",
        )
        self.world.advisory("RUSTSEC-2026-0253", "pkg:cargo/lru@0.18.0", "unsound")
        _, out, _ = self.run_main(["--package", "tool", "--current", "1.0", "--proposed", "1.1"])
        self.assertIn('row: "introduced by this PR" -> Hold; 1.2 does not ship it', out)

    def test_a_fix_this_pr_makes_is_not_a_hold(self):
        self.ruff_like()
        _, out, _ = self.run_main(
            ["--package", "ruff", "--current", "0.16.8", "--proposed", "0.16.9"]
        )
        self.assertIn("FIXED BY THIS PR  RUSTSEC-2026-0308", out)
        self.assertIn('row: "fixed by this PR" -> not a Hold: this PR removes it', out)

    def test_an_advisory_only_the_latest_ships_bars_it_as_a_follow_up_target(self):
        self.two_releases(
            {
                "1.0": [crate("regex", "1.12.0")],
                "1.1": [crate("regex", "1.12.0")],
                "1.2": [crate("lru", "0.18.0")],
            },
            "1.2",
        )
        self.world.advisory("RUSTSEC-2026-0253", "pkg:cargo/lru@0.18.0", "unsound")
        _, out, _ = self.run_main(["--package", "tool", "--current", "1.0", "--proposed", "1.1"])
        self.assertIn("INTRODUCED ABOVE THIS PR  RUSTSEC-2026-0253", out)
        self.assertIn(
            'row: "introduced above this PR" -> not a Hold on this bump; '
            "name no follow-up target that ships it, and 1.2 does",
            out,
        )

    def test_a_package_the_pr_adds_introduces_what_it_ships(self):
        """The ref path, where the missing current pin comes from: `moved()` gives a
        package new to the lockfile `current: None`, and `main()` drops the key."""
        self.world.release("newdep", "1.0", {p: [crate("lru", "0.18.0")] for p in PLATFORMS})
        self.world.latest["newdep"] = "1.0"
        self.world.advisory("RUSTSEC-2026-0253", "pkg:cargo/lru@0.18.0", "unsound")
        pr = [
            {
                "name": "newdep",
                "version": "1.0",
                "source": {"registry": "https://pypi.org/simple"},
                "wheels": [
                    {"url": f"https://files/{f}", "size": len(b)}
                    for f, b in self.world.releases[("newdep", "1.0")]
                ],
            }
        ]
        locks: dict[str, list[dict[str, Any]]] = {"pr-1": pr, "base": []}
        with mock.patch("vendored.lock_at", lambda ref: locks[ref]):
            code, out, _ = self.run_main(["--ref", "pr-1", "--base", "base"])
        self.assertEqual(code, 1, out)
        self.assertIn("INTRODUCED BY THIS PR  RUSTSEC-2026-0253", out)
        self.assertIn('row: "introduced by this PR" -> Hold', out)
        self.assertNotIn("unread", out)

    def test_an_unread_proposal_that_could_flip_the_verdict_says_confidence_is_low(self):
        """The proposal's one unread wheel may ship what the latest ships. If it
        does, this PR introduces it (Hold); if not, only the latest does."""
        self.two_releases(
            {
                "1.0": [crate("regex", "1.12.0")],
                "1.1": [crate("regex", "1.12.0")],
                "1.2": [crate("lru", "0.18.0")],
            },
            "1.2",
        )
        self.world.broken.add("tool-1.1-py3-abi3-win_amd64.whl")
        self.world.advisory("RUSTSEC-2026-0253", "pkg:cargo/lru@0.18.0", "unsound")
        _, out, _ = self.run_main(["--package", "tool", "--current", "1.0", "--proposed", "1.1"])
        self.assertIn("UNDERIVABLE AT THE PROPOSED PIN, AND THE LATEST SHIPS IT", out)
        self.assertIn(
            'row: "underivable" -> not a Hold on this bump, '
            "but which way it points decides the verdict: confidence low",
            out,
        )

    def test_an_unread_proposal_that_cannot_flip_the_verdict_says_so(self):
        """The current pin ships it: whatever the unread wheel holds, the bump either
        leaves it standing or removes it, and neither is a Hold."""
        self.two_releases(
            {"1.0": [crate("lru", "0.18.0")], "1.1": [crate("regex", "1.12.0")]}, "1.1"
        )
        self.world.broken.add("tool-1.1-py3-abi3-win_amd64.whl")
        self.world.advisory("RUSTSEC-2026-0253", "pkg:cargo/lru@0.18.0", "unsound")
        _, out, _ = self.run_main(["--package", "tool", "--current", "1.0", "--proposed", "1.1"])
        self.assertIn("UNDERIVABLE AT THE PROPOSED PIN", out)
        self.assertIn('row: "underivable" -> not a Hold on this bump, whichever way it points', out)

    def test_a_shipped_set_nobody_could_read_caps_confidence_at_medium(self):
        """mypy and librt on #451: no SBOM in any wheel, and no advisory points at
        either. The run called that medium, and 0.59.0's table let it say low."""
        bare = {f"plat{i}": None for i in range(5)}
        for version in ("2.3.1", "2.4.0"):
            self.world.release("mypy", version, bare)
        self.world.latest["mypy"] = "2.4.0"
        _, out, _ = self.run_main(
            ["--package", "mypy", "--current", "2.3.1", "--proposed", "2.4.0"]
        )
        self.assertIn(
            'row: "underivable" -> not a Hold on this bump; '
            "a shipped set that was not read caps confidence at medium",
            out,
        )

    def test_a_package_with_no_wheel_caps_confidence_at_medium_too(self):
        self.world.release("actionlint-py", "1.7.12.24", {})
        self.world.release("actionlint-py", "1.7.12.25", {})
        self.world.latest["actionlint-py"] = "1.7.12.25"
        _, out, _ = self.run_main(
            ["--package", "actionlint-py", "--current", "1.7.12.24", "--proposed", "1.7.12.25"]
        )
        self.assertIn("a shipped set that was not read caps confidence at medium", out)

    def test_the_result_line_counts_what_the_rows_select(self):
        self.ruff_like()
        self.world.advisory("RUSTSEC-2099-0003", "pkg:cargo/regex@1.12.0")
        _, out, _ = self.run_main(
            ["--package", "ruff", "--current", "0.16.7", "--proposed", "0.16.8"]
        )
        self.assertIn("rows select: 0 Hold, 1 follow-up, 1 not a Hold on this bump", out)


class TestTheVerdictIsTheTablesFirstMatch(unittest.TestCase):
    """`verdict()` is what `row()` prints and what the prose suite holds Phase 7's
    table to, so it is checked over every way an advisory can be placed."""

    def test_every_fully_read_placement(self):
        seen = {}
        for cur, pro, lat in (
            (c, p, la) for c in (True, False) for p in (True, False) for la in (True, False, "-")
        ):
            present: dict[str, bool | None] = {"current": cur, "proposed": pro}
            if lat != "-":
                present["latest"] = bool(lat)
            if not any(present.values()):
                continue  # no release ships it, so no advisory reaches the output
            seen[classify(present)] = vendored.verdict(present)
        self.assertEqual(seen["introduced by this PR"], vendored.HOLD)
        self.assertEqual(seen["introduced by this PR, fixed above this PR"], vendored.HOLD)
        self.assertEqual(seen["fixed above this PR"], vendored.FOLLOW_UP)
        for state in ("standing", "fixed by this PR", "introduced above this PR"):
            self.assertEqual(seen[state], vendored.NOT_A_HOLD, state)
        self.assertEqual(seen["fixed by this PR, introduced above this PR"], vendored.NOT_A_HOLD)

    def test_versions_order_numerically(self):
        self.assertGreater(_key("0.16.10"), _key("0.16.9"))
        self.assertGreater(_key("2.49.0"), _key("2.41.5"))


class TestTheLockfileSaysWhatMoved(Harness):
    BASE: ClassVar[list[dict[str, Any]]] = [
        # A lockfile can pin one package at two versions under different markers.
        {"name": "ruff", "version": "0.16.10", "source": {"registry": "https://pypi.org/simple"}},
        {"name": "ruff", "version": "0.16.7", "source": {"registry": "https://pypi.org/simple"}},
        {"name": "rumdl", "version": "0.2.72", "source": {"registry": "https://pypi.org/simple"}},
        {"name": "app", "version": "0.1.0", "source": {"editable": "."}},
    ]
    PR: ClassVar[list[dict[str, Any]]] = [
        {
            "name": "ruff",
            "version": "0.16.8",
            "source": {"registry": "https://pypi.org/simple"},
            "wheels": [{"url": "https://files/ruff-0.16.8-py3-abi3-win_amd64.whl", "size": 10}],
        },
        {"name": "rumdl", "version": "0.2.72", "source": {"registry": "https://pypi.org/simple"}},
        {"name": "Newdep", "version": "1.0", "source": {"registry": "https://pypi.org/simple"}},
        {"name": "app", "version": "0.2.0", "source": {"editable": "."}},
    ]

    def test_only_registry_packages_at_a_new_version(self):
        found = {m["name"]: m for m in moved(self.PR, self.BASE)}
        self.assertEqual(set(found), {"ruff", "Newdep"})
        # The highest, compared as numbers: 0.16.10 is above 0.16.7.
        self.assertEqual(
            (found["ruff"]["current"], found["ruff"]["proposed"]), ("0.16.10", "0.16.8")
        )
        self.assertIsNone(found["Newdep"]["current"])
        self.assertEqual(found["ruff"]["wheels"][0][0], "ruff-0.16.8-py3-abi3-win_amd64.whl")

    def test_the_ref_mode_reads_both_lockfiles(self):
        self.ruff_like()
        locks = {"pr-438": self.PR[:1], "base": self.BASE[1:2]}
        self.world.releases[("ruff", "0.16.8")] = self.world.releases[("ruff", "0.16.8")]
        # The proposed pin's wheels come from the PR's lockfile, which names one.
        self.PR[0]["wheels"] = [
            {"url": f"https://files/{f}", "size": len(b)}
            for f, b in self.world.releases[("ruff", "0.16.8")]
        ]
        with mock.patch("vendored.lock_at", lambda ref: locks[ref]):
            code, out, _ = self.run_main(["--ref", "pr-438", "--base", "base"])
        self.assertEqual(code, 1, out)
        self.assertIn("ruff 0.16.7 -> 0.16.8 (latest 0.16.9)", out)
        self.assertIn("FIXED ABOVE THIS PR", out)

    def test_arguments_that_do_not_fit_exit_2(self):
        for argv in (
            [],
            ["--package", "x"],
            ["--ref", "a"],
            ["--package", "x", "--current", "1", "--proposed", "2", "--ref", "a"],
        ):
            with self.subTest(argv=argv):
                code, _, _ = self.run_main(argv)
                self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
