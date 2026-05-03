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

from config import CHAT_IDS, SCHEDULE as FALLBACK_SCHEDULE, TIMEZONE, get_all_bot_credentials

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("wsi-userbots")

TZ = pytz.timezone(TIMEZONE)

MAX_RECONNECT_ATTEMPTS = 10
BASE_RECONNECT_DELAY = 5
MAX_RECONNECT_DELAY = 300

# Render env vars for Replit integration
REPLIT_WEBHOOK_URL = os.environ.get("REPLIT_WEBHOOK_URL", "").strip()
BOT_PROJECT_ID = os.environ.get("BOT_PROJECT_ID", "").strip()
API_HASH = os.environ.get("API_HASH", "").strip()
API_ID = os.environ.get("API_ID", "").strip()

# Delay between each group message within one bot's send cycle (seconds)
MIN_GROUP_DELAY = 5
MAX_GROUP_DELAY = 30

# How often (seconds) to re-fetch schedules from Replit (6 hours)
SCHEDULE_REFRESH_INTERVAL = 6 * 60 * 60

# Shared mutable schedules — protected by lock
_live_schedule: list = list(FALLBACK_SCHEDULE)
_boss_schedule: list = []
_boss_session_string: str = ""
_boss_phone: str = ""
_schedule_lock = asyncio.Lock()


# ─── Schedule parsing ──────────────────────────────────────────────────────────

def _parse_remote_schedule(raw: list) -> list:
    """Convert [{hour, minute, message}] from Replit API into (sh, sm, eh, em, msg) tuples."""
    result = []
    for entry in raw:
        try:
            h = int(entry.get("hour", 0))
            m = int(entry.get("minute", 0))
            msg = str(entry.get("message", "")).strip()
            if not msg:
                continue
            eh = h
            em = (m + 20) % 60
            if m + 20 >= 60:
                eh = (h + 1) % 24
            result.append((h, m, eh, em, msg))
        except Exception:
            continue
    return result


def _base_url() -> str:
    """Derive the Replit base sessions URL from REPLIT_WEBHOOK_URL."""
    return REPLIT_WEBHOOK_URL.rsplit("/bot-ping", 1)[0]


# ─── Remote schedule fetch ─────────────────────────────────────────────────────

async def fetch_remote_schedule() -> list | None:
    if not REPLIT_WEBHOOK_URL or not BOT_PROJECT_ID or not API_HASH:
        return None
    url = f"{_base_url()}/bot-schedule/{BOT_PROJECT_ID}?api_hash={API_HASH}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    parsed = _parse_remote_schedule(data.get("schedule", []))
                    return parsed if parsed else None
                logger.warning(f"Main schedule fetch returned HTTP {resp.status}")
    except Exception as e:
        logger.warning(f"Main schedule fetch failed: {e}")
    return None


async def fetch_boss_data() -> dict | None:
    """Fetch boss session string + boss schedule from Replit (PIN-exempt endpoint)."""
    if not REPLIT_WEBHOOK_URL or not BOT_PROJECT_ID or not API_HASH:
        return None
    url = f"{_base_url()}/bot-boss/{BOT_PROJECT_ID}?api_hash={API_HASH}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status == 200:
                    return await resp.json()
                logger.warning(f"Boss data fetch returned HTTP {resp.status}")
    except Exception as e:
        logger.warning(f"Boss data fetch failed: {e}")
    return None


async def load_initial_schedules():
    """On startup, load both main schedule and boss data from Replit."""
    global _live_schedule, _boss_schedule, _boss_session_string, _boss_phone

    logger.info("Fetching main schedule from Replit...")
    main = await fetch_remote_schedule()
    async with _schedule_lock:
        if main:
            _live_schedule = main
            logger.info(f"Remote main schedule loaded: {len(main)} slot(s) — {main}")
        else:
            logger.info(f"Using fallback main schedule: {len(_live_schedule)} slot(s)")

    logger.info("Fetching Professor (boss) data from Replit...")
    boss = await fetch_boss_data()
    async with _schedule_lock:
        if boss:
            raw_sched = boss.get("schedule", [])
            parsed = _parse_remote_schedule(raw_sched)
            _boss_schedule = parsed
            _boss_session_string = boss.get("boss_session_string", "")
            _boss_phone = boss.get("boss_phone", "")
            if _boss_session_string:
                logger.info(f"Professor loaded: {_boss_phone}, {len(parsed)} schedule slot(s)")
            else:
                logger.info("No Professor session configured — Professor bot will not run.")
        else:
            logger.info("Could not fetch boss data — Professor bot will not run.")


