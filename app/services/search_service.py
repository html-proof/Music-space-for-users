"""Search that ranks, rather than returning whatever order upstream sent.

`catalog_service.search_songs` is the retrieval half: it asks Gaana, upserts what
comes back, and falls back to a local `ILIKE` when Gaana is unreachable. What it
does not do is *order* -- results arrive in upstream order, and the local fallback
in whatever order Postgres returned rows.

This module adds the ranking half:

1. widen the candidate pool with local lexical matches (the upstream call misses
   songs already in our catalogue whose album or artist matches the query),
2. score every candidate with `search_rank`: lexical relevance weighted 3x
   personalization, plus a small popularity term,
3. trim to `limit`.

Retrieval is never *narrowed* here. A song upstream returned is always in the
response; ranking only reorders. That keeps the endpoint's contract -- "what the
catalogue has for this query" -- unchanged while making the first result the one
the user meant.
"""
import logging
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import settings
from app.ml import search_rank
from app.ml.user_state import UserState, build_user_state, max_play_count
from app.models.history import SearchHistory
from app.models.song import Song
from app.services.cache_service import cache_service
from app.services.catalog_service import catalog_service
from app.utils.cache_keys import search_suggest_key

logger = logging.getLogger("search_service")

# Local rows pulled in as extra ranking candidates. Bounded because this is a
# LIKE scan: on a large catalogue an unbounded one would be the slowest part of
# the request.
LOCAL_CANDIDATE_LIMIT = 200
# Tokens shorter than this match too much to be worth a LIKE clause.
MIN_TOKEN_LENGTH = 2
SUGGEST_CANDIDATE_LIMIT = 120
SUGGEST_CACHE_TTL = 300


class SearchService:
    @staticmethod
    async def _local_candidates(
        db: AsyncSession, query: str, limit: int = LOCAL_CANDIDATE_LIMIT
    ) -> List[Song]:
        """Local songs whose title, artist or album matches any query token.

        Broader than `catalog_service`'s fallback (which matches the whole query
        against title and artist only), because this feeds a ranker: recall costs
        little when the scorer decides the order, and a token match is how
        "arijit tum hi ho" finds a song titled "Tum Hi Ho".
        """
        tokens = [t for t in search_rank._tokens(query) if len(t) >= MIN_TOKEN_LENGTH]
        if not tokens:
            return []

        conditions = []
        for token in tokens[:6]:
            pattern = f"%{token}%"
            conditions.extend([
                Song.title.ilike(pattern),
                Song.artist_name.ilike(pattern),
                Song.album_name.ilike(pattern),
            ])

        try:
            res = await db.execute(
                select(Song)
                .where(or_(*conditions))
                .order_by(Song.play_count.desc())
                .limit(limit)
            )
            return list(res.scalars().all())
        except Exception as e:
            logger.warning("local search candidates failed: %s", e)
            return []

    @staticmethod
    async def _state(db: AsyncSession, user_id: Optional[str]) -> Optional[UserState]:
        if not user_id:
            return None
        try:
            return await build_user_state(db, user_id)
        except Exception as e:
            # Personalization is an improvement, not a requirement: a failure here
            # degrades to purely lexical ranking rather than failing the search.
            logger.warning("user state unavailable for search ranking: %s", e)
            return None

    @classmethod
    async def search_songs(
        cls,
        db: AsyncSession,
        query: str,
        limit: int = 10,
        user_id: Optional[str] = None,
    ) -> List[Song]:
        """Ranked song results for `query`."""
        upstream = await catalog_service.search_songs(db, query, limit=limit)

        if not settings.ML_ENABLED:
            return list(upstream)

        candidates: List[Song] = list(upstream)
        seen = {s.id for s in candidates}
        for song in await cls._local_candidates(db, query):
            if song.id not in seen:
                seen.add(song.id)
                candidates.append(song)

        if not candidates:
            return []

        state = await cls._state(db, user_id)
        try:
            peak = await max_play_count(db)
            ranked = search_rank.rank(
                query,
                candidates,
                state=state,
                limit=limit,
                max_play_count=peak,
                # Nothing is filtered out: -1 is below the lowest possible score,
                # so a candidate with no lexical overlap is ranked last rather
                # than dropped. Upstream decided it was relevant; we only reorder.
                min_lexical=-1.0,
            )
        except Exception:
            logger.exception("search ranking failed; returning upstream order")
            return list(upstream)

        return [r.song for r in ranked]

    @staticmethod
    async def _recent_queries(
        db: AsyncSession, user_id: Optional[str], limit: int = 20
    ) -> List[str]:
        if not user_id:
            return []
        try:
            res = await db.execute(
                select(SearchHistory.query)
                .where(SearchHistory.user_id == user_id)
                .order_by(SearchHistory.timestamp.desc())
                .limit(limit)
            )
            return [q for q in res.scalars().all() if q]
        except Exception as e:
            logger.warning("recent queries unavailable: %s", e)
            return []

    @classmethod
    async def suggest(
        cls,
        db: AsyncSession,
        prefix: str,
        limit: int = 10,
        user_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Autocomplete suggestions, local-only.

        No upstream call: autocomplete fires on every keystroke, and a round trip
        per character would be both slow and a good way to get rate-limited by
        Gaana. Suggestions therefore come from the catalogue we already hold --
        which is exactly the catalogue the user can play.

        The shared (non-personalized) part is cached by prefix; the user's own
        recent searches are merged in afterwards, so the cache stays hot on the
        short prefixes that dominate autocomplete traffic without leaking one
        user's history into another's suggestions.
        """
        prefix = (prefix or "").strip()
        if not prefix:
            return []

        cache_key = search_suggest_key(prefix, limit)
        shared: Optional[List[Dict[str, Any]]] = None
        cached = await cache_service.get_json(cache_key)
        if isinstance(cached, list):
            shared = cached

        if shared is None:
            candidates = await cls._local_candidates(db, prefix, limit=SUGGEST_CANDIDATE_LIMIT)
            shared = search_rank.suggest(prefix, candidates, state=None, limit=limit)
            if shared:
                await cache_service.set_json(cache_key, shared, ttl_seconds=SUGGEST_CACHE_TTL)

        recent = await cls._recent_queries(db, user_id)
        if not recent:
            return shared[:limit]

        # Re-run only the cheap recent-search matching and prepend it. `suggest`
        # dedupes by display text, so a recent query that is also a song title
        # appears once.
        personal = search_rank.suggest(prefix, (), state=None, limit=limit, recent_queries=recent)
        merged: List[Dict[str, Any]] = list(personal)
        seen = {search_rank._norm(item.get("text")) for item in merged}
        for item in shared:
            key = search_rank._norm(item.get("text"))
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)
        return merged[:limit]


search_service = SearchService()
