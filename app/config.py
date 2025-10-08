import os
from dotenv import load_dotenv

load_dotenv()

EVOLUTION_API_URL = os.getenv("EVOLUTION_API_URL")
EVOLUTION_API_KEY = os.getenv("EVOLUTION_API_KEY")
TELEGRAM_API_ID = os.getenv("TELEGRAM_API_ID")
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_BOT_USERNAME = os.getenv("TELEGRAM_BOT_USERNAME")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")
FORWARD_TO_BOT_USERNAME = os.getenv("FORWARD_TO_BOT_USERNAME")
SERVER_UPLOAD_URL = os.getenv("SERVER_UPLOAD_URL")
ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
DB_PATH = "receipts.db"
MONITORED_GROUPS = ["group1@g.us", "group2@g.us"] # Add your WhatsApp group JIDs
# config.py (add)
IMAGES_DIR = os.getenv("IMAGES_DIR", "./app/images")
MATCH_THRESHOLD = int(os.getenv("MATCH_THRESHOLD", "80"))  # fuzzy match threshold (0-100)
