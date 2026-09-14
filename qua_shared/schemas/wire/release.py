"""GitHub release artifact and Releases-tab wire schemas.

These models cover two surfaces:

- public GitHub release assets produced by ``qua_jobs/cut_release.py``;
- admin Releases-tab payloads code-generated to the Inspector frontend.

Release files are consumer-facing, so artifact models use ``extra="forbid"``.
Timestamp tiers are ordered occurrence timelines: every contiguous recited span
is a row, exactly one row per verse is ``canonical`` (the earliest complete
continuous take), and every tier row is an exact prefix of the next tier's.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel, model_serializer, model_validator

from qua_shared.schemas.bucket.release_settings import ReleaseSettings

SCHEMA_VERSION = 3
VERSE_KEY_RE = re.compile(r"^[1-9]\d{0,2}:[1-9]\d{0,2}$")
LOCATION_KEY_RE = re.compile(r"^[1-9]\d{0,2}:[1-9]\d{0,2}:[1-9]\d{0,2}$")

ChangeKind = Literal["added", "refresh", "unchanged"]
AudioCategory = Literal["by_surah", "by_ayah"]
TimestampTier = Literal["verse", "word", "letter"]


# ---------------------------------------------------------------------------
# Public release assets.
# ---------------------------------------------------------------------------


class FileDigest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sha256: str = Field(..., min_length=1)
    bytes: int = Field(..., ge=0)


class ReleaseManifestRecitation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    zip: str
    zip_url: str
    sha256: str
    bytes: int = Field(..., ge=0)
    coverage_ayahs: int = Field(..., ge=0)
    content_hash: str
    change_kind: ChangeKind
    ts_version: str

    #: Timestamp tiers this recitation actually ships, shallowest first. Every
    #: Hafs recitation carries all three; a recitation timed by aligning against
    #: Hafs as a proxy and projecting has no letter geometry and ships
    #: ``["verse", "word"]``. ``content_hash`` is taken over the deepest tier
    #: named here.
    tiers: list[TimestampTier] = Field(default_factory=lambda: ["verse", "word", "letter"])

    #: SDK riwayah slug the coordinates and text belong to. Absent on every
    #: release cut before multi-riwayah support, which were all Hafs.
    riwayah: str = "hafs"


class ReleaseEdition(BaseModel):
    """Provenance for one non-Hafs edition present in a release.

    A consumer rendering these timings needs the exact index revision the words
    were taken from and the font that script was typeset for; the Digital Khatt
    entries in ``static_refs`` answer that for Hafs only.
    """

    model_config = ConfigDict(extra="forbid")

    edition_id: str = Field(..., min_length=1)
    #: Digest of the word index the tier files' text was taken from — the same
    #: value each of this edition's tier files carries as ``script_sha256``.
    words_sha256: str = Field(..., min_length=1)
    #: Digest of the QPC script asset the edition is typeset for. Deliberately
    #: NOT called ``script_sha256``: that name is taken, on every tier file, by
    #: the word-index digest above, and the two are different hex strings.
    script_asset_sha256: str = Field(..., min_length=1)
    font_family: str = Field(..., min_length=1)
    #: Digest of the Hafs->edition word projection the timings were placed with.
    projection_sha256: str | None = None
    #: Release asset names of the edition's word script (``<riwayah>_words.json.gz``,
    #: digest ``words_sha256`` over its canonical JSON) and font - the non-Hafs
    #: counterpart of the Digital Khatt pair in ``static_refs``, which also
    #: carries these two files' digests.
    words_asset: str | None = None
    font_asset: str | None = None


class ReleaseManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[3] = SCHEMA_VERSION
    release_version: str
    created_at: str
    previous_version: str | None = None
    recitation_count: int = Field(..., ge=0)
    static_refs: dict[str, FileDigest] = Field(default_factory=dict)
    #: Keyed by SDK riwayah slug; empty for a Hafs-only release.
    editions: dict[str, ReleaseEdition] = Field(default_factory=dict)
    recitations: dict[str, ReleaseManifestRecitation] = Field(default_factory=dict)
    license: str = "CC-BY-4.0"


class ReleaseCatalogAudio(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Per chapter, the NATIVE source URL the timestamps are relative to. For a
    #: combined non-CDN source (one YouTube/Drive file serving several chapters)
    #: this is the shared source URL — multiple chapters map to it, each at a
    #: different ``chapter_offsets_ms``.
    chapter_urls: dict[str, str] = Field(default_factory=dict)
    #: Per chapter, the ms offset of that chapter's start *inside* its source
    #: URL. Present ONLY for non-CDN sources where the chapter doesn't begin at
    #: byte 0 — combined files (one URL → many chapters) or a single file with a
    #: trimmed lead-in. Absent/empty for CDN by-surah (each chapter is its own
    #: file at offset 0). Map a release tier-file timestamp into the source file
    #: with ``source_ms = chapter_offsets_ms.get(ch, 0) + tier_ms``. Omitted from
    #: the serialized JSON when empty so CDN catalogs stay byte-stable.
    chapter_offsets_ms: dict[str, int] = Field(default_factory=dict)
    sample_rate_hz: int | None = None
    channels: int | None = None
    bitrate_mode: str | None = None
    bitrate_kbps_nominal: int | None = None

    @model_serializer(mode="wrap")
    def _omit_empty_offsets(self, handler):
        data = handler(self)
        if not data.get("chapter_offsets_ms"):
            data.pop("chapter_offsets_ms", None)
        return data


class ReleaseCoverage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    surahs: int = Field(..., ge=0)
    ayahs: int = Field(..., ge=0)
    #: Surahs with ZERO covered verses, as compact ascending runs of surah
    #: numbers (``"1-84"``, ``"4,7,9,37,39-40,45,65"``). Omitted from the JSON
    #: when empty so complete recitations stay byte-stable.
    missing_surahs: str = ""
    #: Within-surah verse gaps (a surah that IS partly covered), as
    #: ``surah:ayah`` runs joined by ``", "`` (``"7:116, 41:15"``,
    #: ``"75:18-40"``). A whole missing surah is in ``missing_surahs``, never
    #: here. Omitted from the JSON when empty.
    missing_verses: str = ""

    @model_serializer(mode="wrap")
    def _omit_empty_missing(self, handler):
        data = handler(self)
        for key in ("missing_surahs", "missing_verses"):
            if not data.get(key):
                data.pop(key, None)
        return data


class ReleaseRecitationCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[3] = SCHEMA_VERSION
    slug: str
    reciter_id: str | None = None
    name_en: str | None = None
    name_ar: str | None = None
    riwayah: str | None = None
    style: str | None = None
    country: str | None = None
    channel: str | None = None
    audio_category: AudioCategory | str | None = None
    recording_context: str | None = None
    recording_year: int | None = None
    variant_label: str | None = None
    audio: ReleaseCatalogAudio = Field(default_factory=ReleaseCatalogAudio)
    coverage: ReleaseCoverage


class ReleaseCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[3] = SCHEMA_VERSION
    recitations: list[ReleaseRecitationCatalog] = Field(default_factory=list)


class RecitationManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[3] = SCHEMA_VERSION
    release_version: str
    slug: str
    created_at: str
    files: dict[str, FileDigest] = Field(default_factory=dict)


class TimestampMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[3] = SCHEMA_VERSION
    slug: str
    audio_category: str | None = None
    units: Literal["ms"] = "ms"
    verse_count: int = Field(..., ge=0)
    occurrence_count: int = Field(..., ge=0)
    tier: TimestampTier
    layout: str
    #: The script the row text is written in. ``digital_khatt_v2`` for Hafs;
    #: another edition names its own index id (e.g. ``warsh-v21+sdk-words-v1``),
    #: whose font is in the manifest's ``editions`` block. Free text rather than
    #: a closed union so a new edition revision does not need a schema bump to
    #: be publishable.
    script: str = Field(..., min_length=1)
    #: Digest of the script named above: the Digital Khatt file for Hafs, the
    #: edition's word index otherwise — matching ``editions[<riwayah>]``'s
    #: ``words_sha256``, not its ``script_asset_sha256``.
    script_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    unicode_indexing: Literal["scalar"] = "scalar"

    #: SDK riwayah slug. Always written since schema 3; the default covers
    #: tier files cut before multi-riwayah support, which were all Hafs.
    riwayah: str = "hafs"


class _TimestampDoc(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    meta: TimestampMeta = Field(alias="_meta")
    rows: list[list[Any]] = Field(default_factory=list)

    expected_tier: ClassVar[TimestampTier]

    @model_validator(mode="after")
    def _validate_rows(self):
        if self.meta.tier != self.expected_tier:
            raise ValueError(f"expected {self.expected_tier!r} tier, got {self.meta.tier!r}")
        refs: set[str] = set()
        canonical_by_ref: dict[str, int] = {}
        for row in self.rows:
            ref, canonical = self._validate_row(row)
            refs.add(ref)
            canonical_by_ref[ref] = canonical_by_ref.get(ref, 0) + int(canonical)
        if self.meta.verse_count != len(refs):
            raise ValueError("verse_count differs from unique occurrence refs")
        if self.meta.occurrence_count != len(self.rows):
            raise ValueError("occurrence_count differs from rows length")
        if any(count != 1 for count in canonical_by_ref.values()):
            raise ValueError("each verse ref must have exactly one canonical occurrence")
        return self

    def _validate_row(self, row: Any) -> tuple[str, bool]:
        raise NotImplementedError


class VerseTimestampsDoc(_TimestampDoc):
    expected_tier: ClassVar[TimestampTier] = "verse"

    def _validate_row(self, row: Any) -> tuple[str, bool]:
        if not (isinstance(row, list) and len(row) == 5 and _is_occurrence_prefix(row)):
            raise ValueError(
                "verse occurrence must be [ref,start_ms,end_ms,canonical,silence_after_ms]"
            )
        return row[0], row[3]


class WordTimestampsDoc(_TimestampDoc):
    expected_tier: ClassVar[TimestampTier] = "word"

    def _validate_row(self, row: Any) -> tuple[str, bool]:
        if not (
            isinstance(row, list)
            and len(row) == 6
            and _is_occurrence_prefix(row)
            and isinstance(row[5], list)
            and all(_is_word(w) for w in row[5])
        ):
            raise ValueError(
                "word occurrence must be [ref,start_ms,end_ms,canonical,silence_after_ms,words]"
            )
        return row[0], row[3]


class LetterTimestampsDoc(_TimestampDoc):
    expected_tier: ClassVar[TimestampTier] = "letter"

    def _validate_row(self, row: Any) -> tuple[str, bool]:
        valid_shape = (
            isinstance(row, list)
            and len(row) == 8
            and _is_occurrence_prefix(row)
            and isinstance(row[5], list)
            and all(_is_word(w) for w in row[5])
            and isinstance(row[6], str)
            and isinstance(row[7], list)
            and all(_is_token(token, len(row[5]), len(row[6])) for token in row[7])
        )
        if not valid_shape:
            raise ValueError(
                "letter occurrence must be "
                "[ref,start_ms,end_ms,canonical,silence_after_ms,words,text,tokens]"
            )
        word_texts = row[6].split(" ") if row[6] else []
        if len(word_texts) != len(row[5]):
            raise ValueError("DigitalKhatt word count differs from timing occurrences")
        word_ranges: list[tuple[int, int]] = []
        cursor = 0
        for text in word_texts:
            word_ranges.append((cursor, cursor + len(text)))
            cursor += len(text) + 1
        painted: set[int] = set()
        for token in row[7]:
            if token[2] < token[1]:
                raise ValueError("animation token end precedes start")
            word_from, word_to = word_ranges[token[0]]
            for start, end in token[4]:
                if start < word_from or end > word_to:
                    raise ValueError("animation paint range crosses its word occurrence")
                scalars = set(range(start, end))
                if painted & scalars:
                    raise ValueError("animation paint ranges overlap")
                painted |= scalars
        return row[0], row[3]


class DigitalKhattWord(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: int | None = None
    surah: str
    ayah: str
    word: str
    location: str
    text: str


class DigitalKhattDoc(RootModel[dict[str, DigitalKhattWord]]):
    @model_validator(mode="after")
    def _validate_locations(self):
        for key, word in self.root.items():
            if not LOCATION_KEY_RE.match(key):
                raise ValueError(f"invalid DigitalKhatt location key: {key!r}")
            if word.location != key:
                raise ValueError(f"DigitalKhatt location mismatch: {key!r} != {word.location!r}")
        return self


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _is_occurrence_prefix(row: list) -> bool:
    """``[ref, start_ms, end_ms, canonical, silence_after_ms]`` — the verse row,
    and the exact prefix every deeper tier's row starts with."""
    return (
        _is_ref(row[0])
        and _is_int(row[1])
        and _is_int(row[2])
        and row[2] >= row[1]
        and isinstance(row[3], bool)
        and _is_int(row[4])
        and row[4] >= 0
    )


