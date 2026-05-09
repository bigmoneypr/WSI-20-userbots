import asyncio
import logging
import os
import random
import re
from datetime import datetime, timedelta

import aiohttp
import pytz
from telethon import TelegramClient, events as TelethonEvents
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
_boss_schedule: list = []          # Library slots — message picked randomly from uploaded library
_boss_fixed_slots: list = []       # Fixed slots — same message every day, verbatim
_boss_session_string: str = ""
_boss_phone: str = ""
_professor_messages: list = []   # Random library messages fetched daily
_greeting_enabled: bool = False   # Greeting auto-response toggle
_schedule_lock = asyncio.Lock()

# ─── Greeting response engine ──────────────────────────────────────────────────

_greeting_cooldowns: dict = {}   # chat_id -> last_response_timestamp
_greeting_lock = asyncio.Lock()

GREETING_COOLDOWN_SEC = 180      # 3 min cooldown per chat before responding again
GREETING_RESPONSE_CHANCE = 0.70  # 70% chance to respond (feels more human)

GREETING_MAP = [
    # English — morning
    {"re": re.compile(r"\bgood\s*morning\b|\bgm\b", re.IGNORECASE),
     "responses": ["Good morning! 🌅", "Morning! ☀️ 😊", "Good morning everyone! 🌅", "Rise and shine! Good morning! ☀️"]},
    # English — afternoon
    {"re": re.compile(r"\bgood\s*afternoon\b|\bga\b", re.IGNORECASE),
     "responses": ["Good afternoon! 😊", "Good afternoon! Hope your day is going great! 🌤", "Good afternoon! 🌤"]},
    # English — evening
    {"re": re.compile(r"\bgood\s*evening\b|\bge\b", re.IGNORECASE),
     "responses": ["Good evening! 🌆", "Good evening everyone! 😊", "Evening! 🌆 😊"]},
    # English — night
    {"re": re.compile(r"\bgood\s*night\b|\bgn\b", re.IGNORECASE),
     "responses": ["Good night! 🌙", "Good night! Sweet dreams! 💤", "Night night! 🌙 💤"]},
    # English — hello/hi
    {"re": re.compile(r"\bhello\b|\bhi\b|\bhey\b|\bhow\s+are\s+you\b|\bhow\s+far\b|\bwhat\s+up\b|\bwassup\b", re.IGNORECASE),
     "responses": ["Hello! 😊", "Hi there! 👋", "Hey! 😊", "Hello! How are you? 😊", "Hi! 👋 😊"]},
    # Yoruba — morning
    {"re": re.compile(r"\be?\s*kaaro\b", re.IGNORECASE),
     "responses": ["E kaaro o! 🌅", "E kaaro! 😊", "E kaaro! Jẹ ki ọjọ rẹ ni ẹwà! 🌅"]},
    # Yoruba — afternoon
    {"re": re.compile(r"\be?\s*kaasan\b|\be?\s*kaasaan\b", re.IGNORECASE),
     "responses": ["E kaasan o! 😊", "E kaasan! 🌤"]},
    # Yoruba — evening
    {"re": re.compile(r"\be?\s*kaale\b", re.IGNORECASE),
     "responses": ["E kaale o! 🌆", "E kaale! 😊"]},
    # Yoruba — greeting/how are you
    {"re": re.compile(r"\bbawo\s*ni\b|\bpele\b|\bẹ\s*káàbọ̀\b", re.IGNORECASE),
     "responses": ["Mo wa o! 😊", "A dupe! Bawo ni? 👋", "Ẹ káàbọ̀! 😊"]},
    # Hausa — morning/greeting
    {"re": re.compile(r"\bina\s*kwana\b|\bsannu\b", re.IGNORECASE),
     "responses": ["Ina kwana! 🌅", "Lafiya lau! 😊", "Sannu! 👋"]},
    # Hausa — afternoon
    {"re": re.compile(r"\bina\s*wuni\b", re.IGNORECASE),
     "responses": ["Ina wuni! 😊", "Lafiya! 🌤"]},
    # Igbo — morning
    {"re": re.compile(r"\butụtụ\b|\bututu\b|\bu\s*tu\s*tu\b", re.IGNORECASE),
     "responses": ["Ụtụtụ ọma! 🌅", "Ụtụtụ ọma o! 😊"]},
    # Arabic / Islamic
    {"re": re.compile(r"\bassalamu?\s*alaikum\b|\bas-?salam\b|\bsalamu?\s*alaikum\b|\bsalam\b|\bwslm\b", re.IGNORECASE),
     "responses": ["Wa alaikum assalam! 🙏", "Wa alaikum salam wa rahmatullahi wa barakatuh! 🤲", "Wa alaikum assalam wa rahmatullahi! 🙏"]},
    # French — morning
    {"re": re.compile(r"\bbonjour\b", re.IGNORECASE),
     "responses": ["Bonjour! 😊", "Bonjour! Bonne journée! 🌟", "Bonjour tout le monde! 😊"]},
    # French — evening
    {"re": re.compile(r"\bbonsoir\b", re.IGNORECASE),
     "responses": ["Bonsoir! 🌆", "Bonsoir! 😊"]},
    # French — night
    {"re": re.compile(r"\bbonne\s*nuit\b", re.IGNORECASE),
     "responses": ["Bonne nuit! 🌙", "Bonne nuit! Faites de beaux rêves! 💤"]},
    # French — hi
    {"re": re.compile(r"\bsalut\b", re.IGNORECASE),
     "responses": ["Salut! 😊", "Salut! Comment ça va? 👋"]},
    # Spanish — morning
    {"re": re.compile(r"\bbuenos?\s*d[ií]as?\b", re.IGNORECASE),
     "responses": ["¡Buenos días! 🌅", "¡Buenos días! Que tengas un buen día! ☀️", "¡Buenos días a todos! 🌅"]},
    # Spanish — afternoon
    {"re": re.compile(r"\bbuenas?\s*tardes?\b", re.IGNORECASE),
     "responses": ["¡Buenas tardes! 😊", "¡Buenas tardes! 🌤"]},
    # Spanish — night
    {"re": re.compile(r"\bbuenas?\s*noches?\b", re.IGNORECASE),
     "responses": ["¡Buenas noches! 🌙", "¡Buenas noches! Dulces sueños! 💤"]},
    # Spanish — hello
    {"re": re.compile(r"\bhola\b", re.IGNORECASE),
     "responses": ["¡Hola! 😊", "¡Hola! ¿Cómo estás? 👋"]},
    # Portuguese — morning
    {"re": re.compile(r"\bbom\s*dia\b", re.IGNORECASE),
     "responses": ["Bom dia! 🌅", "Bom dia! Tenha um ótimo dia! ☀️", "Bom dia a todos! 🌅"]},
    # Portuguese — afternoon
    {"re": re.compile(r"\bboa\s*tarde\b", re.IGNORECASE),
     "responses": ["Boa tarde! 😊", "Boa tarde! 🌤"]},
    # Portuguese — night
    {"re": re.compile(r"\bboa\s*noite\b", re.IGNORECASE),
     "responses": ["Boa noite! 🌙", "Boa noite! Bons sonhos! 💤"]},
    # Portuguese — hello
    {"re": re.compile(r"\bol[aá]\b", re.IGNORECASE),
     "responses": ["Olá! 😊", "Olá! Como vai? 👋"]},
]


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


