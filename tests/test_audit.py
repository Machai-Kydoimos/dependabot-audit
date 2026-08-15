"""Regression tests for audit.py.

Every case here corresponds to a defect that actually shipped, or to a failure
mode the audit exists to detect. Pure functions only: no network, no fixtures on
disk beyond a temp lockfile, stdlib `unittest` so it runs anywhere.

    python3 -m unittest discover -s tests -v

The name of the game is *silent* failure. An audit that reports success while
verifying less than it claimed is worse than one that crashes, so most of these
assert on what gets reported, not just on what gets returned.
"""

from __future__ import annotations

import contextlib
import email.message
import io
import itertools
import json
import pathlib
import shutil
import sys
import tempfile
import unittest
import urllib.error
from typing import ClassVar
from unittest import mock

sys.path.insert(
    0, str(pathlib.Path(__file__).resolve().parent.parent / "skills/dependabot-audit/scripts")
)

import audit
from audit import (
    _get_json,
    _is_prerelease,
    _normalize,
    _version_key,
    check_attestations,
    check_currency,
    check_provenance,
    check_vulns,
    cli,
    derive_changed,
    files_by_version,
    fork_context,
    latest_version,
    load_lock,
    main,
    pypi_sourced,
    select_targets,
)

PYPI_SRC = {"registry": "https://pypi.org/simple"}


def wheel_lock(name, version, *, wheels=None, digest="a" * 64):
    """A one-package lockfile, for comparisons that turn on the artifacts.

    `wheels` is a list of (url, digest) pairs; the default is one wheel named
    after the package, so a test that only cares about the hash can pass `digest`.
    """
    if wheels is None:
        wheels = [(f"https://files.pythonhosted.org/packages/xx/{name}-{version}.whl", digest)]
    rows = "\n".join(
        f'    {{ url = "{url}", hash = "sha256:{sha}", size = 100 }},' for url, sha in wheels
    )
    return f"""
[[package]]
name = "{name}"
version = "{version}"
source = {{ registry = "https://pypi.org/simple" }}
wheels = [
{rows}
]
"""


def changed_names(packages, baseline):
    return [c["name"] for c in derive_changed(packages, baseline)]


ONE_WHEEL = [("https://files.pythonhosted.org/packages/xx/attrs-24.2.0.whl", "a" * 64)]
TWO_WHEELS = [
    *ONE_WHEEL,
    ("https://files.pythonhosted.org/packages/xx/attrs-24.2.0-win.whl", "b" * 64),
]


def write_lock(test: unittest.TestCase, body: str) -> str:
    """A temp lockfile that removes itself when the test finishes."""
    directory = tempfile.mkdtemp()
    test.addCleanup(shutil.rmtree, directory, ignore_errors=True)
    path = pathlib.Path(directory) / "uv.lock"
    path.write_text(body, encoding="utf-8")
    return str(path)


def entry(name, version, *, wheels=(), sdist=None, markers=None):
    """A minimal uv.lock [[package]] block."""
    e = {"name": name, "version": version, "source": PYPI_SRC, "wheels": list(wheels)}
    if sdist:
        e["sdist"] = sdist
    if markers:
        e["resolution-markers"] = markers
    return e


def artifact(filename, digest="a" * 64, size=100):
    return {
        "url": f"https://files.pythonhosted.org/packages/xx/{filename}",
        "hash": f"sha256:{digest}",
        "size": size,
    }


def pypi_meta(version, files, latest=None, releases=None):
    """A minimal Simple API (PEP 691/700) project response.

    `latest` is accepted and ignored: the Simple API has no `info.version`, so
    "latest" is computed from `versions`. Tests that pass it are asserting that
    the computation agrees with what PyPI used to declare.
    """
    releases = releases if releases is not None else {version: files}
    return {
        "meta": {"api-version": "1.4"},
        "name": "p",
        # PEP 700 says this is unordered; sorting it here would hide a comparator
        # that only works because the input arrived sorted.
        "versions": list(reversed(list(releases))),
        "files": [f for group in releases.values() for f in group],
    }


def pypi_file(
    filename,
    digest="a" * 64,
    size=100,
    yanked=False,
    uploaded="2026-01-01T00:00:00Z",
    provenance=None,
):
    record = {
        "filename": filename,
        "hashes": {"sha256": digest},
        "size": size,
        "url": f"https://files.pythonhosted.org/packages/xx/{filename}",
        "yanked": yanked,
        "upload-time": uploaded,
    }
    if provenance:
        record["provenance"] = provenance
    return record


class TestNormalize(unittest.TestCase):
    def test_pep503_variants_collapse(self):
        for name in ("Pillow", "pillow", "PILLOW"):
            self.assertEqual(_normalize(name), "pillow")
        for name in ("foo_bar", "foo-bar", "foo.bar", "foo__bar"):
            self.assertEqual(_normalize(name), "foo-bar")


class TestSelectTargets(unittest.TestCase):
    """Shipped bug: names were matched through a dict, so duplicates vanished."""

    def test_returns_every_entry_for_a_multiversion_package(self):
        packages = [
            entry("rpds-py", "0.30.0", markers=["python_full_version < '3.11'"]),
            entry("rpds-py", "2026.6.3", markers=["python_full_version >= '3.11'"]),
            entry("rumdl", "0.2.52"),
        ]
        targets, unmatched = select_targets(packages, ["rpds-py"])
        self.assertEqual(len(targets), 2, "both pinned versions must be audited")
        self.assertEqual({t["version"] for t in targets}, {"0.30.0", "2026.6.3"})
        self.assertEqual(unmatched, [])

    def test_unmatched_names_are_reported_not_dropped(self):
        packages = [entry("rumdl", "0.2.52")]
        targets, unmatched = select_targets(packages, ["rumdl", "not-a-package"])
        self.assertEqual(len(targets), 1)
        self.assertEqual(unmatched, ["not-a-package"], "silent drop = under-audit")

    def test_matching_is_normalized(self):
        packages = [entry("rpds-py", "1.0")]
        targets, unmatched = select_targets(packages, ["RPDS_PY"])
        self.assertEqual(len(targets), 1)
        self.assertEqual(unmatched, [])


class TestDeriveChanged(unittest.TestCase):
    def _lock(self, body: str) -> str:
        return write_lock(self, body)

    BASE = """
[[package]]
name = "rumdl"
version = "0.2.49"
source = { registry = "https://pypi.org/simple" }

[[package]]
name = "rpds-py"
version = "0.30.0"
source = { registry = "https://pypi.org/simple" }
resolution-markers = ["python_full_version < '3.11'"]

[[package]]
name = "rpds-py"
version = "2026.6.3"
source = { registry = "https://pypi.org/simple" }
resolution-markers = ["python_full_version >= '3.11'"]
"""

    def test_detects_a_version_bump(self):
        base = self._lock(self.BASE)
        new = pypi_sourced(
            [
                entry("rumdl", "0.2.52"),
                entry("rpds-py", "0.30.0"),
                entry("rpds-py", "2026.6.3"),
            ]
        )
        self.assertEqual(changed_names(new, base), ["rumdl"])
        self.assertEqual(derive_changed(new, base)[0]["kind"], "version")

    def test_unchanged_multiversion_package_is_not_a_false_positive(self):
        """Shipped bug: name-keyed comparison flagged rpds-py on every run."""
        base = self._lock(self.BASE)
        new = pypi_sourced(
            [
                entry("rumdl", "0.2.49"),
                entry("rpds-py", "0.30.0"),
                entry("rpds-py", "2026.6.3"),
            ]
        )
        self.assertEqual(changed_names(new, base), [])

    def test_detects_an_added_package(self):
        base = self._lock(self.BASE)
        new = pypi_sourced([entry("rumdl", "0.2.49"), entry("brand-new", "1.0")])
        self.assertEqual(changed_names(new, base), ["brand-new"])
        self.assertEqual(derive_changed(new, base)[0]["kind"], "added")


