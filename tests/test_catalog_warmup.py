import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.song import Song
from app.services.catalog_service import catalog_service
from app.workers.catalog_warmup import distinct_languages, warm_once


def _raw_track(seokey: str, language: str) -> dict:
    return {
        "seokey": seokey,
        "track_id": seokey,
        "title": f"{language} Warmup Track",
        "artists": f"{language} Warmup Artist",
        "album": "Single",
        "duration": "180",
        "images": {"urls": {}},
        "stream_urls": {"urls": {}},
        "language": language,
        "genres": "Pop",
        "is_explicit": False,
    }


@pytest.mark.asyncio
async def test_warm_once_ingests_missing_languages(db_session: AsyncSession, monkeypatch):
    calls = []

    async def fake_get_trending(language, limit):
        calls.append(language)
        return [_raw_track(f"warmup-{language.lower()}", language)]

    monkeypatch.setattr(catalog_service.gaana, "get_trending", fake_get_trending)

    attempted = await warm_once(db_session)
    assert attempted > 0
    languages = await distinct_languages(db_session)
    assert "English" in languages
    assert "Hindi" in languages
    assert "Tamil" in languages


@pytest.mark.asyncio
async def test_warm_once_skips_languages_already_covered(db_session: AsyncSession, monkeypatch):
    db_session.add(Song(external_id="already-there", title="Existing", language="English", duration=200))
    await db_session.commit()

    calls = []

    async def fake_get_trending(language, limit):
        calls.append(language)
        return [_raw_track(f"warmup-{language.lower()}", language)]

    monkeypatch.setattr(catalog_service.gaana, "get_trending", fake_get_trending)

    await warm_once(db_session)
    assert "English" not in calls
    assert "Hindi" in calls


@pytest.mark.asyncio
async def test_warm_once_is_fault_tolerant(db_session: AsyncSession, monkeypatch):
    """One language's upstream failure must not stop the rest from being
    attempted."""
    async def flaky_get_trending(language, limit):
        if language == "Hindi":
            raise RuntimeError("gaana unreachable")
        return [_raw_track(f"warmup-{language.lower()}", language)]

    monkeypatch.setattr(catalog_service.gaana, "get_trending", flaky_get_trending)

    await warm_once(db_session)
    languages = await distinct_languages(db_session)
    assert "English" in languages
    assert "Hindi" not in languages
