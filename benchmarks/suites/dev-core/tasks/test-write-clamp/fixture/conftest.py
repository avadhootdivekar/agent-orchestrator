"""Puts the fixture root on sys.path so tests/ can `import <module>` directly
(pytest's default "prepend" import mode only adds the nearest __init__.py-free
ancestor of the test file itself -- that is tests/, not this directory -- so
without this, `from stats import running_total` would fail with
ModuleNotFoundError). Every bench dev-core fixture that splits code/tests across
two directories carries an identical copy of this file.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
