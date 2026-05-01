import os
from dotenv import load_dotenv

load_dotenv()

# Telegram API credentials - each bot needs its own API_ID and API_HASH
# Set these as environment variables: BOT1_API_ID, BOT1_API_HASH, BOT1_SESSION, etc.

CHAT_IDS = [
    -5183093777,
    -5216183184,
    -5127067052,
    -5119430444,
]

# Nigeria time zone
TIMEZONE = "Africa/Lagos"

# Schedule definition
# Each entry: (start_hour, start_minute, end_hour, end_minute, message)
# Bots will send within the start-end window, staggered across the 20 bots
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

NUM_BOTS = 20


def get_bot_credentials(bot_index: int) -> dict:
    """
    Returns credentials for a given bot (1-indexed in env vars).
    Environment variables expected:
      BOT{n}_API_ID
      BOT{n}_API_HASH
      BOT{n}_SESSION
    """
    n = bot_index + 1
    api_id = os.environ.get(f"BOT{n}_API_ID")
    api_hash = os.environ.get(f"BOT{n}_API_HASH")
    session = os.environ.get(f"BOT{n}_SESSION")
    return {
        "api_id": int(api_id) if api_id else None,
        "api_hash": api_hash,
        "session": session,
    }
