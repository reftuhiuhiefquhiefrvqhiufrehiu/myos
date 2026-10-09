"""Run the NeonVeil test suite.

On some Linux + Qt combinations the interpreter can crash during global
teardown after the browser (QtWebEngine) tests, even when every test has
already passed. That shutdown race is not part of the tested behaviour, so we
report the real result, flush the streams, and leave through ``os._exit`` to
keep CI deterministic.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
ROOT_DIR = TESTS_DIR.parent


def main() -> int:
    sys.path.insert(0, str(TESTS_DIR))
    sys.path.insert(0, str(ROOT_DIR))
    sys.path.insert(0, str(ROOT_DIR / "desktop"))
    suite = unittest.defaultTestLoader.discover(str(TESTS_DIR))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    sys.stdout.flush()
    sys.stderr.flush()
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    os._exit(main())
