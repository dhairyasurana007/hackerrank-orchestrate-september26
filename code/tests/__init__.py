"""Test package.

Tests are discovered from the repository root (``python -m unittest discover -s code/tests
-t .``) so they import as ``code.tests.test_*``. The implementation, however, is imported as
``buyorwait.*`` — the same name ``code/main.py`` uses when run as a script — so that a module
never ends up loaded twice under two names with two copies of its module-level state.
"""

from __future__ import annotations

import sys
from pathlib import Path

_CODE_DIR = Path(__file__).resolve().parent.parent
if str(_CODE_DIR) not in sys.path:
    sys.path.insert(0, str(_CODE_DIR))
