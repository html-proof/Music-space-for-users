from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field


class OnboardingLanguagesRequest(BaseModel):
    languages: List[str] = Field(..., min_length=1)


class ArtistSelectionRef(BaseModel):
    """One artist the user picked on the suggestions screen.

    `name`/`image_url` matter only when `id` is a Gaana identifier rather
    than one of our own UUIDs -- the suggestions screen no longer persists
    most of what it shows (see OnboardingService.get_suggested_artists), so
    an artist selected straight off that unpersisted list has to be upserted
    here with the display data the client already has in hand.
    """
    id: str
    name: Optional[str] = None
    image_url: Optional[str] = None
    # Gaana's own slug, distinct from `id` -- without this, an artist
    # created here has no way to be looked up again via
    # GET /api/catalog/artists/info, which specifically needs the slug.
    seokey: Optional[str] = None


class OnboardingArtistsRequest(BaseModel):
    artists: List[ArtistSelectionRef] = Field(..., min_length=1)


class OnboardingStatusResponse(BaseModel):
    completed: bool
    preferred_languages: List[str] = Field(default_factory=list)
    favorite_artists: List[str] = Field(default_factory=list)
    completed_at: Optional[datetime] = None
