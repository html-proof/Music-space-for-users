"""The languages onboarding offers, and the canonical spelling of each.

Gaana exposes no "list languages" endpoint, so the set of selectable languages
has to come from somewhere. It used to be `SELECT DISTINCT language FROM songs`
-- which made the onboarding screen a function of what had been ingested rather
than of what Gaana actually serves: a fresh deployment showed an empty list, and
a partly-warmed one showed two or three arbitrary languages. A background worker
existed purely to paper over that by bulk-writing songs into the database.

This list is application configuration, not a music catalog. It holds language
*names* and nothing else -- no songs, no albums, no artists, no per-language
content of any kind. It is the vocabulary the client picks from and the argument
this service passes to Gaana; every actual track for a language is fetched live.

Ordered roughly by catalog size on Gaana, so the onboarding grid leads with the
languages most users want.
"""
from typing import List, Optional

GAANA_LANGUAGES: tuple = (
    "Hindi",
    "English",
    "Punjabi",
    "Tamil",
    "Telugu",
    "Malayalam",
    "Kannada",
    "Bengali",
    "Marathi",
    "Gujarati",
    "Bhojpuri",
    "Urdu",
    "Odia",
    "Assamese",
    "Rajasthani",
    "Haryanvi",
)

_BY_LOWER = {name.lower(): name for name in GAANA_LANGUAGES}


def canonical(name: Optional[str]) -> Optional[str]:
    """The canonical spelling of `name`, or None if it is not a known language.

    Case- and whitespace-insensitive, so a client that sends "hindi" or " Tamil "
    still resolves. Returning None (rather than passing the raw string through)
    is what stops arbitrary client input from reaching Gaana as a language.
    """
    if not name:
        return None
    return _BY_LOWER.get(name.strip().lower())


def canonicalize_all(names) -> List[str]:
    """`canonical` over a list, dropping unknowns and duplicates, order kept."""
    out: List[str] = []
    for name in names or []:
        resolved = canonical(name)
        if resolved and resolved not in out:
            out.append(resolved)
    return out