class TestArtifactSubstitutionIsSelected(unittest.TestCase):
    """A lockfile diff can move an artifact without moving the version.

    Keying the changed set on (name, version) alone selects *no packages at all*
    for the one lockfile change most worth catching — and the empty-selection
    guard then offers two explanations, both benign, neither of which is what
    happened. A correctly-refused audit becomes a dismissed one.
    """

    EVIL = "https://files.pythonhosted.org/packages/ev/il/attrs-24.2.0-py3-none-any.whl"

    def _pair(self, base_body, pr_body):
        base = write_lock(self, base_body)
        return pypi_sourced(load_lock(write_lock(self, pr_body))), base

    def test_a_hash_swap_at_the_same_version_is_selected(self):
        pkgs, base = self._pair(
            wheel_lock("attrs", "24.2.0", digest="a" * 64),
            wheel_lock("attrs", "24.2.0", digest="0" * 64),
        )
        changed = derive_changed(pkgs, base)
        self.assertEqual([c["name"] for c in changed], ["attrs"])
        self.assertEqual(changed[0]["kind"], "artifacts", "a hash moved; the version did not")

    def test_a_url_swap_alone_is_selected(self):
        """The provenance shape: same name, same version, different artifact."""
        pkgs, base = self._pair(
            wheel_lock("attrs", "24.2.0"),
            wheel_lock("attrs", "24.2.0", wheels=[(self.EVIL, "0" * 64)]),
        )
        self.assertEqual([c["kind"] for c in derive_changed(pkgs, base)], ["artifacts"])

    def test_an_added_wheel_at_an_unchanged_version_is_selected(self):
        """Legitimate — a new platform wheel — and still worth verifying."""
        pkgs, base = self._pair(
            wheel_lock("attrs", "24.2.0", wheels=ONE_WHEEL),
            wheel_lock("attrs", "24.2.0", wheels=TWO_WHEELS),
        )
        self.assertEqual([c["kind"] for c in derive_changed(pkgs, base)], ["artifacts"])

    def test_a_removed_wheel_is_selected(self):
        pkgs, base = self._pair(
            wheel_lock("attrs", "24.2.0", wheels=TWO_WHEELS),
            wheel_lock("attrs", "24.2.0", wheels=ONE_WHEEL),
        )
        self.assertEqual([c["kind"] for c in derive_changed(pkgs, base)], ["artifacts"])

    def test_a_byte_identical_lockfile_still_derives_nothing(self):
        """The guard this must not weaken: comparing a lockfile against itself."""
        body = wheel_lock("attrs", "24.2.0")
        base = write_lock(self, body)
        pkgs = pypi_sourced(load_lock(write_lock(self, body)))
        self.assertEqual(derive_changed(pkgs, base), [])


class TestProvenance(unittest.TestCase):
    """The core claim of the tool: a tampered artifact must not pass."""

    def test_corrupted_hash_is_caught(self):
        pkg = entry("evil", "1.0", wheels=[artifact("evil-1.0.whl", digest="b" * 64)])
        meta = pypi_meta("1.0", [pypi_file("evil-1.0.whl", digest="a" * 64)])
        result = check_provenance(pkg, meta)
        self.assertFalse(result["ok"])
        self.assertFalse(result["artifacts"][0]["checks"]["sha256"])

    def test_size_mismatch_is_caught(self):
        pkg = entry("p", "1.0", wheels=[artifact("p-1.0.whl", size=999)])
        meta = pypi_meta("1.0", [pypi_file("p-1.0.whl", size=100)])
        self.assertFalse(check_provenance(pkg, meta)["ok"])

    def test_yanked_release_is_caught(self):
        pkg = entry("p", "1.0", wheels=[artifact("p-1.0.whl")])
        meta = pypi_meta("1.0", [pypi_file("p-1.0.whl", yanked=True)])
        result = check_provenance(pkg, meta)
        self.assertFalse(result["ok"])
        self.assertFalse(result["artifacts"][0]["checks"]["not_yanked"])

    def test_artifact_absent_from_the_registry_is_caught(self):
        pkg = entry("p", "1.0", wheels=[artifact("ghost-1.0.whl")])
        meta = pypi_meta("1.0", [pypi_file("p-1.0.whl")])
        result = check_provenance(pkg, meta)
        self.assertFalse(result["ok"])
        self.assertEqual(result["artifacts"][0]["error"], "not on PyPI")

    def test_version_missing_from_the_registry_is_caught(self):
        pkg = entry("p", "9.9", wheels=[artifact("p-9.9.whl")])
        meta = pypi_meta("1.0", [pypi_file("p-1.0.whl")])
        result = check_provenance(pkg, meta)
        self.assertFalse(result["ok"])
        self.assertIn("not present on PyPI", result["error"])

    def test_sdist_is_verified_too_not_only_wheels(self):
        pkg = entry("p", "1.0", sdist=artifact("p-1.0.tar.gz", digest="b" * 64), wheels=[])
        meta = pypi_meta("1.0", [pypi_file("p-1.0.tar.gz", digest="a" * 64)])
        result = check_provenance(pkg, meta)
        self.assertFalse(result["ok"])
        self.assertEqual(result["artifacts"][0]["kind"], "sdist")

    def test_a_clean_package_passes(self):
        pkg = entry("p", "1.0", wheels=[artifact("p-1.0.whl")])
        meta = pypi_meta("1.0", [pypi_file("p-1.0.whl")])
        self.assertTrue(check_provenance(pkg, meta)["ok"])

    def test_an_absent_size_is_not_compared_rather_than_mismatched(self):
        """`size` is optional in a uv.lock artifact table — uv omits it when the
        index does not report one.

        Comparing it unconditionally reports `size MISMATCH` on an artifact whose
        hash and URL match PyPI byte-for-byte, which is the report row a reader is
        least able to dismiss: "the hash matches but the size does not" reads like
        tampering. Absent means *not compared*, and the two have to stay distinct
        or the change loses the information it exists to preserve.
        """
        art = artifact("p-1.0.whl")
        del art["size"]
        pkg = entry("p", "1.0", wheels=[art])
        meta = pypi_meta("1.0", [pypi_file("p-1.0.whl", size=63001)])
        result = check_provenance(pkg, meta)
        self.assertTrue(result["ok"], "a correct artifact must not report BAD")
        self.assertIsNone(result["artifacts"][0]["checks"]["size"], "null, not True")
        self.assertTrue(result["artifacts"][0]["checks"]["sha256"])

    def test_a_present_size_is_still_compared(self):
        """The tri-state must not turn the size check off for everyone."""
        pkg = entry("p", "1.0", wheels=[artifact("p-1.0.whl", size=999)])
        meta = pypi_meta("1.0", [pypi_file("p-1.0.whl", size=100)])
        result = check_provenance(pkg, meta)
        self.assertFalse(result["ok"])
        self.assertIs(result["artifacts"][0]["checks"]["size"], False)