async def schedule_refresher():
    """Background task: refresh both schedules from Replit every 6 hours."""
    global _live_schedule, _boss_schedule, _boss_session_string, _boss_phone
    while True:
        await asyncio.sleep(SCHEDULE_REFRESH_INTERVAL)
        logger.info("Refreshing schedules from Replit...")

        main = await fetch_remote_schedule()
        if main:
            async with _schedule_lock:
                _live_schedule = main
            logger.info(f"Main schedule refreshed: {len(main)} slot(s)")

        boss = await fetch_boss_data()
        if boss:
            parsed = _parse_remote_schedule(boss.get("schedule", []))
            async with _schedule_lock:
                _boss_schedule = parsed
                _boss_session_string = boss.get("boss_session_string", "")
                _boss_phone = boss.get("boss_phone", "")
            logger.info(f"Professor schedule refreshed: {len(parsed)} slot(s)")


# ─── Timing helpers ────────────────────────────────────────────────────────────

def now_nigeria() -> datetime:
    return datetime.now(TZ)


def seconds_until(hour: int, minute: int) -> float:
    now = now_nigeria()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def stagger_delay(bot_index: int, num_bots: int, window_minutes: int = 20) -> float:
    """Spread bot send times evenly across a window with slight jitter."""
    window_seconds = window_minutes * 60
    divisor = max(num_bots - 1, 1)
    base_delay = (bot_index / divisor) * window_seconds
    jitter = random.uniform(-30, 30)
    return max(0, base_delay + jitter)


def exponential_backoff(attempt: int) -> float:
    delay = BASE_RECONNECT_DELAY * (2 ** attempt)
    return min(delay, MAX_RECONNECT_DELAY) + random.uniform(0, 5)


# ─── Status ping ───────────────────────────────────────────────────────────────

async def ping_status(bot_index: int, phone: str, msg_count: int, last_group: str, last_msg: str):
    """Send a status ping to Replit bot monitor (best-effort)."""
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


# ─── Connection helpers ────────────────────────────────────────────────────────

async def ensure_connected(client: TelegramClient, label: str) -> bool:
    if client.is_connected():
        return True
    logger.warning(f"{label}: Disconnected. Attempting to reconnect...")
    for attempt in range(MAX_RECONNECT_ATTEMPTS):
        try:
            await client.connect()
            if await client.is_user_authorized():
                logger.info(f"{label}: Reconnected (attempt {attempt + 1}).")
                return True
            else:
                logger.error(f"{label}: Reconnected but session no longer authorized.")
                return False
        except Exception as e:
            delay = exponential_backoff(attempt)
            logger.warning(f"{label}: Reconnect attempt {attempt + 1} failed: {e}. Retry in {delay:.0f}s...")
            await asyncio.sleep(delay)
    logger.error(f"{label}: All reconnect attempts failed.")
    return False


async def send_with_retry(client: TelegramClient, chat_id: int, msg: str, label: str):
    for attempt in range(3):
        try:
            if not await ensure_connected(client, label):
                logger.error(f"{label}: Cannot send to {chat_id} — not connected.")
                return
            await client.send_message(chat_id, msg)
            logger.info(f"{label}: Sent '{msg}' to chat {chat_id}")
            return
        except FloodWaitError as e:
            logger.warning(f"{label}: FloodWait {e.seconds}s")
            await asyncio.sleep(e.seconds + 5)
        except (SessionRevokedError, AuthKeyUnregisteredError, UserDeactivatedBanError) as e:
            logger.error(f"{label}: Account banned/revoked: {e}. Stopping.")
            raise
        except Exception as e:
            delay = exponential_backoff(attempt)
            logger.warning(f"{label}: Send failed (attempt {attempt + 1}/3): {e}. Retry in {delay:.0f}s...")
            await asyncio.sleep(delay)
    logger.error(f"{label}: All send attempts to {chat_id} failed. Skipping.")


