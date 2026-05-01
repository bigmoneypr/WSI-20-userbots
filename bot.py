import asyncio
import logging
import random
from datetime import datetime, timedelta

import pytz
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import (
    SessionRevokedError,
    AuthKeyUnregisteredError,
    UserDeactivatedBanError,
    FloodWaitError,
)

from config import CHAT_IDS, SCHEDULE, TIMEZONE, get_all_bot_credentials

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("wsi-userbots")

TZ = pytz.timezone(TIMEZONE)

MAX_RECONNECT_ATTEMPTS = 10
BASE_RECONNECT_DELAY = 5      # seconds
MAX_RECONNECT_DELAY = 300     # 5 minutes cap


def now_nigeria() -> datetime:
    return datetime.now(TZ)


def seconds_until(hour: int, minute: int) -> float:
    now = now_nigeria()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def random_delay_for_bot(bot_index: int, num_bots: int, window_minutes: int = 20) -> float:
    window_seconds = window_minutes * 60
    divisor = max(num_bots - 1, 1)
    base_delay = (bot_index / divisor) * window_seconds
    jitter = random.uniform(-30, 30)
    return max(0, base_delay + jitter)


def exponential_backoff(attempt: int) -> float:
    delay = BASE_RECONNECT_DELAY * (2 ** attempt)
    return min(delay, MAX_RECONNECT_DELAY) + random.uniform(0, 5)


async def ensure_connected(client: TelegramClient, bot_num: int) -> bool:
    """Check connection and reconnect if needed. Returns True if connected."""
    if client.is_connected():
        return True

    logger.warning(f"Bot {bot_num}: Disconnected. Attempting to reconnect...")

    for attempt in range(MAX_RECONNECT_ATTEMPTS):
        try:
            await client.connect()
            if await client.is_user_authorized():
                logger.info(f"Bot {bot_num}: Reconnected successfully (attempt {attempt + 1}).")
                return True
            else:
                logger.error(f"Bot {bot_num}: Reconnected but session is no longer authorized.")
                return False
        except Exception as e:
            delay = exponential_backoff(attempt)
            logger.warning(
                f"Bot {bot_num}: Reconnect attempt {attempt + 1}/{MAX_RECONNECT_ATTEMPTS} failed: {e}. "
                f"Retrying in {delay:.0f}s..."
            )
            await asyncio.sleep(delay)

    logger.error(f"Bot {bot_num}: All {MAX_RECONNECT_ATTEMPTS} reconnect attempts failed. Giving up.")
    return False


async def send_with_retry(client: TelegramClient, chat_id: int, msg: str, bot_num: int):
    """Send a message with retry on failure."""
    for attempt in range(3):
        try:
            if not await ensure_connected(client, bot_num):
                logger.error(f"Bot {bot_num}: Cannot send to {chat_id} — not connected.")
                return
            await client.send_message(chat_id, msg)
            logger.info(f"Bot {bot_num}: Sent '{msg}' to chat {chat_id}")
            return
        except FloodWaitError as e:
            logger.warning(f"Bot {bot_num}: FloodWait — sleeping {e.seconds}s before retry.")
            await asyncio.sleep(e.seconds + 5)
        except (SessionRevokedError, AuthKeyUnregisteredError, UserDeactivatedBanError) as e:
            logger.error(f"Bot {bot_num}: Account banned or session revoked: {e}. Stopping this bot.")
            raise
        except Exception as e:
            delay = exponential_backoff(attempt)
            logger.warning(f"Bot {bot_num}: Send failed (attempt {attempt + 1}/3): {e}. Retrying in {delay:.0f}s...")
            await asyncio.sleep(delay)
    logger.error(f"Bot {bot_num}: All send attempts to {chat_id} failed. Skipping.")


async def run_bot(bot_index: int, creds: dict, num_bots: int):
    bot_num = bot_index + 1
    api_id = creds.get("api_id")
    api_hash = creds.get("api_hash")
    session = creds.get("session")

    if not api_id or not api_hash or not session:
        logger.warning(f"Bot {bot_num}: Missing credentials, skipping.")
        return

    # Outer reconnect loop — if the whole bot crashes, restart it
    outer_attempt = 0
    while True:
        client = TelegramClient(
            StringSession(session),
            int(api_id),
            api_hash,
            connection_retries=5,
            retry_delay=3,
            auto_reconnect=True,
        )

        try:
            logger.info(f"Bot {bot_num}: Connecting (outer attempt {outer_attempt + 1})...")
            await client.connect()

            if not await client.is_user_authorized():
                logger.error(f"Bot {bot_num}: Not authorized. Session may be invalid. Stopping.")
                return

            logger.info(f"Bot {bot_num}: Connected and authorized.")

            while True:
                # Find the next scheduled window
                min_wait = float("inf")
                next_event = None

                for (sh, sm, eh, em, msg) in SCHEDULE:
                    wait = seconds_until(sh, sm)
                    if wait < min_wait:
                        min_wait = wait
                        next_event = (sh, sm, eh, em, msg)

                sh, sm, eh, em, msg = next_event
                bot_delay = random_delay_for_bot(bot_index, num_bots)
                total_wait = min_wait + bot_delay

                logger.info(
                    f"Bot {bot_num}: Next '{msg}' at {sh:02d}:{sm:02d} WAT. "
                    f"Sleeping {total_wait:.0f}s (stagger: {bot_delay:.0f}s)."
                )

                await asyncio.sleep(total_wait)

                # Ensure still connected before sending
                if not await ensure_connected(client, bot_num):
                    logger.warning(f"Bot {bot_num}: Could not reconnect. Will retry outer loop.")
                    break

                for chat_id in CHAT_IDS:
                    await send_with_retry(client, chat_id, msg, bot_num)

                await asyncio.sleep(90)

        except (SessionRevokedError, AuthKeyUnregisteredError, UserDeactivatedBanError) as e:
            logger.error(f"Bot {bot_num}: Fatal auth error — {e}. This bot will not restart.")
            return

        except Exception as e:
            outer_attempt += 1
            delay = exponential_backoff(outer_attempt)
            logger.error(
                f"Bot {bot_num}: Unexpected crash — {e}. "
                f"Restarting in {delay:.0f}s (restart #{outer_attempt})..."
            )
            await asyncio.sleep(delay)

        finally:
            try:
                await client.disconnect()
            except Exception:
                pass


async def main():
    all_creds = get_all_bot_credentials()
    num_bots = len(all_creds)
    logger.info(f"Starting {num_bots} WSI userbots with auto-reconnect enabled...")
    tasks = [run_bot(i, creds, num_bots) for i, creds in enumerate(all_creds)]
    await asyncio.gather(*tasks, return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(main())