class TestCurrency(unittest.TestCase):
    def test_lagging_version_is_flagged_with_the_gap(self):
        pkg = entry("p", "1.0")
        meta = pypi_meta(
            "1.0",
            [],
            latest="1.2",
            releases={
                "1.0": [pypi_file("p-1.0.whl", uploaded="2026-01-01T00:00:00Z")],
                "1.1": [pypi_file("p-1.1.whl", uploaded="2026-02-01T00:00:00Z")],
                "1.2": [pypi_file("p-1.2.whl", uploaded="2026-03-01T00:00:00Z")],
            },
        )
        result = check_currency(pkg, meta)
        self.assertFalse(result["current"])
        self.assertEqual([r["version"] for r in result["gap"]], ["1.1", "1.2"])
        self.assertEqual(result["gap"][0]["published"], "2026-02-01T00:00:00Z")

    def _split(self):
        """A package uv forked: rpds-py's real shape, both blocks marker-stamped."""
        return pypi_meta(
            "2020.1.1",
            [],
            latest="2026.6.3",
            releases={
                "0.30.0": [pypi_file("p-0.30.0.whl", uploaded="2019-01-01T00:00:00Z")],
                "2020.1.1": [pypi_file("p-2020.1.1.whl", uploaded="2020-01-01T00:00:00Z")],
                "2026.6.3": [pypi_file("p-2026.6.3.whl", uploaded="2026-06-03T00:00:00Z")],
            },
        )

    def test_held_back_fork_is_not_reported_as_stale(self):
        """The lower fork of a split package trails the registry by design."""
        pkg = entry("rpds-py", "0.30.0", markers=["python_full_version < '3.11'"])
        result = check_currency(pkg, self._split(), pinned=["0.30.0", "2020.1.1"], newest=False)
        self.assertFalse(result["current"])
        self.assertTrue(result["held_back"], "must not read as a staleness finding")

    def test_newest_fork_is_still_checked_for_staleness(self):
        """Shipped bug: uv stamps markers on *every* fork, so both were exempted.

        The highest pin is the one that actually gets installed on a current
        interpreter. Letting its markers excuse a gap hides staleness on the only
        pin a follow-up bump could ever move.
        """
        pkg = entry("rpds-py", "2020.1.1", markers=["python_full_version >= '3.11'"])
        result = check_currency(pkg, self._split(), pinned=["0.30.0", "2020.1.1"], newest=True)
        self.assertTrue(result["constrained"])
        self.assertFalse(result["held_back"], "markers must not excuse the live pin")
        self.assertEqual([r["version"] for r in result["gap"]], ["2026.6.3"])

    def test_publish_time_is_the_earliest_artifact_not_an_arbitrary_one(self):
        """PyPI lists a version's files in no useful order.

        The currency question is whether the version existed before the PR was
        opened, and a wheel built by CI can land hours after its sdist — so the
        wrong artifact's timestamp answers the question wrongly.
        """
        pkg = entry("p", "1.0")
        meta = pypi_meta(
            "1.0",
            [
                pypi_file("p-1.0-py3-none-any.whl", uploaded="2026-03-01T12:00:00Z"),
                pypi_file("p-1.0.tar.gz", uploaded="2026-03-01T09:00:00Z"),
            ],
        )
        self.assertEqual(check_currency(pkg, meta)["locked_published"], "2026-03-01T09:00:00Z")

    def test_prereleases_are_not_listed_in_the_gap(self):
        """A bot never proposes an rc, so listing one sends the reader to a
        changelog that cannot be the answer."""
        pkg = entry("p", "1.2.3")
        meta = pypi_meta(
            "1.2.3",
            [],
            latest="1.3.0",
            releases={
                "1.2.3": [pypi_file("p-1.2.3.whl", uploaded="2026-01-01T00:00:00Z")],
                "1.3.0rc1": [pypi_file("p-1.3.0rc1.whl", uploaded="2026-02-01T00:00:00Z")],
                "1.3.0": [pypi_file("p-1.3.0.whl", uploaded="2026-03-01T00:00:00Z")],
            },
        )
        gap = check_currency(pkg, meta)["gap"]
        self.assertEqual([r["version"] for r in gap], ["1.3.0"])

    def test_post_releases_still_enter_the_gap(self):
        """The pre-release filter must not swallow a release a bot can propose."""
        pkg = entry("p", "1.0")
        meta = pypi_meta(
            "1.0",
            [],
            latest="1.0.post1",
            releases={
                "1.0": [pypi_file("p-1.0.whl", uploaded="2026-01-01T00:00:00Z")],
                "1.0.post1": [pypi_file("p-1.0.post1.whl", uploaded="2026-02-01T00:00:00Z")],
            },
        )
        gap = check_currency(pkg, meta)["gap"]
        self.assertEqual([r["version"] for r in gap], ["1.0.post1"])

    def test_gap_is_ordered_by_publish_time_not_version(self):
        """The report tells the reader to compare the *earliest* against the PR's
        createdAt, so the first row has to be the earliest — and a patch on an
        older line can be published after a higher version."""
        pkg = entry("p", "1.0")
        meta = pypi_meta(
            "1.0",
            [],
            latest="1.10",
            releases={
                "1.0": [pypi_file("p-1.0.whl", uploaded="2026-01-01T00:00:00Z")],
                "1.10": [pypi_file("p-1.10.whl", uploaded="2026-02-01T00:00:00Z")],
                "1.9": [pypi_file("p-1.9.whl", uploaded="2026-03-01T00:00:00Z")],
            },
        )
        gap = check_currency(pkg, meta)["gap"]
        self.assertEqual([r["version"] for r in gap], ["1.10", "1.9"])

    def test_an_epoch_release_stays_in_the_gap(self):
        """A PEP 440 epoch lives in the *first* segment.

        Splitting on `.` made that segment non-numeric, which dragged the whole
        version below unversioned releases and out of the gap entirely — and the
        gap is what Phase 2 reads changelogs across. A version that vanishes from
        the gap is one whose `Security` section never gets read.

        Epochs are rare but real: they exist precisely because a project changed
        versioning scheme, which is when its changelog matters most.
        """
        pkg = entry("p", "1.0")
        meta = pypi_meta(
            "1.0",
            [],
            latest="2!1.0",
            # A wheel filename escapes the version, so `2!1.0` appears as
            # `2_1.0` — which is exactly the case where the file cannot be
            # attributed back to its version. Gap membership comes from
            # `versions`, so the release is still listed; only its timestamp is
            # unknown, and that degradation is visible rather than silent.
            releases={
                "1.0": [pypi_file("p-1.0.whl", uploaded="2026-01-01T00:00:00Z")],
                "2!1.0": [pypi_file("p-2_1.0.whl", uploaded="2026-02-01T00:00:00Z")],
            },
        )
        result = check_currency(pkg, meta)
        self.assertFalse(result["current"])
        self.assertEqual([r["version"] for r in result["gap"]], ["2!1.0"])
        self.assertIsNone(result["gap"][0]["published"], "an unattributable file is not a guess")

    def test_a_dev_release_is_not_listed_in_the_gap(self):
        """Same class as an rc: a bot never proposes one."""
        pkg = entry("p", "1.0")
        meta = pypi_meta(
            "1.0",
            [],
            latest="1.1",
            releases={
                "1.0": [pypi_file("p-1.0.whl", uploaded="2026-01-01T00:00:00Z")],
                "1.1.dev3": [pypi_file("p-1.1.dev3.whl", uploaded="2026-02-01T00:00:00Z")],
                "1.1": [pypi_file("p-1.1.whl", uploaded="2026-03-01T00:00:00Z")],
            },
        )
        self.assertEqual([r["version"] for r in check_currency(pkg, meta)["gap"]], ["1.1"])

    def test_unconstrained_pin_is_not_marked_constrained(self):
        pkg = entry("p", "1.0")
        meta = pypi_meta(
            "1.0",
            [],
            latest="1.1",
            releases={"1.0": [pypi_file("p-1.0.whl")], "1.1": [pypi_file("p-1.1.whl")]},
        )
        self.assertFalse(check_currency(pkg, meta)["constrained"])

    def test_current_version_reports_current(self):
        pkg = entry("p", "1.0")
        meta = pypi_meta("1.0", [pypi_file("p-1.0.whl")])
        result = check_currency(pkg, meta)
        self.assertTrue(result["current"])
        self.assertEqual(result["gap"], [])


