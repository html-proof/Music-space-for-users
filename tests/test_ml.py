"""
The ML subsystem's HTTP surface: status introspection and admin-gated retraining.

`training.train()` itself is exercised indirectly here -- with no listening
history seeded, it takes the fast "no labelled interactions" path, which is
enough to prove the endpoints wire auth, the training lock, and the response
shape correctly without paying for a real fit in every test.
"""
import pytest
from httpx import AsyncClient

from app.api import ml as ml_api
from app.config.settings import settings


# --------------------------------------------------------------------------
# GET /api/ml/status
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_status_requires_authentication(client: AsyncClient):
    res = await client.get("/api/ml/status")
    assert res.status_code in (401, 403)


@pytest.mark.asyncio
async def test_status_reports_feature_registry_and_prior_ranker(
    client: AsyncClient, auth_headers: dict
):
    """With nothing trained yet, status must still be a 200 describing the prior."""
    from app.ml import config

    res = await client.get("/api/ml/status", headers=auth_headers)
    assert res.status_code == 200, res.text
    data = res.json()["data"]

    assert data["feature_count"] == len(config.FEATURE_NAMES)
    assert data["feature_names"] == list(config.FEATURE_NAMES)
    for name in ("ranker", "item_sim", "popularity"):
        assert data["models"][name]["active"] is False

    assert data["ranker_in_use"]["source"] == "prior"
    assert data["ranker_in_use"]["version"] is None
    assert set(data["ranker_in_use"]["weights"]) == set(config.FEATURE_NAMES)


@pytest.mark.asyncio
async def test_status_root_alias_matches_status_path(
    client: AsyncClient, auth_headers: dict
):
    root = await client.get("/api/ml", headers=auth_headers)
    status = await client.get("/api/ml/status", headers=auth_headers)
    assert root.status_code == status.status_code == 200
    assert root.json()["data"]["feature_count"] == status.json()["data"]["feature_count"]


@pytest.mark.asyncio
async def test_status_echoes_ml_settings_and_backend_flags(
    client: AsyncClient, auth_headers: dict, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(settings, "ML_ADMIN_TOKEN", "secret-token")
    res = await client.get("/api/ml/status", headers=auth_headers)
    data = res.json()["data"]

    assert data["settings"]["ml_enabled"] == settings.ML_ENABLED
    assert data["settings"]["retrain_endpoint_configured"] is True
    assert "numpy" in data["backend"]
    assert "sklearn" in data["backend"]


# --------------------------------------------------------------------------
# POST /api/ml/retrain -- authorization
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_retrain_is_disabled_when_no_admin_token_is_configured(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(settings, "ML_ADMIN_TOKEN", None)
    res = await client.post("/api/ml/retrain")
    assert res.status_code == 503
    assert res.json()["error"]["code"] == "ml_retrain_disabled"


@pytest.mark.asyncio
async def test_retrain_rejects_missing_token_when_configured(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(settings, "ML_ADMIN_TOKEN", "secret-token")
    res = await client.post("/api/ml/retrain")
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "forbidden"


@pytest.mark.asyncio
async def test_retrain_rejects_wrong_token(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(settings, "ML_ADMIN_TOKEN", "secret-token")
    res = await client.post(
        "/api/ml/retrain", headers={"X-ML-Admin-Token": "not-it"}
    )
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_retrain_accepts_bearer_prefixed_token(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(settings, "ML_ADMIN_TOKEN", "secret-token")
    res = await client.post(
        "/api/ml/retrain",
        params={"dry_run": True},
        headers={"X-ML-Admin-Token": "Bearer secret-token"},
    )
    assert res.status_code == 200, res.text


@pytest.mark.asyncio
async def test_retrain_rejects_token_of_different_length_without_leaking_timing(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    """Regression guard for the length check in `_authorize` -- a token that is
    merely a prefix or a truncation of the real one must still be `forbidden`,
    not silently accepted via a short-circuiting comparison."""
    monkeypatch.setattr(settings, "ML_ADMIN_TOKEN", "secret-token")
    res = await client.post(
        "/api/ml/retrain", headers={"X-ML-Admin-Token": "secret"}
    )
    assert res.status_code == 403


# --------------------------------------------------------------------------
# POST /api/ml/retrain -- behavior
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_retrain_with_no_history_reports_no_labelled_interactions(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(settings, "ML_ADMIN_TOKEN", "secret-token")
    res = await client.post(
        "/api/ml/retrain", headers={"X-ML-Admin-Token": "secret-token"}
    )
    assert res.status_code == 200, res.text
    data = res.json()["data"]
    assert data["promoted"] is False
    assert "no labelled interactions" in data["reason"]
    assert data["dry_run"] is False


@pytest.mark.asyncio
async def test_retrain_dry_run_is_echoed_back(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(settings, "ML_ADMIN_TOKEN", "secret-token")
    res = await client.post(
        "/api/ml/retrain",
        params={"dry_run": True},
        headers={"X-ML-Admin-Token": "secret-token"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["data"]["dry_run"] is True


@pytest.mark.asyncio
async def test_concurrent_retrain_is_rejected_with_409(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    """Only one training pass may run at a time per process (see the module
    docstring on `_train_lock`); a second request while one holds the lock
    must be turned away rather than queued or raced."""
    monkeypatch.setattr(settings, "ML_ADMIN_TOKEN", "secret-token")
    await ml_api._train_lock.acquire()
    try:
        res = await client.post(
            "/api/ml/retrain", headers={"X-ML-Admin-Token": "secret-token"}
        )
        assert res.status_code == 409
        assert res.json()["error"]["code"] == "training_in_progress"
    finally:
        ml_api._train_lock.release()


# --------------------------------------------------------------------------
# POST /api/ml/invalidate-cache
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_invalidate_cache_is_disabled_when_no_admin_token_is_configured(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(settings, "ML_ADMIN_TOKEN", None)
    res = await client.post("/api/ml/invalidate-cache")
    assert res.status_code == 503


@pytest.mark.asyncio
async def test_invalidate_cache_rejects_wrong_token(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(settings, "ML_ADMIN_TOKEN", "secret-token")
    res = await client.post(
        "/api/ml/invalidate-cache", headers={"X-ML-Admin-Token": "wrong"}
    )
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_invalidate_cache_clears_the_registry_cache(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    from app.ml import registry

    monkeypatch.setattr(settings, "ML_ADMIN_TOKEN", "secret-token")
    registry._cache["ranker"] = (0.0, {"artifact": {}})

    res = await client.post(
        "/api/ml/invalidate-cache", headers={"X-ML-Admin-Token": "secret-token"}
    )
    assert res.status_code == 200, res.text
    assert "ranker" not in registry._cache