# ─── Regular bot ──────────────────────────────────────────────────────────────

async def run_bot(bot_index: int, creds: dict, num_bots: int):
    bot_num = bot_index + 1
    label = f"Bot {bot_num}"
    api_id = creds.get("api_id")
    api_hash = creds.get("api_hash")
    session = creds.get("session")
    phone = creds.get("phone", f"bot_{bot_num}")

    if not api_id or not api_hash or not session:
        logger.warning(f"{label}: Missing credentials, skipping.")
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
            logger.info(f"{label}: Connecting (outer attempt {outer_attempt + 1})...")
            await client.connect()

            if not await client.is_user_authorized():
                logger.error(f"{label}: Not authorized. Stopping.")
                return

            logger.info(f"{label}: Connected and authorized ({phone}).")
            await ping_status(bot_index, phone, msg_count, "", "startup")

            while True:
                async with _schedule_lock:
                    current_schedule = list(_live_schedule)

                if not current_schedule:
                    logger.warning(f"{label}: Schedule is empty, sleeping 60s...")
                    await asyncio.sleep(60)
                    continue

                # Find next scheduled slot
                min_wait = float("inf")
                next_event = None
                for slot in current_schedule:
                    wait = seconds_until(slot[0], slot[1])
                    if wait < min_wait:
                        min_wait = wait
                        next_event = slot

                sh, sm, eh, em, msg = next_event
                delay = stagger_delay(bot_index, num_bots)
                total_wait = min_wait + delay

                logger.info(
                    f"{label}: Next '{msg}' at {sh:02d}:{sm:02d} WAT. "
                    f"Sleeping {total_wait:.0f}s (stagger: {delay:.0f}s)."
                )

                await asyncio.sleep(total_wait)

                if not await ensure_connected(client, label):
                    logger.warning(f"{label}: Could not reconnect. Retrying outer loop.")
                    break

                # Send to each group with a random delay between each one
                last_group = str(CHAT_IDS[-1]) if CHAT_IDS else ""
                for i, chat_id in enumerate(CHAT_IDS):
                    await send_with_retry(client, chat_id, msg, label)
                    last_group = str(chat_id)
                    # Random delay between groups (skip delay after last group)
                    if i < len(CHAT_IDS) - 1:
                        group_delay = random.uniform(MIN_GROUP_DELAY, MAX_GROUP_DELAY)
                        logger.info(f"{label}: Waiting {group_delay:.0f}s before next group...")
                        await asyncio.sleep(group_delay)

                msg_count += len(CHAT_IDS)
                await ping_status(bot_index, phone, msg_count, last_group, msg)

                # Cooldown after full send cycle before sleeping until next slot
                await asyncio.sleep(90)

        except (SessionRevokedError, AuthKeyUnregisteredError, UserDeactivatedBanError) as e:
            logger.error(f"{label}: Fatal auth error — {e}. Will not restart.")
            return

        except Exception as e:
            outer_attempt += 1
            delay = exponential_backoff(outer_attempt)
            logger.error(f"{label}: Crash — {e}. Restarting in {delay:.0f}s (#{outer_attempt})...")
            await asyncio.sleep(delay)

        finally:
            try:
                await client.disconnect()
            except Exception:
                pass


# ─── Professor (Boss) bot ──────────────────────────────────────────────────────

