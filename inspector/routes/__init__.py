"""Inspector route blueprints.

Blueprints are organised into domain subpackages (``auth``, ``admin``,
``claims``, ``segments``, ``audio``, ``public``, ``timestamps``). The shared
``_admin_helpers`` module stays at the package root since it's referenced by
both admin/ and claims/ routes.

``audio_proxy_bp`` is registered unconditionally because ``source.ts`` routes
by_surah audio through ``/api/seg/audio-proxy/<reciter>?url=...`` and that
route is the only thing standing between the user and broken playback. The
proxy degrades to a 302 redirect to the origin CDN when no cache file exists,
so the cost in deployed mode is one extra hop and no background workers run
unless the user explicitly POSTs to ``/prepare-audio``.
"""


def register_blueprints(app):
    """Register all route blueprints on the Flask app."""
    from routes.admin.access import access_admin_bp
    from routes.admin.actions import admin_actions_bp
    from routes.admin.activity import public_activity_admin_bp
    from routes.admin.align import admin_align_bp
    from routes.admin.announcements import admin_announcements_bp
    from routes.admin.catalog import catalog_admin_bp
    from routes.admin.internal import admin_internal_bp
    from routes.admin.jobs import admin_jobs_bp
    from routes.admin.permissions import admin_permissions_bp
    from routes.admin.releases import admin_releases_bp
    from routes.admin.reviews import admin_reviews_bp
    from routes.admin.users import admin_users_bp
    from routes.announcements import announcements_bp
    from routes.audio.clip import segment_clip_bp
    from routes.audio.metadata import audio_meta_bp
    from routes.audio.proxy import audio_proxy_bp
    from routes.auth.auth import auth_bp
    from routes.auth.email_preferences import email_prefs_bp
    from routes.auth.guides import guides_bp
    from routes.auth.health import health_bp
    from routes.auth.notifications import notifications_bp
    from routes.claims.claims import claims_bp
    from routes.claims.requests import requests_bp
    from routes.public.public import public_bp
    from routes.public.static import static_bp
    from routes.samples import samples_bp
    from routes.segments.data import seg_data_bp
    from routes.segments.edit import seg_edit_bp
    from routes.segments.peaks import peaks_bp
    from routes.segments.validation import seg_val_bp
    from routes.timestamps.reports import ts_reports_bp
    from routes.timestamps.timestamps import ts_bp
    from routes.webhooks.ts_jobs import webhooks_bp

    app.register_blueprint(health_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(guides_bp)
    app.register_blueprint(notifications_bp)
    app.register_blueprint(email_prefs_bp)
    app.register_blueprint(announcements_bp)
    app.register_blueprint(claims_bp)
    app.register_blueprint(public_bp)
    app.register_blueprint(access_admin_bp)
    app.register_blueprint(admin_actions_bp)
    app.register_blueprint(catalog_admin_bp)
    app.register_blueprint(public_activity_admin_bp)
    app.register_blueprint(admin_permissions_bp)
    app.register_blueprint(admin_releases_bp)
    app.register_blueprint(admin_jobs_bp)
    app.register_blueprint(admin_align_bp)
    app.register_blueprint(admin_reviews_bp)
    app.register_blueprint(admin_users_bp)
    app.register_blueprint(admin_announcements_bp)
    app.register_blueprint(admin_internal_bp)
    app.register_blueprint(requests_bp)
    app.register_blueprint(ts_bp)
    app.register_blueprint(samples_bp)
    app.register_blueprint(ts_reports_bp)
    app.register_blueprint(seg_data_bp)
    app.register_blueprint(seg_edit_bp)
    app.register_blueprint(seg_val_bp)
    app.register_blueprint(peaks_bp)
    app.register_blueprint(audio_proxy_bp)
    app.register_blueprint(audio_meta_bp)
    app.register_blueprint(segment_clip_bp)
    app.register_blueprint(webhooks_bp)
    app.register_blueprint(static_bp)

    # Dev-only blueprint. Registered unconditionally so the route exists for
    # tests that flip ``INSPECTOR_DEV_MODE`` at runtime; the handler itself
    # returns 404 when dev mode is off. Keeps Flask's setup-finished contract
    # intact without sacrificing prod surface (one no-op route).
    from routes.auth.dev import dev_bp

    app.register_blueprint(dev_bp)
