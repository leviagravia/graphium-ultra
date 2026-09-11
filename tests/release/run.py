#!/usr/bin/env python3
"""Run Graphium's permanent packaging/release qualification authority."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import unittest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default=None)
    args = parser.parse_args()

    release_dir = Path(__file__).resolve().parent
    repo_root = Path(args.repo or release_dir.parents[1]).resolve()
    os.environ["GRAPHIUM_REPO_ROOT"] = str(repo_root)

    # The runner owns the package import root.  Discover from the semantic
    # release directory, but use the repository root as unittest's top level
    # so modules are imported as tests.release.test_*, never as accidental
    # top-level modules dependent on the caller's PYTHONPATH/CWD.
    suite = unittest.defaultTestLoader.discover(
        start_dir=str(release_dir),
        pattern="test_*.py",
        top_level_dir=str(repo_root),
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
