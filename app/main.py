# app/main.py
from flask import Flask, request, jsonify
import os
import logging
import requests
import io
import asyncio
import json
import uuid
from fuzzywuzzy import fuzz
from datetime import datetime, timedelta # Add this import


from .config import WEBHOOK_SECRET, EVOLUTION_API_KEY, IMAGES_DIR, MATCH_THRESHOLD
from .database import (
    setup_database, store_receipt, log_query,
    get_receipt_by_id, mark_receipt_forwarded, log_event,
    get_unforwarded_receipts
)
from .encryption import encrypt_image, decrypt_image
from .telegram_bot import forward_to_bot, send_admin_notification
from .utils import find_match_in_db

recent_image_cache = {}
CACHE_EXPIRATION_SECONDS = 120
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

app = Flask(__name__)

# Ensure DB and images dir exist
setup_database()
os.makedirs(IMAGES_DIR, exist_ok=True)


def extract_text_from_payload(data):
    """Try various locations for text in the webhook payload."""
    candidates = [
        data.get("caption"),
        data.get("text"),
        (data.get("message") or {}).get("caption") if isinstance(data.get("message"), dict) else None,
        (data.get("message") or {}).get("text") if isinstance(data.get("message"), dict) else None,
        data.get("body"),
        data.get("message", {}).get("body") if isinstance(data.get("message"), dict) else None
    ]
    for c in candidates:
        if c:
            return c
    # fallback: scan values
    for v in data.values():
        if isinstance(v, str) and len(v) < 200:
            return v
    return None


def extract_media_url(data):
    """Find a media URL in the payload."""
    keys = ["fileUrl", "mediaUrl", "url", "downloadUrl", "imageUrl"]
    msg = data.get("message") or {}
    # check top-level
    for k in keys:
        if data.get(k):
            return data[k]
    # check nested
    if isinstance(msg, dict):
        for k in keys:
            if msg.get(k):
                return msg[k]
        # check attachments/media
        attachments = msg.get("attachments") or msg.get("media") or []
        if attachments:
            first = attachments[0]
            if isinstance(first, dict):
                for k in keys:
                    if first.get(k):
                        return first[k]
    return None


def download_media(url):
    """Download bytes from a media URL, optionally using API key."""
    headers = {"apikey": EVOLUTION_API_KEY} if EVOLUTION_API_KEY else {}
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.content


def send_verification_message_to_group(group_jid, customer_name):
    from .evolution_api import send_whatsapp_message
    message = f"Verification: we detected a query for *{customer_name}*. Please confirm by replying 'YES'."
    try:
        send_whatsapp_message(group_jid, message)
        log_event("sent_verification", {"group": group_jid, "customer_name": customer_name})
    except Exception as e:
        logging.exception("Failed to send verification message")
        log_event("send_verification_error", {"group": group_jid, "error": str(e)})


def forward_receipt_to_telegram_and_mark(receipt_row):
    """Decrypt and forward a receipt to Telegram, mark as forwarded."""
    path = receipt_row["image_path"]
    if not path or not os.path.exists(path):
        logging.error("Receipt file missing: %s", path)
        log_event("missing_file", {"receipt_id": receipt_row["id"], "path": path})
        return False
    try:
        with open(path, "rb") as f:
            decrypted = decrypt_image(f.read())
        bio = io.BytesIO(decrypted)
        bio.seek(0)
        metadata = {
            "receipt_id": receipt_row["id"],
            "customer_name": receipt_row["customer_name"],
            "source_group": receipt_row["source_group"],
            "timestamp": receipt_row["timestamp"]
        }
        asyncio.run(forward_to_bot(bio, metadata))
        mark_receipt_forwarded(receipt_row["id"])
        log_event("forwarded_to_telegram", metadata)
        return True
    except Exception as e:
        logging.exception("Failed forwarding to Telegram")
        log_event("telegram_forward_error", {"receipt_id": receipt_row["id"], "error": str(e)})
        return False


@app.route('/')
def index():
    return "<h1>Flask App is Running!</h1>"
# Configure logger
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

