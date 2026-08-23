"""Shared cache key builders.

Kept in one place so producers and invalidators cannot drift apart, and so
neither service needs to import the other.
"""


def home_recommendations_key(user_id: str) -> str:
    return f"recommendations:user:{user_id}"


def playback_state_key(user_id: str) -> str:
    return f"playback:user:{user_id}"


def player_channel(user_id: str) -> str:
    return f"user:{user_id}:player"
