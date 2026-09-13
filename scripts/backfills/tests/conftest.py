"""Make the backfill scripts importable as modules (they are CLIs, not a package)."""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
for _p in (_ROOT, _ROOT / "scripts" / "bucket", _ROOT / "scripts" / "backfills"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
