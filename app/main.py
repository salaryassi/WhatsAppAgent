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
    # Using emojis for clear visual cues in the log
    if level == "ERROR":
        prefix = "❌ ERROR"
    elif level == "WARNING":
        prefix = "⚠️ WARNING"
    else:
        prefix = "✅ INFO"
    
    print(f"[{timestamp}] [{prefix}] {message}", flush=True)

# --- Initial Setup ---
with app.app_context():
    setup_database()
    os.makedirs(IMAGES_DIR, exist_ok=True)
    log_print("Database and image directory initialized.")


# --- Helper Functions ---

def extract_text_from_payload(data):
    """Robustly extracts text/caption from various possible webhook payload structures."""
    message = data.get("message", {}) if isinstance(data.get("message"), dict) else {}
    candidates = [
        data.get("caption"), data.get("text"), data.get("body"),
        message.get("caption"), message.get("text"), message.get("body")
    ]
    for text in candidates:
        if isinstance(text, str) and text:
            return text.strip()
    return ""

def extract_media_url(data):
    """Robustly extracts a media URL from various possible webhook payload structures."""
    message = data.get("message", {}) if isinstance(data.get("message"), dict) else {}
    keys = ["fileUrl", "mediaUrl", "url", "downloadUrl", "imageUrl"]
    for key in keys:
        if data.get(key): return data[key]
        if message.get(key): return message[key]
    attachments = message.get("attachments") or message.get("media") or []
    if attachments and isinstance(attachments, list):
        first_attachment = attachments[0]
        if isinstance(first_attachment, dict):
            for key in keys:
                if first_attachment.get(key): return first_attachment[key]
    return None

def download_media(url):
    """Downloads media content from a URL using the Evolution API key."""
    headers = {"apikey": EVOLUTION_API_KEY} if EVOLUTION_API_KEY else {}
    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()
    return response.content

def forward_receipt_to_telegram_and_mark(receipt_row):
    """Decrypts, forwards a receipt to Telegram, and marks it as forwarded in the DB."""
    path = receipt_row["image_path"]
    if not path or not os.path.exists(path):
        log_print(f"Receipt file missing: {path} for receipt ID {receipt_row['id']}", level="ERROR")
        log_event("missing_file", {"receipt_id": receipt_row["id"], "path": path})
        return False
    try:
        with open(path, "rb") as f:
            decrypted_data = decrypt_image(f.read())
        image_stream = io.BytesIO(decrypted_data)
        metadata = {
            "receipt_id": receipt_row["id"], "customer_name": receipt_row["customer_name"],
            "source_group": receipt_row["source_group"], "timestamp": receipt_row["timestamp"]
        }
        asyncio.run(forward_to_bot(image_stream, metadata))
        mark_receipt_forwarded(receipt_row["id"])
        log_print(f"Forwarded receipt {receipt_row['id']} to Telegram.")
        log_event("forwarded_to_telegram", metadata)
        return True
    except Exception as e:
        log_print(f"Failed forwarding receipt {receipt_row['id']} to Telegram. Error: {e}", level="ERROR")
        log_event("telegram_forward_error", {"receipt_id": receipt_row["id"], "error": str(e)})
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
            return jsonify({"status": "error", "message": "Invalid JSON or empty body"}), 400
    except Exception as e:
        log_print(f"Failed to parse request body as JSON. Error: {e}", level="ERROR")
        return jsonify({"status": "error", "message": "Failed to parse JSON"}), 400

    now = datetime.now()
    expired_keys = [k for k, v in recent_image_cache.items() if now - v['timestamp'] > timedelta(seconds=CACHE_EXPIRATION_SECONDS)]
    if expired_keys:
        for key in expired_keys:
            del recent_image_cache[key]
        log_print(f"🧹 Expired {len(expired_keys)} image(s) from cache.")
    
    messages = data.get("messages", [])
    if not messages:
        log_print("Webhook received but contains no 'messages' field.", level="WARNING")
        return jsonify({"status": "ok", "message": "No messages to process"}), 200

    for msg in messages:
        chat_id = msg.get("chatId", "")
        sender_id = msg.get("author") or msg.get("from")
        
        if not chat_id.endswith("@g.us") or not sender_id:
            continue

        text = extract_text_from_payload(msg)
        media_url = extract_media_url(msg)
        log_print(f"Processing message from '{sender_id}' in group '{chat_id}'. Text: '{text[:50]}...' | Media: {'Yes' if media_url else 'No'}")

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
                    log_print(f"Stored receipt {receipt_id} for '{customer_name}' from group '{chat_id}'.")
                else:
                    log_print(f"Received 'recc' for '{customer_name}' but could not find an associated image.", level="WARNING")
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
                    log_print(f"🎯 MATCH FOUND | Query: '{text}' in '{chat_id}' | ReceiptID: {matched_id} | Score: {score}")
                    receipt = get_receipt_by_id(matched_id)
                    if receipt: forward_receipt_to_telegram_and_mark(receipt)
                else:
                    log_print(f"🔎 No match found for query: '{text}' in group '{chat_id}'.")
            except Exception as e:
                log_print(f"Error during match check for query: '{text}'. Error: {e}", level="ERROR")

    return jsonify({"status": "processed"}), 200


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)