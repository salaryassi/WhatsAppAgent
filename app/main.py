# app/main.py

import os
import io
import json
import uuid
import requests
import asyncio
from datetime import datetime, timedelta

from flask import Flask, request, jsonify
from fuzzywuzzy import fuzz

from .config import EVOLUTION_API_KEY, IMAGES_DIR, MATCH_THRESHOLD
from .database import (
    get_db_connection,
    setup_database,
    store_receipt,
    get_receipt_by_id,
    mark_receipt_forwarded,
    log_event
)
from .encryption import encrypt_image, decrypt_image
from .telegram_bot import forward_to_bot
from .utils import find_match_in_db

# --- In-Memory Cache for Recent Images ---
recent_image_cache = {}
CACHE_EXPIRATION_SECONDS = 120

# --- Flask App Initialization ---
app = Flask(__name__)

# --- Custom Print Function for Reliable Logging ---
def log_print(message, level="INFO"):
    """A reliable print-based logger that shows timestamps and flushes immediately."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    level_map = {"ERROR": "❌ ERROR", "WARNING": "⚠️ WARNING", "INFO": "✅ INFO", "DEBUG": "🐞 DEBUG"}
    prefix = level_map.get(level, "✅ INFO")
    print(f"[{timestamp}] [{prefix}] {message}", flush=True)

# --- Initial Setup ---
with app.app_context():
    setup_database()
    os.makedirs(IMAGES_DIR, exist_ok=True)
    log_print("Database and image directory initialized.")


# --- CORE LOGIC: Smartly Extract Data from Webhooks ---

# In app/main.py, replace the existing function with this one.

# In app/main.py, replace the existing function with this final version.

def extract_messages_from_payload(payload):
    """
    Intelligently finds and returns a list of message objects from various webhook event types.
    This version is robust and handles different JSON structures from WAHA without crashing.
    """
    event_data = payload.get('event')
    
    # --- THIS IS THE CRITICAL FIX ---
    # Check if event_data is a dictionary. If not, it's a simple event with no message.
    if not isinstance(event_data, dict):
        log_print(f"Received simple event of type: '{event_data}'. No message to process.", level="DEBUG")
        return [] # Return empty list and stop.

    # If we get here, we know event_data is a dictionary, so .get() is safe.
    event_type = event_data.get('event')
    log_print(f"Received nested event of type: '{event_type}'", level="DEBUG")

    if event_type == 'message_create':
        # For new messages, the data is inside this dictionary.
        return event_data.get('data', [])
        
    elif event_type == 'unread_count':
        messages = []
        unread_chats = event_data.get('data', [])
        for chat in unread_chats:
            if 'lastMessage' in chat:
                messages.append(chat['lastMessage'])
        return messages
        
    # For all other event types, return an empty list.
    return []

def extract_text_from_payload(msg_obj):
    """Extracts caption or body text from a message object."""
    # The 'caption' seems to be the reliable field for text with media
    text = msg_obj.get('caption') or msg_obj.get('body')
    if isinstance(text, str):
        return text.strip()
    return ""

def extract_media_url(msg_obj):
    """Extracts a media URL from a message object."""
    # Based on logs, the URL is in 'deprecatedMms3Url'
    return msg_obj.get('deprecatedMms3Url') or msg_obj.get('fileUrl')

# --- Other Helper Functions --- (download_media, forward_receipt, etc. remain the same)
def download_media(url):
    headers = {"apikey": EVOLUTION_API_KEY} if EVOLUTION_API_KEY else {}
    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()
    return response.content

def forward_receipt_to_telegram_and_mark(receipt_row):
    path = receipt_row["image_path"]
    if not path or not os.path.exists(path):
        log_print(f"Receipt file missing: {path} for receipt ID {receipt_row['id']}", level="ERROR")
        return False
    try:
        with open(path, "rb") as f: decrypted_data = decrypt_image(f.read())
        image_stream = io.BytesIO(decrypted_data)
        metadata = {"receipt_id": receipt_row["id"], "customer_name": receipt_row["customer_name"], "source_group": receipt_row["source_group"], "timestamp": receipt_row["timestamp"]}
        asyncio.run(forward_to_bot(image_stream, metadata))
        mark_receipt_forwarded(receipt_row["id"])
        log_print(f"Forwarded receipt {receipt_row['id']} to Telegram.")
        return True
    except Exception as e:
        log_print(f"Failed forwarding receipt {receipt_row['id']} to Telegram. Error: {e}", level="ERROR")
        return False

# --- Flask Routes ---

@app.route('/')
def index():
    return "<h1>WhatsApp Agent is Running!</h1>"


@app.route('/whatsapp_webhook', methods=['POST'])
def webhook():
    log_print("--- 📨 Endpoint Hit: /whatsapp_webhook ---")

    try:
        data = request.get_json()
        if data is None:
            log_print("Request body is not valid JSON or is empty.", level="ERROR")
            return jsonify({"status": "error"}), 400
    except Exception as e:
        log_print(f"Failed to parse request body as JSON. Error: {e}", level="ERROR")
        return jsonify({"status": "error"}), 400

    # 1. --- Use the new smart function to find messages ---
    messages = extract_messages_from_payload(data)
    
    if not messages:
        log_print("Webhook did not contain any processable messages. Task complete.")
        return jsonify({"status": "ok", "message": "No messages to process"}), 200
    
    log_print(f"Successfully extracted {len(messages)} message(s) to process.")

    # 2. --- Process each extracted message ---
    for msg in messages:
        # Use 'from' for group ID and 'author' for sender ID, based on logs
        chat_id = msg.get('from')
        sender_id = msg.get('author')
        
        if not chat_id or not chat_id.endswith("@g.us") or not sender_id:
            continue

        text = extract_text_from_payload(msg)
        media_url = extract_media_url(msg)

        # --- THIS IS THE NEW LOGGING YOU REQUESTED ---
        log_print(f"Extracted Details -> Group: {chat_id}, Sender: {sender_id}, Text: '{text[:70]}...', Media: {'Yes' if media_url else 'No'}", level="DEBUG")

        # 3. --- The rest of the logic remains the same ---
        if "recc " in text.lower():
            try:
                customer_name = text.lower().split("recc ", 1)[1]
                log_print(f"🧾 Found 'recc' keyword for customer: '{customer_name}'")

                image_to_process_url = media_url
                if not image_to_process_url:
                    cache_key = (chat_id, sender_id)
                    if cache_key in recent_image_cache:
                        image_to_process_url = recent_image_cache[cache_key]['url']
                        log_print(f"🧠 Found recent image in cache for {cache_key}.")
                        del recent_image_cache[cache_key]

                if image_to_process_url:
                    image_data = download_media(image_to_process_url)
                    encrypted_data = encrypt_image(image_data)
                    filename = f"{uuid.uuid4().hex}.enc"
                    image_path = os.path.join(IMAGES_DIR, filename)
                    with open(image_path, "wb") as f: f.write(encrypted_data)
                    
                    receipt_id = store_receipt(customer_name, image_path, chat_id)
                    log_event("receipt_stored", {"receipt_id": receipt_id, "customer": customer_name})
                    log_print(f"Stored receipt {receipt_id} for '{customer_name}'.")
                else:
                    log_print(f"Received 'recc' for '{customer_name}' but no associated image found.", level="WARNING")
            except Exception as e:
                log_print(f"Error processing 'recc' message. Error: {e}", level="ERROR")
        
        elif media_url:
            cache_key = (chat_id, sender_id)
            recent_image_cache[cache_key] = {"url": media_url, "timestamp": datetime.now()}
            log_print(f"🖼️  Cached image from {cache_key}. Cache size: {len(recent_image_cache)}.")

        elif text:
            try:
                conn = get_db_connection()
                matched_id, score = find_match_in_db(text, conn)
                conn.close()

                if matched_id and score >= MATCH_THRESHOLD:
                    log_print(f"🎯 MATCH FOUND | Query: '{text}' | ReceiptID: {matched_id} | Score: {score}")
                    receipt = get_receipt_by_id(matched_id)
                    if receipt: forward_receipt_to_telegram_and_mark(receipt)
                else:
                    log_print(f"🔎 No match for query: '{text}'.")
            except Exception as e:
                log_print(f"Error during match check for query: '{text}'. Error: {e}", level="ERROR")

    return jsonify({"status": "processed"}), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)