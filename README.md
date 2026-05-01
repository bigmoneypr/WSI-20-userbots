# WSI 20 Userbots

Automated Telegram userbot system — 20 accounts sending scheduled messages to 4 group chats in Nigeria time (WAT, UTC+1).

## Schedule (Nigeria / WAT Time)

| Window | Message |
|--------|---------|
| 5:00 AM – 5:20 AM | Ready |
| 6:10 AM – 6:30 AM | Done |
| 7:00 AM – 7:20 AM | Ready |
| 8:10 AM – 8:30 AM | Done |
| 10:00 AM – 10:20 AM | Ready |
| 11:10 AM – 11:30 AM | Done |
| 12:10 PM – 12:30 PM | Ready |
| 1:45 PM – 2:05 PM | Done |

Each bot sends staggered within each window so all 20 don't message at the exact same time.

## Target Chats

All 20 bots send to:
- `-5183093777`
- `-5216183184`
- `-5127067052`
- `-5119430444`

## Setup

### 1. Get API credentials
Go to [my.telegram.org](https://my.telegram.org), log in, and create an app. You'll get an `API_ID` and `API_HASH` for each account.

### 2. Generate session strings
Run locally (do NOT run on server):
```bash
pip install telethon
python generate_session.py
```
Do this once per account. Copy the printed session string.

### 3. Deploy to Render

1. Connect this GitHub repo on [render.com](https://render.com)
2. Create a **Background Worker** service
3. Set environment variables for each bot:
   - `BOT1_API_ID`, `BOT1_API_HASH`, `BOT1_SESSION`
   - `BOT2_API_ID`, `BOT2_API_HASH`, `BOT2_SESSION`
   - ... up to `BOT20_*`

### 4. Start the service
Render will run `python bot.py` which starts all 20 bots concurrently.

## Environment Variables

Each bot `n` (1–20) needs three variables:
- `BOTn_API_ID` — integer API ID from my.telegram.org
- `BOTn_API_HASH` — string API hash from my.telegram.org
- `BOTn_SESSION` — session string from `generate_session.py`
