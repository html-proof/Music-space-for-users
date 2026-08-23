from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
from app.schemas.song import SongResponse, ArtistResponse


class RecommendationCategoryResponse(BaseModel):
    id: str
    title: str
    description: Optional[str] = None
    category_type: str  # made_for_you, recently_played, because_you_listened_to, daily_mix, discover_weekly, trending, new_releases, mood_mix, language_mix
    items: List[SongResponse] = Field(default_factory=list)


class HomeRecommendationsResponse(BaseModel):
    greeting: str
    categories: List[RecommendationCategoryResponse] = Field(default_factory=list)
    top_mix: Optional[List[SongResponse]] = None


class SimilarArtistsResponse(BaseModel):
    artist_id: str
    similar_artists: List[ArtistResponse] = Field(default_factory=list)