class TestVersionOrdering(unittest.TestCase):
    """PEP 440, as much of it as PyPI can serve.

    The previous key split on "." and mapped non-numeric segments to -1, which
    worked for the ordinary cases and put an epoch *below* unversioned releases.
    Ordering became load-bearing once "latest" was the script's own computation
    rather than a field PyPI hands over.
    """

    def assertAscending(self, versions):
        for lower, higher in itertools.pairwise(versions):
            self.assertLess(
                _version_key(lower),
                _version_key(higher),
                f"{lower} must sort below {higher}",
            )

    def test_an_epoch_outranks_everything_below_it(self):
        """The defect: `2!1.0` -> (-1, 0), which sorts below plain `1.0`."""
        self.assertAscending(["0.9", "1.0", "99.0", "1!0.1", "2!1.0"])

    def test_numeric_segments_compare_numerically(self):
        self.assertAscending(["1.9", "1.10"])
        self.assertAscending(["0.16.2", "0.16.10"])

    def test_the_full_release_cycle_sorts_in_order(self):
        self.assertAscending(["1.0.dev1", "1.0a1", "1.0a2", "1.0b1", "1.0rc1", "1.0", "1.0.post1"])

    def test_a_dev_of_a_post_release_sorts_after_the_release(self):
        self.assertAscending(["1.0", "1.0.post1.dev1", "1.0.post1"])

    def test_trailing_zeros_do_not_make_a_new_version(self):
        """PEP 440: 1.0 and 1.0.0 are the same version."""
        self.assertEqual(_version_key("1.0"), _version_key("1.0.0"))
        self.assertEqual(_version_key("1.0"), _version_key("1.0.0.0"))

    def test_spelling_variants_are_the_same_version(self):
        for a, b in (("1.0alpha1", "1.0a1"), ("1.0c1", "1.0rc1"), ("1.0-1", "1.0.post1")):
            self.assertEqual(_version_key(a), _version_key(b), f"{a} != {b}")

    def test_something_unorderable_raises_rather_than_sorting_to_the_bottom(self):
        """Silently sorting it low is how an epoch release fell out of the gap."""
        with self.assertRaises(ValueError):
            _version_key("not-a-version")

    def test_prerelease_detection(self):
        for version in ("1.3.0rc1", "1.0a1", "1.0b2", "1.1.dev3", "1.0alpha1"):
            self.assertTrue(_is_prerelease(version), version)
        for version in ("1.0", "1.0.post1", "2!1.0", "1.0.1"):
            self.assertFalse(_is_prerelease(version), f"{version} is a release a bot can propose")


class TestSimpleApiShape(unittest.TestCase):
    """The Simple API carries no per-file version, so files must be attributed.

    Measured across 24,512 real files from 12 projects: 2 unattributed, both old
    setuptools sdists named `setuptools-69.3.tar.gz` where PyPI lists the version
    as `69.3.0`. The property that matters is not the hit rate — it is that a miss
    costs a *timestamp*, never a gap entry.
    """

    def test_files_are_attributed_to_their_version(self):
        project = pypi_meta(
            "1.0",
            [],
            releases={
                "1.0": [pypi_file("p-1.0-py3-none-any.whl"), pypi_file("p-1.0.tar.gz")],
                "1.1": [pypi_file("p-1.1-py3-none-any.whl")],
            },
        )
        grouped = files_by_version(project)
        self.assertEqual(len(grouped["1.0"]), 2)
        self.assertEqual(len(grouped["1.1"]), 1)

    def test_the_longest_matching_version_wins(self):
        """`1.0.1` must not be filed under `1.0`."""
        project = pypi_meta(
            "1.0",
            [],
            releases={
                "1.0": [pypi_file("p-1.0.tar.gz")],
                "1.0.1": [pypi_file("p-1.0.1.tar.gz")],
            },
        )
        grouped = files_by_version(project)
        self.assertEqual([f["filename"] for f in grouped["1.0"]], ["p-1.0.tar.gz"])
        self.assertEqual([f["filename"] for f in grouped["1.0.1"]], ["p-1.0.1.tar.gz"])

    def test_an_unattributable_file_costs_a_timestamp_not_a_version(self):
        project = pypi_meta("1.0", [], releases={"1.0": [pypi_file("mystery-file.whl")]})
        self.assertEqual(files_by_version(project)["1.0"], [])
        self.assertIn("1.0", project["versions"], "the version itself must survive")

    def test_latest_is_computed_and_excludes_prereleases(self):
        project = pypi_meta(
            "1.0",
            [],
            releases={
                "1.0": [pypi_file("p-1.0.whl")],
                "1.1": [pypi_file("p-1.1.whl")],
                "2.0rc1": [pypi_file("p-2.0rc1.whl")],
            },
        )
        self.assertEqual(latest_version(project, files_by_version(project)), "1.1")

    def test_a_fully_yanked_release_is_not_the_latest(self):
        """Recommending a version whose every file is yanked would be wrong."""
        project = pypi_meta(
            "1.0",
            [],
            releases={
                "1.0": [pypi_file("p-1.0.whl")],
                "1.1": [pypi_file("p-1.1.whl", yanked=True)],
            },
        )
        self.assertEqual(latest_version(project, files_by_version(project)), "1.0")

    def test_a_partly_yanked_release_still_counts(self):
        """One yanked wheel out of several does not withdraw the release."""
        project = pypi_meta(
            "1.0",
            [],
            releases={
                "1.0": [pypi_file("p-1.0.whl")],
                "1.1": [pypi_file("p-1.1-a.whl", yanked=True), pypi_file("p-1.1-b.whl")],
            },
        )
        self.assertEqual(latest_version(project, files_by_version(project)), "1.1")


PUBLISHER = {"kind": "GitHub", "repository": "python-attrs/attrs", "workflow": "pypi-package.yml"}
OTHER_PUBLISHER = {"kind": "GitHub", "repository": "evil/attrs", "workflow": "release.yml"}


