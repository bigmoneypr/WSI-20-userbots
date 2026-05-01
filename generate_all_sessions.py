"""
Run this script ONCE on your local computer to generate session strings
for all 20 Telegram accounts in one go.

You only need ONE API_ID and ONE API_HASH (from my.telegram.org — login once).
The same credentials work for all 20 accounts.

Usage:
  pip install telethon
  python generate_all_sessions.py

At the end it prints:
  - BOTS_SESSIONS  (just the session strings — paste on Render)
  - API_ID and API_HASH to also add on Render
"""

import asyncio
import json
import random
from telethon import TelegramClient
from telethon.sessions import StringSession

# Realistic Android device profiles — Telegram sees these as normal mobile logins
ANDROID_DEVICES = [
    {"device_model": "Samsung Galaxy S23",  "system_version": "Android 13", "app_version": "10.3.2"},
    {"device_model": "Samsung Galaxy S22",  "system_version": "Android 13", "app_version": "10.3.1"},
    {"device_model": "Xiaomi 13 Pro",       "system_version": "Android 13", "app_version": "10.2.9"},
    {"device_model": "OnePlus 11",          "system_version": "Android 13", "app_version": "10.3.0"},
    {"device_model": "Google Pixel 7",      "system_version": "Android 13", "app_version": "10.3.2"},
    {"device_model": "Samsung Galaxy A54",  "system_version": "Android 13", "app_version": "10.1.5"},
    {"device_model": "Tecno Camon 20",      "system_version": "Android 12", "app_version": "10.0.9"},
    {"device_model": "Infinix Note 30",     "system_version": "Android 12", "app_version": "10.1.2"},
    {"device_model": "Itel P40",            "system_version": "Android 12", "app_version": "10.0.8"},
    {"device_model": "Samsung Galaxy A34",  "system_version": "Android 13", "app_version": "10.2.1"},
]


async def generate_session(api_id: int, api_hash: str, bot_number: int, total: int) -> str | None:
    print(f"\n{'='*55}")
    print(f"  BOT {bot_number} of {total}")
    print(f"{'='*55}")

    phone = input("  Phone number (e.g. +2348012345678) or 'skip': ").strip()
    if phone.lower() == "skip":
        print(f"  Bot {bot_number} skipped.")
        return None

    device = random.choice(ANDROID_DEVICES)
    print(f"  Using device profile: {device['device_model']}")

    client = TelegramClient(
        StringSession(), api_id, api_hash,
        device_model=device["device_model"],
        system_version=device["system_version"],
        app_version=device["app_version"],
        lang_code="en",
        system_lang_code="en-US",
    )

    try:
        await client.connect()
        if not await client.is_user_authorized():
            await client.send_code_request(phone)
            code = input("  OTP code sent to Telegram: ").strip()
            try:
                await client.sign_in(phone, code)
            except Exception:
                password = input("  Two-step verification password: ").strip()
                await client.sign_in(password=password)

        session_string = client.session.save()
        print(f"  Bot {bot_number}: session generated!")
        return session_string

    except Exception as e:
        print(f"  Error: {e}")
        return None
    finally:
        await client.disconnect()


async def main():
    print("\n" + "="*55)
    print("  WSI 20 Userbots — Bulk Session Generator")
    print("="*55)
    print()
    print("You need ONE API_ID and ONE API_HASH for all bots.")
    print("Get them from: https://my.telegram.org")
    print("  1. Log in with any one phone number")
    print("  2. Click 'API development tools'")
    print("  3. Create an app (any name, any platform)")
    print("  4. Copy the App api_id and App api_hash below")
    print()

    api_id_str = input("Enter API_ID: ").strip()
    api_hash   = input("Enter API_HASH: ").strip()

    try:
        api_id = int(api_id_str)
    except ValueError:
        print("API_ID must be a number. Exiting.")
        return

    num_str = input("\nHow many bots? (1-20, default 20): ").strip()
    num_bots = 20
    if num_str.isdigit():
        num_bots = max(1, min(20, int(num_str)))

    print(f"\nNow enter the phone number for each of the {num_bots} accounts.")
    print("You will receive an OTP on Telegram for each one.\n")

    sessions = []

    for i in range(1, num_bots + 1):
        session = await generate_session(api_id, api_hash, i, num_bots)
        if session:
            sessions.append(session)

    print("\n\n" + "="*55)
    print(f"  DONE! {len(sessions)} session(s) generated.")
    print("="*55)

    if not sessions:
        print("No sessions generated. Exiting.")
        return

    bots_sessions_json = json.dumps(sessions, indent=2)

    print("\n\n--- STEP 1: Add this to Render as API_ID ---")
    print(api_id)

    print("\n--- STEP 2: Add this to Render as API_HASH ---")
    print(api_hash)

    print("\n--- STEP 3: Add this to Render as BOTS_SESSIONS ---")
    print(bots_sessions_json)

    print("\n" + "="*55)
    print("Only 3 environment variables needed on Render:")
    print("  API_ID, API_HASH, BOTS_SESSIONS")
    print("="*55 + "\n")

    # Save to file for convenience
    output = {
        "API_ID": api_id,
        "API_HASH": api_hash,
        "BOTS_SESSIONS": sessions,
    }
    with open("sessions_output.json", "w") as f:
        json.dump(output, f, indent=2)

    print("Also saved to: sessions_output.json")
    print("!! DELETE this file after copying to Render — it contains sensitive data !!\n")


if __name__ == "__main__":
    asyncio.run(main())
