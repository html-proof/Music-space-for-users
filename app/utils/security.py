import hashlib
import secrets


def hash_token(token: str) -> str:
    """Generates a secure SHA-256 hash of a session token."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_session_token() -> str:
    """Generates a cryptographically secure random session token."""
    return secrets.token_urlsafe(32)


def constant_time_equals(supplied: str, configured: str) -> bool:
    """Length- and content-independent comparison for shared-secret admin tokens.

    Plain `==` on str short-circuits at the first differing byte, which leaks
    the secret one character at a time to anyone who can measure response
    timing. `secrets.compare_digest` is constant-time only for equal-length
    inputs, so the length check happens first (comparing lengths alone leaks
    far less than comparing content).
    """
    if len(supplied) != len(configured):
        return False
    return secrets.compare_digest(supplied.encode("utf-8"), configured.encode("utf-8"))
