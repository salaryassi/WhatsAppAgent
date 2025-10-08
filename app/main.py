# app/main.py

import os
import io
import json
import uuid
import requests
import asyncio
import threading
from datetime import datetime, timedelta

from flask import Flask, request, jsonify
from fuzzywuzzy import fuzz

# Assuming these modules exist and are correctly implemented
from .database import (get_db_connection, setup_database, store_receipt,
                     get_receipt_by_id, mark_receipt_forwarded, log_event)
from .encryption import encrypt_image, decrypt_image
from .telegram_bot import forward_to_bot
from .utils import find_match_in_db

# --- SCRIPT CONFIGURATION (No external config file needed) ---
# Secrets should still come from the environment for security
EVOLUTION_API_KEY = os.getenv("EVOLUTION_API_KEY")

# Application settings
RECEIPT_KEYWORD = "recc "      # The keyword to identify a receipt, whitespace is important!
MATCH_THRESHOLD = 85           # The confidence score needed for a fuzzy match (e.g., 85%)
IMAGES_DIR = "app/images"      # Directory to store encrypted images
CACHE_EXPIRATION_SECONDS = 120 # How long to keep an image in cache without a "recc" message

# --- In-Memory Cache for Recent Images ---
# Links a (chat_id, sender_id) to a recently sent image URL
recent_image_cache = {}

# --- Flask App Initialization ---
app = Flask(__name__)

# --- Custom Print Function for Reliable & Verbose Logging ---
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
    
    # The flush=True is critical for seeing logs immediately in Docker
    print(f"[{timestamp}] [{prefix}] {message}", flush=True)

# --- Initial Setup ---
with app.app_context():
    setup_database()
    os.makedirs(IMAGES_DIR, exist_ok=True)
    log_print("Database and image directory initialized.")

# ==============================================================================
#  HELPER & BACKGROUND TASK FUNCTIONS
# ==============================================================================

def extract_message_details(msg: dict) -> dict:
    """Extracts key details from a message payload into a structured dictionary."""
    log_print(f"Extracting details from raw message payload...")
    message = msg.get("message", {}) if isinstance(msg.get("message"), dict) else {}
    
    def get_first_valid(*keys):
        for key in keys:
            if msg.get(key): return msg[key]
            if message.get(key): return message[key]
        return None

    details = {
        "chat_id": get_first_valid("chatId", "remoteJid"),
        "sender_id": get_first_valid("author", "from"),
        "text": get_first_valid("caption", "text", "body", "message"),
        "media_url": get_first_valid("fileUrl", "mediaUrl", "url"),
    }
    log_print(f"Extracted Details: ChatID={details['chat_id']}, Sender={details['sender_id']}, MediaURL={details['media_url'] is not None}")
    return details

def download_media(url: str) -> bytes:
    """Downloads media content from a URL using the Evolution API key."""
    log_print(f"Downloading media from URL: {url}")
    headers = {"apikey": EVOLUTION_API_KEY} if EVOLUTION_API_KEY else {}
    try:
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        log_print("Media download successful.")
        return response.content
    except requests.RequestException as e:
        log_print(f"Failed to download media. Error: {e}", level="ERROR")
        raise

def process_receipt_task(customer_name: str, media_url: str, chat_id: str):
    """Background task to download, encrypt, and store a receipt image."""
    log_print(f"[THREAD] Starting background receipt processing for '{customer_name}'.")
    try:
        image_data = download_media(media_url)
        encrypted_data = encrypt_image(image_data)
        
        filename = f"{uuid.uuid4().hex}.enc"
        image_path = os.path.join(IMAGES_DIR, filename)
        
        with open(image_path, "wb") as f:
            f.write(encrypted_data)
            
        receipt_id = store_receipt(customer_name, image_path, chat_id)
        log_event("receipt_stored", {"receipt_id": receipt_id, "customer": customer_name})
        log_print(f"[THREAD] Stored receipt {receipt_id} for '{customer_name}'. Task finished.")
    except Exception as e:
        log_print(f"[THREAD] Error in process_receipt_task for '{customer_name}'. Error: {e}", level="ERROR")
        log_event("receipt_process_error", {"customer": customer_name, "error": str(e)})

def process_query_task(query_text: str, chat_id: str):
    """Background task to find a matching receipt and forward it."""
    log_print(f"[THREAD] Starting background query processing for: '{query_text}'.")
    try:
        conn = get_db_connection()
        matched_id, score = find_match_in_db(query_text, conn)
        conn.close()

        if matched_id and score >= MATCH_THRESHOLD:
            log_print(f"[THREAD] 🎯 MATCH FOUND | Query: '{query_text}' | ReceiptID: {matched_id} | Score: {score}")
            receipt = get_receipt_by_id(matched_id)
            if receipt:
                forward_receipt_to_telegram(receipt)
        else:
            log_print(f"[THREAD] 🤷 No match found for query: '{query_text}'.")
    except Exception as e:
        log_print(f"[THREAD] Error in process_query_task for '{query_text}'. Error: {e}", level="ERROR")
    log_print(f"[THREAD] Query task for '{query_text}' finished.")