class TestAttestations(unittest.TestCase):
    """PEP 740 answers the question a hash comparison cannot.

    A hash check catches a lockfile edited after it was written honestly. It
    cannot catch a bad artifact PyPI itself is serving, because then the record
    and the lockfile agree — and agreement is the whole test.
    """

    def _project(self, *, previous_publisher=None, current_publisher=None):
        releases = {
            "1.0": [pypi_file("p-1.0.whl", provenance="https://pypi.org/integrity/p/1.0/x")],
            "2.0": [pypi_file("p-2.0.whl", provenance="https://pypi.org/integrity/p/2.0/x")],
        }
        if previous_publisher is None:
            del releases["1.0"][0]["provenance"]
        if current_publisher is None:
            del releases["2.0"][0]["provenance"]
        project = pypi_meta("2.0", [], releases=releases)
        responses = {
            "https://pypi.org/integrity/p/1.0/x": previous_publisher,
            "https://pypi.org/integrity/p/2.0/x": current_publisher,
        }

        def fake(url, payload=None, accept=None):
            publisher = responses[url]
            return {"attestation_bundles": [{"publisher": publisher}] if publisher else []}

        return project, fake

    def _check(self, project, fake, *, previous=""):
        pkg = entry("p", "2.0", wheels=[artifact("p-2.0.whl")])
        with mock.patch("audit._get_json", fake):
            return check_attestations(pkg, project, previous=previous)

    def test_an_attested_artifact_names_its_publisher(self):
        result = self._check(*self._project(current_publisher=PUBLISHER))
        self.assertEqual(result["attested"], 1)
        self.assertEqual(result["artifacts"][0]["publisher"], PUBLISHER)

    def test_no_attestation_is_not_a_finding(self):
        """Normal for anything predating Trusted Publishing. Collapsing it into a
        warning would make the row noise on most lockfiles and train the reader
        to skip it — which is the failure the ecosystem references warn about."""
        result = self._check(*self._project())
        self.assertEqual(result["attested"], 0)
        self.assertEqual(result["unattested"], 1)
        self.assertFalse(result["changed"], "absence must not read as a change")

    def test_the_same_publisher_across_a_bump_is_not_a_finding(self):
        result = self._check(
            *self._project(previous_publisher=PUBLISHER, current_publisher=PUBLISHER),
            previous="1.0",
        )
        self.assertFalse(result["changed"])
        self.assertEqual(result["previous"]["version"], "1.0")

    def test_a_publisher_that_moved_between_releases_is_a_finding(self):
        """The takeover signal, and it needs no external source of truth: both
        versions are in the same Simple API response."""
        result = self._check(
            *self._project(previous_publisher=PUBLISHER, current_publisher=OTHER_PUBLISHER),
            previous="1.0",
        )
        self.assertTrue(result["changed"])
        self.assertEqual(result["previous"]["publisher"], PUBLISHER)

    def test_an_unattested_predecessor_is_not_a_change(self):
        """A project that adopted Trusted Publishing between releases is the
        common case, and reporting it as a publisher change would be wrong."""
        result = self._check(
            *self._project(current_publisher=PUBLISHER),
            previous="1.0",
        )
        self.assertFalse(result["changed"])
        self.assertIsNone(result["previous"])


FORK_PINS = {"rpds-py": ["0.30.0", "2026.6.3"], "rumdl": ["0.2.53"]}
SPLIT = FORK_PINS["rpds-py"]


class TestForkContext(unittest.TestCase):
    """Which pin of a forked package is the live one, and what the others are."""

    def test_highest_pin_of_a_fork_is_the_newest(self):
        self.assertEqual(fork_context(entry("rpds-py", "2026.6.3"), FORK_PINS), (SPLIT, True))

    def test_lower_pin_of_a_fork_is_not(self):
        self.assertEqual(fork_context(entry("rpds-py", "0.30.0"), FORK_PINS), (SPLIT, False))

    def test_a_lone_pin_is_its_own_newest(self):
        self.assertEqual(fork_context(entry("rumdl", "0.2.53"), FORK_PINS), (["0.2.53"], True))

    def test_siblings_come_back_in_version_order_not_lockfile_order(self):
        """The report prints these, so the order is part of the output.

        A lockfile's blocks are not sorted by version, and reading a fork list
        that runs backwards invites the reader to take the first entry as the
        live pin when it is the held-back one.
        """
        pins = {"rpds-py": ["2026.6.3", "0.30.0"]}
        self.assertEqual(fork_context(entry("rpds-py", "0.30.0"), pins)[0], SPLIT)


class _MainHarness(unittest.TestCase):
    """Shared harness for the tests that drive main() with the network stubbed."""

    LOCK = f"""
[[package]]
name = "rumdl"
version = "0.2.53"
source = {{ registry = "https://pypi.org/simple" }}
wheels = [
    {{ url = "https://files.pythonhosted.org/packages/xx/rumdl-0.2.53-py3-none-any.whl", hash = "sha256:{"a" * 64}", size = 100 }},
]

[[package]]
name = "fpga-simulator"
version = "0.20.0"
source = {{ editable = "." }}
"""

    def _run(self, argv, meta=None, fails=None, entry_point=None):
        """Run main() with the network stubbed. Returns (exit code, stdout, stderr).

        `fails` maps a host fragment to the exception the stubbed fetch should
        raise for it, so an outage is simulated without touching the network.
        `entry_point` selects `cli()` instead, which is where the 0/1/2 contract
        is actually enforced against an *unforeseen* failure.
        """

        def fake_get_json(url, payload=None, accept=None):
            for fragment, exc in (fails or {}).items():
                if fragment in url:
                    raise exc
            if "osv.dev" in url:
                queries = json.loads(payload)["queries"]
                return {"results": [{} for _ in queries]}
            return meta

        out, err = io.StringIO(), io.StringIO()
        with (
            mock.patch("audit._get_json", fake_get_json),
            mock.patch.object(sys, "argv", ["audit.py", *argv]),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            try:
                code: int | str | None = (entry_point or main)()
            except SystemExit as exc:  # fail() raises rather than returns
                code = exc.code
        return code, out.getvalue(), err.getvalue()

    def _meta(self):
        return pypi_meta("0.2.53", [pypi_file("rumdl-0.2.53-py3-none-any.whl")])


class TestMainContract(_MainHarness):
    """The documented exit status, and the counts behind the verdict line.

    `main()` owns the 0/1/2 contract and was untested, which is how a run that
    selected no packages came to print CLEAN and exit 0 — the same silent
    under-audit `select_targets` guards against, at the point where the whole
    audit is empty rather than merely short.
    """

    def test_empty_changed_set_exits_2_instead_of_reporting_clean(self):
        code, out, err = self._run([write_lock(self, self.LOCK), "--changed", ""])
        self.assertEqual(code, 2, "an audit of nothing is a failed audit")
        self.assertNotIn("CLEAN", out)
        self.assertIn("nothing selected", err)

    def test_changed_vs_an_identical_lockfile_exits_2(self):
        """The likely shape: --changed-vs aimed at the wrong lockfile.

        Comparing a lockfile against itself — or against the branch it was read
        from — makes every (name, version) pair match, which reads as "no
        changes" rather than "wrong input".
        """
        lock = write_lock(self, self.LOCK)
        code, out, _ = self._run([lock, "--changed-vs", lock])
        self.assertEqual(code, 2)
        self.assertNotIn("CLEAN", out)

    def test_verdict_line_carries_the_counts(self):
        code, out, _ = self._run(
            [write_lock(self, self.LOCK), "--changed", "rumdl"], meta=self._meta()
        )
        self.assertEqual(code, 0)
        self.assertIn("CLEAN", out)
        self.assertIn("1 package(s), 1 artifact(s) checked", out)

    def test_non_pypi_packages_are_named_not_silently_dropped(self):
        """A git or private-index dependency is outside the audit; say so."""
        _, out, _ = self._run(
            [write_lock(self, self.LOCK), "--changed", "rumdl"], meta=self._meta()
        )
        self.assertIn("NOT checked", out)
        self.assertIn("fpga-simulator", out)

    def test_an_unrecorded_size_survives_into_the_report(self):
        """A third state beside match/MISMATCH, or the distinction is lost at the
        last step — and `--json` gets null, which is the honest encoding."""
        lock = self.LOCK.replace(", size = 100 }", " }")
        code, out, _ = self._run([write_lock(self, lock), "--changed", "rumdl"], meta=self._meta())
        self.assertEqual(code, 0, "a correct artifact with no size is not a finding")
        self.assertIn("size not recorded", out)
        self.assertNotIn("size MISMATCH", out)

        _, out, _ = self._run(
            [write_lock(self, lock), "--changed", "rumdl", "--json"], meta=self._meta()
        )
        self.assertIsNone(json.loads(out)["provenance"][0]["artifacts"][0]["checks"]["size"])

    def test_an_artifact_swap_at_an_unchanged_version_is_announced(self):
        """The diagnostic is the whole point: the empty-selection message offered
        two benign explanations, and neither was what happened."""
        base = write_lock(self, self.LOCK)
        pr = write_lock(self, self.LOCK.replace("a" * 64, "0" * 64))
        code, out, err = self._run([pr, "--changed-vs", base], meta=self._meta())
        self.assertIn("ARTIFACTS CHANGED", err)
        self.assertEqual(code, 1, "the swapped hash no longer matches PyPI")
        self.assertIn("MISMATCH", out)

    def test_json_mode_stays_parseable_alongside_changed_vs(self):
        """--changed-vs is the mode the skill recommends, so its diagnostic line
        must not land on stdout in front of the JSON."""
        base = write_lock(self, self.LOCK.replace('version = "0.2.53"', 'version = "0.2.52"', 1))
        code, out, _ = self._run(
            [write_lock(self, self.LOCK), "--changed-vs", base, "--json"],
            meta=self._meta(),
        )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["skipped"], ["fpga-simulator"])


