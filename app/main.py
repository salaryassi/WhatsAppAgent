# app/main.py

import os
import io
import json
import uuid
import requests
import asyncio
import re
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


# --- CORE LOGIC: Robust Message Extraction for WAHA / webjs shapes ---

def _normalize_last_message_obj(lm):
    """
    Accepts a lastMessage-like object that may contain an inner '_data' dict
    and returns a flattened dict with common keys.
    """
    if not isinstance(lm, dict):
        return {}

    lm_data = lm.get('_data') if '_data' in lm and isinstance(lm.get('_data'), dict) else lm

    normalized = {
        "body": lm_data.get("body") or lm_data.get("caption") or "",
        "from": lm_data.get("from") or lm_data.get("remote") or lm.get("from") or lm.get("remote"),
        "author": None,
        # author can be inside nested dicts; try a few fallbacks
        "author": lm_data.get("author") or (lm_data.get("participant") and lm_data.get("participant").get("_serialized")) if isinstance(lm_data.get("participant"), dict) else lm_data.get("author"),
        "hasMedia": bool(lm_data.get("hasMedia") or lm.get("hasMedia")),
        "deprecatedMms3Url": lm_data.get("deprecatedMms3Url") or lm_data.get("mmsUrl") or lm_data.get("fileUrl") or lm.get("mediaUrl"),
        "fileUrl": lm_data.get("fileUrl") or lm.get("mediaUrl") or lm.get("mmsUrl"),
        # keep raw dict available for edge-cases
        "_raw": lm_data
    }
    return normalized


def extract_messages_from_payload(payload):
    """
    Find and return a list of normalized message dicts from WAHA / webjs payloads.
    Handles:
      - { "event": "message", ... }
      - { "event": { "event":"message_create", "data":[ ... ] } }
      - { "event": { "event":"unread_count", "data":[ { "lastMessage": {...} }, ... ] } }
      - { "messages": [ ... ] } (common other shape)
    Each returned message is a dict with keys: body, from, author, deprecatedMms3Url, fileUrl, hasMedia, _raw
    """
    try:
        # defensive: if payload is a JSON string
        if isinstance(payload, str):
            payload = json.loads(payload)
    except Exception:
        # leave as-is
        pass

    # If the top-level is a message object (common fallback)
    if payload.get("event") == "message" or payload.get("type") == "message":
        log_print("Detected top-level 'message' event.", level="DEBUG")
        return [ _normalize_last_message_obj(payload) ]

    # If messages list provided
    if isinstance(payload.get("messages"), list):
        log_print("Detected 'messages' array at top-level.", level="DEBUG")
        return [ _normalize_last_message_obj(m) for m in payload.get("messages") if isinstance(m, dict) ]

    evt = payload.get("event")
    # Nested event object (the WAHA log shows this)
    if isinstance(evt, dict):
        inner_type = evt.get("event")
        log_print(f"Detected nested event type: '{inner_type}'", level="DEBUG")

        if inner_type == "message_create":
            data = evt.get("data", [])
            msgs = []
            for d in data:
                # these items are usually already message dicts
                normalized = _normalize_last_message_obj(d)
                # If not normalized (empty), try more aggressive fallback:
                if not normalized["body"] and isinstance(d, dict):
                    normalized = _normalize_last_message_obj(d.get("message") or d.get("lastMessage") or d)
                msgs.append(normalized)
            return msgs

        if inner_type == "unread_count":
            data = evt.get("data", [])
            msgs = []
            for item in data:
                # WAHA sends item["lastMessage"] with inner "_data"
                lm = item.get("lastMessage") or item.get("last_message") or item.get("message")
                if not lm:
                    continue
                normalized = _normalize_last_message_obj(lm)
                msgs.append(normalized)
            return msgs

    # As a last resort, try to find any 'lastMessage' fields anywhere in the JSON
    found = []
    def _walk_for_last_message(obj):
        if isinstance(obj, dict):
            if "lastMessage" in obj:
                found.append(obj["lastMessage"])
            for v in obj.values():
                _walk_for_last_message(v)
        elif isinstance(obj, list):
            for el in obj:
                _walk_for_last_message(el)

    _walk_for_last_message(payload)
    if found:
        log_print(f"Found {len(found)} 'lastMessage' objects via fallback walk.", level="DEBUG")
        return [ _normalize_last_message_obj(lm) for lm in found ]

    log_print("No messages found in payload by extractor.", level="DEBUG")
    return []


def extract_text_from_payload(msg_obj):
    """Extracts caption or body text from a normalized message object."""
    if not isinstance(msg_obj, dict):
        return ""
    text = (msg_obj.get("body") or msg_obj.get("caption") or "")
    if isinstance(text, str):
        return text.strip()
    return ""


def extract_media_url(msg_obj):
    """Extracts a media URL from a normalized message object."""
    if not isinstance(msg_obj, dict):
        return None
    # prefer explicit fields
    for k in ("deprecatedMms3Url", "fileUrl"):
        url = msg_obj.get(k)
        if url:
            return url
    # fallback into raw
    raw = msg_obj.get("_raw") or {}
    return raw.get("fileUrl") or raw.get("mediaUrl") or raw.get("mmsUrl")


