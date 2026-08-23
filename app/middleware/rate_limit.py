import time
import logging
from typing import Dict, List
from fastapi import Request, HTTPException, status
from app.config.settings import settings

logger = logging.getLogger("rate_limit")
_client_requests: Dict[str, List[float]] = {}


async def rate_limiter(request: Request):
    """Simple sliding window in-memory rate limiter per IP/client."""
    client_ip = request.client.host if request.client else "unknown"
    now = time.time()
    window = 60.0  # 1 minute

    # Clean old requests
    requests = _client_requests.get(client_ip, [])
    requests = [t for t in requests if now - t < window]

    if len(requests) >= settings.RATE_LIMIT_PER_MINUTE:
        logger.warning(f"Rate limit exceeded for IP: {client_ip}")
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"code": "RATE_LIMIT_EXCEEDED", "message": "Too many requests. Please slow down."}
        )

    requests.append(now)
    _client_requests[client_ip] = requests