class TestFailuresAreNotFindings(_MainHarness):
    """Exit 2 means the audit could not run; exit 1 means it found something.

    An unhandled exception exits 1, so any failure left unhandled borrows the
    status reserved for findings — a registry outage reads as a vulnerability.
    """

    def test_a_missing_lockfile_exits_2(self):
        code, _, err = self._run(["/nonexistent/uv.lock", "--changed", "p"])
        self.assertEqual(code, 2)
        self.assertIn("cannot read", err)

    def test_malformed_toml_exits_2(self):
        code, _, err = self._run([write_lock(self, "not toml [[[\n"), "--changed", "p"])
        self.assertEqual(code, 2)
        self.assertIn("cannot read", err)

    def test_a_lockfile_with_no_package_entries_exits_2(self):
        code, _, err = self._run([write_lock(self, "version = 1\n"), "--changed", "p"])
        self.assertEqual(code, 2)
        self.assertIn("lockfile", err)

    def test_an_unreadable_baseline_exits_2(self):
        code, _, err = self._run(
            [write_lock(self, self.LOCK), "--changed-vs", "/nonexistent/base.lock"]
        )
        self.assertEqual(code, 2)
        self.assertIn("baseline", err)

    def test_an_osv_outage_exits_2_not_1(self):
        code, _, err = self._run(
            [write_lock(self, self.LOCK), "--changed", "rumdl"],
            meta=self._meta(),
            fails={"osv.dev": urllib.error.URLError("simulated outage")},
        )
        self.assertEqual(code, 2, "an outage must not read as a vulnerability")
        self.assertIn("OSV", err)

    def test_a_pypi_outage_exits_2(self):
        code, _, err = self._run(
            [write_lock(self, self.LOCK), "--changed", "rumdl"],
            fails={"pypi.org": urllib.error.URLError("simulated outage")},
        )
        self.assertEqual(code, 2)
        self.assertIn("PyPI", err)

    def test_a_version_that_cannot_be_ordered_exits_2(self):
        """Not a crash and not a finding: an input the script cannot judge."""
        lock = self.LOCK.replace('version = "0.2.53"', 'version = "not-a-version"', 1)
        code, out, err = self._run(
            [write_lock(self, lock), "--changed", "rumdl"], meta=self._meta()
        )
        self.assertEqual(code, 2)
        self.assertIn("PEP 440", err)
        self.assertNotIn("CLEAN", out)

    def test_an_unforeseen_exception_exits_2_not_1(self):
        """The three cases above are the *foreseen* failures. This is the rest.

        A wheel entry with no `url` key — plausible from a non-PyPI index, a
        future format revision, or a hand-edited lockfile. Left unguarded it
        raises KeyError out of main(), Python prints a traceback, and the process
        exits 1 — the status the contract reserves for "ran and found something".
        The lockfile is written by the PR under audit, which is the input least
        entitled to be well-formed.
        """
        no_url = """
[[package]]
name = "rumdl"
version = "0.2.53"
source = { registry = "https://pypi.org/simple" }
wheels = [
    { hash = "sha256:aaaa", size = 100 },
]
"""
        code, out, err = self._run(
            [write_lock(self, no_url), "--changed", "rumdl"],
            meta=self._meta(),
            entry_point=cli,
        )
        self.assertEqual(code, 2, "a crash must not borrow the status that means a finding")
        self.assertIn("KeyError", err)
        self.assertNotIn("RESULT", out)

    def test_the_guard_does_not_swallow_a_real_verdict(self):
        """SystemExit has to re-raise first, or fail()'s exit 2 and main()'s
        legitimate 0/1 are all rewritten into 2."""
        code, out, _ = self._run(
            [write_lock(self, self.LOCK), "--changed", "rumdl"],
            meta=self._meta(),
            entry_point=cli,
        )
        self.assertEqual(code, 0)
        self.assertIn("CLEAN", out)


