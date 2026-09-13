"""Domain constants for the inspector: validation categories, Quranic reference sets."""

# Classifier-emitted categories carried by ``validate_reciter_segments``'s
# ``category_counts`` rollup (per-segment flags + ``missing_words``, which is
# verse-derived but counted alongside). The canonical category metadata lives
# in ``services.validation.registry.IssueRegistry``; this tuple is kept for the
# few legacy callers that need the bare category-name set.
VALIDATION_CATEGORIES = (
    "failed",
    "low_confidence",
    "low_confidence_v2",
    "hidden_pause",
    "false_split",
    "unmarked_wasl",
    "boundary_adj",
    "cross_verse",
    "missing_words",
    "audio_bleeding",
    "repetitions",
    "muqattaat",
    "qalqala",
)

# Accordion-guide "view keys" \u2014 the canonical set a signed-in user can mark as
# read via ``POST /api/guides/viewed``. Collapsed form: ``low_confidence_v2``
# folds into ``low_confidence`` (one required reading), so the alias never
# appears here or in the ``guide_views`` table. This is the same set as the FE
# registry's ``ALL_GUIDE_KEYS`` (tabs/segments/guides/registry.ts) \u2014 keep
# the two in lockstep when adding a guide. It is a SUPERSET of the FE's
# ``REQUIRED_GUIDE_KEYS`` onboarding set: an owner-only category (registry
# ``owner_only``) keeps its guide readable and recordable but never gates a
# first edit. ``GUIDE_VIEW_KEY_ALIASES`` maps any raw category the FE might
# post to its stored key.
GUIDE_VIEW_KEYS = (
    "overview",
    "general_editing",
    "failed",
    "missing_verses",
    "missing_words",
    "low_confidence",
    "boundary_adj",
    "repetitions",
    "cross_verse",
    "qalqala",
    "muqattaat",
    "basmala_amin",
    "flagging",
)
GUIDE_VIEW_KEY_ALIASES = {
    "low_confidence_v2": "low_confidence",
}


def guide_view_key(category: str) -> str | None:
    """Collapse a raw guide category to its stored view key, or None if unknown.

    Mirrors the FE ``guideViewKey`` so the same input resolves identically on
    both sides; returns None for anything outside the known set (the route
    rejects with 400) so the table can never accrue arbitrary keys.
    """
    key = GUIDE_VIEW_KEY_ALIASES.get(category, category)
    return key if key in GUIDE_VIEW_KEYS else None


# Quranic stop/pause signs (sili, qili, small meem, jeem)
STOP_SIGNS = set("\u06d6\u06d7\u06d8\u06da")

# Huruf muqattaat opening verses (surah, ayah) tuples
MUQATTAAT_VERSES = {
    (2, 1),
    (3, 1),
    (7, 1),
    (10, 1),
    (11, 1),
    (12, 1),
    (13, 1),
    (14, 1),
    (15, 1),
    (19, 1),
    (20, 1),
    (26, 1),
    (27, 1),
    (28, 1),
    (29, 1),
    (30, 1),
    (31, 1),
    (32, 1),
    (36, 1),
    (38, 1),
    (40, 1),
    (41, 1),
    (42, 1),
    (42, 2),
    (43, 1),
    (44, 1),
    (45, 1),
    (46, 1),
    (50, 1),
    (68, 1),
}

# Qalqala letters
QALQALA_LETTERS = {"\u0642", "\u0637", "\u0628", "\u062c", "\u062f"}

# Known standalone single-word segment references (surah, ayah, word) tuples
STANDALONE_REFS = {
    (9, 13, 13),
    (16, 16, 1),
    (43, 35, 1),
    (70, 11, 1),
    (79, 27, 6),
    (37, 9, 1),
    (37, 24, 1),
    (44, 37, 9),
    (46, 35, 22),
    (44, 28, 1),
}

# Known standalone single-word segment texts (bare-skeleton form)
STANDALONE_WORDS = {
    "\u0643\u0644\u0627",  # \u0643\u0644\u0627
    "\u0630\u0644\u0643",  # \u0630\u0644\u0643
    "\u0643\u0630\u0644\u0643",  # \u0643\u0630\u0644\u0643
    "\u0633\u0628\u062d\u0646\u0647\u06e5",  # \u0633\u0628\u062d\u0646\u0647\u06e5
    "\u0628\u0644\u0649",  # \u0628\u0644\u0649
    "\u0641\u0648\u0643\u0647",  # \u0641\u0648\u0643\u0647 (\u0641\u0648\u0627\u0643\u0647)
    "\u0648\u0628\u0671\u0644\u064a\u0644",  # \u0648\u0628\u0671\u0644\u064a\u0644
    "\u0647\u0630\u0627",  # \u0647\u0630\u0627
}

# Phonemes that indicate a long vowel at word boundary
BOUNDARY_VOWELS = {"a:", "a\u02e4:", "u:", "i:"}

# Timestamp audio category directory names under data/timestamps/
# Index 0 (by_ayah_audio) is the default category.
TS_AUDIO_CATEGORIES = ("by_ayah_audio", "by_surah_audio")

# Audio metadata category directory names under data/audio/
AUDIO_META_CATEGORIES = ("by_surah", "by_ayah")

# Substring marker used to identify by-ayah audio sources
AUDIO_SOURCE_AYAH_MARKER = "by_ayah"

# Edit-history JSONL schema version — immutable record-format fact.
HISTORY_SCHEMA_VERSION = 1


def _assert_categories_match_registry() -> None:
    """Guard against drift between this module's literal and the registry.

    ``VALIDATION_CATEGORIES`` must be a subset of the registry's category set;
    every entry is also expected to be either per-segment or the verse-scoped
    ``missing_words`` (the one verse-scope category the per-chapter counter
    surfaces alongside per-segment flags).
    """
    try:
        from services.validation.registry import IssueRegistry  # noqa: WPS433
    except Exception:
        return
    registry_keys = set(IssueRegistry.keys())
    literal_keys = set(VALIDATION_CATEGORIES)
    drift = literal_keys - registry_keys
    assert not drift, f"VALIDATION_CATEGORIES has categories not in IssueRegistry: {sorted(drift)}"


_assert_categories_match_registry()
