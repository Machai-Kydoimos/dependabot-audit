"""vendored.py against the real wheels and OSV, on the three releases behind #171.

The hermetic suite builds its own wheels, so it can only agree with what this
repo believes PyPI serves and OSV answers. These read the published ones:

  - ruff 0.16.7 -> 0.16.8: both ship `salsa` 0.28.2, and RUSTSEC-2026-0308 is
    fixed only above the PR, in 0.16.9, whose notes never name it;
  - rumdl 0.2.75 -> 0.2.76: `rustls` moved in `Cargo.lock` and ships in no wheel,
    so its advisory must never appear;
  - pydantic-core 2.41.4 -> 2.41.5: no wheel carries an SBOM -- underivable.

    RUN_NETWORK_TESTS=1 python3 -m unittest discover -s integration -v

Needs the network. The releases are immutable, but OSV is not: a new advisory on
a crate one of them ships would add a row, so each test asserts on its own
advisory and not on the absence of all others.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import unittest

SCRIPT = (
    pathlib.Path(__file__).resolve().parent.parent / "skills/dependabot-audit/scripts/vendored.py"
)

live = unittest.skipUnless(
    os.environ.get("RUN_NETWORK_TESTS"),
    "set RUN_NETWORK_TESTS=1; this reads wheels from PyPI and advisories from OSV",
)


def vendored(package: str, current: str, proposed: str) -> tuple[int, str]:
    done = subprocess.run(
        [sys.executable, str(SCRIPT), "--package", package, "--current", current,
         "--proposed", proposed],
        capture_output=True, text=True, check=False, timeout=900,
    )  # fmt: skip
    return done.returncode, done.stdout + done.stderr


@live
class TestThePublishedWheels(unittest.TestCase):
    def test_ruffs_salsa_fix_is_above_the_pr(self) -> None:
        code, out = vendored("ruff", "0.16.7", "0.16.8")
        self.assertEqual(code, 1, out)
        self.assertIn("FIXED ABOVE THIS PR  RUSTSEC-2026-0308", out)
        self.assertIn("proposed 0.16.8: in 17 of 17 wheel(s)", out)

    def test_rumdls_rustls_is_never_shipped(self) -> None:
        code, out = vendored("rumdl", "0.2.75", "0.2.76")
        self.assertIn(code, (0, 1), out)
        self.assertNotIn("RUSTSEC-2026-0285", out)
        self.assertIn("7 compiled wheel(s), 7 with an SBOM read", out)

    def test_a_release_without_sboms_is_underivable(self) -> None:
        code, out = vendored("pydantic-core", "2.41.4", "2.41.5")
        self.assertEqual(code, 1, out)
        self.assertIn("proposed 2.41.5: 120 compiled wheel(s), 0 with an SBOM read", out)
        self.assertIn("no SBOM in the first 3 of 120 compiled wheel(s)", out)


if __name__ == "__main__":
    unittest.main()