class TestEcosystemBoundary(_MainHarness):
    """Handed another ecosystem's lockfile, the script used to blame itself.

    Observed on `BIRSAx2/mdcat`, a Rust repo:

        error: unexpected AttributeError: 'str' object has no attribute 'get'
               This is a bug, not a finding.

    Everything about that was right except the diagnosis. Exit 2 is correct and
    no false CLEAN was printed — the failure-versus-finding contract held against
    an input it was never designed to see. But `Cargo.lock` writes `source` as a
    string where uv writes a table, so the run reached `_is_pypi` and died, and
    the message sent the reader hunting for a defect that does not exist.

    Since 0.8.0 the supported surface is `uv.lock` and GitHub Actions, so this
    message is the *boundary of the tool* and the first thing anyone arriving
    with a different lockfile sees. Phase 1 leads with the script, so arriving
    there innocently is the common path, not the careless one.

    `poetry.lock` was worse than the Rust case and is the reason this class
    covers more than the three formats the issue named. It parses, yields zero
    PyPI-sourced packages, and exits 2 saying *"either this lockfile did not
    change, or it is being compared against itself"* — a confident false
    diagnosis, in this plugin's own vocabulary, on Python's other lockfile.
    """

    # Fixtures are the smallest excerpt that carries the real signature; each
    # was checked against a full file from a public repository.
    FOREIGN: ClassVar[dict[str, tuple[str, str]]] = {
        "Cargo.lock": (
            'version = 4\n\n[[package]]\nname = "adler2"\nversion = "2.0.0"\n'
            'source = "registry+https://github.com/rust-lang/crates.io-index"\n'
            'checksum = "512761e0bb2578dd7380c6baaa0f4ce03e84f95e960231d1dec8bf4d7d6e2627"\n',
            "Cargo.lock",
        ),
        "poetry.lock": (
            '[[package]]\nname = "anyio"\nversion = "4.14.2"\noptional = false\n'
            'python-versions = ">=3.10"\nfiles = [\n    {file = "anyio-4.14.2.tar.gz", '
            'hash = "sha256:cfa1"},\n]\n\n[metadata]\nlock-version = "2.1"\n',
            "poetry.lock",
        ),
        "package-lock.json": (
            '{\n  "name": "npm",\n  "lockfileVersion": 3,\n  "packages": {}\n}\n',
            "package-lock.json",
        ),
        "go.sum": (
            "cloud.google.com/go v0.123.0 h1:2NAUJwPR47q+E35uaJeYoNhuNEM9kM8SjgRgdeOJUSE=\n"
            "cloud.google.com/go v0.123.0/go.mod h1:xBoMV08QcqUGuPW65Qfm1o9Y4zKZBpGS+7b=\n",
            "go.sum",
        ),
        # The other half of the pair a Go bump's diff contains, and the half
        # whose name reads like the file Phase 1 asks for. `cli/cli`'s: the TOML
        # parser reached line 1 column 8 of `module github.com/cli/cli/v2` and
        # reported `Expected '=' after a key in a key/value pair` — a syntax
        # complaint about a valid file.
        "go.mod": (
            "module github.com/cli/cli/v2\n\ngo 1.26.0\n\ntoolchain go1.26.6\n\n"
            "require (\n\tcharm.land/bubbles/v2 v2.1.1\n)\n",
            "go.mod",
        ),
        "Pipfile.lock": (
            '{\n  "_meta": {"pipfile-spec": 6, "hash": {"sha256": "43"}},\n  "default": {}\n}\n',
            "Pipfile.lock",
        ),
        # The header alone is two comment lines, which is *valid TOML* — the
        # case that proved the sniff has to run before the parse, not after it
        # fails. The entry below is what a real file carries and is not.
        "yarn.lock": (
            "# THIS IS AN AUTOGENERATED FILE. DO NOT EDIT THIS FILE DIRECTLY.\n"
            "# yarn lockfile v1\n\n"
            '"@babel/core@^7.0.0":\n  version "7.26.10"\n',
            "yarn.lock",
        ),
        "pnpm-lock.yaml": (
            "lockfileVersion: '9.0'\n\nsettings:\n  autoInstallPeers: true\n",
            "pnpm",
        ),
        "pyproject.toml": (
            '[build-system]\nrequires = ["hatchling"]\n\n[project]\nname = "x"\n',
            "manifest, not a lockfile",
        ),
    }

    def _file(self, name: str, body: str) -> str:
        directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        path = pathlib.Path(directory) / name
        path.write_text(body, encoding="utf-8")
        return str(path)

    def test_each_foreign_lockfile_is_named_rather_than_blamed_on_the_script(self):
        for name, (body, expected) in self.FOREIGN.items():
            with self.subTest(name):
                code, out, err = self._run([self._file(name, body), "--changed", "p"])
                self.assertEqual(code, 2, "the exit code was never what needed changing")
                self.assertIn(expected, err, f"{name} is not named in the message")
                self.assertNotIn(
                    "This is a bug",
                    err,
                    "reserved for genuinely unexpected exceptions; spending it here is "
                    "what makes it worthless when it does appear",
                )
                self.assertIn("references/uv-lock.md", err, "no route out of the boundary")
                self.assertNotIn("CLEAN", out)

    def test_a_cargo_lock_no_longer_reaches_the_code_that_crashed_on_it(self):
        """The specific defect: `source` is a string, `_is_pypi` calls `.get` on it."""
        body, _ = self.FOREIGN["Cargo.lock"]
        code, _, err = self._run(
            [self._file("Cargo.lock", body), "--changed", "p"], entry_point=cli
        )
        self.assertEqual(code, 2)
        self.assertNotIn("AttributeError", err)

    def test_a_poetry_lock_is_not_reported_as_compared_against_itself(self):
        """It parsed, matched nothing, and diagnosed the *invocation* instead.

        The worst of the set: no crash, no traceback, and a message that reads
        as a mistake the caller made rather than the boundary they hit.
        """
        body, _ = self.FOREIGN["poetry.lock"]
        path = self._file("poetry.lock", body)
        code, _, err = self._run([path, "--changed-vs", path])
        self.assertEqual(code, 2)
        self.assertIn("poetry.lock", err)
        self.assertNotIn("compared against itself", err)

    def test_a_foreign_baseline_is_named_too(self):
        """`--changed-vs` reads a second lockfile, through the same door."""
        body, _ = self.FOREIGN["Cargo.lock"]
        code, _, err = self._run(
            [write_lock(self, self.LOCK), "--changed-vs", self._file("Cargo.lock", body)]
        )
        self.assertEqual(code, 2)
        self.assertIn("Cargo.lock", err)
        self.assertNotIn("cannot read baseline", err, "it reads fine; it is not ours to audit")

    def test_toml_with_packages_that_are_not_uv_shaped_is_refused_not_crashed(self):
        """The formats above are the ones worth naming, not the whole world.

        Anything else with `[[package]]` blocks still must not be walked into:
        proceeding means `pypi_sourced` returns nothing and the run reports on an
        empty selection. Refusing without a name is honest; guessing one is not.
        """
        unknown = 'version = 1\n\n[[package]]\nname = "x"\nversion = "1"\ndeps = ["y"]\n'
        code, _, err = self._run([self._file("mystery.lock", unknown), "--changed", "x"])
        self.assertEqual(code, 2)
        self.assertIn("not a uv.lock", err)
        self.assertNotIn("This is a bug", err)

    def test_a_real_uv_lock_still_loads(self):
        """The direction that would be worse than the defect being fixed.

        A sniffer that misfires refuses a lockfile the plugin does support. The
        first draft ordered the checks the other way — identify uv first, then
        diagnose — and a real `poetry.lock` passed it, because poetry writes a
        `[package.source]` table too.
        """
        packages = load_lock(write_lock(self, self.LOCK))
        self.assertEqual([p["name"] for p in packages], ["rumdl", "fpga-simulator"])

    def test_a_uv_lock_of_only_git_and_editable_sources_still_loads(self):
        """No registry, no wheels — the shape most likely to look foreign."""
        vcs = """
[[package]]
name = "x"
version = "1.0"
source = { git = "https://example.invalid/x.git" }

[[package]]
name = "y"
version = "0.1"
source = { editable = "." }
"""
        self.assertEqual([p["name"] for p in load_lock(write_lock(self, vcs))], ["x", "y"])


class TestForkDisclosure(_MainHarness):
    """A forked package is verified in full and installed in part.

    `uv sync --locked` asserts the whole lockfile is consistent with the
    manifest, across every fork. The install then materialises only the
    resolution for the interpreter that happens to be present. Phase 1 verifies
    every fork's artifacts against the registry, so a green Phase 5 on 3.14 says
    nothing about whether the 3.11 fork's artifacts install — and nothing in the
    output distinguished the two.

    Mechanised here rather than left to `SKILL.md`, per the rule in
    CONTRIBUTING: prose is the weakest of the three levers, and a disclosure the
    report is merely asked to remember is one it can omit silently.
    """

    FORKED = f"""
[[package]]
name = "rpds-py"
version = "0.30.0"
source = {{ registry = "https://pypi.org/simple" }}
resolution-markers = ["python_full_version < '3.11'"]
wheels = [
    {{ url = "https://files.pythonhosted.org/packages/xx/p-0.30.0.whl", hash = "sha256:{"a" * 64}", size = 100 }},
]

[[package]]
name = "rpds-py"
version = "2026.6.3"
source = {{ registry = "https://pypi.org/simple" }}
resolution-markers = ["python_full_version >= '3.11'"]
wheels = [
    {{ url = "https://files.pythonhosted.org/packages/xx/p-2026.6.3.whl", hash = "sha256:{"a" * 64}", size = 100 }},
]
"""

    def _forked_meta(self):
        return pypi_meta(
            "2026.6.3",
            [],
            releases={
                "0.30.0": [pypi_file("p-0.30.0.whl")],
                "2026.6.3": [pypi_file("p-2026.6.3.whl")],
            },
        )

    def test_the_report_names_every_pin_of_a_forked_package(self):
        code, out, _ = self._run(
            [write_lock(self, self.FORKED), "--changed", "rpds-py"], meta=self._forked_meta()
        )
        self.assertEqual(code, 0)
        self.assertIn("forked packages", out)
        self.assertIn("0.30.0, 2026.6.3", out, "both pins, in version order")

    def test_the_disclosure_says_the_install_covers_one_of_them(self):
        """Counting the pins is not the point; the asymmetry is.

        "2 pins" alongside a green install still reads as two installs. The row
        has to say that the provenance checks covered every fork and the install
        covered one.
        """
        _, out, _ = self._run(
            [write_lock(self, self.FORKED), "--changed", "rpds-py"], meta=self._forked_meta()
        )
        self.assertIn("one of them installed", out)
        self.assertIn("uv run python -V", out, "the interpreter is the thing to record")

    def test_an_unforked_lockfile_says_nothing_about_forks(self):
        """The row would be noise on most lockfiles, and noise trains the reader
        to skip the section it appears in."""
        code, out, _ = self._run(
            [write_lock(self, self.LOCK), "--changed", "rumdl"], meta=self._meta()
        )
        self.assertEqual(code, 0)
        self.assertNotIn("forked packages", out)

    def test_the_pins_survive_into_json_mode(self):
        """The machine-readable half has to carry it too, or a caller reading
        JSON gets the pre-fix output back."""
        _, out, _ = self._run(
            [write_lock(self, self.FORKED), "--changed", "rpds-py", "--json"],
            meta=self._forked_meta(),
        )
        currency = json.loads(out)["currency"]
        self.assertEqual([c["pinned"] for c in currency], [["0.30.0", "2026.6.3"]] * 2)
        self.assertEqual({c["pins"] for c in currency}, {2})


