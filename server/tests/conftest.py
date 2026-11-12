"""Shared pytest setup: make both import conventions resolvable.

The checkier tests import ``checkier.*`` (requires ``server/`` on sys.path)
while the HTTP-contract tests import ``server.app.*`` (requires the repo
root on sys.path, with ``server`` as a namespace package). Put both here so
``python -m pytest tests/ -q`` works from the ``server/`` directory (and any
other cwd) regardless of which module set is collected.
"""

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SERVER_DIR = _REPO_ROOT / "server"

for _p in (str(_SERVER_DIR), str(_REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)
