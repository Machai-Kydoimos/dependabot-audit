"""The standing guard on the one real cost of the Simple API migration.

`audit.py` reads the Simple API (PEP 691/700/714), which is the specified
interface and the only one exposing PEP 740 provenance. It has no
`info.version`, so "latest" stopped being something PyPI hands over and became
this script's own computation over `versions` — which means the version
comparator is load-bearing, and a comparator that is subtly wrong under-reports
silently.

This checks the computation against what the legacy endpoint still declares,
across real projects with real version histories. It is the only thing that
would notice the two drifting apart.

    RUN_NETWORK_TESTS=1 python3 -m unittest discover -s integration -v

Deliberately not in the hermetic suite: it needs PyPI, and the hermetic suite's
value is that it runs offline and free on every commit.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import unittest
import urllib.request

sys.path.insert(
    0, str(pathlib.Path(__file__).resolve().parent.parent / "skills/dependabot-audit/scripts")
)

from audit import UA, _version_key, fetch_project, files_by_version, latest_version

# Chosen for awkwardness rather than popularity: a calendar-versioned project, a
# dotted name, one with thousands of releases, one with a rich pre-release
# history, and one that publishes both wheels and sdists for many platforms.
PROJECTS = [
    "attrs",
    "boto3",
    "certifi",
    "cryptography",
    "django",
    "numpy",
    "packaging",
    "pip",
    "python-dateutil",
    "ruff",
    "setuptools",
    "typing-extensions",
    "urllib3",
    "zope.interface",
]

requires_network = unittest.skipUnless(
    os.environ.get("RUN_NETWORK_TESTS"),
    "set RUN_NETWORK_TESTS=1; this queries pypi.org",
)


def legacy_latest(name: str) -> str:
    """What the endpoint the script no longer uses still says `latest` is."""
    request = urllib.request.Request(
        f"https://pypi.org/pypi/{name}/json", headers={"User-Agent": UA}
    )
    with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
        return str(json.load(response)["info"]["version"])


@requires_network
class TestComputedLatestAgreesWithPyPI(unittest.TestCase):
    def test_every_project(self):
        for name in PROJECTS:
            with self.subTest(project=name):
                project = fetch_project(name)
                computed = latest_version(project, files_by_version(project))
                self.assertEqual(
                    _version_key(computed),
                    _version_key(legacy_latest(name)),
                    f"{name}: computed {computed}, PyPI declares {legacy_latest(name)}",
                )


@requires_network
class TestTheSimpleApiStillLooksTheWayWeReadIt(unittest.TestCase):
    """Assumptions this script makes about the response, stated as assertions.

    Every one of these is something that would break a check quietly rather than
    loudly if the API moved underneath it.
    """

    def test_the_fields_the_script_reads_are_present(self):
        project = fetch_project("attrs")
        self.assertIn("versions", project, "PEP 700; the gap is computed from this")
        for record in project["files"]:
            self.assertIn("sha256", record["hashes"])
            for field in ("filename", "url", "size", "upload-time"):
                self.assertIn(field, record)

    def test_provenance_is_present_on_some_files_and_absent_on_others(self):
        """Both states have to keep existing, or the three-state report is wrong.

        Coverage is partial and version-dependent by nature: Trusted Publishing
        postdates most of PyPI. If every file gained provenance, "no attestation
        is not a finding" would deserve revisiting.
        """
        files = fetch_project("cryptography")["files"]
        attested = [f for f in files if f.get("provenance")]
        self.assertTrue(attested, "no attested files at all — has PEP 740 gone away?")
        self.assertLess(len(attested), len(files), "every file attested — revisit the reporting")

    def test_version_attribution_covers_almost_everything(self):
        """A file that cannot be attributed costs a timestamp, never a gap entry.

        The threshold is deliberately loose. What would matter is a *structural*
        change — filenames that stop carrying versions at all — not the handful
        of old sdists whose names predate normalisation.
        """
        total = attributed = 0
        for name in ("attrs", "django", "numpy", "setuptools", "zope.interface"):
            project = fetch_project(name)
            grouped = files_by_version(project)
            total += len(project["files"])
            attributed += sum(len(group) for group in grouped.values())
        self.assertGreater(attributed / total, 0.99, f"{attributed}/{total} files attributed")


if __name__ == "__main__":
    unittest.main()
