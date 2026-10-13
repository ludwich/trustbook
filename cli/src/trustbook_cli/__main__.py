"""``python -m trustbook_cli``: the same CLI and exit codes as ``trustbook``.

The fallback for machines whose application control blocks the pip-generated ``trustbook.exe``
(docs/cli.md, "If trustbook.exe is blocked").
"""

from __future__ import annotations

import sys

from trustbook_cli.main import main

if __name__ == "__main__":
    sys.exit(main())
