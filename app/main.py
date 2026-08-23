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
from app.middleware.rate_limit import rate_limiter
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
    init_firebase()
    await init_db()
    await cache_service.initialize()
    yield
    # Shutdown
    logger.info(f"Shutting down {settings.APP_NAME}...")
    await cache_service.close()
    await catalog_service.close()


app = FastAPI(
    title="Spotify-like Music App API",
    description="Production-ready FastAPI backend with Firebase Auth, Supabase PostgreSQL, Redis, WebSockets, and Gaana music streaming catalog.",
    version="1.0.0",
    lifespan=lifespan
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
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

    return api_error(code=code, message=message, status_code=exc.status_code, details=details)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.exception(f"Unhandled exception: {exc}")
    return api_error(
        code="INTERNAL_SERVER_ERROR",
        message=f"An unexpected error occurred: {type(exc).__name__}",
        status_code=500,
        details=str(exc) if settings.DEBUG else None
    )


# Health Check
@app.get("/health", tags=["System"])
async def health_check():
    return api_response({
        "status": "healthy",
        "app": settings.APP_NAME,
        "env": settings.APP_ENV,
        "redis_connected": cache_service.redis is not None
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
app.include_router(ws_router)


