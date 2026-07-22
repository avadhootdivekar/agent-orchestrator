"""Puts the fixture root on sys.path so tests/ and .grading/tests/ can `import tracker`
directly (pytest's default "prepend" import mode only adds the nearest __init__.py-free
ancestor of the test file itself, not this directory). Every bench dev-medium fixture
that splits code/tests across directories carries an identical copy of this file
(matches dev-core's own conftest.py convention).
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
