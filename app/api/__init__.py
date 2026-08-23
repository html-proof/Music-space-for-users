from app.api.auth import router as auth_router
from app.api.users import router as users_router
from app.api.devices import router as devices_router
from app.api.player import router as player_router
from app.api.history import router as history_router
from app.api.library import router as library_router
from app.api.playlists import router as playlists_router
from app.api.recommendations import router as recommendations_router
from app.api.search import router as search_router
from app.api.catalog import router as catalog_router
from app.api.stats import router as stats_router
from app.api.ml import router as ml_router
from app.api.lyrics import router as lyrics_router
from app.api.downloads import router as downloads_router
from app.api.onboarding import router as onboarding_router

__all__ = [
    "auth_router",
    "users_router",
    "devices_router",
    "player_router",
    "history_router",
    "library_router",
    "playlists_router",
    "recommendations_router",
    "search_router",
    "catalog_router",
    "stats_router",
    "ml_router",
    "lyrics_router",
    "downloads_router",
    "onboarding_router",
]
