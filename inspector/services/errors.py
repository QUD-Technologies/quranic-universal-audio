"""Shared error-envelope builder + stable machine codes.

Every JSON error the API returns is the flat, additive shape::

    {"error": <human-readable message>, "code": <stable code>,
     "context": {...}?, "details": {...}?}

``error`` is plain prose safe to surface to a user; ``code`` is a stable
machine constant the frontend maps to friendly copy (so a raw backend string
never has to reach a user); ``context`` carries display data (e.g. a humanized
state label) and ``details`` carries structured payloads (e.g. mark-ready
category counts). ``context``/``details`` are omitted when empty.

This is intentionally tiny — a pure dict builder, no exception hierarchy and
no Flask coupling. Routes/decorators wrap ``error_body`` into a response via
``api_error`` (in ``utils/decorators.py``); the app-level ``StateError``
handlers (app.py) build the same shape directly from ``error_body``.
"""

from __future__ import annotations

from typing import Any


class Codes:
    """Stable error codes. The frontend's ``friendlyError`` maps these to copy;
    keep them in lockstep with ``frontend/src/lib/errors/friendly.ts``."""

    # Auth / permission
    AUTH_REQUIRED = "AUTH_REQUIRED"
    FORBIDDEN_CAPABILITY = "FORBIDDEN_CAPABILITY"
    NOT_AUTHORIZED = "NOT_AUTHORIZED"

    # Edit-lock gates (require_edit_lock)
    NOT_EDITABLE_STATE = "NOT_EDITABLE_STATE"
    MARKED_READY_FROZEN = "MARKED_READY_FROZEN"
    VISIBILITY_BLOCKED = "VISIBILITY_BLOCKED"
    NOT_CLAIM_HOLDER = "NOT_CLAIM_HOLDER"

    # State machine
    STATE_PRECONDITION = "STATE_PRECONDITION"
    REASON_REQUIRED = "REASON_REQUIRED"
    RELEASE_BLOCKED_MARKED_READY = "RELEASE_BLOCKED_MARKED_READY"

    # Mark-ready family
    MARK_READY_CHECKLIST = "MARK_READY_CHECKLIST"
    MARK_READY_BLOCKING_COUNTS = "MARK_READY_BLOCKING_COUNTS"
    MARK_READY_PAYLOAD = "MARK_READY_PAYLOAD"
    MARK_READY_NO_SEGMENTS = "MARK_READY_NO_SEGMENTS"
    QUALITY_STATUS_PAYLOAD = "QUALITY_STATUS_PAYLOAD"

    # Admin / structural
    LAST_OWNER = "LAST_OWNER"
    UNKNOWN_RECITER = "UNKNOWN_RECITER"

    # Storage
    READ_ONLY = "READ_ONLY"

    # Editions (multi-riwayah)
    RIWAYAH_MISMATCH = "RIWAYAH_MISMATCH"
    UNSUPPORTED_RIWAYAH = "UNSUPPORTED_RIWAYAH"
    EDITIONS_UNAVAILABLE = "EDITIONS_UNAVAILABLE"
    REF_NOT_IN_EDITION = "REF_NOT_IN_EDITION"


def error_body(
    message: str,
    *,
    code: str,
    context: dict[str, Any] | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the canonical error dict. ``context``/``details`` omitted when falsy."""
    body: dict[str, Any] = {"error": message, "code": code}
    if context:
        body["context"] = context
    if details:
        body["details"] = details
    return body
