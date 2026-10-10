"""Per-delivery recitation profile — ``reciters/<slug>/recitation_profile.json``.

Written by the batch re-time (sibling repo ``qua``,
``qua_sdk.integrations.recitation_profile``) for Hafs deliveries: per madd type the
occurrence count and mean duration, a ``verdict`` (with its ``share``) for the types with
a length choice, then ghunnah and silence. A type with no occurrences has a null
``mean_ms`` and no verdict. Produced outside this repo, so unknown keys are ignored.
Served trimmed (no counts, no shares) as ``TsRecitationProfile`` by
``GET /api/ts/profile/<slug>``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

MaddLength = Literal["qasr", "tawassut", "ishbaa"]


class ProfileStats(BaseModel):
    model_config = ConfigDict(extra="ignore")

    n: int = 0
    mean_ms: int | None = None


class MaddStats(ProfileStats):
    verdict: MaddLength | None = None
    share: float | None = None


class SilenceStats(ProfileStats):
    median_ms: int | None = None


class MaddProfile(BaseModel):
    model_config = ConfigDict(extra="ignore")

    tabii: MaddStats = MaddStats()
    munfasil: MaddStats = MaddStats()
    muttasil: MaddStats = MaddStats()
    lazim: MaddStats = MaddStats()
    arid: MaddStats = MaddStats()
    leen: MaddStats = MaddStats()


class RecitationProfileDoc(BaseModel):
    model_config = ConfigDict(extra="ignore")

    schema_version: int
    madd: MaddProfile = MaddProfile()
    ghunnah: ProfileStats = ProfileStats()
    silence: SilenceStats = SilenceStats()
