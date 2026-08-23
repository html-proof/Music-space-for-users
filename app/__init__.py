# App package
from app.main import app

# Legacy validation patterns for backward compatibility with tests
SEO_KEY_BASE_PATTERN = r"^[a-z0-9\-]+$"
ARTIST_ID_PATTERN = r"^[0-9]+$"
LANGUAGE_PATTERN = r"^[a-zA-Z]+(?:\s[a-zA-Z]+)*$"
SEARCH_QUERY_PATTERN = r"^[a-zA-Z0-9\s\-'.&]+$"

__all__ = [
    "app",
    "SEO_KEY_BASE_PATTERN",
    "ARTIST_ID_PATTERN",
    "LANGUAGE_PATTERN",
    "SEARCH_QUERY_PATTERN",
]
