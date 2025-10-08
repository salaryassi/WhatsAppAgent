import json
import logging
from datetime import datetime

# Configure logger
logging.basicConfig(
    filename="telegram_log.txt",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

# Simulate the same function interface as your real Telegram functions

async def forward_to_bot(image_data, metadata):
    """
    Simulates sending image + metadata to Telegram by logging the event.
    """
    log_entry = {
        "event": "forward_to_bot",
        "status": "simulated",
        "image_data_type": str(type(image_data)),
        "metadata": metadata,
        "timestamp": datetime.utcnow().isoformat()
    }
    logging.info(json.dumps(log_entry, ensure_ascii=False, indent=2))
    print("[LOG] Forwarded image (simulated):", metadata.get("receipt_id"))


async def send_admin_notification(message):
    """
    Simulates sending admin notification via Telegram.
    """
    log_entry = {
        "event": "admin_notification",
        "status": "simulated",
        "message": message,
        "timestamp": datetime.utcnow().isoformat()
    }
    logging.info(json.dumps(log_entry, ensure_ascii=False, indent=2))
    print("[LOG] Admin notification (simulated):", message)
