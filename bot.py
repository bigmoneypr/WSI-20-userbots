import asyncio
import logging
import random
from datetime import datetime, timedelta

import pytz
from telethon import TelegramClient
from telethon.sessions import StringSession

from config import CHAT_IDS, SCHEDULE, TIMEZONE, NUM_BOTS, get_bot_credentials

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("wsi-userbots")

TZ = pytz.timezone(TIMEZONE)


def now_nigeria() -> datetime:
    return datetime.now(TZ)


def seconds_until(hour: int, minute: int) -> float:
    now = now_nigeria()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def random_delay_for_bot(bot_index: int, window_minutes: int = 20) -> float:
    """
    Spread 20 bots across the window so they don't all message at once.
    Bot 0 sends near the start, bot 19 near the end.
    Adds a small random jitter of ±30 seconds.
    """
    window_seconds = window_minutes * 60
    base_delay = (bot_index / (NUM_BOTS - 1)) * window_seconds
    jitter = random.uniform(-30, 30)
    return max(0, base_delay + jitter)


async def run_bot(bot_index: int):
    creds = get_bot_credentials(bot_index)
    if not creds["api_id"] or not creds["api_hash"] or not creds["session"]:
        logger.warning(f"Bot {bot_index + 1}: Missing credentials, skipping.")
        return

    client = TelegramClient(
        StringSession(creds["session"]),
        creds["api_id"],
        creds["api_hash"],
    )

    logger.info(f"Bot {bot_index + 1}: Connecting...")
    await client.connect()

    if not await client.is_user_authorized():
        logger.error(f"Bot {bot_index + 1}: Not authorized. Check session string.")
        await client.disconnect()
        return

    logger.info(f"Bot {bot_index + 1}: Connected and authorized.")

    try:
        while True:
            now = now_nigeria()
            # Find the next scheduled window
            next_event = None
            min_wait = float("inf")

            for (sh, sm, eh, em, msg) in SCHEDULE:
                wait = seconds_until(sh, sm)
                if wait < min_wait:
                    min_wait = wait
                    next_event = (sh, sm, eh, em, msg)

            sh, sm, eh, em, msg = next_event
            bot_delay = random_delay_for_bot(bot_index)
            total_wait = min_wait + bot_delay

            logger.info(
                f"Bot {bot_index + 1}: Next event '{msg}' at {sh:02d}:{sm:02d} Nigeria time. "
                f"Waiting {total_wait:.0f}s (includes {bot_delay:.0f}s stagger delay)."
            )

            await asyncio.sleep(total_wait)

            # Send to all chat IDs
            for chat_id in CHAT_IDS:
                try:
                    await client.send_message(chat_id, msg)
                    logger.info(f"Bot {bot_index + 1}: Sent '{msg}' to chat {chat_id}")
                except Exception as e:
                    logger.error(f"Bot {bot_index + 1}: Failed to send to {chat_id}: {e}")

            # Sleep 60 seconds to avoid re-triggering the same window
            await asyncio.sleep(60)

    finally:
        await client.disconnect()
        logger.info(f"Bot {bot_index + 1}: Disconnected.")


async def main():
    logger.info(f"Starting {NUM_BOTS} WSI userbots...")
    tasks = [run_bot(i) for i in range(NUM_BOTS)]
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())
