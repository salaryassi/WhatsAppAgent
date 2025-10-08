# app/main.py
from flask import Flask, request, jsonify
import os
import logging
import requests
import io
import asyncio
import json
from .config import WEBHOOK_SECRET, EVOLUTION_API_KEY, IMAGES_DIR, MATCH_THRESHOLD
from .database import setup_database, store_receipt, log_query, get_receipt_by_id, mark_receipt_forwarded, log_event
from .encryption import encrypt_image, decrypt_image
from .telegram_bot import forward_to_bot, send_admin_notification
from .utils import find_match_in_db
from fuzzywuzzy import fuzz

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
app = Flask(__name__)

# Ensure DB and images dir exist
setup_database()
os.makedirs(IMAGES_DIR, exist_ok=True)

def extract_text_from_payload(data):
    """
    Flexible extractor — tries to find text/caption inside various possible fields of the Evolution webhook payload.
    """
    # Common fields tried in order:
    candidates = [
        data.get("caption"),
        data.get("text"),
        # nested message structures some webhooks use:
        (data.get("message") or {}).get("caption") if isinstance(data.get("message"), dict) else None,
        (data.get("message") or {}).get("text") if isinstance(data.get("message"), dict) else None,
        data.get("body"),
        data.get("message", {}).get("body") if isinstance(data.get("message"), dict) else None
    ]
    for c in candidates:
        if c:
            return c
    # if the webhook includes "chat" etc:
    if isinstance(data, dict):
        for v in data.values():
            if isinstance(v, str) and len(v) < 200:  # naive guess
                return v
    return None

def extract_media_url(data):
    """
    Try to find a direct media URL in the incoming payload. Evolution-like webhooks sometimes include "fileUrl", "mediaUrl" or nested structures.
    """
    keys = ["fileUrl", "mediaUrl", "url", "downloadUrl", "imageUrl"]
    for k in keys:
        if k in data and data[k]:
            return data[k]
    # nested options
    msg = data.get("message") or {}
    if isinstance(msg, dict):
        for k in keys:
            if k in msg and msg[k]:
                return msg[k]
        # sometimes attachments list
        attachments = msg.get("attachments") or msg.get("media") or []
        if isinstance(attachments, list) and attachments:
            first = attachments[0]
            for k in keys:
                if k in first:
                    return first[k]
            # maybe object with 'url'
            if isinstance(first, dict) and first.get("url"):
                return first.get("url")
    return None

def download_media(url):
    """
    Download bytes from a media URL. Use EVOLUTION_API_KEY if required.
    """
    headers = {}
    if EVOLUTION_API_KEY:
        headers["apikey"] = EVOLUTION_API_KEY
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.content

def send_verification_message_to_group(group_jid, customer_name):
    # Use your evolution_api.send_whatsapp_message if desired.
    from .evolution_api import send_whatsapp_message
    message = f"Verification: we detected a query for *{customer_name}*. Please confirm by replying 'YES' or provide more details."
    try:
        send_whatsapp_message(group_jid, message)
        log_event("sent_verification", {"group": group_jid, "customer_name": customer_name})
    except Exception as e:
        logging.exception("Failed to send verification message")
        log_event("send_verification_error", {"group": group_jid, "error": str(e)})

def forward_receipt_to_telegram_and_mark(receipt_row):
    """
    Read encrypted file, decrypt, forward to telegram bot, mark DB forwarded.
    This is synchronous wrapper that uses asyncio.run to call telegram async helper.
    """
    import os
    path = receipt_row["image_path"]
    if not path or not os.path.exists(path):
        logging.error("Receipt file missing: %s", path)
        log_event("missing_file", {"receipt_id": receipt_row["id"], "path": path})
        return False

    with open(path, "rb") as f:
        encrypted_data = f.read()
    try:
        decrypted = decrypt_image(encrypted_data)
    except Exception as e:
        logging.exception("decrypt failed")
        log_event("decrypt_error", {"receipt_id": receipt_row["id"], "error": str(e)})
        return False

    # prepare metadata
    metadata = {
        "receipt_id": receipt_row["id"],
        "customer_name": receipt_row["customer_name"],
        "source_group": receipt_row["source_group"],
        "timestamp": receipt_row["timestamp"]
    }

    # create bytesio
    bio = io.BytesIO(decrypted)
    bio.seek(0)

    try:
        # forward to telegram (pyrogram async)
        asyncio.run(forward_to_bot(bio, metadata))
        mark_receipt_forwarded(receipt_row["id"])
        log_event("forwarded_to_telegram", metadata)
        return True
    except Exception as e:
        logging.exception("telegram forward failed")
        log_event("telegram_forward_error", {"receipt_id": receipt_row["id"], "error": str(e)})
        return False

