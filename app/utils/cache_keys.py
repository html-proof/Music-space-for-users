"""Shared cache key builders.

Kept in one place so producers and invalidators cannot drift apart, and so
neither service needs to import the other.
"""


def home_recommendations_key(user_id: str) -> str:
    return f"recommendations:user:{user_id}"


def playback_state_key(user_id: str) -> str:
    return f"playback:user:{user_id}"


def radio_station_key(user_id: str) -> str:
    """
    The user's active radio station (seed plus already-served tracks).

    Deliberately cache-resident rather than a database column: it is disposable
    session state, and keeping it here avoids a schema migration. Redis rather
    than process memory so it survives the web service spinning down.
    """
    return f"radio:station:user:{user_id}"


def player_channel(user_id: str) -> str:
    return f"user:{user_id}:player"


def similar_songs_key(song_id: str, limit: int) -> str:
    """Similar-songs list for one seed.

    Keyed by song rather than by user because the result is item-to-item: it does
    not depend on who is asking, which makes it shareable across every user who
    opens the same track.
    """
    return f"ml:similar:{song_id}:{limit}"


def search_suggest_key(prefix: str, limit: int) -> str:
    """Autocomplete suggestions for a query prefix.

    Not user-scoped: personalized suggestions are merged in after the cache read
    (see `search_rank.suggest`), so caching the shared part keeps the hit rate
    high on the short prefixes that dominate autocomplete traffic.
    """
    return f"ml:suggest:{prefix.strip().lower()}:{limit}"


def catalog_languages_key() -> str:
    """The languages Gaana serves, discovered from its own chart listing.

    Shared across every user (it is catalog metadata, not personalization) and
    held longer than the other catalog keys: the set of languages Gaana curates
    charts for changes on the order of months, while the charts themselves move
    daily.
    """
    return "catalog:languages"