async def fetch_professor_messages() -> list:
    """Fetch random professor messages from the library (PIN-exempt endpoint)."""
    if not REPLIT_WEBHOOK_URL or not BOT_PROJECT_ID or not API_HASH:
        return []
    url = f"{_base_url()}/bot-professor-messages/{BOT_PROJECT_ID}?api_hash={API_HASH}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    msgs = [m for m in data.get("messages", []) if m and str(m).strip()]
                    logger.info(f"Professor message library: fetched {len(msgs)} message(s) for today.")
                    return msgs
                logger.warning(f"Professor messages fetch returned HTTP {resp.status}")
    except Exception as e:
        logger.warning(f"Professor messages fetch failed: {e}")
    return []


async def load_initial_schedules():
    """On startup, load both main schedule and boss data from Replit."""
    global _live_schedule, _boss_schedule, _boss_session_string, _boss_phone, _professor_messages, _greeting_enabled

    logger.info("Fetching main schedule from Replit...")
    main = await fetch_remote_schedule()
    async with _schedule_lock:
        if main:
            _live_schedule = main
            logger.info(f"Remote main schedule loaded: {len(main)} slot(s) — {main}")
        else:
            logger.info(f"Using fallback main schedule: {len(_live_schedule)} slot(s)")

    logger.info("Fetching Professor (boss) data from Replit...")
    boss, prof_msgs = await asyncio.gather(fetch_boss_data(), fetch_professor_messages())
    async with _schedule_lock:
        if boss:
            raw_sched = boss.get("schedule", [])
            parsed = _parse_remote_schedule(raw_sched)
            _boss_schedule = parsed
            raw_fixed = boss.get("fixed_slots", [])
            _boss_fixed_slots = _parse_remote_schedule(raw_fixed)
            _boss_session_string = boss.get("boss_session_string", "")
            _boss_phone = boss.get("boss_phone", "")
            _greeting_enabled = bool(boss.get("greeting_enabled", False))
            if _boss_session_string:
                logger.info(f"Professor loaded: {_boss_phone}, {len(parsed)} library slot(s), {len(_boss_fixed_slots)} fixed slot(s), greeting={'ON' if _greeting_enabled else 'OFF'}")
            else:
                logger.info("No Professor session configured — Professor bot will not run.")
        else:
            logger.info("Could not fetch boss data — Professor bot will not run.")
        if prof_msgs:
            _professor_messages = prof_msgs
            logger.info(f"Professor message library loaded: {len(prof_msgs)} message(s)")