def _is_ref(v: Any) -> bool:
    return isinstance(v, str) and bool(VERSE_KEY_RE.fullmatch(v))


def _is_word(v: Any) -> bool:
    return isinstance(v, list) and len(v) == 3 and _is_int(v[0]) and _is_int(v[1]) and _is_int(v[2])


def _is_token(v: Any, word_count: int, text_length: int) -> bool:
    return (
        isinstance(v, list)
        and len(v) == 5
        and _is_int(v[0])
        and 0 <= v[0] < word_count
        and _is_int(v[1])
        and _is_int(v[2])
        and isinstance(v[3], bool)
        and isinstance(v[4], list)
        and bool(v[4])
        and all(
            isinstance(span, list)
            and len(span) == 2
            and _is_int(span[0])
            and _is_int(span[1])
            and 0 <= span[0] < span[1] <= text_length
            for span in v[4]
        )
    )


# ---------------------------------------------------------------------------
# Admin Releases-tab wire payloads.
# ---------------------------------------------------------------------------


class StaleReason(str, Enum):
    """Why an artifact is stale — drives the remediation suggestion.

    Severity-ordered (``segments_edited`` > ``ts_regen`` > ``catalog_edit``):
    when more than one applies to a row the stronger wins, because regenerating
    timestamps (the fix for ``segments_edited``) subsumes a republish, which in
    turn subsumes a catalog refresh. The reason→action mapping lives in
    ``qua_shared/release_staleness.py``.

    ``segments_edited`` lives on the ``ts`` track only (computed, never stamped):
    segments were edited after the timestamps were generated, so the generated
    timestamps are behind the segments and need regenerating. The other two live
    on the published ``hf`` / ``gh`` tracks.
    """

    TS_REGEN = "ts_regen"  # per-verse timestamps changed → full republish / cut
    CATALOG_EDIT = "catalog_edit"  # catalog metadata changed → HF catalog refresh / next cut
    SEGMENTS_EDITED = "segments_edited"  # segments edited after TS gen → regenerate timestamps


