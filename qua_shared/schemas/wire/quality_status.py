"""Request body for ``POST /api/quality-status/<slug>``.

Sets (a ``QualityStatus`` value) or clears (``null``) a delivery's manual
recitation quality rating. Routed through the audit-only
``reciter.quality_set`` transition.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from ..config.state import QualityStatus


class QualityStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quality_status: QualityStatus | None
