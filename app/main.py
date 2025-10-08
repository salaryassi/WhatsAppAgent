# app/main.py

"""
Refactored Flask webhook receiver for WAHA (webjs) events.

Key improvements:
 - Robust normalization of WAHA payload shapes (top-level payload wrapper, nested _data, unread_count, message_create).
 - Correct handling of captions vs base64 media blobs: prefers human-readable top-level body/caption and avoids treating base64 image data as text.
 - Cache for recently received media per (group, sender) with expiration.
 - `recc <name>` detection tolerant to spacing/case and works when caption or text contains the keyword.
 - Print-based logging via `log_print` (timestamps + emoji levels) to be docker-friendly.

Drop this file into your project (replace current app/main.py). Restart the Flask service and test by sending images with captions like: "Recc salar yassi".
"""

import os
import io
import json
import uuid
import requests
import asyncio
import re
from datetime import datetime, timedelta
from urllib.parse import urlparse, urlunparse

from flask import Flask, request, jsonify
from fuzzywuzzy import fuzz

# Project modules (assumed present in your package)
from .config import EVOLUTION_API_KEY, IMAGES_DIR, MATCH_THRESHOLD
from .database import (
    get_db_connection,
    setup_database,
    store_receipt,
    get_receipt_by_id,
    mark_receipt_forwarded,
    log_event,
)
from .encryption import encrypt_image, decrypt_image
from .telegram_bot import forward_to_bot
from .utils import find_match_in_db


# ----------------------
# Configuration / Cache
# ----------------------
recent_image_cache = {}  # {(group_id, sender_id): {"url": ..., "timestamp": datetime}}
CACHE_EXPIRATION_SECONDS = 120

WAHA_MEDIA_HOST_OVERRIDE = os.environ.get("WAHA_MEDIA_HOST_OVERRIDE", "").strip()

def _rewrite_media_url_for_container(url: str) -> str:
    """
    Safely rewrite WAHA 'localhost' URLs to something reachable by this container.

    - If WAHA_MEDIA_HOST_OVERRIDE is set it may be:
        * "waha:3000"     -> internal compose host:port
        * "46.20.111.31:8080" -> external host:port
        * "waha" or "46.20.111.31" -> host only (we will preserve original port if present)
    - If no override, fall back to 'host.docker.internal' replacement where possible.
    """
    if not url:
        return url

    parsed = urlparse(url)
    hostname = parsed.hostname
    port = parsed.port

    if hostname not in ("localhost", "127.0.0.1"):
        return url  # nothing to rewrite

    # decide new netloc
    if WAHA_MEDIA_HOST_OVERRIDE:
        # if override includes a colon, assume "host:port"
        if ":" in WAHA_MEDIA_HOST_OVERRIDE:
            new_host, new_port = WAHA_MEDIA_HOST_OVERRIDE.split(":", 1)
            new_netloc = f"{new_host}:{new_port}"
        else:
            # override only host — preserve original port if present
            if port:
                new_netloc = f"{WAHA_MEDIA_HOST_OVERRIDE}:{port}"
            else:
                new_netloc = WAHA_MEDIA_HOST_OVERRIDE
    else:
        # fallback: try docker host gateway name (works on many hosts with extra_hosts)
        if port:
            new_netloc = f"host.docker.internal:{port}"
        else:
            new_netloc = "host.docker.internal"

    new_parsed = parsed._replace(netloc=new_netloc)
    rewritten = urlunparse(new_parsed)
    return rewritten