async def run_boss_bot():
    """The Professor — runs on its own separate schedule, sends after all regular bots."""
    global _boss_session_string, _boss_phone, _boss_schedule

    async with _schedule_lock:
        sess_str = _boss_session_string
        phone = _boss_phone

    if not sess_str:
        logger.info("Professor: No session configured. Waiting for schedule refresh...")
        # Keep checking every 30 min in case it gets configured later
        while True:
            await asyncio.sleep(30 * 60)
            async with _schedule_lock:
                sess_str = _boss_session_string
                phone = _boss_phone
            if sess_str:
                logger.info(f"Professor: Session now available ({phone}). Starting.")
                break

    if not API_ID or not API_HASH:
        logger.error("Professor: API_ID or API_HASH env var not set. Cannot start Professor bot.")
        return

    outer_attempt = 0
    msg_count = 0
    label = f"Professor ({phone})"

    while True:
        client = TelegramClient(
            StringSession(sess_str),
            int(API_ID),
            API_HASH,
            connection_retries=5,
            retry_delay=3,
            auto_reconnect=True,
        )

        try:
            logger.info(f"{label}: Connecting...")
            await client.connect()

            if not await client.is_user_authorized():
                logger.error(f"{label}: Not authorized. Session may be revoked.")
                return

            logger.info(f"{label}: Connected and authorized.")

            while True:
                async with _schedule_lock:
                    boss_sched = list(_boss_schedule)
                    # Also refresh session string in case it was updated
                    sess_str = _boss_session_string
                    phone = _boss_phone

                if not boss_sched:
                    logger.info(f"{label}: No schedule set — sleeping 30min, will check again.")
                    await asyncio.sleep(30 * 60)
                    continue

                # Find next slot
                min_wait = float("inf")
                next_event = None
                for slot in boss_sched:
                    wait = seconds_until(slot[0], slot[1])
                    if wait < min_wait:
                        min_wait = wait
                        next_event = slot

                sh, sm, eh, em, msg = next_event
                # Professor sends slightly after the stagger window ends (bots finish first)
                boss_extra_delay = random.uniform(60, 180)
                total_wait = min_wait + boss_extra_delay

                logger.info(
                    f"{label}: Next message at {sh:02d}:{sm:02d} WAT. "
                    f"Sleeping {total_wait:.0f}s (extra delay: {boss_extra_delay:.0f}s)."
                )

                await asyncio.sleep(total_wait)

                if not await ensure_connected(client, label):
                    logger.warning(f"{label}: Could not reconnect. Retrying outer loop.")
                    break

                # Professor sends to all groups with a longer, more deliberate delay between each
                last_group = str(CHAT_IDS[-1]) if CHAT_IDS else ""
                for i, chat_id in enumerate(CHAT_IDS):
                    await send_with_retry(client, chat_id, msg, label)
                    last_group = str(chat_id)
                    if i < len(CHAT_IDS) - 1:
                        # Professor waits longer — feels more authoritative
                        professor_delay = random.uniform(30, 90)
                        logger.info(f"{label}: Waiting {professor_delay:.0f}s before next group...")
                        await asyncio.sleep(professor_delay)

                msg_count += len(CHAT_IDS)
                # Use bot_index = 999 as a unique marker for the professor
                await ping_status(999, phone, msg_count, last_group, msg)

                await asyncio.sleep(90)

        except (SessionRevokedError, AuthKeyUnregisteredError, UserDeactivatedBanError) as e:
            logger.error(f"{label}: Fatal auth error — {e}. Will not restart.")
            return

        except Exception as e:
            outer_attempt += 1
            delay = exponential_backoff(outer_attempt)
            logger.error(f"{label}: Crash — {e}. Restarting in {delay:.0f}s...")
            await asyncio.sleep(delay)

        finally:
            try:
                await client.disconnect()
            except Exception:
                pass


# ─── Entry point ───────────────────────────────────────────────────────────────

async def main():
    all_creds = get_all_bot_credentials()
    num_bots = len(all_creds)
    logger.info(f"Starting {num_bots} WSI userbots...")

    # Load schedules + boss data before starting anything
    await load_initial_schedules()

    tasks = [run_bot(i, creds, num_bots) for i, creds in enumerate(all_creds)]
    tasks.append(run_boss_bot())
    tasks.append(schedule_refresher())
    await asyncio.gather(*tasks, return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(main())
