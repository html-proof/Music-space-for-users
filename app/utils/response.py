from datetime import datetime, timezone
from typing import Any, Optional, Dict
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: Optional[Any] = None


class ApiResponse(BaseModel):
    success: bool
    data: Optional[Any] = None
    error: Optional[ErrorDetail] = None
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


def api_response(data: Any = None, status_code: int = 200) -> JSONResponse:
    """Returns a standardized successful API response."""
    payload = {
        "success": True,
        "data": data,
        "error": None,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    return JSONResponse(status_code=status_code, content=payload)


def api_error(
    code: str,
    message: str,
    status_code: int = 400,
    details: Any = None,
    headers: Optional[Dict[str, str]] = None
) -> JSONResponse:
    """Returns a standardized error API response."""
    payload = {
        "success": False,
        "data": None,
        "error": {
            "code": code,
            "message": message,
            "details": details
        },
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    return JSONResponse(status_code=status_code, content=payload, headers=headers)
