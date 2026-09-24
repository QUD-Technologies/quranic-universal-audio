"""Capability registry — the canonical, data-driven authorization vocabulary.

Inspector authorization used to be hardcoded role math (``is_maintainer`` /
``is_owner`` checks scattered across state handlers, route decorators, and
predicates). This module replaces the *vocabulary* of that model: every gated
action is a named ``Capability`` with a per-tier default that encodes the
**current** behavior exactly. An owner can deviate from these defaults at
runtime via ``permission_overrides`` (see ``services/db/repo_permissions.py``);
the resolver (``services/auth/capabilities.py``) overlays overrides on these
defaults and is the single authoritative gate.

Design rules baked in here:

- ``OWNER`` is never a key in ``default_grants`` — owners are superusers and
  always pass ``can()``. The matrix only governs ANONYMOUS / CONTRIBUTOR /
  MAINTAINER.
- ``manage_permissions`` is ``owner_only_fixed`` — it is the recovery anchor
  (only owners can ever change permissions, so the system can never be locked
  out) and is never toggleable. The resolver hard-codes it owner-only.
- ``anon_eligible`` capabilities are the only ones where ANONYMOUS is a real
  toggle. Action capabilities that need a signed-in identity (claim a row,
  edit, submit) are NOT anon-eligible — the UI renders their anonymous cell as
  N/A and the resolver refuses an anonymous grant even if a row exists.
- ``default_grants`` == today's *effective* behavior (the tighter of route +
  handler). e.g. ``reciter.undiscard`` is owner-only because the route is
  ``@require_role(OWNER)`` even though the state handler is maintainer-level.
  An empty ``permission_overrides`` table therefore reproduces the legacy
  system bit-for-bit (guarded by a parity test).

This module is backend + registry data only; it is NOT codegen'd to the FE.
The FE consumes the transformed ``admin_permissions`` response models instead.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

# ----------------------------------------------------------------------
# Tiers (matrix columns). OWNER is intentionally absent — superuser.
# ----------------------------------------------------------------------

ANONYMOUS = "anonymous"
CONTRIBUTOR = "contributor"
MAINTAINER = "maintainer"

#: The toggleable tiers, in display order. OWNER is never here.
TIERS: tuple[str, ...] = (ANONYMOUS, CONTRIBUTOR, MAINTAINER)

#: The one capability that gates the Permissions tab + its endpoints. Fixed
#: owner-only; never toggleable. Centralized so the resolver and the route
#: layer agree on the id without a string literal drifting.
MANAGE_PERMISSIONS = "manage_permissions"


class Capability(BaseModel):
    """One named, gateable action. Immutable registry record."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(..., min_length=1)
    group: str = Field(..., min_length=1)
    label: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)
    anon_eligible: bool = False
    owner_only_fixed: bool = False
    #: tier -> default allowed. Keys ⊆ TIERS. ``anonymous`` present iff
    #: ``anon_eligible``. OWNER never appears.
    default_grants: dict[str, bool] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_shape(self) -> Capability:
        keys = set(self.default_grants)
        if not keys <= set(TIERS):
            raise ValueError(
                f"capability {self.id!r}: default_grants has unknown tiers {keys - set(TIERS)!r}"
            )
        if self.anon_eligible and ANONYMOUS not in keys:
            raise ValueError(f"capability {self.id!r}: anon_eligible but no anonymous default")
        if not self.anon_eligible and ANONYMOUS in keys:
            raise ValueError(f"capability {self.id!r}: anonymous default on a non-anon cap")
        return self

    def applicable(self, tier: str) -> bool:
        """Is ``tier`` a meaningful column for this capability?

        Anonymous is only meaningful for ``anon_eligible`` caps; contributor /
        maintainer are always meaningful. (Owner is never passed here.)
        """
        if tier == ANONYMOUS:
            return self.anon_eligible
        return tier in (CONTRIBUTOR, MAINTAINER)

    def default_for(self, tier: str) -> bool:
        """Baseline grant for ``tier`` (False when absent)."""
        return bool(self.default_grants.get(tier, False))


