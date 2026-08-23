import hashlib
import secrets


def hash_token(token: str) -> str:
    """Generates a secure SHA-256 hash of a session token."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_session_token() -> str:
    """Generates a cryptographically secure random session token."""
    return secrets.token_urlsafe(32)
