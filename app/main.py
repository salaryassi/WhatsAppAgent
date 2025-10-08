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

from .config import WEBHOOK_SECRET, EVOLUTION_API_KEY, IMAGES_DIR, MATCH_THRESHOLD
from .database import (
    setup_database, store_receipt, log_query,
    get_receipt_by_id, mark_receipt_forwarded, log_event,
    get_unforwarded_receipts
)
from .encryption import encrypt_image, decrypt_image
from .telegram_bot import forward_to_bot, send_admin_notification
from .utils import find_match_in_db

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

    try:
        data = request.get_json(force=True)
    except Exception as e:
        logging.error(f"❌ Failed to parse JSON body: {e}")
        return jsonify({"status": "invalid_json"}), 400

    messages = data.get("messages", [])
    if not messages:
        logging.warning("⚠️ No messages found in webhook payload")
        return jsonify({"status": "no_messages"}), 200

    for msg in messages:
        chat_id = msg.get("chatId", "")
        sender_name = msg.get("senderName", "").strip()
        text = msg.get("body", "").strip()

        logging.info(f"📨 Processing message from '{sender_name}' in '{chat_id}' — text: '{text}'")

        # --- Only process group messages ---
        if not chat_id.endswith("@g.us"):
            logging.info(f"➡️ Ignored message (not a group): {chat_id}")
            continue

        # --- Log query ---
        try:
            log_query(
                customer_name=sender_name,
                query_group=chat_id,
                matched_receipt_id=None,
                status="received"
            )
            logging.info(f"✅ Logged message from '{sender_name}' in group '{chat_id}'")
        except Exception as e:
            logging.error(f"❌ Error logging message for '{sender_name}' in '{chat_id}': {e}")
            continue

        # --- Match check ---
        try:
            conn = get_db_connection()
            matched_receipt_id, best_score = find_match_in_db(sender_name, conn)
            conn.close()

            if matched_receipt_id:
                logging.info(
                    f"🎯 MATCH FOUND | Sender: '{sender_name}' | Group: '{chat_id}' | ReceiptID: {matched_receipt_id} | Score: {best_score}"
                )
                # TODO: Add Telegram forward here if needed
            else:
                logging.info(f"🔎 No match found for '{sender_name}' in '{chat_id}'")
        except Exception as e:
            logging.error(f"❌ Error checking match for '{sender_name}' in '{chat_id}': {e}")

    logging.info("✅ Finished processing webhook batch")
    return jsonify({"status": "processed"}), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
