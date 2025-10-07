import sqlite3
import uuid
from datetime import datetime
import logging
from .config import DB_PATH

logger = logging.getLogger("app.database")

def get_db_connection():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def setup_database():
    conn = get_db_connection()
    logger.info("Setting up database (if not exists). DB_PATH=%s", DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS receipts (
            id TEXT PRIMARY KEY,
            customer_name TEXT,
            image_path TEXT,
            source_group TEXT,
            timestamp DATETIME,
            forwarded INTEGER DEFAULT 0
        );
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS queries (
            id TEXT PRIMARY KEY,
            customer_name TEXT,
            query_group TEXT,
            timestamp DATETIME,
            matched_receipt_id TEXT,
            status TEXT
        );
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action TEXT,
            metadata TEXT,
            timestamp DATETIME
        );
    """)
    conn.commit()
    conn.close()

# Store a receipt record and return the receipt id
def store_receipt(customer_name, image_path, source_group):
    conn = get_db_connection()
    receipt_id = str(uuid.uuid4())
    timestamp = datetime.utcnow().isoformat()
    conn.execute(
        "INSERT INTO receipts (id, customer_name, image_path, source_group, timestamp, forwarded) VALUES (?, ?, ?, ?, ?, 0)",
        (receipt_id, customer_name, image_path, source_group, timestamp)
    )
    conn.commit()
    conn.close()
    logger.info("Stored receipt %s for %s from group %s", receipt_id, customer_name, source_group)
    return receipt_id

def mark_receipt_forwarded(receipt_id):
    conn = get_db_connection()
    conn.execute("UPDATE receipts SET forwarded = 1 WHERE id = ?", (receipt_id,))
    conn.commit()
    conn.close()
    logger.info("Marked receipt %s as forwarded", receipt_id)

def list_unforwarded_receipts(limit=100):
    conn = get_db_connection()
    cur = conn.execute("SELECT id, customer_name, image_path, source_group, timestamp FROM receipts WHERE forwarded = 0 ORDER BY timestamp LIMIT ?", (limit,))
    rows = cur.fetchall()
    conn.close()
    return rows

def log_event(action, metadata):
    conn = get_db_connection()
    ts = datetime.utcnow().isoformat()
    conn.execute("INSERT INTO events (action, metadata, timestamp) VALUES (?, ?, ?)", (action, metadata, ts))
    conn.commit()
    conn.close()
    logger.debug("Logged event %s : %s", action, metadata)
