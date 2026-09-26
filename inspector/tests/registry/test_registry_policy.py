"""Snapshot tests pinning the issue-policy matrix verbatim.

The matrix is the source of truth listed in plan Appendix A. A flag flip
shows up here as a one-line, deliberately reviewed diff.
"""

from __future__ import annotations

import pytest

from services.validation.registry import IssueRegistry

# Locked policy matrix from plan §Appendix A.
# (canIgnore, autoSuppress, persistsIgnore, scope, card_type, severity)
EXPECTED_MATRIX = {
    "failed": {
        "can_ignore": False,
        "auto_suppress": True,
        "persists_ignore": False,
        "scope": "per_segment",
        "card_type": "generic",
        "severity": "error",
    },
    "missing_verses": {
        "can_ignore": False,
        "auto_suppress": True,
        "persists_ignore": False,
        "scope": "per_verse",
        "card_type": "missingVerses",
        "severity": "error",
    },
    "missing_words": {
        "can_ignore": False,
        "auto_suppress": False,
        "persists_ignore": False,
        "scope": "per_verse",
        "card_type": "missingWords",
        "severity": "error",
    },
    "structural_errors": {
        "can_ignore": False,
        "auto_suppress": True,
        "persists_ignore": False,
        "scope": "per_chapter",
        "card_type": "error",
        "severity": "error",
    },
    "low_confidence": {
        "can_ignore": True,
        "auto_suppress": True,
        "persists_ignore": True,
        "scope": "per_segment",
        "card_type": "generic",
        "severity": "warning",
    },
    "low_confidence_v2": {
        "can_ignore": True,
        "auto_suppress": True,
        "persists_ignore": True,
        "scope": "per_segment",
        "card_type": "generic",
        "severity": "warning",
    },
    "repetitions": {
        "can_ignore": True,
        "auto_suppress": True,
        "persists_ignore": True,
        "scope": "per_segment",
        "card_type": "generic",
        "severity": "warning",
    },
    "audio_bleeding": {
        "can_ignore": True,
        "auto_suppress": True,
        "persists_ignore": True,
        "scope": "per_segment",
        "card_type": "generic",
        "severity": "warning",
    },
    "boundary_adj": {
        "can_ignore": True,
        "auto_suppress": True,
        "persists_ignore": True,
        "scope": "per_segment",
        "card_type": "generic",
        "severity": "warning",
    },
    "cross_verse": {
        "can_ignore": False,
        "auto_suppress": False,
        "persists_ignore": False,
        "scope": "per_segment",
        "card_type": "generic",
        "severity": "warning",
    },
    "qalqala": {
        "can_ignore": False,
        "auto_suppress": False,
        "persists_ignore": False,
        "scope": "per_segment",
        "card_type": "generic",
        "severity": "info",
    },
    "muqattaat": {
        "can_ignore": False,
        "auto_suppress": False,
        "persists_ignore": False,
        "scope": "per_segment",
        "card_type": "generic",
        "severity": "info",
    },
    "basmala_amin": {
        "can_ignore": True,
        "auto_suppress": True,
        "persists_ignore": True,
        "scope": "per_segment",
        "card_type": "generic",
        "severity": "info",
    },
    "hidden_pause": {
        "can_ignore": True,
        "auto_suppress": True,
        "persists_ignore": True,
        "scope": "per_segment",
        "card_type": "generic",
        "severity": "info",
    },
    "missed_waqf": {
        "can_ignore": True,
        "auto_suppress": True,
        "persists_ignore": True,
        "scope": "per_segment",
        "card_type": "generic",
        "severity": "info",
    },
    "false_split": {
        "can_ignore": True,
        "auto_suppress": True,
        "persists_ignore": True,
        "scope": "per_segment",
        "card_type": "generic",
        "severity": "info",
    },
    "unmarked_wasl": {
        "can_ignore": True,
        "auto_suppress": True,
        "persists_ignore": True,
        "scope": "per_segment",
        "card_type": "generic",
        "severity": "info",
    },
}

EXPECTED_CATEGORIES = set(EXPECTED_MATRIX.keys())


def _registry():
    return IssueRegistry


def test_registry_pins_matrix_verbatim():
    reg = _registry()
    for cat, expected in EXPECTED_MATRIX.items():
        row = reg[cat]
        for key, value in expected.items():
            actual = getattr(row, key) if hasattr(row, key) else row[key]
            assert actual == value, (
                f"category {cat}: field {key} = {actual!r} but plan Appendix A pins it to {value!r}"
            )


def test_registry_has_all_categories():
    reg = _registry()
    assert set(reg.keys()) == EXPECTED_CATEGORIES


def test_registry_scope_field_resolves_per_category():
    reg = _registry()
    valid_scopes = {"per_segment", "per_verse", "per_chapter"}
    for cat in EXPECTED_CATEGORIES:
        row = reg[cat]
        scope = getattr(row, "scope", None) or row["scope"]
        assert scope in valid_scopes, f"{cat} scope={scope!r} not in {valid_scopes}"


def test_registry_severity_field_resolves():
    reg = _registry()
    valid = {"error", "warning", "info"}
    for cat in EXPECTED_CATEGORIES:
        row = reg[cat]
        sev = getattr(row, "severity", None) or row["severity"]
        assert sev in valid


def test_registry_card_type_dispatch():
    reg = _registry()
    expected_types = {
        "missing_verses": "missingVerses",
        "missing_words": "missingWords",
        "structural_errors": "error",
    }
    for cat in EXPECTED_CATEGORIES:
        row = reg[cat]
        ct = getattr(row, "card_type", None) or row["card_type"]
        if cat in expected_types:
            assert ct == expected_types[cat]
        else:
            assert ct == "generic"


def test_registry_accordion_order_is_complete():
    reg = _registry()
    orders = []
    for cat in EXPECTED_CATEGORIES:
        row = reg[cat]
        orders.append(getattr(row, "accordion_order", None) or row["accordion_order"])
    n = len(EXPECTED_CATEGORIES)
    assert sorted(orders) == list(range(1, n + 1)), (
        f"expected accordion_order to be a 1..{n} permutation; got sorted={sorted(orders)}"
    )