class SuggestedAction(BaseModel):
    """The remediation the FE should offer (or merely note) for a stale row.

    Resolved server-side from ``(StaleReason, track)`` so the reason→action
    mapping stays single-sourced (no BE↔FE parity drift). The FE maps the
    ``action`` code to an API call and gates the control on ``capability``.
    """

    model_config = ConfigDict(extra="allow")

    action: str  # "refresh_hf_catalog" | "republish_hf" | "recut_gh" | "none"
    kind: Literal["actionable", "informational"]
    label: str
    capability: str | None = None


class AdminReleaseRow(BaseModel):
    model_config = ConfigDict(extra="allow")

    version: str
    produced_at: str
    stale_since: str | None = None
    stale_reason: StaleReason | None = None
    suggested_action: SuggestedAction | None = None
    #: Count of timestamp-affecting edits since this row was produced. Only set
    #: on the ``ts`` track when ``stale_reason == segments_edited``.
    edits_since: int | None = None
    #: Chapters touched by those edits — the default scope for an affected-only
    #: regeneration. Only set on the ``ts`` track when ``segments_edited``.
    affected_chapters: list[int] | None = None


class AdminGhReleaseMember(BaseModel):
    model_config = ConfigDict(extra="allow")

    change_kind: ChangeKind
    stale_since: str | None = None
    stale_reason: StaleReason | None = None
    suggested_action: SuggestedAction | None = None
    release_id: int | None = None
    ts_version: str | None = None