def _c(
    id: str,
    group: str,
    label: str,
    description: str,
    *,
    contributor: bool,
    maintainer: bool,
    anon: bool | None = None,
    owner_only_fixed: bool = False,
) -> Capability:
    """Terse constructor. ``anon=None`` ⇒ not anon-eligible; a bool ⇒
    anon-eligible with that default."""
    grants: dict[str, bool] = {CONTRIBUTOR: contributor, MAINTAINER: maintainer}
    if anon is not None:
        grants[ANONYMOUS] = anon
    return Capability(
        id=id,
        group=group,
        label=label,
        description=description,
        anon_eligible=anon is not None,
        owner_only_fixed=owner_only_fixed,
        default_grants=grants,
    )


# ----------------------------------------------------------------------
# Group labels (display order is the order of first appearance below).
# ----------------------------------------------------------------------

G_VIEW = "Public read & visibility"
G_LIFECYCLE = "Reciter lifecycle"
G_REQUESTS = "Requests & intake"
G_CLAIMS = "Claims & review"
G_ROLES = "Roles & access"
G_MODERATION = "Activity & moderation"
G_ADMIN = "Admin surfaces"
G_RELEASES = "Releases & distribution"
G_IDENTITY = "Identity disclosure"
G_SUBSCRIPTIONS = "Email notifications"


# ----------------------------------------------------------------------
# The registry. default_grants == current effective behavior.
# ----------------------------------------------------------------------