def forward_receipt_to_telegram(receipt_row: dict):
    """Decrypts, forwards a receipt to Telegram, and marks it as forwarded."""
    receipt_id = receipt_row["id"]
    path = receipt_row["image_path"]
    log_print(f"Preparing to forward receipt {receipt_id} to Telegram.")
    
    if not os.path.exists(path):
        log_print(f"File missing: {path} for receipt ID {receipt_id}", level="ERROR")
        return

    try:
        with open(path, "rb") as f:
            decrypted_data = decrypt_image(f.read())
        
        image_stream = io.BytesIO(decrypted_data)
        metadata = {
            "receipt_id": receipt_id, "customer_name": receipt_row["customer_name"],
            "source_group": receipt_row["source_group"], "timestamp": receipt_row["timestamp"]
        }
        
        log_print(f"Calling async forward_to_bot for receipt {receipt_id}...")
        asyncio.run(forward_to_bot(image_stream, metadata))
        
        mark_receipt_forwarded(receipt_id)
        log_print(f"Successfully forwarded receipt {receipt_id} to Telegram and marked in DB.")
        log_event("forwarded_to_telegram", metadata)
    except Exception as e:
        log_print(f"Failed forwarding receipt {receipt_id} to Telegram. Error: {e}", level="ERROR")

# ==============================================================================
#  FLASK WEBHOOK ENDPOINT
# ==============================================================================

@app.route('/')
def index():
    return "<h1>✅ WhatsApp Agent is Running!</h1>"

@app.route('/whatsapp_webhook', methods=['POST'])
def webhook():
    """Receives webhook, validates, and dispatches tasks to background threads."""
    log_print("--- 📨 WEBHOOK HIT ---")
    data = request.get_json()
    if not data or "messages" not in data:
        log_print("Webhook received with invalid or empty JSON body.", level="WARNING")
        return jsonify({"status": "error", "message": "Invalid JSON"}), 400

    log_print(f"Webhook contains {len(data.get('messages', []))} message(s). Starting processing loop...")
    for msg in data.get("messages", []):
        details = extract_message_details(msg)
        chat_id = details["chat_id"]
        
        # Skip if there's no chat ID (e.g., status updates or other events)
        if not chat_id:
            log_print("Skipping message with no Chat ID.", level="WARNING")
            continue

        text = details["text"] or ""
        media_url = details["media_url"]
        sender_id = details["sender_id"]

        # --- Message Routing Logic ---
        # Case 1: Message contains the receipt keyword (e.g., "recc John Doe")
        if RECEIPT_KEYWORD in text.lower():
            log_print(f"Routing -> Receipt message detected.")
            customer_name = text.lower().split(RECEIPT_KEYWORD, 1)[1].strip()
            
            image_to_process_url = media_url
            if not image_to_process_url:
                cache_key = (chat_id, sender_id)
                if cache_key in recent_image_cache:
                    image_to_process_url = recent_image_cache.pop(cache_key)['url']
                    log_print(f"🧠 Found recent image in cache for {cache_key}.")

            if image_to_process_url and customer_name:
                thread = threading.Thread(target=process_receipt_task, args=(customer_name, image_to_process_url, chat_id))
                thread.start()
                log_print(f"Dispatched background thread for receipt: '{customer_name}'.")
            else:
                log_print(f"Received '{RECEIPT_KEYWORD}' for '{customer_name}' but no associated image.", level="WARNING")

        # Case 2: Message is just an image (cache it)
        elif media_url:
            log_print(f"Routing -> Image-only message detected.")
            cache_key = (chat_id, sender_id)
            recent_image_cache[cache_key] = {"url": media_url, "timestamp": datetime.now()}
            log_print(f"🖼️  Cached image from {cache_key}. Cache size: {len(recent_image_cache)}.")

        # Case 3: Message is just text (treat as a query)
        elif text:
            log_print(f"Routing -> Text-only query detected.")
            thread = threading.Thread(target=process_query_task, args=(text, chat_id))
            thread.start()
            log_print(f"Dispatched background thread for query: '{text[:50]}...'.")

    # Clean up expired cache items after processing all messages
    now = datetime.now()
    expired_keys = [k for k, v in recent_image_cache.items() if now - v['timestamp'] > timedelta(seconds=CACHE_EXPIRATION_SECONDS)]
    if expired_keys:
        log_print(f"🧹 Cleaning up {len(expired_keys)} expired cache entries.")
        for key in expired_keys:
            del recent_image_cache[key]

    log_print("--- ✅ WEBHOOK PROCESSING COMPLETE ---")
    return jsonify({"status": "acknowledged"}), 200

if __name__ == '__main__':
    log_print("Starting Flask web server...")
    # debug=False is recommended when using threading to avoid Flask's reloader causing issues.
    app.run(host='0.0.0.0', port=5000, debug=False)