class AdminPublishError(BaseModel):
    """A reciter's failure in the most recent batch publish. Surfaced on the
    row so the FE can re-bucket it into "Failed to publish" until a retry
    supersedes it with a successful HF release."""

    model_config = ConfigDict(extra="allow")

    message: str
    job_id: str
    at: str | None = None


class AdminReleaseStatusRow(BaseModel):
    model_config = ConfigDict(extra="allow")

    slug: str
    name_en: str | None = None
    name_ar: str | None = None
    state: str | None = None
    riwayah: str
    style: str
    channel: str
    #: True when the open claim is marked-ready but no TS has been generated yet
    #: (``state == under_review`` + ``marked_ready_at`` set). Drives the
    #: "Ready to generate" bucket.
    marked_ready: bool = False
    #: Login of the reviewer who marked it ready — shown on Ready-to-generate
    #: rows (null otherwise).
    reviewer_login: str | None = None
    ts: AdminReleaseRow | None
    hf: AdminReleaseRow | None
    gh: AdminGhReleaseMember | None
    publish_error: AdminPublishError | None = None
    #: Count of segments carrying a flag (from detailed.json) — computed ONLY for
    #: Ready-to-generate rows so the admin sees outstanding flags before
    #: generating. None on every other bucket (no extra bucket read).
    flagged_issues_count: int | None = None


