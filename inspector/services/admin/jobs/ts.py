"""TS-gen kind handler (aligner timestamps runs).

Thin façade over ``services.admin.timestamps_jobs`` (which ``routes/admin/reviews.py``
and ``routes/webhooks/ts_jobs.py`` import directly), exposing the kind-uniform
``launch`` / ``complete`` the poll dispatcher in ``jobs.base`` registers alongside
``hf_publish`` + ``cut_release``.
"""

from __future__ import annotations

from services.admin import timestamps_jobs as _impl

# Must equal the HF TS job's ``labels.task`` ("timestamps") — the poll
# dispatcher (``base._poll_terminal_jobs``) matches a job to its handler by
# that label, so registering under "ts" silently skips every TS completion.
# The bucket record dir stays "ts" (abbreviated via ``base._KIND_DIR``); only
# the dir abbreviates, the kind/label do not.
KIND = "timestamps"

# Re-export public API for the uniform jobs package surface.
launch = _impl.launch
running_job_for = _impl.running_job_for
job_status = _impl.job_status
cancel_job = _impl.cancel_job
list_job_records = _impl.list_job_records
read_job_record = _impl.read_job_record


def complete(slug: str | None, job_id: str) -> None:
    """Kind-uniform completion handler — adapts to ``complete_timestamps_job``.

    Idempotent: the legacy handler short-circuits on already-released rows
    and absent shards.
    """
    if slug is None:
        return
    _impl.complete_timestamps_job(slug, job_id)


def register() -> None:
    from . import base

    base.register_handler(KIND, complete)
