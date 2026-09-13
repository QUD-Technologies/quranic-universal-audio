"""Issue Registry: single source of truth for validation-category metadata.

Each row pins:
- ``kind``               — registry key (matches the dict key).
- ``card_type``          — UI dispatch tag for the validation card subcomponent.
- ``severity``           — ``"error" | "warning" | "info"``.
- ``accordion_order``    — 1-based render order in the validation accordion.
- ``can_ignore``         — base gate for the Ignore button on the category card.
- ``auto_suppress``      — whether an edit launched from this card writes the
                           category into ``seg.ignored_categories`` (per-segment
                           scope only; chapter / verse scopes treat this flag as
                           declarative, since revalidation is the source of
                           truth there).
- ``persists_ignore``    — whether the category survives save serialization.
                           ``False`` means the category is filtered out of
                           ``ignored_categories`` before persisting to
                           ``detailed.json``.
- ``owner_only``         — the category is invisible to everyone but an owner:
                           it is dropped from the validation accordion, from the
                           mark-ready blocking gate, and from the required
                           editing-guide set. Defaults to ``False``.
- ``scope``              — granularity of the issue: ``"per_segment"``,
                           ``"per_verse"``, or ``"per_chapter"``.
- ``display_title``      — user-facing accordion header for the category.
- ``description``        — short sentence shown in tooltips / help; may be
                           empty when the title is self-explanatory.

The registry is also exposed as a hand-mirrored TypeScript twin at
``inspector/frontend/src/tabs/segments/domain/registry.ts``. The two sides are
asserted equal by ``__tests__/registry/parity.test.ts``.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import asdict, dataclass
from typing import Any, Literal

CardType = Literal["generic", "missingWords", "missingVerses", "error"]
Severity = Literal["error", "warning", "info"]
Scope = Literal["per_segment", "per_verse", "per_chapter"]


@dataclass(frozen=True)
class IssueDefinition:
    """Frozen row describing one validation category."""

    kind: str
    card_type: CardType
    severity: Severity
    accordion_order: int
    can_ignore: bool
    auto_suppress: bool
    persists_ignore: bool
    scope: Scope
    display_title: str
    description: str
    owner_only: bool = False

    # Permit ``row["field"]`` access alongside ``row.field`` so registry rows
    # interoperate with monkeypatched plain-dict rows used in the extensibility
    # test (``test_registry_extensibility.py``) and with future JSON snapshots.
    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)


_REGISTRY: dict[str, IssueDefinition] = {
    "failed": IssueDefinition(
        kind="failed",
        card_type="generic",
        severity="error",
        accordion_order=1,
        can_ignore=False,
        auto_suppress=True,
        persists_ignore=False,
        scope="per_segment",
        display_title="Failed Alignments",
        description="",
    ),
    "missing_verses": IssueDefinition(
        kind="missing_verses",
        card_type="missingVerses",
        severity="error",
        accordion_order=2,
        can_ignore=False,
        auto_suppress=True,
        persists_ignore=False,
        scope="per_verse",
        display_title="Missing Verses",
        description="",
    ),
    "missing_words": IssueDefinition(
        kind="missing_words",
        card_type="missingWords",
        severity="error",
        accordion_order=3,
        can_ignore=False,
        auto_suppress=False,
        persists_ignore=False,
        scope="per_verse",
        display_title="Missing Words",
        description="",
    ),
    "structural_errors": IssueDefinition(
        kind="structural_errors",
        card_type="error",
        severity="error",
        accordion_order=4,
        can_ignore=False,
        auto_suppress=True,
        persists_ignore=False,
        scope="per_chapter",
        display_title="Structural Errors",
        description="",
    ),
    "low_confidence": IssueDefinition(
        kind="low_confidence",
        card_type="generic",
        severity="warning",
        accordion_order=5,
        can_ignore=True,
        auto_suppress=True,
        persists_ignore=True,
        scope="per_segment",
        display_title="Low Confidence",
        description="",
    ),
    "low_confidence_v2": IssueDefinition(
        kind="low_confidence_v2",
        card_type="generic",
        severity="warning",
        accordion_order=6,
        can_ignore=True,
        auto_suppress=True,
        persists_ignore=True,
        scope="per_segment",
        display_title="Low Confidence v2",
        description="MFA tight-beam probe disagreed with the DP alignment for these segments. Treat as a second-opinion warning.",
    ),
    "repetitions": IssueDefinition(
        kind="repetitions",
        card_type="generic",
        severity="warning",
        accordion_order=9,
        can_ignore=True,
        auto_suppress=True,
        persists_ignore=True,
        scope="per_segment",
        display_title="Detected Repetitions",
        description="Detected part of the text repeated in the audio. Split if so, ignore if a false flag.",
    ),
    "audio_bleeding": IssueDefinition(
        kind="audio_bleeding",
        card_type="generic",
        severity="warning",
        accordion_order=7,
        can_ignore=True,
        auto_suppress=True,
        persists_ignore=True,
        scope="per_segment",
        display_title="Audio Bleeding",
        description="",
    ),
    "boundary_adj": IssueDefinition(
        kind="boundary_adj",
        card_type="generic",
        severity="warning",
        accordion_order=8,
        can_ignore=True,
        auto_suppress=True,
        persists_ignore=True,
        scope="per_segment",
        display_title="May Require Boundary Adjustment",
        description="",
    ),
    "cross_verse": IssueDefinition(
        kind="cross_verse",
        card_type="generic",
        severity="warning",
        accordion_order=10,
        can_ignore=False,
        auto_suppress=False,
        persists_ignore=False,
        scope="per_segment",
        display_title="Cross-verse",
        description="Split cross verses at the verse boundary. If the reciter recited continuously across the boundary, annotate the new inter-segment boundary as wasl. Auto split is for speeding things up.",
    ),
    "qalqala": IssueDefinition(
        kind="qalqala",
        card_type="generic",
        severity="info",
        accordion_order=11,
        can_ignore=False,
        auto_suppress=False,
        persists_ignore=False,
        scope="per_segment",
        display_title="Qalqala",
        description="",
        owner_only=True,
    ),
    "muqattaat": IssueDefinition(
        kind="muqattaat",
        card_type="generic",
        severity="info",
        accordion_order=12,
        can_ignore=False,
        auto_suppress=False,
        persists_ignore=False,
        scope="per_segment",
        display_title="Muqattaʼat",
        description="",
    ),
    "basmala_amin": IssueDefinition(
        kind="basmala_amin",
        card_type="generic",
        severity="info",
        accordion_order=13,
        can_ignore=True,
        auto_suppress=True,
        persists_ignore=True,
        scope="per_segment",
        display_title="Basmala + Amin",
        description="",
    ),
    "hidden_pause": IssueDefinition(
        kind="hidden_pause",
        card_type="generic",
        severity="info",
        accordion_order=14,
        can_ignore=True,
        auto_suppress=True,
        persists_ignore=True,
        scope="per_segment",
        display_title="Hidden Pause (review)",
        description="Re-segmentation found a pause inside this segment. Auto Split places the proposed cut; ignore if there is no pause.",
    ),
    "false_split": IssueDefinition(
        kind="false_split",
        card_type="generic",
        severity="info",
        accordion_order=15,
        can_ignore=True,
        auto_suppress=True,
        persists_ignore=True,
        scope="per_segment",
        display_title="False Split (review)",
        description="Re-segmentation heard continuous speech across this segment's end. Merge with the next segment if the cut is inside speech; ignore if the pause is real.",
    ),
    "unmarked_wasl": IssueDefinition(
        kind="unmarked_wasl",
        card_type="generic",
        severity="info",
        accordion_order=16,
        can_ignore=True,
        auto_suppress=True,
        persists_ignore=True,
        scope="per_segment",
        display_title="Unmarked Wasl (review)",
        description="Every re-segmentation arm read straight through this verse-to-verse join with no stop, but the boundary is not marked wasl. Mark it wasl (or merge if it is one utterance); ignore if there is a real stop.",
    ),
}


class _RegistryView(Mapping[str, IssueDefinition]):
    """Mapping facade over ``_REGISTRY`` exposing a stable public API.

    Tests rely on ``IssueRegistry[cat]``, ``IssueRegistry.keys()``, and the
    ``_registry`` attribute (used by ``test_registry_extensibility.py`` to
    monkeypatch a synthetic row in).
    """

    def __init__(self, store: dict[str, IssueDefinition]) -> None:
        self._registry = store

    def __getitem__(self, key: str) -> IssueDefinition:
        return self._registry[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._registry)

    def __len__(self) -> int:
        return len(self._registry)

    def __contains__(self, key: object) -> bool:
        return key in self._registry


IssueRegistry: _RegistryView = _RegistryView(_REGISTRY)


ALL_CATEGORIES: tuple[str, ...] = tuple(_REGISTRY.keys())
PER_SEGMENT_CATEGORIES: tuple[str, ...] = tuple(
    k for k, v in _REGISTRY.items() if v.scope == "per_segment"
)
PER_VERSE_CATEGORIES: tuple[str, ...] = tuple(
    k for k, v in _REGISTRY.items() if v.scope == "per_verse"
)
PER_CHAPTER_CATEGORIES: tuple[str, ...] = tuple(
    k for k, v in _REGISTRY.items() if v.scope == "per_chapter"
)
CAN_IGNORE_CATEGORIES: tuple[str, ...] = tuple(k for k, v in _REGISTRY.items() if v.can_ignore)
AUTO_SUPPRESS_CATEGORIES: tuple[str, ...] = tuple(
    k for k, v in _REGISTRY.items() if v.auto_suppress
)
PERSISTS_IGNORE_CATEGORIES: tuple[str, ...] = tuple(
    k for k, v in _REGISTRY.items() if v.persists_ignore
)
OWNER_ONLY_CATEGORIES: tuple[str, ...] = tuple(k for k, v in _REGISTRY.items() if v.owner_only)


def filter_persistent_ignores(categories: list[str] | None) -> list[str]:
    """Return ``categories`` minus any whose registry entry has
    ``persists_ignore=False``.

    The legacy ``"_all"`` marker passes through unchanged — it predates the
    per-category registry and represents a session-level "ignore everything"
    flag.
    """
    if not categories:
        return []
    out: list[str] = []
    for cat in categories:
        if cat == "_all":
            out.append(cat)
            continue
        defn = _REGISTRY.get(cat)
        if defn is None or defn.persists_ignore:
            out.append(cat)
    return out


def registry_as_dict() -> dict[str, dict[str, Any]]:
    """Serialize the registry to a plain dict (for parity snapshots / fixtures)."""
    return {k: asdict(v) for k, v in _REGISTRY.items()}


__all__ = [
    "IssueDefinition",
    "IssueRegistry",
    "ALL_CATEGORIES",
    "PER_SEGMENT_CATEGORIES",
    "PER_VERSE_CATEGORIES",
    "PER_CHAPTER_CATEGORIES",
    "CAN_IGNORE_CATEGORIES",
    "AUTO_SUPPRESS_CATEGORIES",
    "PERSISTS_IGNORE_CATEGORIES",
    "OWNER_ONLY_CATEGORIES",
    "filter_persistent_ignores",
    "registry_as_dict",
]
