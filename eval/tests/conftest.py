"""Make ``eval/runner.py`` importable as ``runner`` (eval/ is not a package)."""

import sys
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent.parent
if str(EVAL_DIR) not in sys.path:
    sys.path.insert(0, str(EVAL_DIR))