async def schedule_refresher():
    """Background task: refresh both schedules from Replit every 6 hours."""
    global _live_schedule, _boss_schedule, _boss_session_string, _boss_phone, _professor_messages, _greeting_enabled
    while True:
        await asyncio.sleep(SCHEDULE_REFRESH_INTERVAL)
        logger.info("Refreshing schedules from Replit...")

        main, boss, prof_msgs = await asyncio.gather(
            fetch_remote_schedule(), fetch_boss_data(), fetch_professor_messages()
        )

        if main:
            async with _schedule_lock:
                _live_schedule = main
            logger.info(f"Main schedule refreshed: {len(main)} slot(s)")

        if boss:
            parsed = _parse_remote_schedule(boss.get("schedule", []))
            fixed = _parse_remote_schedule(boss.get("fixed_slots", []))
            new_greeting = bool(boss.get("greeting_enabled", False))
            async with _schedule_lock:
                _boss_schedule = parsed
                _boss_fixed_slots = fixed
                _boss_session_string = boss.get("boss_session_string", "")
                _boss_phone = boss.get("boss_phone", "")
                _greeting_enabled = new_greeting
            logger.info(f"Professor refreshed: {len(parsed)} library slot(s), {len(fixed)} fixed slot(s), greeting={'ON' if new_greeting else 'OFF'}")

        if prof_msgs:
            async with _schedule_lock:
                _professor_messages = prof_msgs
            logger.info(f"Professor message library refreshed: {len(prof_msgs)} message(s)")


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

                num_groups = len(CHAT_IDS)
                if num_groups == 0:
                    await asyncio.sleep(90)
                    continue

                # ── Group rotation ────────────────────────────────────────────
                # Each bot is assigned exactly ONE group per cycle. The assignment
                # rotates every cycle (keyed on date + scheduled hour) so the same
                # bots never always cover the same group. With 20 bots + 3 groups,
                # ~6-7 different accounts post in each group per cycle, all at
                # different times thanks to the stagger delay already applied above.
                #
                # Formula: assigned_group = (bot_index + rotation_offset) % num_groups
                # rotation_offset changes daily per time slot so groups see new faces.
                now = now_nigeria()
                rotation_offset = (now.timetuple().tm_yday * len(current_schedule) +
                                   current_schedule.index(next_event) if next_event in current_schedule else now.hour)
                assigned_group_idx = (bot_index + rotation_offset) % num_groups
                assigned_chat_id = CHAT_IDS[assigned_group_idx]

                logger.info(
                    f"{label}: Assigned to group {assigned_group_idx + 1} of {num_groups} "
                    f"(rotation offset {rotation_offset}) — sending '{msg}'"
                )

                await send_with_retry(client, assigned_chat_id, msg, label)
                msg_count += 1
                await ping_status(bot_index, phone, msg_count, str(assigned_chat_id), msg)

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

            # ── Greeting response handler ────────────────────────────────────
            # Attaches to this client; fires asynchronously while we sleep in
            # the scheduling loop below. Re-registered each outer-loop restart.
            def _make_greeting_handler(prof_client):
                async def _handle_greeting(event):
                    global _greeting_enabled, _greeting_cooldowns
                    async with _schedule_lock:
                        if not _greeting_enabled:
                            return
                    text = (event.raw_text or "").strip()
                    if not text:
                        return
                    matched = None
                    for entry in GREETING_MAP:
                        if entry["re"].search(text):
                            matched = entry
                            break
                    if not matched:
                        return
                    chat_id = event.chat_id
                    now_ts = asyncio.get_event_loop().time()
                    async with _greeting_lock:
                        last = _greeting_cooldowns.get(chat_id, 0)
                        if now_ts - last < GREETING_COOLDOWN_SEC:
                            return
                        _greeting_cooldowns[chat_id] = now_ts
                    if random.random() > GREETING_RESPONSE_CHANCE:
                        return
                    reply_text = random.choice(matched["responses"])
                    delay = random.uniform(4, 22)
                    await asyncio.sleep(delay)
                    try:
                        await prof_client.send_message(chat_id, reply_text)
                        logger.info(f"Professor[Greeter]: Replied '{reply_text}' to '{text[:40]}' in chat {chat_id}")
                    except FloodWaitError as fw:
                        logger.warning(f"Professor[Greeter]: FloodWait {fw.seconds}s — skipping reply")
                    except Exception as e:
                        logger.warning(f"Professor[Greeter]: Failed to send greeting reply: {e}")
                return _handle_greeting

            if CHAT_IDS:
                client.add_event_handler(
                    _make_greeting_handler(client),
                    TelethonEvents.NewMessage(chats=CHAT_IDS, incoming=True),
                )
                logger.info(f"{label}: Greeting handler attached to {len(CHAT_IDS)} group(s).")
            # ────────────────────────────────────────────────────────────────

            while True:
                async with _schedule_lock:
                    boss_sched = list(_boss_schedule)
                    boss_fixed = list(_boss_fixed_slots)
                    # Also refresh session string in case it was updated
                    sess_str = _boss_session_string
                    phone = _boss_phone

                # Combine library slots (type='lib') and fixed slots (type='fix')
                # Each combined entry: (sh, sm, eh, em, msg, slot_type)
                combined = [(*s, 'lib') for s in boss_sched] + [(*s, 'fix') for s in boss_fixed]

                if not combined:
                    logger.info(f"{label}: No schedule set — sleeping 30min, will check again.")
                    await asyncio.sleep(30 * 60)
                    continue

                # Find next slot across both lists
                min_wait = float("inf")
                next_event = None
                for slot in combined:
                    wait = seconds_until(slot[0], slot[1])
                    if wait < min_wait:
                        min_wait = wait
                        next_event = slot

                sh, sm, eh, em, msg, slot_type = next_event

                if slot_type == 'fix':
                    # Fixed slot — always send verbatim, no library involved
                    send_msg = msg
                    logger.info(f"{label}: [FIXED] Next at {sh:02d}:{sm:02d} WAT. Msg: {send_msg[:60]!r}")
                else:
                    # Library slot — pick from random library, fall back to slot msg
                    async with _schedule_lock:
                        lib = list(_professor_messages)
                    if lib:
                        send_msg = lib.pop(0)
                        async with _schedule_lock:
                            if not _professor_messages:
                                _professor_messages.extend(lib if lib else [send_msg])
                            else:
                                _professor_messages.pop(0)
                    else:
                        send_msg = msg
                    logger.info(f"{label}: [LIBRARY] Next at {sh:02d}:{sm:02d} WAT. Msg: {send_msg[:60]!r}")

                # Professor sends slightly after the stagger window ends (bots finish first)
                boss_extra_delay = random.uniform(60, 180)
                total_wait = min_wait + boss_extra_delay

                logger.info(
                    f"{label}: Sleeping {total_wait:.0f}s (extra delay: {boss_extra_delay:.0f}s)."
                )

                await asyncio.sleep(total_wait)

                if not await ensure_connected(client, label):
                    logger.warning(f"{label}: Could not reconnect. Retrying outer loop.")
                    break

                # Professor sends to all groups with a longer, more deliberate delay between each
                last_group = str(CHAT_IDS[-1]) if CHAT_IDS else ""
                for i, chat_id in enumerate(CHAT_IDS):
                    await send_with_retry(client, chat_id, send_msg, label)
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


