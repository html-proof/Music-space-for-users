import json
import logging
import base64
from typing import Optional, Dict, Any
import firebase_admin
from firebase_admin import credentials, auth
from app.config.settings import settings

logger = logging.getLogger("firebase")
_firebase_initialized = False


def init_firebase() -> bool:
    global _firebase_initialized
    if _firebase_initialized:
        return True

    try:
        if settings.FIREBASE_CREDENTIALS_PATH:
            cred = credentials.Certificate(settings.FIREBASE_CREDENTIALS_PATH)
            firebase_admin.initialize_app(cred)
            _firebase_initialized = True
            logger.info("Firebase Admin initialized via credentials file.")
            return True

        if settings.FIREBASE_PROJECT_ID and settings.FIREBASE_CLIENT_EMAIL and settings.FIREBASE_PRIVATE_KEY:
            private_key = settings.FIREBASE_PRIVATE_KEY.replace("\\n", "\n")
            cred_dict = {
                "type": "service_account",
                "project_id": settings.FIREBASE_PROJECT_ID,
                "private_key": private_key,
                "client_email": settings.FIREBASE_CLIENT_EMAIL,
                "token_uri": "https://oauth2.googleapis.com/token",
            }
            cred = credentials.Certificate(cred_dict)
            firebase_admin.initialize_app(cred)
            _firebase_initialized = True
            logger.info("Firebase Admin initialized via environment variables.")
            return True

        if settings.FIREBASE_EMULATOR_ENABLED:
            logger.warning(
                "Firebase Admin running in DEV MOCK mode without live GCP credentials. "
                "Only 'test_token_<uid>' and 'mock_<base64>' tokens are accepted."
            )
            _firebase_initialized = False
            return True

        logger.critical(
            "Firebase credentials not supplied and emulator mode is disabled -- "
            "every authenticated request will be rejected with 401."
        )
        return False
    except Exception as e:
        logger.error(f"Error initializing Firebase Admin: {e}")
        return False


def _mock_payload(uid: str) -> Dict[str, Any]:
    return {
        "uid": uid,
        "email": f"{uid}@example.com",
        "name": f"User {uid}",
        "picture": f"https://api.dicebear.com/7.x/avataaars/svg?seed={uid}",
        "auth_time": 1700000000,
        "exp": 2000000000,
        "firebase": {"sign_in_provider": "google.com"},
    }


def _dev_mock_enabled() -> bool:
    """
    Mock tokens are only ever honoured when the emulator is explicitly enabled
    AND the environment is not a production one. Settings refuses that
    combination at startup, so this is a second, independent guard.
    """
    return settings.FIREBASE_EMULATOR_ENABLED and not settings.is_production()


def verify_firebase_token(token: str) -> Dict[str, Any]:
    """
    Verifies a Firebase ID token and returns its decoded claims.

    Raises ValueError for any token that cannot be cryptographically verified.
    In local development with FIREBASE_EMULATOR_ENABLED=True, two explicitly
    prefixed mock formats are accepted -- "test_token_<uid>" and
    "mock_<base64-json>". No other token is ever trusted without verification.
    """
    if not token or not token.strip():
        raise ValueError("Authentication token is empty.")

    if _dev_mock_enabled():
        if token.startswith("test_token_"):
            uid = token[len("test_token_"):]
            if not uid:
                raise ValueError("Mock token is missing a uid.")
            return _mock_payload(uid)

        if token.startswith("mock_"):
            try:
                payload = json.loads(base64.b64decode(token[5:]).decode())
            except Exception as e:
                raise ValueError(f"Malformed mock token: {e}")
            if not isinstance(payload, dict) or not payload.get("uid"):
                raise ValueError("Mock token payload must contain a uid.")
            return payload

    if not _firebase_initialized:
        # No verifier available. Previously this fell through to trusting the
        # raw token as a uid; that is an authentication bypass, so refuse.
        raise ValueError("Firebase Authentication is not configured.")

    try:
        return auth.verify_id_token(token)
    except Exception as e:
        logger.warning(f"Firebase token verification failed: {e}")
        raise ValueError(f"Invalid Firebase ID token: {e}")


def is_firebase_initialized() -> bool:
    """Whether live Firebase Admin (auth verification, messaging) is available.

    False in dev-mock mode -- callers that need to send FCM pushes must skip
    rather than error, since there is no live app to send through.
    """
    return _firebase_initialized


def delete_firebase_user(uid: str) -> bool:
    """Deletes a user from Firebase Auth if live Firebase is enabled."""
    if _firebase_initialized:
        try:
            auth.delete_user(uid)
            logger.info(f"Deleted Firebase user {uid}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete Firebase user {uid}: {e}")
            return False
    return True
