"""
Run this script locally (NOT on Render) to generate a session string
for each Telegram account.

Usage:
  pip install telethon
  python generate_session.py

It will ask for your phone number, then the OTP code sent to Telegram.
Copy the printed session string and set it as BOT{n}_SESSION on Render.
"""

import asyncio
from telethon import TelegramClient
from telethon.sessions import StringSession


async def generate():
    api_id = int(input("Enter your API_ID (from my.telegram.org): ").strip())
    api_hash = input("Enter your API_HASH (from my.telegram.org): ").strip()
    phone = input("Enter phone number (with country code, e.g. +2348012345678): ").strip()

    client = TelegramClient(StringSession(), api_id, api_hash)
    await client.start(phone=phone)

    session_string = client.session.save()
    print("\n========== SESSION STRING ==========")
    print(session_string)
    print("====================================")
    print("Copy the string above and set it as an environment variable on Render.")

    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(generate())
