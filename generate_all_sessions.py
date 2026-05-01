"""
Run this script ONCE on your local computer to generate session strings
for all your Telegram accounts in one go.

Usage:
  pip install telethon
  python generate_all_sessions.py

At the end it prints the complete BOTS_CONFIG JSON value ready to paste
directly into Render as a single environment variable.
"""

import asyncio
import json
from telethon import TelegramClient
from telethon.sessions import StringSession


async def generate_session(bot_number: int) -> dict | None:
    print(f"\n{'='*55}")
    print(f"  BOT {bot_number} of 20")
    print(f"{'='*55}")

    skip = input("Press ENTER to continue, or type 'skip' to skip this bot: ").strip().lower()
    if skip == "skip":
        print(f"Bot {bot_number} skipped.")
        return None

    api_id_str = input("  API_ID   (from my.telegram.org): ").strip()
    api_hash   = input("  API_HASH (from my.telegram.org): ").strip()
    phone      = input("  Phone number (e.g. +2348012345678): ").strip()

    if not api_id_str or not api_hash or not phone:
        print("  Missing input — skipping this bot.")
        return None

    try:
        api_id = int(api_id_str)
    except ValueError:
        print("  API_ID must be a number — skipping this bot.")
        return None

    client = TelegramClient(StringSession(), api_id, api_hash)

    try:
        await client.connect()
        if not await client.is_user_authorized():
            await client.send_code_request(phone)
            code = input("  Enter the OTP code sent to your Telegram: ").strip()
            try:
                await client.sign_in(phone, code)
            except Exception:
                password = input("  Two-step verification password: ").strip()
                await client.sign_in(password=password)

        session_string = client.session.save()
        print(f"\n  Bot {bot_number} session generated successfully!")
        return {
            "api_id": api_id,
            "api_hash": api_hash,
            "session": session_string,
        }
    except Exception as e:
        print(f"  Error: {e}")
        return None
    finally:
        await client.disconnect()


async def main():
    print("\n" + "="*55)
    print("  WSI 20 Userbots — Session Generator")
    print("  Generates all session strings in one run.")
    print("  You will need: API_ID, API_HASH (from my.telegram.org)")
    print("  and the phone number for each of the 20 accounts.")
    print("="*55)

    num_bots_str = input("\nHow many bots do you want to set up? (1-20, default 20): ").strip()
    num_bots = 20
    if num_bots_str.isdigit():
        num_bots = max(1, min(20, int(num_bots_str)))

    results = []

    for i in range(1, num_bots + 1):
        creds = await generate_session(i)
        if creds:
            results.append(creds)

    print("\n\n" + "="*55)
    print("  ALL DONE!")
    print(f"  {len(results)} bot(s) configured successfully.")
    print("="*55)

    if not results:
        print("\nNo sessions were generated. Exiting.")
        return

    bots_config_json = json.dumps(results, indent=2)

    print("\n")
    print("COPY EVERYTHING BETWEEN THE LINES BELOW:")
    print("-"*55)
    print(bots_config_json)
    print("-"*55)
    print("\nPaste this as the value of BOTS_CONFIG on Render.")
    print("That's all — just ONE environment variable needed!\n")

    # Also save to a local file for convenience
    output_file = "bots_config.json"
    with open(output_file, "w") as f:
        json.dump(results, f, indent=2)
    print(f"Also saved to: {output_file}")
    print("WARNING: Delete this file after use — it contains sensitive session strings!\n")


if __name__ == "__main__":
    asyncio.run(main())
