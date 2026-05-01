import os
import json
from dotenv import load_dotenv

load_dotenv()

CHAT_IDS = [
    -5183093777,
    -5216183184,
    -5127067052,
    -5119430444,
]

TIMEZONE = "Africa/Lagos"

SCHEDULE = [
    (5,  0,  5, 20, "Ready"),
    (6, 10,  6, 30, "Done"),
    (7,  0,  7, 20, "Ready"),
    (8, 10,  8, 30, "Done"),
    (10,  0, 10, 20, "Ready"),
    (11, 10, 11, 30, "Done"),
    (12, 10, 12, 30, "Ready"),
    (13, 45, 14,  5, "Done"),
]

# One shared API_ID and API_HASH for all bots (from my.telegram.org — login once)
SHARED_API_ID = int(os.environ.get("API_ID", "0"))
SHARED_API_HASH = os.environ.get("API_HASH", "")


def get_all_bot_credentials() -> list[dict]:
    """
    Reads BOTS_SESSIONS — a JSON array of session strings only.
    The shared API_ID and API_HASH are set separately as API_ID and API_HASH.

    BOTS_SESSIONS format (just the session strings):
    [
      "1BVtsOK8...",
      "1BVtsOK8...",
      ...
    ]

    Or alternatively, BOTS_CONFIG with full per-bot credentials (legacy):
    [
      {"api_id": 123, "api_hash": "abc", "session": "..."},
      ...
    ]
    """
    if not SHARED_API_ID or not SHARED_API_HASH:
        # Fall back to per-bot credentials if shared not set
        raw = os.environ.get("BOTS_CONFIG")
        if not raw:
            raise RuntimeError(
                "Set API_ID and API_HASH (shared), plus BOTS_SESSIONS (list of session strings). "
                "Or set BOTS_CONFIG with full per-bot credentials."
            )
        bots = json.loads(raw)
        return bots

    # Preferred: shared API creds + just session strings
    raw = os.environ.get("BOTS_SESSIONS")
    if raw:
        sessions = json.loads(raw)
        return [
            {"api_id": SHARED_API_ID, "api_hash": SHARED_API_HASH, "session": s}
            for s in sessions
        ]

    # Also support BOTS_CONFIG with full objects
    raw = os.environ.get("BOTS_CONFIG")
    if raw:
        return json.loads(raw)

    raise RuntimeError(
        "No bot credentials found. Set API_ID, API_HASH, and BOTS_SESSIONS environment variables."
    )
