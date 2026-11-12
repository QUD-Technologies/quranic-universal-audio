"""Public release quality label — the binary view of a delivery's maintainer rating.

``delivery_states.quality_status`` keeps the three-way internal rating (clean /
almost_clean / needs_work, or unrated). Releases publish only whether a recitation
is ``verified`` (rated clean) or has ``known_issues`` (everything else).
"""

from __future__ import annotations

from qua_shared.schemas.wire.release import ReleaseQuality

VERIFIED: ReleaseQuality = "verified"
KNOWN_ISSUES: ReleaseQuality = "known_issues"


def release_quality(quality_status: str | None) -> ReleaseQuality:
    """``verified`` for a clean rating; ``known_issues`` for any other rating or none."""
    return VERIFIED if quality_status == "clean" else KNOWN_ISSUES
