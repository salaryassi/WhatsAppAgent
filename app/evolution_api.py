import requests
import logging
from .config import EVOLUTION_API_URL, EVOLUTION_API_KEY

logger = logging.getLogger("app.evolution_api")

HEADERS = {"apikey": EVOLUTION_API_KEY} if EVOLUTION_API_KEY else {}

def send_whatsapp_message(group_jid: str, message: str, delay: int = 1200):
    """
    Send a text message back to the given group via Evolution API (WAHA).
    """
    if not EVOLUTION_API_URL:
        raise RuntimeError("EVOLUTION_API_URL not configured")

    url = f"{EVOLUTION_API_URL.rstrip('/')}/api/messages/send"
    payload = {
        "number": group_jid,
        "options": {"delay": delay},
        "textMessage": {"text": message}
    }
    try:
        logger.info("Sending message to %s via Evolution API", group_jid)
        r = requests.post(url, json=payload, headers=HEADERS, timeout=10)
        r.raise_for_status()
        logger.debug("Evolution API response: %s", r.text)
        return r.json()
    except Exception as exc:
        logger.exception("Failed to send whatsapp message: %s", exc)
        raise

def download_media(media_url: str, save_to: str, stream=True, timeout=30):
    """
    Download media from a given media_url (often provided by WAHA).
    Adds api key header if needed.
    Returns path to saved file.
    """
    if not media_url:
        raise ValueError("media_url required")
    try:
        logger.info("Downloading media from %s", media_url)
        with requests.get(media_url, headers=HEADERS, stream=stream, timeout=timeout) as r:
            r.raise_for_status()
            with open(save_to, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
        logger.info("Saved media to %s", save_to)
        return save_to
    except Exception:
        logger.exception("Failed downloading media")
        raise
