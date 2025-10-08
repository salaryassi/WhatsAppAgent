"""
main.py - Flask webhook server

Responsibilities:
- Verify webhook secret
- Accept incoming webhook payloads from Evolution API (WAHA)
- Download media if provided, encrypt + store, and record in DB
- Enqueue forwarding to Telegram worker
- Provide health check and simple admin endpoints
"""

import os
import logging
from flask import Flask, request, jsonify
from werkzeug.utils import secure_filename

# Use relative imports since this is part of a package
from . import config, database, encryption, evolution_api, utils, telegram_worker

WEBHOOK_SECRET = config.WEBHOOK_SECRET
MONITORED_GROUPS = config.MONITORED_GROUPS
EVOLUTION_API_KEY = config.EVOLUTION_API_KEY

# Logging setup
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s")
logger = logging.getLogger("app.main")

# Ensure directories exist
os.makedirs("/app/images", exist_ok=True)
os.makedirs("/app/uploads", exist_ok=True)
os.makedirs("/app/logs", exist_ok=True)

database.setup_database()

app = Flask(__name__)

# Start Telegram worker (persistent pyrogram client)
telegram_worker.telegram_worker.start()


def save_and_encrypt_file(temp_path, dest_filename):
    """
    Read the temp file bytes, encrypt them if encryption is configured,
    and write to the images directory with dest_filename.
    Returns final path.
    """
    with open(temp_path, "rb") as f:
        raw = f.read()
    encrypted = encryption.encrypt_bytes(raw)
    final_path = os.path.join("/app/images", secure_filename(dest_filename))
    with open(final_path, "wb") as out:
        out.write(encrypted)
    logger.info("Saved encrypted file to %s", final_path)
    return final_path


@app.route("/")
def index():
    return "<h1>WhatsAppAgent running</h1>"


@app.route("/healthz")
def healthz():
    return jsonify({"status": "ok"}), 200


@app.route("/whatsapp_webhook", methods=["POST"])
def whatsapp_webhook():
    logger.info("Incoming webhook request")
    received_secret = request.headers.get("X-Webhook-Secret")
    if WEBHOOK_SECRET and received_secret != WEBHOOK_SECRET:
        logger.warning("Invalid webhook secret. Received: %s", received_secret)
        return jsonify({"status": "unauthorized"}), 401

    payload = request.get_json(silent=True) or {}
    logger.info("Webhook payload: %s", payload)

    chat_id = payload.get("chatId") or payload.get("chat_id") or payload.get("from")
    group_id = chat_id
    message_type = payload.get("type", "unknown")

    if MONITORED_GROUPS and group_id not in MONITORED_GROUPS:
        logger.info("Message from non-monitored group: %s. Ignoring.", group_id)
        return jsonify({"status": "ignored", "reason": "group not monitored"}), 200

    # Extract text
    text = payload.get("text") or payload.get("body") or payload.get("message", {}).get("text") or payload.get("message", {}).get("caption")
    candidate_name = payload.get("customer_name") or payload.get("senderName") or text

    media_info = payload.get("media") or payload.get("message", {}).get("media")

    saved_receipt_id = None
    saved_image_path = None

    try:
        if media_info and isinstance(media_info, dict):
            media_url = media_info.get("url") or media_info.get("mediaUrl") or media_info.get("fileUrl")
            if media_url:
                file_name = secure_filename(media_info.get("fileName") or f"{group_id}_{int(__import__('time').time())}.bin")
                tmp_path = os.path.join("/app/uploads", file_name + ".tmp")
                evolution_api.download_media(media_url, tmp_path)
                saved_image_path = save_and_encrypt_file(tmp_path, file_name)
                saved_receipt_id = database.store_receipt(candidate_name or "unknown", saved_image_path, group_id)
                database.log_event("receipt_saved", f"{saved_receipt_id}|{group_id}")
                logger.info("Processed media and stored as receipt %s", saved_receipt_id)
                caption = f"Receipt: {candidate_name}\nsource_group: {group_id}\nreceipt_id: {saved_receipt_id}"
                telegram_worker.telegram_worker.enqueue_document(saved_image_path, caption=caption)
                database.log_event("receipt_enqueued_forward", saved_receipt_id)
            else:
                logger.info("Media object present but no URL found: %s", media_info.keys())
        else:
            if candidate_name:
                match_id, score = utils.find_match_in_db(candidate_name)
                logger.info("Match attempt for '%s' -> %s (score=%s)", candidate_name, match_id, score)
                if match_id:
                    conn = database.get_db_connection()
                    row = conn.execute("SELECT image_path FROM receipts WHERE id = ?", (match_id,)).fetchone()
                    conn.close()
                    if row:
                        image_path = row["image_path"]
                        caption = f"Matched Receipt {match_id} for query '{candidate_name}' (score {score})"
                        telegram_worker.telegram_worker.enqueue_document(image_path, caption=caption)
                        database.mark_receipt_forwarded(match_id)
                        database.log_event("match_forwarded", f"{match_id}|{candidate_name}|{score}")

        return jsonify({"status": "ok"}), 200

    except Exception as exc:
        logger.exception("Error handling webhook: %s", exc)
        database.log_event("webhook_error", str(exc))
        telegram_worker.telegram_worker.enqueue_message(f"Webhook handler error: {exc}")
        return jsonify({"status": "error", "detail": str(exc)}), 500


if __name__ == "__main__":
    logger.info("Starting Flask development server (use gunicorn for production)")
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=False)