# ─── Group lock / unlock helpers ──────────────────────────────────────────────

async def lock_all_groups(client: TelegramClient, label: str):
    """Restrict all members from sending messages in every group (slow mode off, send locked)."""
    from telethon.tl.functions.messages import EditChatDefaultBannedRightsRequest
    from telethon.tl.types import ChatBannedRights

    rights = ChatBannedRights(
        until_date=None,
        send_messages=True,
        send_media=True,
        send_stickers=True,
        send_gifs=True,
        send_games=True,
        send_inline=True,
        embed_links=True,
    )
    for chat_id in CHAT_IDS:
        try:
            await client(EditChatDefaultBannedRightsRequest(peer=chat_id, banned_rights=rights))
            logger.info(f"{label}: 🔒 Locked group {chat_id}")
        except Exception as e:
            logger.error(f"{label}: Failed to lock group {chat_id}: {e}")
        await asyncio.sleep(2)


async def unlock_all_groups(client: TelegramClient, label: str):
    """Remove send restrictions from all groups so members can talk freely again."""
    from telethon.tl.functions.messages import EditChatDefaultBannedRightsRequest
    from telethon.tl.types import ChatBannedRights

    rights = ChatBannedRights(
        until_date=None,
        send_messages=False,
        send_media=False,
        send_stickers=False,
        send_gifs=False,
        send_games=False,
        send_inline=False,
        embed_links=False,
    )
    for chat_id in CHAT_IDS:
        try:
            await client(EditChatDefaultBannedRightsRequest(peer=chat_id, banned_rights=rights))
            logger.info(f"{label}: 🔓 Unlocked group {chat_id}")
        except Exception as e:
            logger.error(f"{label}: Failed to unlock group {chat_id}: {e}")
        await asyncio.sleep(2)


