"""Job-launch package. One subsystem per kind; shared infra in ``base``.

Public entry points re-exported here so callers (routes, tests, admin tools)
don't have to know which module owns each kind:

  - ``ts``           — Generate timestamps (aligner timing run).
  - ``hf_publish``   — Push a recitation to the public HF dataset.
  - ``cut_release``  — Cut a new global GH release snapshot.

Each kind exposes ``launch(...)``, ``running_job_for(...)``, ``job_status(...)``
and ``complete(job_id, payload)``. The poll fallback in ``base`` dispatches to
each kind's ``complete`` on terminal-success so the webhook + poll paths are
idempotent on a single ``(kind, slug, version)`` key.

The HF Job NEVER writes ``db/inspector.db``. All DB mutations flow through
Inspector via the ``complete()`` handlers (called by the webhook receiver in
``routes/webhooks`` OR by the in-process poll fallback). The HF Job side only
writes to bucket files.
"""

from __future__ import annotations

from . import base, cut_release, hf_publish, ts

__all__ = ["base", "ts", "hf_publish", "cut_release"]
