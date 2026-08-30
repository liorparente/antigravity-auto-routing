"""Repository test suite discovery loader for standard unittest runners."""

import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent


def load_tests(
    loader: unittest.TestLoader,
    standard_tests: unittest.TestSuite,
    pattern: str | None,
) -> unittest.TestSuite:
    suite = unittest.TestSuite()
    worker_routing_dir = str(REPO_ROOT / "skills" / "worker-routing")
    council_tests_dir = str(REPO_ROOT / "skills" / "council-review" / "tests")

    suite.addTests(
        loader.discover(
            start_dir=worker_routing_dir,
            top_level_dir=worker_routing_dir,
            pattern=pattern or "test*.py",
        )
    )
    suite.addTests(
        loader.discover(
            start_dir=council_tests_dir,
            top_level_dir=council_tests_dir,
            pattern=pattern or "test*.py",
        )
    )
    return suite