@app.route('/whatsapp_webhook', methods=['POST'], endpoint='whatsapp_webhook')
def webhook():
    logging.info("📩 Incoming webhook request received")
    data = request.get_json(force=True)

    # Clean up expired entries from the cache
    expired_keys = [
        key for key, value in recent_image_cache.items()
        if datetime.now() - value['timestamp'] > timedelta(seconds=CACHE_EXPIRATION_SECONDS)
    ]
    for key in expired_keys:
        del recent_image_cache[key]
        logging.info(f"🧹 Expired image from cache for key: {key}")


    messages = data.get("messages", [])
    if not messages:
        logging.warning("⚠️ No messages found in webhook payload")
        return jsonify({"status": "no_messages"}), 200

    for msg in messages:
        chat_id = msg.get("chatId", "")
        # IMPORTANT: You need the sender's unique ID, not just display name
        sender_id = msg.get("author") or msg.get("sender", {}).get("id") or msg.get("from")
        text = extract_text_from_payload(msg) or ""
        media_url = extract_media_url(msg)

        if not chat_id.endswith("@g.us") or not sender_id:
            logging.info("➡️ Ignoring message (not a group or no sender ID)")
            continue

        logging.info(f"📨 Processing message from '{sender_id}' in '{chat_id}'")

        # --- LOGIC FOR "RECC" KEYWORD ---
        if "recc " in text.lower():
            try:
                # Extract name which comes after "recc "
                customer_name = text.lower().split("recc ", 1)[1].strip()
                logging.info(f"🧾 Found 'recc' keyword for customer: '{customer_name}'")

                image_to_process_url = None

                # Case 1: Image is sent with "recc" in the caption
                if media_url:
                    logging.info("✅ Found image in the same message as 'recc'.")
                    image_to_process_url = media_url
                
                # Case 2: "recc" is in a text message, look for a recent image
                else:
                    cache_key = (chat_id, sender_id)
                    if cache_key in recent_image_cache:
                        logging.info(f"🧠 Found recent image in cache for {cache_key}.")
                        image_to_process_url = recent_image_cache[cache_key]['url']
                        del recent_image_cache[cache_key] # Remove after use

                if image_to_process_url:
                    # Download, encrypt, and store the receipt
                    image_data = download_media(image_to_process_url)
                    encrypted_data = encrypt_image(image_data)
                    
                    filename = f"{uuid.uuid4().hex}.enc"
                    image_path = os.path.join(IMAGES_DIR, filename)
                    
                    with open(image_path, "wb") as f:
                        f.write(encrypted_data)
                    
                    receipt_id = store_receipt(customer_name, image_path, chat_id)
                    log_event("receipt_stored", {"receipt_id": receipt_id, "customer": customer_name})
                    logging.info(f"✅ Stored receipt {receipt_id} for '{customer_name}' from group '{chat_id}'")
                else:
                    logging.warning(f"⚠️ Received 'recc' for '{customer_name}' but could not find an associated image.")

            except Exception as e:
                logging.error(f"❌ Error processing 'recc' message: {e}")

        # --- LOGIC FOR CACHING AN IMAGE ---
        elif media_url:
            cache_key = (chat_id, sender_id)
            recent_image_cache[cache_key] = {
                "url": media_url,
                "timestamp": datetime.now()
            }
            logging.info(f"🖼️ Cached image from {cache_key}. Cache size is now {len(recent_image_cache)}.")

        # --- LOGIC FOR MATCHING A NAME (NO KEYWORD, NO IMAGE) ---
        else:
            try:
                # Use sender's name from text as the customer name to query
                customer_name_query = text.strip()
                if not customer_name_query:
                    continue

                conn = get_db_connection()
                matched_receipt_id, best_score = find_match_in_db(customer_name_query, conn)
                conn.close()

                if matched_receipt_id and best_score >= MATCH_THRESHOLD:
                    logging.info(f"🎯 MATCH FOUND | Query: '{customer_name_query}' | Group: '{chat_id}' | ReceiptID: {matched_receipt_id} | Score: {best_score}")
                    # Here you would trigger the verification message and forwarding flow
                    receipt_row = get_receipt_by_id(matched_receipt_id)
                    if receipt_row:
                       forward_receipt_to_telegram_and_mark(receipt_row)

                else:
                    logging.info(f"🔎 No match found for query: '{customer_name_query}' in '{chat_id}'")
            except Exception as e:
                logging.error(f"❌ Error during match check for '{text}': {e}")


    return jsonify({"status": "processed"}), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
