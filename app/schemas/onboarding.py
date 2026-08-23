from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field


class OnboardingLanguagesRequest(BaseModel):
    languages: List[str] = Field(..., min_length=1)


class OnboardingArtistsRequest(BaseModel):
    artist_ids: List[str] = Field(..., min_length=1)


class OnboardingStatusResponse(BaseModel):
    completed: bool
    preferred_languages: List[str] = Field(default_factory=list)
    favorite_artists: List[str] = Field(default_factory=list)
    completed_at: Optional[datetime] = None