CAPABILITIES: tuple[Capability, ...] = (
    # --- A. Public read & visibility (anon-eligible; today fully open). Only
    #        the dashboard-level read surfaces are gated — turning anonymous
    #        off here makes the public site private. Heavy content-read paths
    #        (timestamps / segments / peaks) are deliberately NOT gated to
    #        avoid touching tangled published-content hot paths. ---
    _c(
        "view.catalog",
        G_VIEW,
        "Browse the catalog",
        "See the reciter catalog on the dashboard (the reciter list + "
        "per-reciter detail). Turn anonymous off to make the site private.",
        anon=True,
        contributor=True,
        maintainer=True,
    ),
    _c(
        "view.public_activity",
        G_VIEW,
        "See the activity rail",
        "Read the public Recent Activity feed on the dashboard.",
        anon=True,
        contributor=True,
        maintainer=True,
    ),
    _c(
        "timestamps.report",
        G_VIEW,
        "Report a timestamps issue",
        "Use the Timestamps-tab Report flow to file a categorized issue "
        "(audio / timing / tajweed / other) on the playing verse or a "
        "specific cell. Open to everyone incl. anonymous by default; turn "
        "anonymous off to require sign-in.",
        anon=True,
        contributor=True,
        maintainer=True,
    ),
    # --- B. Reciter lifecycle ---
    _c(
        "catalog.add",
        G_LIFECYCLE,
        "Add a reciter",
        "Create a new reciter/recitation entry in the catalog.",
        contributor=False,
        maintainer=True,
    ),
    _c(
        "catalog.edit",
        G_LIFECYCLE,
        "Edit catalog metadata",
        "Change reciter identity and delivery metadata.",
        contributor=False,
        maintainer=True,
    ),
    _c(
        "reciter.publish",
        G_LIFECYCLE,
        "Publish a recitation",
        "Publish a marked-ready recitation so it goes for timestamp generation and becomes public.",
        contributor=False,
        maintainer=True,
    ),
    _c(
        "reciter.unpublish",
        G_LIFECYCLE,
        "Unpublish a recitation",
        "Pull a published recitation back to review.",
        contributor=False,
        maintainer=True,
    ),
    _c(
        "reciter.unlock_for_revision",
        G_LIFECYCLE,
        "Unlock for revision",
        "Reopen a published recitation for further edits.",
        contributor=False,
        maintainer=True,
    ),
    _c(
        "reciter.discard_content",
        G_LIFECYCLE,
        "Permanently discard recitation content",
        "Delete a recitation's bucket content and active published projections; owner only.",
        contributor=False,
        maintainer=False,
    ),
    _c(
        "reciter.discard",
        G_LIFECYCLE,
        "Discard a recitation",
        "Hide a recitation from the public dashboard (soft delete).",
        contributor=False,
        maintainer=True,
    ),
    _c(
        "reciter.undiscard",
        G_LIFECYCLE,
        "Restore a discarded recitation",
        "Bring a discarded recitation back into public view.",
        contributor=False,
        maintainer=False,
    ),
    # --- C. Requests & intake ---
    _c(
        "request.submit",
        G_REQUESTS,
        "Submit a request",
        "Ask for a new recitation to be aligned, or submit an intake "
        "(new combination / new reciter) via the wizard.",
        contributor=True,
        maintainer=True,
    ),
    _c(
        "request.review",
        G_REQUESTS,
        "Review the request queue",
        "Open the Requests compartment and read submitted requests.",
        contributor=False,
        maintainer=True,
    ),
    _c(
        "request.reject_soft",
        G_REQUESTS,
        "Send a request back",
        "Return a pending request to the requester for correction.",
        contributor=False,
        maintainer=False,
    ),
    _c(
        "request.reject_hard",
        G_REQUESTS,
        "Discard a request",
        "Reject and discard a pending request (hides the combination).",
        contributor=False,
        maintainer=False,
    ),
    _c(
        "intake.probe",
        G_REQUESTS,
        "Probe an intake source",
        "Run a reachability check against an intake's audio source.",
        contributor=False,
        maintainer=False,
    ),
    _c(
        "intake.resolve",
        G_REQUESTS,
        "Resolve an intake",
        "Return or discard a slugless intake request.",
        contributor=False,
        maintainer=False,
    ),
    _c(
        "intake.align",
        G_REQUESTS,
        "Run the align pipeline",
        "Start, retry or cancel the native alignment of a delivery awaiting "
        "alignment (acquire audio → align on the aligner Space → sidecars → "
        "assemble). Owner and maintainer by default.",
        contributor=False,
        maintainer=True,
    ),
    _c(
        "intake.align_unlimited",
        G_REQUESTS,
        "Bypass align limits",
        "Start align runs past the shared budget (2 GPU runs per 24 h, 1 CPU "
        "run at a time). Runs started with this never count toward the budget. "
        "Owner only by default.",
        contributor=False,
        maintainer=False,
    ),
    # --- D. Claims & review ---
    _c(
        "claim.acquire",
        G_CLAIMS,
        "Claim a recitation",
        "Take an available recitation for review (becomes its reviewer).",
        contributor=True,
        maintainer=True,
    ),
    _c(
        "claim.mark_ready",
        G_CLAIMS,
        "Mark a claim ready",
        "Submit your reviewed recitation as ready for an admin to publish.",
        contributor=True,
        maintainer=True,
    ),
    _c(
        "claim.mark_ready_skip_gates",
        G_CLAIMS,
        "Skip mark-ready validation gates",
        "Submit mark-ready without satisfying the form checklist or the "
        "zero-count blocking-validation gate. Owners always hold this; "
        "granting it to other tiers lets them bypass the safety rails — "
        "use sparingly.",
        contributor=False,
        maintainer=False,
    ),
    _c(
        "claim.unmark_ready",
        G_CLAIMS,
        "Unmark a claim",
        "Reopen your own marked-ready recitation to keep editing.",
        contributor=True,
        maintainer=True,
    ),
    _c(
        "segment.edit",
        G_CLAIMS,
        "Edit your claimed recitation",
        "Trim, split, merge, and re-reference segments on a recitation you hold the claim for.",
        contributor=True,
        maintainer=True,
    ),
    _c(
        "segment.edit_as_admin",
        G_CLAIMS,
        "Edit any recitation under review",
        "Edit a recitation that is under review regardless of who claimed it.",
        contributor=False,
        maintainer=True,
    ),
    _c(
        "review.send_back",
        G_CLAIMS,
        "Send a recitation back",
        "Reject a marked-ready recitation and return it for more review.",
        contributor=False,
        maintainer=True,
    ),
    _c(
        "claim.force_release",
        G_CLAIMS,
        "Force-release a claim",
        "Forcibly remove another user's claim on a recitation.",
        contributor=False,
        maintainer=False,
    ),
    _c(
        "claim.reassign",
        G_CLAIMS,
        "Reassign a claim",
        "Transfer a recitation's claim to a different reviewer.",
        contributor=False,
        maintainer=False,
    ),
    _c(
        "user.lookup",
        G_CLAIMS,
        "Look up an HF user",
        "Resolve a Hugging Face login to a user (used by the reassign flow).",
        contributor=False,
        maintainer=True,
    ),
    # --- E. Roles & access ---
    _c(
        "users.view",
        G_ROLES,
        "View the users list",
        "Open the Users compartment, per-user detail, and visitor stats.",
        contributor=False,
        maintainer=True,
    ),
    _c(
        "roles.assign_maintainer",
        G_ROLES,
        "Manage maintainers",
        "Grant, revoke, or update the maintainer role on other users.",
        contributor=False,
        maintainer=True,
    ),
    _c(
        "roles.assign_owner",
        G_ROLES,
        "Manage owners",
        "Grant, revoke, or update the owner role on other users.",
        contributor=False,
        maintainer=False,
    ),
    # --- F. Activity & moderation ---
    _c(
        "activity.delete",
        G_MODERATION,
        "Delete activity cards",
        "Tombstone (hide for everyone) an entry on the public activity rail.",
        contributor=False,
        maintainer=False,
    ),
    # --- G. Admin surfaces ---
    _c(
        "reviews.view",
        G_ADMIN,
        "View the reviews queue",
        "Open the Reviews compartment — oversight of every recitation's review state.",
        contributor=False,
        maintainer=True,
    ),
    _c(
        "reviews.generate_timestamps",
        G_ADMIN,
        "Generate timestamps",
        "Launch the MFA alignment job for a marked-ready recitation (first "
        "publish) or an already-released one (regenerate), writing per-chapter "
        "timestamps to the bucket. First publish auto-releases; regenerate keeps "
        "it released and flags its HF/GH releases stale for re-publish (also "
        "requires the Publish capability).",
        contributor=False,
        maintainer=True,
    ),
    _c(
        "timestamps.view_unreleased",
        G_ADMIN,
        "Preview unreleased timestamps",
        "View generated timestamps in the Timestamps tab for recitations "
        "not yet released (owner preview before publish).",
        contributor=False,
        maintainer=False,
    ),
    _c(
        "timestamps.view_validation",
        G_ADMIN,
        "View validation flags",
        "See the verse-level low-confidence accordion on the Timestamps tab "
        "(multi-beam alignment flags from the generate-timestamps job). "
        "Independent of unreleased preview — a holder without "
        "view_unreleased still sees flags for released recitations only.",
        contributor=False,
        maintainer=True,
    ),
    _c(
        "segments.view_boundary_review",
        G_ADMIN,
        "View boundary-review flags",
        "See the Hidden Pause, False Split and Unmarked Wasl accordions on the "
        "Segments tab (offline re-segmentation evidence, keyed by segment). "
        "Review-only: none of these categories blocks mark-ready.",
        contributor=False,
        maintainer=True,
    ),
    _c(
        "timestamps.resolve_report",
        G_ADMIN,
        "Resolve a timestamps report",
        "Mark a submitted Timestamps-tab report as resolved (with an optional "
        "note). The reporter is notified. Owner-only by default; delegate to "
        "maintainers to share triage.",
        contributor=False,
        maintainer=False,
    ),
    _c(
        "timestamps.view_stale_reports",
        G_ADMIN,
        "View stale timestamps reports",
        "Filter to Timestamps reports invalidated by a later timestamp "
        "regeneration (their targeted content changed). Owner-only by default.",
        contributor=False,
        maintainer=False,
    ),
    _c(
        "timestamps.view_nonpublic_reports",
        G_ADMIN,
        "View non-public timestamps reports",
        "See tajweed and phoneme report flags on the Timestamps tab. Timing "
        "reports are public to everyone; tajweed and phoneme reports are visible "
        "only to the reporter and to holders of this capability. Maintainers and "
        "owners by default.",
        contributor=False,
        maintainer=True,
    ),
    _c(
        "announcements.send",
        G_ADMIN,
        "Send announcements",
        "Compose and revoke global announcements shown in every user's "
        "My Notifications rail, including signed-out visitors. Owner-only by "
        "default.",
        contributor=False,
        maintainer=False,
    ),
    _c(
        "samples.manage",
        G_ADMIN,
        "Manage alignment samples",
        "Upload, edit and export alignment samples (one audio + one aligner "
        "JSON) in the Segments tab. Maintainer by default.",
        contributor=False,
        maintainer=True,
    ),
    _c(
        "notifications.receive_integrity_alerts",
        G_ADMIN,
        "Receive data-integrity alerts",
        "Get a My Notifications card when the daily sweep finds a recitation "
        "chapter whose timestamps shard has gone missing from the bucket. "
        "Owner-only by default.",
        contributor=False,
        maintainer=False,
    ),
    _c(
        "notifications.receive_review_alerts",
        G_ADMIN,
        "Receive review alerts",
        "Get a My Notifications card when a new request arrives, when a "
        "contributor marks a recitation ready with written notes, or when a "
        "segment is flagged or replied to. Owner-only by default; delegate to "
        "maintainers to share the review load.",
        contributor=False,
        maintainer=False,
    ),
    _c(
        MANAGE_PERMISSIONS,
        G_ADMIN,
        "Manage permissions",
        "See and change this Permissions tab. Owner-only and fixed — it is "
        "the recovery anchor, so it can never be delegated or turned off.",
        contributor=False,
        maintainer=False,
        owner_only_fixed=True,
    ),
    # --- H. Releases & distribution (v2) ---
    _c(
        "release.publish_hf",
        G_RELEASES,
        "Publish to HF dataset",
        "Push a recitation's data to the public HF dataset (per-recitation track).",
        contributor=False,
        maintainer=True,
    ),
    _c(
        "release.cut_gh",
        G_RELEASES,
        "Cut a GH release",
        "Cut a new global GitHub release snapshot containing every currently "
        "eligible recitation. Owner-only by default — it's the public-facing "
        "version bump.",
        contributor=False,
        maintainer=False,
    ),
    _c(
        "release.manage_automation",
        G_RELEASES,
        "Manage release automations",
        "See and change the Releases-tab Automation section — the owner-set "
        "rules that auto-generate timestamps, cut GH releases, publish the HF "
        "dataset, and regenerate stale timestamps/metadata. Owner-only by "
        "default: these rules spend HF Job compute on their own schedule.",
        contributor=False,
        maintainer=False,
    ),
    # --- I. Identity disclosure ---
    _c(
        "identity.see_actor",
        G_IDENTITY,
        "See who did what",
        "Reveal the actor's login and id on the activity rail and request "
        "queue (otherwise identity is redacted).",
        contributor=False,
        maintainer=False,
    ),
    _c(
        "segments.see_flagger_identity",
        G_IDENTITY,
        "See who flagged a segment",
        "Reveal the full login and id of the flagger and follow-up authors on "
        "a flagged segment. Others see only the author's role (e.g. "
        "'a contributor').",
        contributor=False,
        maintainer=True,
    ),
    _c(
        "timestamps.see_reporter_identity",
        G_IDENTITY,
        "See who reported a timestamps issue",
        "Reveal the login next to each Timestamps-tab report (and in the report "
        "notification). Others see the report only. Owner-only by default; "
        "delegate to maintainers to share triage.",
        contributor=False,
        maintainer=False,
    ),
    # --- J. Email notifications (self-service; anon-eligible) ---
    _c(
        "notify.email_subscriptions",
        G_SUBSCRIPTIONS,
        "Manage email notifications",
        "Open the email-notifications modal and subscribe an email address to "
        "catalog + workflow events. Subscriptions are keyed by email, so this "
        "works for signed-out visitors too; turn anonymous off to require sign-in.",
        anon=True,
        contributor=True,
        maintainer=True,
    ),
    _c(
        "notify.owner_request_emails",
        G_SUBSCRIPTIONS,
        "Email me on new requests",
        "Get a no-reply email when a new request or submission arrives — an "
        "existing-delivery edit request, or a new/existing-reciter intake "
        "submission. Owner-only by default; delegate to maintainers to share "
        "triage. Toggled from the same email-notifications modal as the "
        "public subscriptions, but only visible to holders of this capability.",
        contributor=False,
        maintainer=False,
    ),
)


CAPABILITIES_BY_ID: dict[str, Capability] = {c.id: c for c in CAPABILITIES}

#: Group labels in display order (first appearance in CAPABILITIES).
GROUP_ORDER: tuple[str, ...] = tuple(dict.fromkeys(c.group for c in CAPABILITIES))


def get(capability_id: str) -> Capability | None:
    return CAPABILITIES_BY_ID.get(capability_id)


# Defensive: ids must be unique.
assert len(CAPABILITIES_BY_ID) == len(CAPABILITIES), "duplicate capability id"
