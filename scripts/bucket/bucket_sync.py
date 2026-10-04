"""Sync a local directory to/from a bucket prefix (plan-and-apply).

  # Pull a slug folder to disk for local inspection
  bucket_sync.py hf://buckets/QUD-Technologies/quranic-inspector-bucket/reciters/husary ./_scratch/husary

  # Push edits back
  bucket_sync.py ./_scratch/husary hf://buckets/QUD-Technologies/quranic-inspector-bucket-dev/reciters/husary

Use --dry-run first to see what would change; ``--yes-prod`` required when the
destination is the prod bucket.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import _bootstrap as bs  # noqa: E402


def _is_prod_target(dst: str) -> bool:
    return f"hf://buckets/{bs.BUCKETS['prod']}/" in dst


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("src", help="source: local path or hf://buckets/... URI")
    p.add_argument("dst", help="destination: local path or hf://buckets/... URI")
    p.add_argument("--dry-run", action="store_true", help="print planned ops, don't execute")
    p.add_argument("--delete", action="store_true", help="remove dst files that aren't in src")
    p.add_argument("--yes-prod", action="store_true", help="required to write to the prod bucket")
    a = p.parse_args()

    bs.ensure_utf8_stdout()
    bs.load_hf_token()

    if _is_prod_target(a.dst) and not a.yes_prod and not a.dry_run:
        print("refusing to sync TO prod bucket without --yes-prod", file=sys.stderr)
        return 2

    from huggingface_hub import sync_bucket

    res = sync_bucket(
        source=a.src,
        dest=a.dst,
        delete=a.delete,
        dry_run=a.dry_run,
        quiet=True,
    )
    print(json.dumps(res.summary(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
