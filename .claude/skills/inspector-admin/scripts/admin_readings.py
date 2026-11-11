"""Write ``reciters/<slug>/readings.json`` for released Hafs deliveries lacking a current one.

Same build as the Timestamps readings panel's first request
(``services/reference/readings.refresh``), run ahead so no visitor pays it. Bucket file writes
only — no DB write, so the Space keeps running. With no slugs it covers every released Hafs
delivery whose summary is missing or of an older ``schema_version``; ``--force`` rebuilds
current ones too.

  admin_readings.py --prod --dry-run
  admin_readings.py --prod --yes-prod
  admin_readings.py mishary_rashid_al_afasy_mp3quran --force --prod --yes-prod
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import _bootstrap as bs  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("slugs", nargs="*", help="deliveries to build (default: every released Hafs one)")
    p.add_argument("--force", action="store_true", help="rebuild current summaries too")
    bs.add_common_args(p)
    a = p.parse_args()

    def _run(ctx) -> int:
        from services.reference import readings

        slugs = a.slugs or readings.released_hafs_slugs()
        todo = [s for s in slugs if a.force or readings.stored_doc(s) is None]
        print(f"{len(todo)}/{len(slugs)} deliveries to build")
        if a.dry_run:
            for s in todo:
                print(f"  {s}")
            return 0

        failed = []
        for s in todo:
            started = time.perf_counter()
            try:
                summary = readings.refresh(s)
                if readings.stored_doc(s) is None:
                    raise RuntimeError("summary built but not stored")
            except Exception as exc:  # noqa: BLE001 — report and carry on with the rest
                failed.append(s)
                print(f"  {s:42} FAILED {exc}")
                continue
            print(f"  {s:42} {len(summary.rows):3} rows  {time.perf_counter() - started:5.1f}s")
        print(f"== built {len(todo) - len(failed)}/{len(todo)}; failed: {failed or 'none'} ==")
        return 1 if failed else 0

    return bs.run(a, _run, need_actor=False, mutates=True, safe_write=False)


if __name__ == "__main__":
    raise SystemExit(main())
