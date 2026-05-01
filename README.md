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

Each bot sends staggered within the window so all 20 don't message at exactly the same time.

## Target Chats

All 20 bots send to:
- `-5183093777`
- `-5216183184`
- `-5127067052`
- `-5119430444`

---

## Setup Guide

### Step 1 — Get Telegram API credentials

1. Go to [my.telegram.org](https://my.telegram.org) and log in with each phone number
2. Click **API development tools**
3. Create an app — you'll get an `api_id` (number) and `api_hash` (string)
4. Do this for each of the 20 accounts

### Step 2 — Generate session strings (run locally once per account)

```bash
pip install telethon
python generate_session.py
```

Enter the phone number and OTP when prompted. Copy the printed session string for each account.

### Step 3 — Build your BOTS_CONFIG value

Create a JSON array like this (one object per bot):

```json
[
  {"api_id": 12345678, "api_hash": "abc123def456", "session": "1BVtsOK8..."},
  {"api_id": 12345678, "api_hash": "abc123def456", "session": "1BVtsOK8..."},
  ...
]
```

You will paste this entire JSON block as the value of **one single environment variable** called `BOTS_CONFIG` on Render.

### Step 4 — Deploy to Render

1. Go to [render.com](https://render.com) → **New** → **Background Worker**
2. Connect your GitHub account → select `WSI-20-userbots` repo
3. Settings:
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `python bot.py`
4. Under **Environment Variables**, add just **ONE variable**:
   - **Key:** `BOTS_CONFIG`
   - **Value:** your full JSON array from Step 3
5. Click **Create Worker** — done!

---

## Environment Variable Format

Only **one** environment variable is needed:

| Key | Value |
|-----|-------|
| `BOTS_CONFIG` | JSON array of all 20 bot credentials |
