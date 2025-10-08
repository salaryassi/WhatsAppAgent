import os
from dotenv import load_dotenv

load_dotenv()

# Application configuration read from environment
EVOLUTION_API_URL = os.getenv("EVOLUTION_API_URL")
EVOLUTION_API_KEY = os.getenv("EVOLUTION_API_KEY")
TELEGRAM_API_ID = int(os.getenv("TELEGRAM_API_ID")) if os.getenv("TELEGRAM_API_ID") else None
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_BOT_USERNAME = os.getenv("TELEGRAM_BOT_USERNAME")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")
FORWARD_TO_BOT_USERNAME = os.getenv("FORWARD_TO_BOT_USERNAME")
SERVER_UPLOAD_URL = os.getenv("SERVER_UPLOAD_URL")
ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")


# parse MONITORED_GROUPS as list
_MONITORED_GROUPS_RAW = os.getenv("MONITORED_GROUPS", "")
MONITORED_GROUPS = [g.strip() for g in _MONITORED_GROUPS_RAW.split(",") if g.strip()]
import os
from dotenv import load_dotenv

DB_DIR = "/app/db"
os.makedirs(DB_DIR, exist_ok=True)
DB_PATH = os.path.join(DB_DIR, "receipts.db")

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
MONITORED_GROUPS = ["120363403036388430@g.us", "group2@g.us"] # Add your WhatsApp group JIDs