def log_print(message, level="INFO"):
    """Print-based logger with timestamp and emoji level markers.

    Use this to ensure logs flush immediately in containerized environments.
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    level_map = {"ERROR": "❌ ERROR", "WARNING": "⚠️ WARNING", "INFO": "✅ INFO", "DEBUG": "🐞 DEBUG"}
    prefix = level_map.get(level, "✅ INFO")
    print(f"[{timestamp}] [{prefix}] {message}", flush=True)


# ----------------------
# Startup / ensure directories
# ----------------------
with app.app_context():
    setup_database()
    os.makedirs(IMAGES_DIR, exist_ok=True)
    log_print("Database and image directory initialized.")


# ----------------------
# Normalization helpers
# ----------------------

def _looks_like_base64_image(s: str) -> bool:
    """Heuristic to detect base64-encoded images (avoid treating them as captions).

    - Many JPEG blobs start with '/9j/'.
    - Very long strings composed only of base64 characters are likely base64 content.
    """
    if not isinstance(s, str) or not s:
        return False
    s = s.strip()
    if s.startswith("/9j/"):
        return True
    # long and base64 charset only
    if len(s) > 200 and re.fullmatch(r"[A-Za-z0-9+/=\s\r\n]+", s):
        return True
    return False


def _normalize_last_message_obj(lm):
    """Flatten WAHA/webjs message objects and pick the best human-readable body.

    The incoming object shapes vary: WAHA sometimes places readable text on the top-level
    `payload.body` while the raw `_data.body` may contain base64. This function prefers
    human-readable candidates and only falls back to base64 if nothing else is available.

    Returns a dict with these keys:
      - body: the chosen human text (or empty string)
      - from: group jid or sender jid
      - author: author id
      - hasMedia: boolean
      - deprecatedMms3Url / fileUrl: potential media URL locations (or None)
      - _raw: raw data dict used
    """
    if not isinstance(lm, dict):
        return {}

    # Top-level candidates (WAHA often fills these)
    top_body = lm.get("body")
    top_from = lm.get("from") or lm.get("remote")
    top_author = lm.get("author") or lm.get("participant")

    # _data often holds a nested dict with similar fields
    lm_data = lm.get("_data") if isinstance(lm.get("_data"), dict) else lm

    # Candidate text fields in order of preference
    candidates = [
        top_body,
        lm_data.get("body"),
        lm_data.get("caption"),
        lm.get("caption"),
        lm.get("text"),
        lm_data.get("text"),
    ]

    # Choose the first candidate that looks like normal text (not base64)
    chosen_body = ""
    for c in candidates:
        if not c:
            continue
        if isinstance(c, str) and not _looks_like_base64_image(c):
            chosen_body = c.strip()
            break

    # Fallback: take the shortest non-empty candidate (helps when caption is base64 but top_body missing)
    if not chosen_body:
        for c in candidates:
            if isinstance(c, str) and c.strip():
                chosen_body = c.strip()
                break

    # Media detection: WAHA may put media info under media or under _data.* fields
    media = lm.get("media") or lm_data.get("media") or {}
    media_url = None
    if isinstance(media, dict):
        media_url = media.get("url") or media.get("fileUrl") or media.get("mmsUrl")

    # Check other common fields for media URLs
    media_url = media_url or lm_data.get("deprecatedMms3Url") or lm_data.get("fileUrl") or lm_data.get("mediaUrl") or lm.get("mediaUrl")

    normalized = {
        "body": chosen_body,
        "from": top_from or lm_data.get("from") or lm_data.get("remote"),
        "author": (top_author if isinstance(top_author, str) else (lm_data.get("author") or None)),
        "hasMedia": bool(lm.get("hasMedia") or lm_data.get("hasMedia") or media_url),
        "deprecatedMms3Url": lm_data.get("deprecatedMms3Url"),
        "fileUrl": media_url,
        "_raw": lm_data,
    }
    return normalized


def extract_messages_from_payload(payload):
    """Extract a list of normalized message dicts from various WAHA webhook shapes.

    Handles the following patterns seen in WAHA / webjs:
      - Top-level event == 'message' with actual message under `payload` (WAHA wrapper)
      - event == { event: 'message_create', data: [...] }
      - event == { event: 'unread_count', data: [ { lastMessage: {...} }, ... ] }
      - Top-level `messages` array
      - Fallback: walk the JSON to find any `lastMessage` keys

    Each returned item is run through `_normalize_last_message_obj`.
    """
    try:
        if isinstance(payload, str):
            payload = json.loads(payload)
    except Exception:
        # leave payload as-is if parsing fails
        pass

    # New: WAHA often uses { event: 'message', payload: { ... } }
    if payload.get("event") == "message":
        inner = payload.get("payload")
        if isinstance(inner, dict):
            log_print("Top-level event=='message' with inner 'payload' — normalizing inner payload.", level="DEBUG")
            return [_normalize_last_message_obj(inner)]
        log_print("Detected top-level 'message' event (no inner 'payload').", level="DEBUG")
        return [_normalize_last_message_obj(payload)]

    # If there's an explicit messages array
    if isinstance(payload.get("messages"), list):
        log_print("Detected 'messages' array at top-level.", level="DEBUG")
        return [_normalize_last_message_obj(m) for m in payload.get("messages") if isinstance(m, dict)]

    evt = payload.get("event")
    # Nested event object e.g. event: { event: 'unread_count', data: [...] }
    if isinstance(evt, dict):
        inner_type = evt.get("event")
        log_print(f"Detected nested event type: '{inner_type}'", level="DEBUG")

        if inner_type == "message_create":
            data = evt.get("data", [])
            msgs = []
            for d in data:
                normalized = _normalize_last_message_obj(d)
                if not normalized.get("body") and isinstance(d, dict):
                    # try common nested locations
                    normalized = _normalize_last_message_obj(d.get("message") or d.get("lastMessage") or d)
                msgs.append(normalized)
            return msgs

        if inner_type == "unread_count":
            data = evt.get("data", [])
            msgs = []
            for item in data:
                lm = item.get("lastMessage") or item.get("last_message") or item.get("message")
                if not lm:
                    continue
                normalized = _normalize_last_message_obj(lm)
                msgs.append(normalized)
            return msgs

    # Fallback: walk the JSON to find lastMessage keys anywhere
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
        return [_normalize_last_message_obj(lm) for lm in found]

    log_print("No messages found in payload by extractor.", level="DEBUG")
    return []


# ----------------------
# Media download / forward helpers
# ----------------------

def download_media(url):
    """Download media using configured API key (if present)."""
    if not url:
        raise ValueError("No media URL provided")

    url_for_request = _rewrite_media_url_for_container(url)
    headers = {"apikey": EVOLUTION_API_KEY} if EVOLUTION_API_KEY else {}
    log_print(f"Downloading media from {url_for_request} (original: {url})", level="DEBUG")
    response = requests.get(url_for_request, headers=headers, timeout=30)
    response.raise_for_status()
    return response.content



def forward_receipt_to_telegram_and_mark(receipt_row):
    """Decrypt stored image and forward to telegram, then mark as forwarded in DB."""
    path = receipt_row["image_path"]
    if not path or not os.path.exists(path):
        log_print(f"Receipt file missing: {path} for receipt ID {receipt_row['id']}", level="ERROR")
        return False
    try:
        with open(path, "rb") as f:
            decrypted_data = decrypt_image(f.read())
        image_stream = io.BytesIO(decrypted_data)
        metadata = {
            "receipt_id": receipt_row["id"],
            "customer_name": receipt_row["customer_name"],
            "source_group": receipt_row["source_group"],
            "timestamp": receipt_row["timestamp"],
        }
        asyncio.run(forward_to_bot(image_stream, metadata))
        mark_receipt_forwarded(receipt_row["id"])
        log_print(f"Forwarded receipt {receipt_row['id']} to Telegram.")
        return True
    except Exception as e:
        log_print(f"Failed forwarding receipt {receipt_row['id']} to Telegram. Error: {e}", level="ERROR")
        return False


# ----------------------
# Flask Routes
# ----------------------

@app.route("/")
def index():
    return "<h1>WhatsApp Agent is Running!</h1>"


@app.route("/whatsapp_webhook", methods=["POST"])
def webhook():
    log_print("--- 📨 Endpoint Hit: /whatsapp_webhook ---")

    try:
        data = request.get_json(silent=True)
        if data is None:
            raw = request.data.decode(errors="ignore")
            log_print(f"Request JSON parse failed. Raw body: {raw[:1000]}", level="ERROR")
            return jsonify({"status": "error", "reason": "invalid_json"}), 400
    except Exception as e:
        log_print(f"Failed to parse request body as JSON. Error: {e}", level="ERROR")
        return jsonify({"status": "error"}), 400

    # Helpful debug: show a truncated representation of the payload
    try:
        pretty = json.dumps(data, indent=2) if isinstance(data, dict) else str(data)
        log_print(f"Incoming payload (truncated): {pretty[:2000]}", level="DEBUG")
    except Exception:
        log_print("Incoming payload (unable to stringify)", level="DEBUG")

    # Extract normalized messages
    messages = extract_messages_from_payload(data)

    if not messages:
        log_print("Webhook did not contain any processable messages. Task complete.", level="DEBUG")
        return jsonify({"status": "ok", "message": "No messages to process"}), 200

    log_print(f"Successfully extracted {len(messages)} message(s) to process.", level="DEBUG")

    for idx, msg in enumerate(messages):
        chat_id = msg.get("from")
        sender_id = msg.get("author")
        text = msg.get("body") or ""
        media_url = msg.get("fileUrl") or msg.get("deprecatedMms3Url")

        log_print(f"Message[{idx}] -> group: {chat_id}, author: {sender_id}, text: '{(text or '')[:200]}', media: {'Yes' if media_url else 'No'}", level="DEBUG")

        # Ensure we only process group messages (those containing @g.us)
        if not chat_id or not isinstance(chat_id, str) or "@g.us" not in chat_id:
            log_print(f"Skipping non-group or malformed chat_id: {chat_id}", level="DEBUG")
            continue

        # Detect 'recc <name>' in text or caption (case-insensitive)
        customer_name = None
        if text:
            m = re.search(r"\brecc\s+(.+)", text, flags=re.IGNORECASE)
            if m:
                customer_name = m.group(1).strip()
                log_print(f"🧾 Detected 'recc' keyword. Extracted customer_name: '{customer_name}'", level="INFO")

        # If 'recc' found -> store image (attached or from cache)
        if customer_name:
            try:
                image_to_process_url = media_url

                # If no media directly attached, try cache for (group, sender)
                if not image_to_process_url:
                    cache_key = (chat_id, sender_id)
                    cached = recent_image_cache.get(cache_key)
                    if cached:
                        age = (datetime.now() - cached["timestamp"]).total_seconds()
                        if age <= CACHE_EXPIRATION_SECONDS:
                            image_to_process_url = cached["url"]
                            log_print(f"🧠 Found recent image in cache for {cache_key} (age {age:.1f}s).", level="DEBUG")
                            try:
                                del recent_image_cache[cache_key]
                            except KeyError:
                                pass
                        else:
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
            continue

        # If the message has media (but wasn't a 'recc'), cache it for potential subsequent 'recc' command
        if media_url:
            cache_key = (chat_id, sender_id)
            recent_image_cache[cache_key] = {"url": media_url, "timestamp": datetime.now()}
            log_print(f"🖼️  Cached image from {cache_key}. Cache size: {len(recent_image_cache)}.", level="DEBUG")
            continue

        # Otherwise, plain text: try to match an existing stored receipt and forward
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
    # For local development you can keep debug=True; for production set debug=False
    app.run(host='0.0.0.0', port=5000, debug=True)