class TestOsvBatching(_MainHarness):
    """`querybatch` rejects a batch over 1000 queries with a 400.

    Measured against api.osv.dev: 1000 queries -> OK, 1001 -> HTTP 400. It fails
    safe — the HTTPError is caught and routed through fail() — but it kills an
    otherwise-complete audit at the last step, after every provenance and currency
    call has been paid for, with a message pointing at OSV rather than at the
    lockfile size. A 1000-package lockfile is ordinary for a monorepo.
    """

    def _pkgs(self, count):
        return [{"name": f"p{i}", "version": "1.0"} for i in range(count)]

    def _stub(self):
        """Records each batch's size and returns one empty result per query."""
        sizes = []

        def fake(url, payload=None):
            queries = json.loads(payload)["queries"]
            sizes.append(len(queries))
            return {"results": [{} for _ in queries]}

        return fake, sizes

    def test_a_batch_over_the_limit_is_chunked(self):
        fake, sizes = self._stub()
        with mock.patch("audit._get_json", fake):
            result = check_vulns(self._pkgs(2500))
        self.assertEqual(sizes, [1000, 1000, 500], "every chunk must stay within the limit")
        self.assertEqual(result["queried"], 2500)

    def test_exactly_the_limit_is_one_batch(self):
        fake, sizes = self._stub()
        with mock.patch("audit._get_json", fake):
            check_vulns(self._pkgs(audit.OSV_BATCH_LIMIT))
        self.assertEqual(sizes, [audit.OSV_BATCH_LIMIT], "1000 is accepted; do not split it")

    def test_findings_survive_chunking_and_stay_with_their_package(self):
        """zip(..., strict=True) is the guard that a chunk came back short; the
        pairing is what makes a hit name the right package."""

        def fake(url, payload=None):
            queries = json.loads(payload)["queries"]
            return {
                "results": [
                    {"vulns": [{"id": f"GHSA-{q['package']['name']}"}]}
                    if q["package"]["name"] == "p1500"
                    else {}
                    for q in queries
                ]
            }

        with mock.patch("audit._get_json", fake):
            result = check_vulns(self._pkgs(2500))
        self.assertEqual([h["name"] for h in result["hits"]], ["p1500"])
        self.assertEqual(result["hits"][0]["ids"], ["GHSA-p1500"])

    def test_a_paginated_result_is_followed_rather_than_truncated(self):
        """A querybatch result carries one page. Unread, the extra ids are simply
        dropped from the report — under-reporting, silently."""
        calls = []

        def fake(url, payload=None):
            queries = json.loads(payload)["queries"]
            calls.append(queries)
            if queries[0].get("page_token") == "more":
                return {"results": [{"vulns": [{"id": "GHSA-second"}]}]}
            return {"results": [{"vulns": [{"id": "GHSA-first"}], "next_page_token": "more"}]}

        with mock.patch("audit._get_json", fake):
            result = check_vulns(self._pkgs(1))
        self.assertEqual(result["hits"][0]["ids"], ["GHSA-first", "GHSA-second"])
        self.assertEqual(len(calls), 2, "the second page was never fetched")


class TestRetry(unittest.TestCase):
    """One retry, and only for the failures worth retrying."""

    URL = "https://example.test/x"

    def _http_error(self, code):
        # HTTPError is a file object; closing it keeps the suite ResourceWarning-free.
        hdrs = email.message.Message()
        exc = urllib.error.HTTPError(self.URL, code, "simulated", hdrs, None)
        self.addCleanup(exc.close)
        return exc

    def _urlopen(self, script):
        calls = []

        def fake(req, timeout=None):
            calls.append(getattr(req, "full_url", req))
            item = script.pop(0)
            if isinstance(item, Exception):
                raise item
            return contextlib.closing(io.StringIO(item))

        return fake, calls

    def test_a_server_error_is_retried_once(self):
        fake, calls = self._urlopen([self._http_error(503), '{"ok": true}'])
        with mock.patch("urllib.request.urlopen", fake), mock.patch("time.sleep"):
            self.assertEqual(_get_json(self.URL), {"ok": True})
        self.assertEqual(len(calls), 2, "one transient 502 must not lose the audit")

    def test_a_429_is_retried_and_honours_retry_after(self):
        """The one 4xx that means "try again".

        Both registries this script talks to rate-limit, and an audit issues one
        PyPI call per changed package plus the OSV batch — exactly the burst shape
        that trips a limiter. Aborting at exit 2 is correct per the contract and
        avoidable.
        """
        exc = self._http_error(429)
        exc.headers["Retry-After"] = "7"
        fake, calls = self._urlopen([exc, '{"ok": true}'])
        slept: list[float] = []
        with mock.patch("urllib.request.urlopen", fake), mock.patch("time.sleep", slept.append):
            self.assertEqual(_get_json(self.URL), {"ok": True})
        self.assertEqual(len(calls), 2, "429 explicitly means try again")
        self.assertEqual(slept, [7.0], "Retry-After was ignored")

    def test_an_absurd_retry_after_is_capped(self):
        """Failing fast at exit 2 beats a silent ten-minute stall."""
        exc = self._http_error(429)
        exc.headers["Retry-After"] = "600"
        fake, _ = self._urlopen([exc, '{"ok": true}'])
        slept: list[float] = []
        with mock.patch("urllib.request.urlopen", fake), mock.patch("time.sleep", slept.append):
            _get_json(self.URL)
        self.assertEqual(slept, [audit.RETRY_AFTER_CAP])

    def test_a_retry_after_http_date_falls_back_to_the_backoff(self):
        """Retry-After may also be an HTTP-date, which is not worth parsing for
        one retry — but it must not crash the way through."""
        exc = self._http_error(429)
        exc.headers["Retry-After"] = "Wed, 21 Oct 2026 07:28:00 GMT"
        fake, _ = self._urlopen([exc, '{"ok": true}'])
        slept: list[float] = []
        with mock.patch("urllib.request.urlopen", fake), mock.patch("time.sleep", slept.append):
            _get_json(self.URL)
        self.assertEqual(slept, [audit.BACKOFF])

    def test_a_404_is_not_retried(self):
        """A 4xx is an answer, not a hiccup: retrying delays a message that is
        already the information the caller needs."""
        fake, calls = self._urlopen([self._http_error(404)])
        with (
            mock.patch("urllib.request.urlopen", fake),
            mock.patch("time.sleep"),
            self.assertRaises(urllib.error.HTTPError),
        ):
            _get_json(self.URL)
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
