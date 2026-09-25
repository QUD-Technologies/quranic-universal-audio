"""Online intake plan — title matching, enumeration shapes, the plan lifecycle,
identity proposal/check, and mint → align.

Titles are real ones from the playlists already ingested offline (Drive folders
with combined + juz' files, YouTube playlists with reciter names that contain
surah names, English file names whose index is not the surah). yt-dlp and the
Drive API are never called: ``enumerate_source`` is stubbed at the plan seam.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from qua_shared.schemas import (
    Actor,
    Channel,
    IntakePlan,
    IntakePlanUpdate,
    IntakeSource,
    PlanEntry,
    PlanEntryEdit,
    PlanIdentity,
    ReciterCatalog,
    ReciterEntry,
    Riwayah,
    Role,
    Source,
    SourceLink,
    Style,
    Vocab,
)
from qua_shared.schemas.config.pending_requests import ProposedEdits
from services import db
from services import hf_bucket as _hf_bucket
from services.admin.align_pipeline import runs
from services.admin.intake_plan import enumerate as enumerate_mod
from services.admin.intake_plan import identity, match, mint, plan
from services.db import _serde, repo_catalog, repo_requests
from services.storage import storage_paths
from services.storage.hf_bucket import get_backend

OWNER = Actor(hf_user_id="u-owner", login_at_time="owner", role=Role.OWNER)
REQUESTER = Actor(hf_user_id="u-c", login_at_time="contrib", role=Role.CONTRIBUTOR)
PLAYLIST = "https://drive.google.com/drive/folders/1RKoDakVUXItSvj3bxiNC7JXBw8BzNCSg"


# ---------------------------------------------------------------------------
# match
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("title", "chapters", "confidence"),
    [
        ("المصحف المرتل للقارئ بدر التركي | سورة الفاتحة", (1,), "exact"),
        # The reciter's name holds a surah name (محمد = 47): reading stops at كاملة.
        ("سورة الفاتحة كاملة للشيخ أ. د.محمد برهجي من مسجد رسول الله ﷺ", (1,), "exact"),
        # A file index that is not the surah: the name wins.
        ("07 سورة الأنفال - الشيخ محمد أيوب.mp3", (8,), "high"),
        ("01 سورتا الفاتحة والبقرة - الشيخ محمد أيوب.mp3", (1, 2), "high"),
        ("المصحف المرتل - سورة الفاتحة وسورة البقرة - فضيلة الشيخ", (1, 2), "high"),
        ("٠٣ | سورة آل عمران | رمضان ١٤٤٧ هـ", (3,), "exact"),
        ("034سورة سبأ برواية حفص عن عاصم", (34,), "exact"),
        ("سورة محمد صلى الله عليه وسلم | رمضان", (47,), "exact"),
        ("Surah An-Nas || Dr. Ayman Swaid || Systematic Recitation", (114,), "exact"),
        ("1- Surah Al-Fathihah|سورة الفاتحة ||Shaikh Ahmad Naseem", (1,), "exact"),
        ("Al-Muhanna Quran 22 Al-Muminun.mp3", (23,), "high"),
        ("Al-Muhanna Quran 01 Al-Fatihah Al-Baqarah.mp3", (1, 2), "high"),
        ("002 - البقرة.mp3", (2,), "high"),
        ("031.mp3", (31,), "high"),
        # A prayer name is not a surah name without a keyword.
        ("هذا يوم لا ينطقون || الشيخ محمد عبادة || روائع صلاة الفجر", (), "none"),
        ("مصحف المدينة النبوية المرتل - الشيخ محمد أيوب.jpg", (), "none"),
    ],
)
def test_match_title(title, chapters, confidence):
    got = match.match_title(title)
    assert (got.chapters, got.confidence) == (chapters, confidence)


def test_juz_entries_run_to_the_next_entry_and_are_low_confidence():
    titles = [
        "45 جزء الأحقاف - الشيخ محمد أيوب.mp3",
        "46 جزء الذاريات - الشيخ محمد أيوب.mp3",
        "47 جزء قد سمع - الشيخ محمد أيوب.mp3",
        "48 جزء تبارك - الشيخ محمد أيوب.mp3",
        "49 جزء عم - الشيخ محمد أيوب.mp3",
    ]
    got = match.match_entries(titles)
    assert [(m.chapters[0], m.chapters[-1]) for m in got] == [
        (46, 50),
        (51, 57),
        (58, 66),
        (67, 77),
        (78, 114),
    ]
    assert {m.confidence for m in got} == {"low"}


def test_a_named_claim_beats_a_file_number():
    got = match.match_entries(
        ["1419 - 01 - Introduction", "1419 - 02 - al-Faatiha", "1419 - 34 - al-Kaafirun & an-Nasr"]
    )
    assert [(m.chapters, m.confidence) for m in got] == [
        ((), "none"),
        ((1,), "high"),
        ((109, 110), "high"),
    ]


def test_duplicate_claims_are_lowered():
    got = match.match_entries(["سورة الفلق", "سورة الفلق (إعادة)", "سورة الناس"])
    assert [m.confidence for m in got] == ["low", "low", "exact"]


# ---------------------------------------------------------------------------
# enumerate
# ---------------------------------------------------------------------------


def test_links_sharing_a_url_become_one_combined_entry():
    listing = enumerate_mod.enumerate_source(
        IntakeSource(
            method="links",
            links=[
                SourceLink(chapter=2, url="cdn.x/b.mp3"),
                SourceLink(chapter=1, url="cdn.x/b.mp3"),
                SourceLink(chapter=3, url="cdn.x/c.mp3"),
            ],
        )
    )
    assert [(e.url, e.chapters) for e in listing.entries] == [
        ("https://cdn.x/b.mp3", [1, 2]),
        ("https://cdn.x/c.mp3", [3]),
    ]


class _FakeYDL:
    info: dict = {}

    def __init__(self, opts):
        self.opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url, download=False):
        return self.info


class _FakeYtDlp:
    YoutubeDL = _FakeYDL


def test_ytdlp_listing_prefers_file_urls_and_refuses_truncation(monkeypatch):
    monkeypatch.setattr(enumerate_mod, "_ytdlp", lambda: _FakeYtDlp)
    _FakeYDL.info = {
        "uploader": "uploader@example",
        "playlist_count": 2,
        "entries": [
            {
                "title": "001 - الفاتحة.mp3",
                "url": "https://archive.org/download/item/001.mp3",
                "webpage_url": "https://archive.org/details/item",
            },
            {"title": "[Deleted video]", "url": "https://www.youtube.com/watch?v=x", "id": "x"},
        ],
    }
    listing = enumerate_mod.enumerate_source(
        IntakeSource(method="playlist", playlist_url="https://archive.org/details/item")
    )
    assert listing.host == "archive"
    assert listing.entries[0].url == "https://archive.org/download/item/001.mp3"
    assert listing.entries[1].unavailable is True

    _FakeYDL.info = {**_FakeYDL.info, "playlist_count": 114}
    with pytest.raises(enumerate_mod.EnumerationError, match="reports 114 entries"):
        enumerate_mod.enumerate_source(
            IntakeSource(method="playlist", playlist_url="https://archive.org/details/item")
        )


# ---------------------------------------------------------------------------
# plan lifecycle + identity + mint
# ---------------------------------------------------------------------------


@pytest.fixture
def intake_env(tmp_path, monkeypatch):

    monkeypatch.setenv("INSPECTOR_BACKEND", "filesystem")
    monkeypatch.setenv("INSPECTOR_FILESYSTEM_ROOT", str(tmp_path))
    monkeypatch.delenv("INSPECTOR_YTDLP_COOKIES", raising=False)
    backend = _hf_bucket.FilesystemBackend(tmp_path)
    _hf_bucket.set_backend(backend)
    vocab = Vocab(
        riwayat=[Riwayah(slug="hafs_an_asim", short="hafs", name="Hafs")],
        styles=[Style(slug="murattal", short="murattal", name="Murattal")],
        sources=[Source(slug="google_drive", name="Google Drive")],
        channels=[
            Channel(slug="drive", short="drive", name="Drive", host_patterns=["drive.google.com"]),
            Channel(slug="youtube", short="yt", name="YouTube", host_patterns=["*.youtube.com"]),
        ],
    )
    with db.transaction():
        repo_catalog.load_vocab(vocab)
        repo_catalog.insert_reciter(ReciterEntry(reciter_id="mohammed_ayyub", name_en="Ayyub"))

    class _InlineThread:
        def __init__(self, target, args, **_kw):
            self._run = lambda: target(*args)

        def start(self):
            self._run()

    monkeypatch.setattr(plan.threading, "Thread", _InlineThread)
    yield backend
    _hf_bucket.reset_backend()


def _submit(kind="existing_reciter_new_combo", reciter_id="mohammed_ayyub", **edits) -> str:

    extra = {
        "reciter_id": reciter_id,
        "source": {"method": "playlist", "playlist_url": PLAYLIST},
        "attestations": {},
    }

    with db.transaction():
        return repo_requests.submit(
            slug=None,
            requester=REQUESTER,
            proposed_edits=ProposedEdits(riwayah="hafs_an_asim", style="murattal", **edits),
            comments=None,
            auto_claim=False,
            kind=kind,
            extra_payload=extra,
        )


def _listing() -> enumerate_mod.Listing:
    raw = [
        (
            "01 سورتا الفاتحة والبقرة - الشيخ محمد أيوب.mp3",
            "https://drive.google.com/file/d/AAAAAAAAAAAA/view",
        ),
        (
            "02 سورة آل عمران - الشيخ محمد أيوب.mp3",
            "https://drive.google.com/file/d/BBBBBBBBBBBB/view",
        ),
        ("مقدمة.mp3", "https://drive.google.com/file/d/CCCCCCCCCCCC/view"),
    ]
    return enumerate_mod.Listing(
        host="drive",
        source_url=PLAYLIST,
        entries=[
            enumerate_mod.RawEntry(url=u, title=t, index=i) for i, (t, u) in enumerate(raw, 1)
        ],
    )


def test_build_proposes_identity_and_flags_the_partial_delivery(intake_env, monkeypatch):
    monkeypatch.setattr(plan._enumerate, "enumerate_source", lambda _src: _listing())
    rid = _submit()
    view = plan.build(rid)
    assert view.status == "enumerating"

    view = plan.get(rid)
    assert view is not None and view.status == "ready"
    assert [e.chapters for e in view.entries] == [[1, 2], [3], []]
    assert view.identity.channel == "drive" and view.identity.source == "google_drive"
    assert view.identity.slug == "mohammed_ayyub_drive"
    assert view.coverage.chapters == [1, 2, 3] and view.coverage.combined_entries == 1
    assert view.errors == []
    assert any("111 chapter(s) missing" in w for w in view.warnings)
    assert {o.slug for o in view.channel_options} == {"drive", "youtube"}


def test_enumeration_failure_is_shown_on_the_plan(intake_env, monkeypatch):
    def boom(_src):
        raise enumerate_mod.EnumerationError("could not list the folder")

    monkeypatch.setattr(plan._enumerate, "enumerate_source", boom)
    rid = _submit()
    plan.build(rid)
    view = plan.get(rid)
    assert (
        view is not None and view.status == "failed" and view.error == "could not list the folder"
    )


def test_update_marks_edits_manual_and_rechecks(intake_env, monkeypatch):
    monkeypatch.setattr(plan._enumerate, "enumerate_source", lambda _src: _listing())
    rid = _submit()
    plan.build(rid)
    current = plan.get(rid)
    assert current is not None
    body = IntakePlanUpdate(
        entries=[PlanEntryEdit(key="e2", chapters=[2, 3])],
        identity=current.identity,
    )
    view = plan.update(rid, body)
    assert view.entries[1].confidence == "manual"
    assert view.coverage.duplicates == [2]
    assert any("more than one entry" in e for e in view.errors)


def test_plan_check_rejects_gapped_chapters_and_youtube_without_cookies(intake_env):
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    p = IntakePlan(
        status="ready",
        host="youtube",
        entries=[PlanEntry(key="e1", url="u", title="t", chapters=[1, 3], confidence="manual")],
        identity=PlanIdentity(
            slug="x_yt", reciter_id="mohammed_ayyub", channel="youtube", source="google_drive"
        ),
        created_at=now,
        updated_at=now,
    )
    view = plan.to_view(p, kind="existing_reciter_new_combo")
    assert any("consecutive" in e for e in view.errors)
    assert any("INSPECTOR_YTDLP_COOKIES" in e for e in view.errors)


def test_identity_slug_follows_the_convention_and_disambiguates():

    catalog = ReciterCatalog(
        vocab=Vocab(
            riwayat=[Riwayah(slug="warsh_an_nafi", short="warsh", name="Warsh")],
            styles=[Style(slug="mujawwad", short="mujawwad", name="Mujawwad")],
            channels=[Channel(slug="youtube", short="yt", name="YouTube")],
        )
    )
    ident = PlanIdentity(reciter_id="abc", channel="youtube", recording_year=2019)
    edits = {"riwayah": "warsh_an_nafi", "style": "mujawwad"}
    assert identity.propose_slug(ident, edits, catalog) == "abc_warsh_mujawwad_2019_yt"


def test_youtube_uploader_becomes_a_new_source():

    catalog = ReciterCatalog(
        vocab=Vocab(
            channels=[
                Channel(slug="youtube", short="yt", name="YouTube", host_patterns=["*.youtube.com"])
            ]
        )
    )
    ident = identity.propose(
        kind="new_reciter",
        payload={"proposed_edits": {"name_en": "Badr Al-Turki"}},
        host="youtube",
        source_url="https://www.youtube.com/playlist?list=PL1",
        uploader="Badr Channel",
        uploader_url="https://www.youtube.com/@badr",
        catalog=catalog,
    )
    assert (ident.reciter_id, ident.source, ident.channel) == (
        "badr_al_turki",
        "badr_channel_youtube",
        "youtube",
    )
    assert ident.new_source_name == "Badr Channel (YouTube)"
    assert ident.slug == "badr_al_turki_yt"
    assert identity.check(ident, kind="new_reciter", catalog=catalog) == []


def test_generic_hosts_propose_their_source_and_bare_hosts_match_wildcards():
    catalog = ReciterCatalog(
        vocab=Vocab(
            channels=[
                Channel(
                    slug="archive_org",
                    short="archive",
                    name="Archive",
                    host_patterns=["*.archive.org"],
                )
            ]
        )
    )
    ident = identity.propose(
        kind="existing_reciter_new_combo",
        payload={"reciter_id": "abc", "proposed_edits": {}},
        host="archive",
        source_url="https://archive.org/details/item",
        uploader="someone@example.com",
        uploader_url=None,
        catalog=catalog,
    )
    assert (ident.channel, ident.source, ident.new_source_name) == (
        "archive_org",
        "archive_org",
        "Internet Archive",
    )
    assert ident.slug == "abc_archive"


def test_mint_writes_combined_manifest_and_starts_the_run(intake_env, monkeypatch):

    monkeypatch.setattr(plan._enumerate, "enumerate_source", lambda _src: _listing())
    started: list[str] = []
    monkeypatch.setattr(runs, "start", lambda slug, actor, **kw: started.append(slug))
    rid = _submit()
    plan.build(rid)

    result = mint.mint_and_align(rid, OWNER, device="CPU", exempt=True)

    assert result.slug == "mohammed_ayyub_drive" and result.align_started is True
    assert started == ["mohammed_ayyub_drive"]
    manifest = get_backend().read_json(storage_paths.audio_manifest_path(result.slug))
    assert isinstance(manifest, dict)
    ch1, ch3 = manifest["chapters"]["1"], manifest["chapters"]["3"]
    assert ch1["source_url"] == "https://drive.google.com/file/d/AAAAAAAAAAAA/view"
    assert ch1["url"].endswith(f"/reciters/{result.slug}/audio/1.mp3")
    assert ch3 == {
        **ch3,
        "url": "https://drive.google.com/file/d/BBBBBBBBBBBB/view",
        "source_url": None,
    }
    delivery = repo_catalog.find_delivery(result.slug)
    assert delivery is not None and delivery.source_url == PLAYLIST and delivery.chapter_count == 3
    row = repo_requests.get_by_id(rid)
    assert (row["status"], row["slug"]) == ("accepted", result.slug)
    with pytest.raises(plan.PlanError):
        plan.build(rid)  # an ingested intake has no plan to rebuild


def test_mint_reports_a_refused_start_without_undoing_the_mint(intake_env, monkeypatch):

    monkeypatch.setattr(plan._enumerate, "enumerate_source", lambda _src: _listing())

    def refuse(*_a, **_kw):
        raise runs.AlignRunError("shared GPU budget spent", 429)

    monkeypatch.setattr(runs, "start", refuse)
    rid = _submit()
    plan.build(rid)
    result = mint.mint_and_align(rid, OWNER, device="GPU", exempt=False)
    assert result.align_started is False and "budget" in (result.align_error or "")
    assert repo_catalog.find_delivery(result.slug) is not None


def test_mint_refuses_a_plan_with_errors(intake_env, monkeypatch):
    monkeypatch.setattr(plan._enumerate, "enumerate_source", lambda _src: _listing())
    rid = _submit()
    plan.build(rid)
    stored = plan.stored_plan(repo_requests.get_by_id(rid))
    assert stored is not None
    stored.identity.channel = ""
    payload = _serde.json_loads(repo_requests.get_by_id(rid)["payload"])
    payload["plan"] = stored.model_dump(mode="json")

    with db.transaction():
        repo_requests.set_payload(rid, payload)
    with pytest.raises(plan.PlanError, match="channel"):
        mint.mint_and_align(rid, OWNER, device="CPU", exempt=True)