# --- Other Helper Functions --- (download_media, forward_receipt, etc. remain mostly the same)
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
        with open(path, "rb") as f:
            decrypted_data = decrypt_image(f.read())
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
        data = request.get_json(silent=True)
        if data is None:
            raw = request.data.decode(errors='ignore')
            log_print(f"Request JSON parse failed. Raw body: {raw[:1000]}", level="ERROR")
            return jsonify({"status": "error", "reason": "invalid_json"}), 400
    except Exception as e:
        log_print(f"Failed to parse request body as JSON. Error: {e}", level="ERROR")
        return jsonify({"status": "error"}), 400

    # log the incoming payload (shortened)
    try:
        pretty = json.dumps(data, indent=2) if isinstance(data, dict) else str(data)
        log_print(f"Incoming payload (truncated): {pretty[:2000]}", level="DEBUG")
    except Exception:
        log_print("Incoming payload (unable to stringify)", level="DEBUG")

    # 1. --- Use the improved extractor to find messages ---
    messages = extract_messages_from_payload(data)

    if not messages:
        log_print("Webhook did not contain any processable messages. Task complete.", level="DEBUG")
        return jsonify({"status": "ok", "message": "No messages to process"}), 200

    log_print(f"Successfully extracted {len(messages)} message(s) to process.", level="DEBUG")

    # 2. --- Process each extracted message ---
    for idx, msg in enumerate(messages):
        # normalized fields
        chat_id = msg.get("from")
        sender_id = msg.get("author")
        text = extract_text_from_payload(msg)
        media_url = extract_media_url(msg)

        # Print full per-message debug so you can see that WAHA's payload arrived
        log_print(f"Message[{idx}] -> group: {chat_id}, author: {sender_id}, text: '{text[:200]}', media: {'Yes' if media_url else 'No'}", level="DEBUG")

        # validate group and sender (only groups processed)
        if not chat_id or not isinstance(chat_id, str) or "@g.us" not in chat_id:
            log_print(f"Skipping non-group or malformed chat_id: {chat_id}", level="DEBUG")
            continue

        # --- RECC detection (case-insensitive, tolerant) ---
        customer_name = None
        if text:
            m = re.search(r'\brecc\s+(.+)', text, flags=re.IGNORECASE)
            if m:
                customer_name = m.group(1).strip()
                log_print(f"🧾 Detected 'recc' keyword. Extracted customer_name: '{customer_name}'", level="INFO")

        # 3. --- Process "recc <name>" messages ---
        if customer_name:
            try:
                image_to_process_url = media_url
                # if no media attached, try to find recent cached image for (group, author)
                if not image_to_process_url:
                    cache_key = (chat_id, sender_id)
                    cached = recent_image_cache.get(cache_key)
                    if cached:
                        age = (datetime.now() - cached["timestamp"]).total_seconds()
                        if age <= CACHE_EXPIRATION_SECONDS:
                            image_to_process_url = cached["url"]
                            log_print(f"🧠 Found recent image in cache for {cache_key} (age {age:.1f}s).", level="DEBUG")
                            # consume it
                            try:
                                del recent_image_cache[cache_key]
                            except KeyError:
                                pass
                        else:
                            # expired
                            log_print(f"Found cached image for {cache_key} but it expired (age {age:.1f}s).", level="DEBUG")
                            try:
                                del recent_image_cache[cache_key]
                            except KeyError:
                                pass

                if image_to_process_url:
                    image_data = download_media(image_to_process_url)
                    encrypted_data = encrypt_image(image_data)
                    filename = f"{uuid.uuid4().hex}.enc"
                    image_path = os.path.join(IMAGES_DIR, filename)
                    with open(image_path, "wb") as f:
                        f.write(encrypted_data)

                    receipt_id = store_receipt(customer_name, image_path, chat_id)
                    log_event("receipt_stored", {"receipt_id": receipt_id, "customer": customer_name})
                    log_print(f"Stored receipt {receipt_id} for '{customer_name}'.")
                else:
                    log_print(f"Received 'recc' for '{customer_name}' but no associated image found.", level="WARNING")
            except Exception as e:
                log_print(f"Error processing 'recc' message. Error: {e}", level="ERROR")
            # done with this message
            continue

        # 4. --- If message contains a media (image) -> cache it for potential later 'recc' command ---
        if media_url:
            cache_key = (chat_id, sender_id)
            recent_image_cache[cache_key] = {"url": media_url, "timestamp": datetime.now()}
            log_print(f"🖼️  Cached image from {cache_key}. Cache size: {len(recent_image_cache)}.", level="DEBUG")
            continue

        # 5. --- If plain text (not 'recc') -> try to match stored receipts ---
        if text:
            try:
                conn = get_db_connection()
                matched_id, score = find_match_in_db(text, conn)
                conn.close()
                if matched_id and score >= MATCH_THRESHOLD:
                    log_print(f"🎯 MATCH FOUND | Query: '{text}' | ReceiptID: {matched_id} | Score: {score}")
                    receipt = get_receipt_by_id(matched_id)
                    if receipt:
                        forward_receipt_to_telegram_and_mark(receipt)
                else:
                    log_print(f"🔎 No match for query: '{text}'. Score: {score if matched_id else 'N/A'}", level="DEBUG")
            except Exception as e:
                log_print(f"Error during match check for query: '{text}'. Error: {e}", level="ERROR")

    return jsonify({"status": "processed"}), 200


if __name__ == '__main__':
    # debug True here is fine during development; for production set debug=False
    app.run(host='0.0.0.0', port=5000, debug=True)
