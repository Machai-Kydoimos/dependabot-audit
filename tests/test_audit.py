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

import pathlib
import sys
import tempfile
import unittest

sys.path.insert(
    0, str(pathlib.Path(__file__).resolve().parent.parent / "skills/dependabot-audit/scripts")
)

from audit import (  # noqa: E402
    _normalize,
    check_currency,
    check_provenance,
    derive_changed,
    pypi_sourced,
    select_targets,
)

PYPI_SRC = {"registry": "https://pypi.org/simple"}


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
    """A minimal PyPI JSON response."""
    return {
        "info": {"version": latest or version},
        "releases": (releases or {version: files}),
    }


def pypi_file(filename, digest="a" * 64, size=100, yanked=False, uploaded="2026-01-01T00:00:00Z"):
    return {
        "filename": filename,
        "digests": {"sha256": digest},
        "size": size,
        "url": f"https://files.pythonhosted.org/packages/xx/{filename}",
        "yanked": yanked,
        "upload_time_iso_8601": uploaded,
    }


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
        tmp = tempfile.NamedTemporaryFile("w", suffix=".lock", delete=False)
        tmp.write(body)
        tmp.close()
        return tmp.name

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
        self.assertEqual(derive_changed(new, base), ["rumdl"])

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
        self.assertEqual(derive_changed(new, base), [])

    def test_detects_an_added_package(self):
        base = self._lock(self.BASE)
        new = pypi_sourced([entry("rumdl", "0.2.49"), entry("brand-new", "1.0")])
        self.assertEqual(derive_changed(new, base), ["brand-new"])


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
        pkg = entry(
            "p", "1.0", sdist=artifact("p-1.0.tar.gz", digest="b" * 64), wheels=[]
        )
        meta = pypi_meta("1.0", [pypi_file("p-1.0.tar.gz", digest="a" * 64)])
        result = check_provenance(pkg, meta)
        self.assertFalse(result["ok"])
        self.assertEqual(result["artifacts"][0]["kind"], "sdist")

    def test_a_clean_package_passes(self):
        pkg = entry("p", "1.0", wheels=[artifact("p-1.0.whl")])
        meta = pypi_meta("1.0", [pypi_file("p-1.0.whl")])
        self.assertTrue(check_provenance(pkg, meta)["ok"])


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

    def test_marker_constrained_pin_is_not_reported_as_stale(self):
        """A pin held back by an environment marker trails the registry by design."""
        pkg = entry("rpds-py", "0.30.0", markers=["python_full_version < '3.11'"])
        meta = pypi_meta(
            "0.30.0",
            [],
            latest="2026.6.3",
            releases={"0.30.0": [pypi_file("x.whl")], "2026.6.3": [pypi_file("y.whl")]},
        )
        result = check_currency(pkg, meta)
        self.assertFalse(result["current"])
        self.assertTrue(result["constrained"], "must not read as a staleness finding")

    def test_unconstrained_pin_is_not_marked_constrained(self):
        pkg = entry("p", "1.0")
        meta = pypi_meta("1.0", [], latest="1.1", releases={"1.0": [], "1.1": []})
        self.assertFalse(check_currency(pkg, meta)["constrained"])

    def test_current_version_reports_current(self):
        pkg = entry("p", "1.0")
        meta = pypi_meta("1.0", [pypi_file("p-1.0.whl")])
        result = check_currency(pkg, meta)
        self.assertTrue(result["current"])
        self.assertEqual(result["gap"], [])


if __name__ == "__main__":
    unittest.main()
