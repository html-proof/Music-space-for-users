import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi

from app.config.settings import settings
from app.config.firebase import init_firebase
from app.db.init_db import init_db
from app.services.cache_service import cache_service
from app.services.catalog_service import catalog_service
from app.websocket.player_socket import ws_router
from app.websocket.pubsub import player_pubsub
from app.middleware.rate_limit import RateLimitMiddleware
from app.utils.response import api_response, api_error

from app.api import (
    auth_router,
    users_router,
    devices_router,
    player_router,
    history_router,
    library_router,
    playlists_router,
    recommendations_router,
    search_router,
    catalog_router,
    stats_router,
    ml_router,
    lyrics_router,
    downloads_router,
)

logging.basicConfig(
    level=logging.INFO if not settings.DEBUG else logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info(f"Starting {settings.APP_NAME} in {settings.APP_ENV} mode...")
    if settings.FIREBASE_EMULATOR_ENABLED:
        logger.warning(
            "FIREBASE_EMULATOR_ENABLED is on: mock 'test_token_<uid>' bearer tokens "
            "are accepted. Never enable this outside local development."
        )
    init_firebase()
    await init_db()
    await cache_service.initialize()
    await player_pubsub.start()

    # Retraining in-process is opt-in; the Render cron job is the default. The
    # handle is kept so shutdown can cancel it -- an orphaned task holding a DB
    # session would keep the pool from closing.
    trainer_task = None
    if settings.ML_ENABLED and settings.ML_AUTO_TRAIN_ENABLED:
        import asyncio
        from app.workers.ml_trainer import auto_train_loop

        trainer_task = asyncio.create_task(auto_train_loop())

    yield
    # Shutdown
    logger.info(f"Shutting down {settings.APP_NAME}...")
    if trainer_task:
        trainer_task.cancel()
    await player_pubsub.stop()
    await cache_service.close()
    await catalog_service.close()


app = FastAPI(
    title="Spotify-like Music App API",
    description="Production-ready FastAPI backend with Firebase Auth, Supabase PostgreSQL, Redis, WebSockets, and Gaana music streaming catalog.",
    version="1.0.0",
    lifespan=lifespan,
)

# Rate limiting. The limiter module existed but was never wired in, leaving the
# API with no limit at all. Added before CORS so that CORSMiddleware ends up
# outermost and a 429 still carries the headers a browser needs to read it.
app.add_middleware(RateLimitMiddleware)

# CORS. Reflecting an arbitrary origin while also allowing credentials lets any
# site read authenticated responses, so the wildcard and credentials are never
# combined: an explicit origin list is required to allow credentials.
_cors_allow_credentials = not settings.cors_allows_wildcard()
if not _cors_allow_credentials:
    logger.warning(
        "CORS_ORIGINS contains '*', so credentialed cross-origin requests are "
        "disabled. Set CORS_ORIGINS to an explicit comma-separated list of "
        "origins to allow cookies and Authorization headers from the browser."
    )
if settings.is_production() and settings.cors_allows_wildcard():
    logger.critical("Production deployment is serving CORS_ORIGINS='*'. Set an explicit origin list.")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=_cors_allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Exception Handlers
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    if isinstance(exc.detail, dict):
        code = exc.detail.get("code", "HTTP_ERROR")
        message = exc.detail.get("message", "An error occurred")
        details = exc.detail.get("details")
    else:
        code = "HTTP_ERROR"
        message = str(exc.detail)
        details = None

    return api_error(
        code=code,
        message=message,
        status_code=exc.status_code,
        details=details,
        headers=getattr(exc, "headers", None)
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.exception(f"Unhandled exception: {exc}")
    # The exception type and message are internal detail; they are logged above
    # and only echoed to the client when DEBUG is on.
    return api_error(
        code="INTERNAL_SERVER_ERROR",
        message="An unexpected error occurred.",
        status_code=500,
        details={"type": type(exc).__name__, "error": str(exc)} if settings.DEBUG else None
    )


# Health Check
@app.get("/health", tags=["System"])
async def health_check():
    return api_response({
        "status": "healthy",
        "app": settings.APP_NAME,
        "env": settings.APP_ENV,
        "redis_connected": cache_service.is_connected,
        "redis_enabled": settings.REDIS_ENABLED,
        # /health is unauthenticated, so the raw error text (a server-side
        # detail) is only echoed when DEBUG is on; the flag is always safe.
        "redis_credentials_rejected": cache_service.disabled_reason is not None,
        "redis_error": cache_service.disabled_reason if settings.DEBUG else None,
    })


@app.get("/", tags=["System"])
async def root():
    return api_response({
        "name": settings.APP_NAME,
        "version": "1.0.0",
        "docs": "/docs",
        "redoc": "/redoc"
    })


# Mount Routers
app.include_router(auth_router)
app.include_router(users_router)
app.include_router(devices_router)
app.include_router(player_router)
app.include_router(history_router)
app.include_router(library_router)
app.include_router(playlists_router)
app.include_router(recommendations_router)
app.include_router(search_router)
app.include_router(catalog_router)
app.include_router(stats_router)
app.include_router(ml_router)
app.include_router(lyrics_router)
app.include_router(downloads_router)
app.include_router(ws_router)


