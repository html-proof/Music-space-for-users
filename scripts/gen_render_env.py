"""
Build `render_upload.env` for Render Dashboard > Environment > "Add from .env".

The Firebase service-account key is read from firebase-credentials.json (which is
gitignored and was never committed). The two secrets that leaked in commit
cde23c5 -- the Supabase password and the Upstash token -- are NOT baked into this
script; supply the rotated values in the environment, or leave the placeholders
and paste them into the Render Dashboard by hand.

    DATABASE_URL=... REDIS_URL=... CORS_ORIGINS=... python scripts/gen_render_env.py

The output file is gitignored (`render_upload.env` and `*.env`). Keep it that way:
it holds a private key.
"""
import json
import os
import sys

CRED_FILE = "firebase-credentials.json"
OUT_FILE = "render_upload.env"

# Anything left at its placeholder is reported at the end so a half-filled file
# cannot be uploaded unnoticed.
PLACEHOLDERS = {
    "DATABASE_URL": "postgresql+asyncpg://postgres.PROJECT_REF:ROTATED_PASSWORD@aws-0-REGION.pooler.supabase.com:5432/postgres",
    "REDIS_URL": "rediss://default:ROTATED_UPSTASH_TOKEN@model-dog-84669.upstash.io:6379",
    "CORS_ORIGINS": "https://your-frontend.example.com",
}


def firebase_vars() -> dict:
    try:
        with open(CRED_FILE, encoding="utf-8") as fh:
            cred = json.load(fh)
    except FileNotFoundError:
        sys.exit(
            f"{CRED_FILE} not found. Download the service-account JSON from "
            "Firebase Console > Project settings > Service accounts."
        )
    except json.JSONDecodeError as e:
        sys.exit(f"{CRED_FILE} is not valid JSON: {e}")

    missing = [k for k in ("project_id", "client_email", "private_key") if not cred.get(k)]
    if missing:
        sys.exit(f"{CRED_FILE} is missing required field(s): {', '.join(missing)}")

    return {
        "FIREBASE_PROJECT_ID": cred["project_id"],
        "FIREBASE_CLIENT_EMAIL": cred["client_email"],
        # init_firebase() turns the literal \n escapes back into newlines, and a
        # single-line value is what Render's .env parser accepts.
        "FIREBASE_PRIVATE_KEY": '"{}"'.format(cred["private_key"].replace("\n", "\\n")),
    }


def main() -> None:
    fb = firebase_vars()
    resolved = {k: os.environ.get(k) or v for k, v in PLACEHOLDERS.items()}

    sections = [
        ("Application", [
            ("APP_NAME", "SpotifyCloneBackend"),
            ("APP_ENV", "production"),
            ("DEBUG", "False"),
        ]),
        ("Supabase PostgreSQL -- session-mode pooler (port 5432).", [
            ("DATABASE_URL", resolved["DATABASE_URL"]),
        ]),
        ("Upstash Redis -- cache and cross-instance playback pub/sub.", [
            ("REDIS_ENABLED", "True"),
            ("REDIS_URL", resolved["REDIS_URL"]),
        ]),
        ("Firebase Admin SDK.", [
            ("FIREBASE_PROJECT_ID", fb["FIREBASE_PROJECT_ID"]),
            ("FIREBASE_CLIENT_EMAIL", fb["FIREBASE_CLIENT_EMAIL"]),
            ("FIREBASE_PRIVATE_KEY", fb["FIREBASE_PRIVATE_KEY"]),
            # Must stay False: while on, "test_token_<uid>" bearers are accepted,
            # so anyone could impersonate any account. Settings refuses to boot
            # if this is True with a production APP_ENV.
            ("FIREBASE_EMULATOR_ENABLED", "False"),
        ]),
        ("CORS -- explicit origins. '*' disables credentialed requests.", [
            ("CORS_ORIGINS", resolved["CORS_ORIGINS"]),
        ]),
        ("Rate limiting. Render terminates TLS at its edge, so the real client "
         "IP only arrives via X-Forwarded-For.", [
            ("RATE_LIMIT_PER_MINUTE", "120"),
            ("TRUST_PROXY_HEADERS", "True"),
        ]),
        ("Recommendation tuning.", [
            ("MIN_LISTEN_SECONDS", "30"),
            ("MIN_COMPLETION_PERCENTAGE", "50"),
            ("RECOMMENDATION_CACHE_TTL_SECONDS", "3600"),
        ]),
    ]

    lines = [
        "# GaanaPy -- Render production environment",
        "# Render Dashboard > your service > Environment > Add from .env",
        "# Contains a private key. Never commit this file.",
    ]
    for heading, pairs in sections:
        lines.append("")
        lines.append(f"# {heading}")
        lines.extend(f"{k}={v}" for k, v in pairs)

    with open(OUT_FILE, "w", newline="\n", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")

    print(f"Wrote {OUT_FILE} ({sum(len(p) for _, p in sections)} variables).")

    still_placeholder = [k for k, v in PLACEHOLDERS.items() if resolved[k] == v]
    if still_placeholder:
        print("\nStill placeholder -- edit before uploading:")
        for k in still_placeholder:
            print(f"  {k}")


if __name__ == "__main__":
    main()
