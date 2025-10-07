#!/usr/bin/env python3
"""
Prelogin script for creating a Telegram user session file
"""

import os
import logging
from pyrogram import Client
from config import TELEGRAM_API_ID, TELEGRAM_API_HASH

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("prelogin")

SESSION_NAME = os.getenv("TELEGRAM_SESSION_PATH", "whatsapp_agent_session")

def main():
    print(f"Starting prelogin. Session file will be saved as: {SESSION_NAME}.session")
    print("You will need to enter your phone number and the code sent by Telegram.")

    app = Client(SESSION_NAME, api_id=TELEGRAM_API_ID, api_hash=TELEGRAM_API_HASH)

    with app:
        me = app.get_me()
        print(f"✅ Logged in successfully as {me.first_name} (@{me.username})")
        print(f"Session file '{SESSION_NAME}.session' created.")

if __name__ == "__main__":
    main()
