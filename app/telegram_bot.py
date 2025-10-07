from pyrogram import Client
import json
from .config import (
    TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_BOT_TOKEN,
    FORWARD_TO_BOT_USERNAME, ADMIN_CHAT_ID
)

app = Client(
    "my_bot",
    api_id=TELEGRAM_API_ID,
    api_hash=TELEGRAM_API_HASH,
    bot_token=TELEGRAM_BOT_TOKEN
)

async def forward_to_bot(image_data, metadata):
    async with app:
        await app.send_document(
            chat_id=FORWARD_TO_BOT_USERNAME,
            document=image_data,
            file_name=f"{metadata['receipt_id']}.jpg",
            caption=json.dumps(metadata)
        )

async def send_admin_notification(message):
    async with app:
        await app.send_message(chat_id=ADMIN_CHAT_ID, text=message)