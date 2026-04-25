"""Enforce pydocstyle conformance across src/ and tests/.

Configuration lives in setup.cfg (with a nested override in
tests/.pydocstyle that relaxes per-test docstring requirements). This
test invokes pydocstyle as a subprocess and fails — printing the full
violation list — if any docstring drifts out of conformance.

Pydocstyle is invoked as a subprocess rather than via its Python API so
the test surface matches what a developer or CI runs from the shell.
"""

import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
TARGETS = ["src", "tests", "setup.py"]


def _pydocstyle_available() -> bool:
    try:
        subprocess.run(
            [sys.executable, "-m", "pydocstyle", "--version"],
            check=True,
            capture_output=True,
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


@pytest.mark.skipif(
    not _pydocstyle_available(),
    reason="pydocstyle not installed (install via pip install -e '.[dev]')",
)
def test_pydocstyle_clean():
    """src/ and tests/ must be free of pydocstyle violations."""
    result = subprocess.run(
        [sys.executable, "-m", "pydocstyle", *TARGETS],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.fail(
            "pydocstyle reported violations:\n\n"
            f"{result.stdout}{result.stderr}"
        )
