from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field


class DeviceRegisterRequest(BaseModel):
    device_id: str = Field(..., description="Unique hardware/client identifier")
    device_name: str = Field(..., description="E.g., Sebastian's iPhone, Windows PC")
    device_type: str = Field("mobile", description="mobile, tablet, desktop, web, tv")
    platform: Optional[str] = None  # iOS, Android, Windows, macOS, Web
    os_version: Optional[str] = None
    app_version: Optional[str] = None
    push_token: Optional[str] = None


class DeviceHeartbeatRequest(BaseModel):
    is_online: bool = True
    ip_address: Optional[str] = None


class DeviceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    device_id: str
    device_name: str
    device_type: str
    platform: Optional[str] = None
    os_version: Optional[str] = None
    app_version: Optional[str] = None
    ip_address: Optional[str] = None
    last_active_at: datetime
    is_online: bool
    push_token: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class UserSessionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    device_id: str
    started_at: datetime
    last_activity_at: datetime
    ended_at: Optional[datetime] = None
    is_active: bool