class AdminLatestGhRelease(BaseModel):
    model_config = ConfigDict(extra="allow")

    version: str
    produced_at: str
    external_uri: str | None = None


class AdminReleasesSummary(BaseModel):
    model_config = ConfigDict(extra="allow")

    version: str | None
    produced_at: str | None
    external_uri: str | None
    member_count: int
    total_bytes: int
    days_since_cut: int | None


class AdminInFlightJob(BaseModel):
    model_config = ConfigDict(extra="allow")

    kind: Literal["hf_publish", "hf_publish_batch", "cut_release", "timestamps", "refresh_catalog"]
    slug: str | None
    job_id: str
    started_at: str | None
    url: str | None = None


class AdminLastBatch(BaseModel):
    """Summary of the most recent batch publish, for the dismissable banner."""

    model_config = ConfigDict(extra="allow")

    job_id: str
    at: str | None = None
    published_count: int
    failed_count: int


class AdminReleasesStatusResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    latest_gh_release: AdminLatestGhRelease | None
    summary: AdminReleasesSummary | None
    in_flight: list[AdminInFlightJob]
    recitations: list[AdminReleaseStatusRow]
    last_batch: AdminLastBatch | None = None
    #: Number of current HF rows stale solely due to a catalog edit — drives the
    #: summary-card "Refresh dataset catalog" CTA badge.
    catalog_drift_count: int = 0


class AdminReleasePreviewRow(BaseModel):
    model_config = ConfigDict(extra="allow")

    slug: str
    name_en: str | None = None
    name_ar: str | None = None
    riwayah: str
    style: str
    channel: str
    coverage_surahs: int | None = None
    change_kind: ChangeKind
    ts_version: str


class AdminReleasePreviewCounts(BaseModel):
    model_config = ConfigDict(extra="allow")

    added: int
    refresh: int
    unchanged: int


class AdminReleaseLinks(BaseModel):
    model_config = ConfigDict(extra="allow")

    repo: str
    hf_dataset: str


class AdminReleasePreviewResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    computed_version: str | None
    needs_manual_version: bool
    previous_version: str | None
    change_counts: AdminReleasePreviewCounts
    added: list[AdminReleasePreviewRow]
    refreshed: list[AdminReleasePreviewRow]
    release_date: str
    license: str
    links: AdminReleaseLinks
    changelog_preview_md: str
    expected_version_at_preview: str | None


class AdminCutReleaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str | None = None
    expected_version_at_preview: str | None = None
    #: Clip-edge padding for the release tiers. ``None`` = server defaults. The
    #: SAME knobs the HF publish uses, so the two channels stay consistent.
    settings: ReleaseSettings | None = None


class AdminPublishBatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slugs: list[str] = Field(..., min_length=1)
    #: Clip-edge padding for the published rows. ``None`` = server defaults.
    settings: ReleaseSettings | None = None


class AdminLaunchResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    job_id: str
    url: str | None
