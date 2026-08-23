from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict


class UserProfileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    firebase_uid: str
    email: Optional[str] = None
    display_name: Optional[str] = None
    photo_url: Optional[str] = None
    country: Optional[str] = "IN"
    language: Optional[str] = "English"
    created_at: datetime
    last_login: datetime


class SyncUserRequest(BaseModel):
    email: Optional[str] = None
    display_name: Optional[str] = None
    photo_url: Optional[str] = None
    country: Optional[str] = None
    language: Optional[str] = None