# ─── Group locker (runs as part of the Professor's session) ───────────────────

STAGGER_WINDOW_MIN = 20  # must match window_minutes in stagger_delay()
LOCK_OFFSET_MIN    = STAGGER_WINDOW_MIN + 2   # 2 min after last bot finishes Ready (+22)
UNLOCK_OFFSET_MIN  = -2                        # 2 min before first Done bot starts


async def run_group_locker():
    """
    Watches the main bot schedule for 'Ready' and 'Done' message slots.
    - 2 min after 'Ready' time  → Professor locks all groups
    - 2 min after 'Done'  time  → Professor unlocks all groups

    Uses the Professor's own Telegram session so it has admin rights.
    """
    label = "Professor[Locker]"

    # Wait until we have a boss session
    while True:
        async with _schedule_lock:
            sess_str = _boss_session_string

        if sess_str:
            break
        logger.info(f"{label}: No Professor session yet — waiting 5 min before retrying...")
        await asyncio.sleep(5 * 60)

    if not API_ID or not API_HASH:
        logger.error(f"{label}: API_ID / API_HASH not set. Group locker cannot start.")
        return

    outer_attempt = 0

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
            await client.connect()
            if not await client.is_user_authorized():
                logger.error(f"{label}: Session not authorized. Cannot lock/unlock groups.")
                return
            logger.info(f"{label}: Connected. Watching schedule for Ready/Done triggers.")

            while True:
                # Refresh session string and schedule each loop
                async with _schedule_lock:
                    sess_str = _boss_session_string
                    current_schedule = list(_live_schedule)

                if not current_schedule:
                    await asyncio.sleep(60)
                    continue

                # Build list of upcoming trigger events
                # Each entry: (seconds_to_wait, action, trigger_msg, slot_h, slot_m)
                events = []
                for (sh, sm, eh, em, msg) in current_schedule:
                    if "ready" in msg.lower():
                        offset_min = LOCK_OFFSET_MIN    # +2 → 2 min AFTER Ready
                        action = "lock"
                    elif "done" in msg.lower():
                        offset_min = UNLOCK_OFFSET_MIN  # -2 → 2 min BEFORE Done
                        action = "unlock"
                    else:
                        continue

                    # Target time = slot time ± offset
                    total_minutes = sh * 60 + sm + offset_min
                    target_h = (total_minutes // 60) % 24
                    target_m = total_minutes % 60
                    wait = seconds_until(target_h, target_m)
                    events.append((wait, action, msg, sh, sm))

                if not events:
                    logger.info(f"{label}: No 'Ready'/'Done' slots in schedule. Checking again in 30 min.")
                    await asyncio.sleep(30 * 60)
                    continue

                # Pick the soonest upcoming event
                events.sort(key=lambda x: x[0])
                wait, action, trigger_msg, sh, sm = events[0]

                direction = "2 min after" if action == "lock" else "2 min before"
                logger.info(
                    f"{label}: Next {action.upper()} in {wait / 60:.1f} min "
                    f"({direction} '{trigger_msg}' slot at {sh:02d}:{sm:02d} WAT)"
                )

                await asyncio.sleep(wait)

                if not await ensure_connected(client, label):
                    logger.warning(f"{label}: Cannot connect — skipping {action}.")
                    await asyncio.sleep(60)
                    continue

                if action == "lock":
                    logger.info(f"{label}: 🔒 Locking all groups now...")
                    await lock_all_groups(client, label)
                else:
                    logger.info(f"{label}: 🔓 Unlocking all groups now...")
                    await unlock_all_groups(client, label)

                # Brief cooldown so we don't immediately re-trigger the same slot
                await asyncio.sleep(90)

        except (SessionRevokedError, AuthKeyUnregisteredError, UserDeactivatedBanError) as e:
            logger.error(f"{label}: Fatal auth error — {e}. Group locker stopping.")
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
    tasks.append(run_group_locker())
    tasks.append(schedule_refresher())
    await asyncio.gather(*tasks, return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(main())