@app.route('/')
def index():
    return "<h1>Flask App is Running!</h1>"

@app.route('/whatsapp_webhook', methods=['POST'])
def webhook():
    logging.info("--- Webhook Connection Attempt Received ---")
    received_secret = request.headers.get('X-Webhook-Secret')
    if received_secret != WEBHOOK_SECRET:
        logging.error(f"Unauthorized webhook secret: {received_secret}")
        return jsonify({"status": "unauthorized"}), 401

    data = request.json or {}
    logging.info("Webhook payload: %s", json.dumps(data)[:2000])

    # Get common fields
    chat_id = data.get("chatId") or data.get("chat_id") or (data.get("message") or {}).get("chatId")
    source_group = chat_id or data.get("from") or data.get("sender") or "unknown_group"

    # 1) If there is media -> treat as a receipt
    media_url = extract_media_url(data)
    caption = extract_text_from_payload(data)
    if media_url:
        # If caption does not exist we attempt to parse customer name from nearby fields
        customer_name = caption or data.get("caption") or data.get("body") or "unknown"

        try:
            media_bytes = download_media(media_url)
        except Exception as e:
            logging.exception("media download failed")
            return jsonify({"status": "error", "reason": "media download failed"}), 500

        # encrypt and save
        rid = uuid.uuid4().hex
        file_path = os.path.join(IMAGES_DIR, f"{rid}.enc")
        try:
            enc = encrypt_image(media_bytes)
            with open(file_path, "wb") as out:
                out.write(enc)
        except Exception as e:
            logging.exception("encrypt/save failed")
            return jsonify({"status": "error", "reason": "save failed"}), 500

        # store into DB
        stored_id = store_receipt(customer_name, file_path, source_group)
        log_event("stored_receipt", {"receipt_id": stored_id, "customer_name": customer_name, "source_group": source_group})
        # Optionally notify admin
        try:
            asyncio.run(send_admin_notification(f"New receipt stored: {customer_name} (id: {stored_id}) from {source_group}"))
        except Exception:
            logging.exception("admin notify failed")
        return jsonify({"status": "ok", "receipt_id": stored_id}), 200

    # 2) If plain text -> treat as a query and try to match
    text = extract_text_from_payload(data)
    if text:
        customer_name_query = text.strip()
        log_query(customer_name_query, source_group, matched_receipt_id=None, status="received")

        # Build in-memory search over receipts: we reuse function find_match_in_db which matches against unforwarded receipts
        # We'll re-implement a simple search here to make sure we correctly compare to DB rows
        from .database import get_unforwarded_receipts
        receipts = get_unforwarded_receipts()
        best_score = 0
        best_receipt = None
        for r in receipts:
            rname = r["customer_name"] or ""
            score = fuzz.token_sort_ratio(customer_name_query, rname)
            if score > best_score:
                best_score = score
                best_receipt = r

        # if found high enough => verify and forward
        if best_receipt and best_score >= MATCH_THRESHOLD:
            # notify the querying group asking them to confirm
            send_verification_message_to_group(source_group, customer_name_query)
            log_query(customer_name_query, source_group, matched_receipt_id=best_receipt["id"], status="matched")
            # forward the image to telegram bot
            forwarded = forward_receipt_to_telegram_and_mark(best_receipt)
            if forwarded:
                return jsonify({"status": "matched_forwarded", "receipt_id": best_receipt["id"], "score": best_score}), 200
            else:
                return jsonify({"status": "matched_but_forward_failed", "receipt_id": best_receipt["id"], "score": best_score}), 500
        else:
            log_query(customer_name_query, source_group, matched_receipt_id=None, status="no_match")
            return jsonify({"status": "no_match", "best_score": best_score}), 200

    return jsonify({"status": "ignored"}), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
