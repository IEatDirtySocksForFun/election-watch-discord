"""Test bootstrap.

The documented command is the plain ``pytest`` executable, which puts the venv's script
directory (not the repository root) on sys.path. Make ``import electionwatch`` work without
installing the package.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Tests use a separate copy of the default races, so editing config/races.yaml for a
# particular bot doesn't break the suite. test_bot_config.py checks that the shipped file still loads.
os.environ["EW_RACES_FILE"] = str(ROOT / "tests" / "fixtures" / "races.yaml")
