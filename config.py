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


def get_all_bot_credentials() -> list[dict]:
    """
    Reads a single environment variable BOTS_CONFIG containing a JSON array.

    Format (paste this as the value of BOTS_CONFIG on Render):
    [
      {"api_id": 12345, "api_hash": "abc123", "session": "..."},
      {"api_id": 67890, "api_hash": "def456", "session": "..."},
      ...
    ]
    """
    raw = os.environ.get("BOTS_CONFIG")
    if not raw:
        raise RuntimeError(
            "BOTS_CONFIG environment variable is not set. "
            "Set it to a JSON array of bot credentials."
        )
    try:
        bots = json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"BOTS_CONFIG is not valid JSON: {e}")

    if not isinstance(bots, list) or len(bots) == 0:
        raise RuntimeError("BOTS_CONFIG must be a non-empty JSON array.")

    return bots
