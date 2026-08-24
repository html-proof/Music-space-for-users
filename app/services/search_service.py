"""Search that ranks, rather than returning whatever order Gaana sent.

Retrieval is Gaana and only Gaana. `catalog_service.search_songs` asks Gaana and
returns what came back; this module reorders that set and nothing more. Both
halves of the split matter:

* **Retrieval never widens.** A local `ILIKE` over the songs table used to be
  merged into the candidate pool, which meant search could return a song Gaana
  had not matched -- results were partly a view of our own ingest, and got more
  stale-looking the longer a deployment ran.
* **Retrieval never narrows.** Every song Gaana returned is in the response;
  ranking only changes the order, so the endpoint contract ("what Gaana has for
  this query") is unchanged while the first result is the one the user meant.

Scoring is `search_rank`: lexical relevance weighted 3x personalization, plus a
small popularity term. Personalization is drawn from the user state in Postgres
-- their listening history -- which is user data, not catalog.
"""
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import select
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

# How many Gaana results back an autocomplete request. Larger than the number of
# suggestions shown, because several results usually collapse to one suggestion
# (same artist, same album).
SUGGEST_CANDIDATE_LIMIT = 20
SUGGEST_CACHE_TTL = 300
# Autocomplete fires per keystroke and each miss is now a Gaana call, so a
# one-character prefix -- which matches most of the catalog and narrows nothing
# -- is answered from the user own recent searches alone.
MIN_SUGGEST_PREFIX = 2


class SearchService:
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
        """Ranked song results for `query`, all of them from Gaana."""
        upstream = await catalog_service.search_songs(db, query, limit=limit)

        if not settings.ML_ENABLED:
            return list(upstream)

        candidates: List[Song] = list(upstream)
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
                # than dropped. Gaana decided it was relevant; we only reorder.
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
        """Autocomplete suggestions, drawn from Gaana.

        These used to be a `LIKE` scan of the songs table, which made the
        dropdown a view of our own ingest: a user typing a song Gaana has but we
        had never fetched got no suggestion at all, while songs ingested months
        ago kept surfacing.

        Autocomplete fires on every keystroke, so a naive Gaana call per
        character would be slow and a good way to get rate-limited. Two things
        keep the volume down, and neither reintroduces a local catalog: the
        shared (non-personalized) part of each prefix is cached for
        SUGGEST_CACHE_TTL, so only the first user to type a given prefix pays
        for it; and prefixes shorter than MIN_SUGGEST_PREFIX skip the call
        entirely, since they narrow nothing.

        The user own recent searches are merged in afterwards rather than being
        cached, so one user history never leaks into another suggestions.
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
            candidates: List[Song] = []
            if len(prefix) >= MIN_SUGGEST_PREFIX:
                try:
                    candidates = list(
                        await catalog_service.search_songs(
                            db, prefix, limit=SUGGEST_CANDIDATE_LIMIT
                        )
                    )
                except Exception:
                    # A failed lookahead must not fail the keystroke; the user
                    # recent searches below still produce something useful.
                    logger.warning("suggest lookup for %r failed", prefix, exc_info=True)
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
