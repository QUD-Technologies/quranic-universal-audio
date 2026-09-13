"""Render the GitHub-release body from a human-editable Markdown template.

The release preview route and ``qua_jobs/cut_release.py`` both call this
module, so the admin preview and the shipped GitHub body stay in lockstep.
The static prose, tables, and schema snippets live as real Markdown in
``docs/templates/release_body.md``; this module fills only the computed
placeholders: the dated ``release_title``, the per-cut ``recitation_changes``
membership block, and the config-driven ``release_footer`` links.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from qua_shared.config_loader import load_template

_PLACEHOLDER_RE = re.compile(r"{{\s*([a-z_]+)\s*}}")
_REQUIRED_BLOCKS = {
    "release_title",
    "recitation_changes",
    "release_footer",
}


def _neutralise(value: object) -> str:
    """Render any value as text with HTML tags neutralised."""
    s = "" if value is None else str(value)
    return s.replace("<", "&lt;").replace(">", "&gt;")


def _escape_cell(value: object) -> str:
    """Neutralise a value for a GFM table cell."""
    s = _neutralise(value).replace("\\", "\\\\").replace("|", "\\|")
    return " ".join(s.split())


def _reciter_cell(m: dict) -> str:
    en = (m.get("name_en") or "").strip()
    ar = (m.get("name_ar") or "").strip()
    label = f"{en} - {ar}" if en and ar else (en or ar or "(unnamed)")
    return _escape_cell(label)


def _coverage_cell(m: dict) -> str:
    ayahs = m.get("coverage_ayahs")
    if ayahs is not None:
        return f"{int(ayahs):,} ayahs"
    surahs = m.get("coverage_surahs")
    if surahs is not None:
        return f"{int(surahs)} surahs"
    return "-"


def _missing_cell(m: dict) -> str:
    """Concise coverage-gap cell: whole missing surahs + within-surah gaps."""
    surahs = (m.get("missing_surahs") or "").strip()
    verses = (m.get("missing_verses") or "").strip()
    parts: list[str] = []
    if surahs:
        parts.append(f"surahs {surahs}")
    if verses:
        parts.append(verses)
    return _escape_cell("; ".join(parts)) if parts else "—"


def _member_table(members: list[dict]) -> list[str]:
    rows = [
        "| Reciter | Riwayah | Style | Channel | Coverage | Missing |",
        "|---|---|---|---|---|---|",
    ]
    for m in members:
        rows.append(
            f"| {_reciter_cell(m)} | {_escape_cell(m.get('riwayah'))} "
            f"| {_escape_cell(m.get('style'))} | {_escape_cell(m.get('channel'))} "
            f"| {_coverage_cell(m)} | {_missing_cell(m)} |"
        )
    return rows


def _has_missing(members: list[dict]) -> bool:
    return any((m.get("missing_surahs") or m.get("missing_verses")) for m in members)


# Removed _MISSING_CALLOUT and its use


def _accordion(summary: str, body_lines: list[str]) -> list[str]:
    return [f"<details><summary>{summary}</summary>", "", *body_lines, "</details>"]


def _plural(n: int) -> str:
    return "" if n == 1 else "s"


def _summary_sentence(
    previous_version: str | None, n_added: int, n_refresh: int, n_carried: int
) -> str:
    if previous_version is None:
        total = n_added + n_refresh + n_carried
        return f"First release: **{total}** recitation{_plural(total)}."
    parts: list[str] = []
    if n_added:
        parts.append(f"adds {n_added}")
    if n_refresh:
        parts.append(f"refreshes {n_refresh}")
    body = ", ".join(parts) if parts else "no membership changes"
    tail = f" ({n_carried} carried)" if n_carried else ""
    return f"{body[0].upper()}{body[1:]}{tail} over {previous_version}."


def _render_template(blocks: Mapping[str, str]) -> str:
    template = load_template("release_body")
    placeholders = set(_PLACEHOLDER_RE.findall(template))
    unknown = placeholders - _REQUIRED_BLOCKS
    missing = _REQUIRED_BLOCKS - placeholders
    if unknown:
        raise ValueError(f"unknown release template placeholders: {sorted(unknown)}")
    if missing:
        raise ValueError(f"missing release template placeholders: {sorted(missing)}")

    out = template
    for key in _REQUIRED_BLOCKS:
        out = re.sub(r"{{\s*" + key + r"\s*}}", lambda _m, k=key: blocks[k].rstrip(), out)
    return out.rstrip() + "\n"


def _recitation_changes(
    *, previous_version: str | None, added: list[dict], refreshed: list[dict], carried: list[dict]
) -> str:
    n_carried = len(carried)
    out: list[str] = [
        "## Recitations",
        "",
        _summary_sentence(previous_version, len(added), len(refreshed), n_carried),
        "",
    ]
    # Removed the missing callout extension here
    if added:
        out.extend(
            _accordion(
                f"Added recitations - {len(added)}",
                _member_table(added),
            )
        )
        out.append("")
    if refreshed:
        out.extend(
            _accordion(
                f"Refreshed recitations - {len(refreshed)}",
                _member_table(refreshed),
            )
        )
        out.append("")
    if carried:
        out.extend(
            _accordion(
                f"Unchanged recitations - {n_carried}",
                _member_table(carried),
            )
        )
    return "\n".join(out).rstrip()


def _release_footer(*, license_id: str, owner: str, repo: str, hf_dataset: str) -> str:
    return f"**License:** {license_id}"


def render_changelog(
    *,
    version: str,
    previous_version: str | None,
    release_date: str,
    members: list[dict],
    static_refs_changed_keys: tuple[str, ...] | list[str] = (),
    owner: str = "",
    repo: str = "",
    hf_dataset: str = "",
    license_id: str = "CC-BY-4.0",
) -> str:
    """Return the full release body markdown."""
    added = [m for m in members if m.get("change_kind") == "added"]
    refreshed = [m for m in members if m.get("change_kind") == "refresh"]
    carried = [m for m in members if m.get("change_kind") == "unchanged"]
    return _render_template(
        {
            "release_title": f"# {release_date}",
            "recitation_changes": _recitation_changes(
                previous_version=previous_version,
                added=added,
                refreshed=refreshed,
                carried=carried,
            ),
            "release_footer": _release_footer(
                license_id=license_id,
                owner=owner,
                repo=repo,
                hf_dataset=hf_dataset,
            ),
        }
    )
