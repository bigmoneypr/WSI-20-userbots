import asyncio
import logging
import os
import random
from datetime import datetime, timedelta

import aiohttp
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
BASE_RECONNECT_DELAY = 5
MAX_RECONNECT_DELAY = 300

# Optional: set these on Render to enable bot status monitoring
# REPLIT_WEBHOOK_URL = https://<your-replit-domain>/api/sessions/bot-ping
# BOT_PROJECT_ID = <your project id>
# API_HASH = <your api hash>  (already required by config)
REPLIT_WEBHOOK_URL = os.environ.get("REPLIT_WEBHOOK_URL", "").strip()
BOT_PROJECT_ID = os.environ.get("BOT_PROJECT_ID", "").strip()
API_HASH = os.environ.get("API_HASH", "").strip()


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


async def ping_status(bot_index: int, phone: str, msg_count: int, last_group: str, last_msg: str):
    """Send a status ping to the Replit session manager (best-effort, never blocks bots)."""
    if not REPLIT_WEBHOOK_URL or not BOT_PROJECT_ID or not API_HASH:
        return
    payload = {
        "project_id": BOT_PROJECT_ID,
        "bot_index": bot_index,
        "phone": phone,
        "msg_count": msg_count,
        "last_group": last_group,
        "last_msg": last_msg,
        "api_hash": API_HASH,
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                REPLIT_WEBHOOK_URL,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    logger.debug(f"Ping returned {resp.status}")
    except Exception as e:
        logger.debug(f"Ping failed (non-critical): {e}")


async def ensure_connected(client: TelegramClient, bot_num: int) -> bool:
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
    phone = creds.get("phone", f"bot_{bot_num}")

    if not api_id or not api_hash or not session:
        logger.warning(f"Bot {bot_num}: Missing credentials, skipping.")
        return

    msg_count = 0
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

            # Send initial ping so monitor shows bot is alive
            await ping_status(bot_index, phone, msg_count, "", "startup")

            while True:
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

                if not await ensure_connected(client, bot_num):
                    logger.warning(f"Bot {bot_num}: Could not reconnect. Will retry outer loop.")
                    break

                last_group = str(CHAT_IDS[-1]) if CHAT_IDS else ""
                for chat_id in CHAT_IDS:
                    await send_with_retry(client, chat_id, msg, bot_num)
                    last_group = str(chat_id)

                msg_count += len(CHAT_IDS)

                # Ping status monitor after sending
                await ping_status(bot_index, phone, msg_count, last_group, msg)

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
