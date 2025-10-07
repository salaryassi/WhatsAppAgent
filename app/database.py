import sqlite3
import uuid
from datetime import datetime
import json
from .config import DB_PATH

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def setup_database():
    conn = get_db_connection()
    conn.execute("CREATE TABLE IF NOT EXISTS receipts (id TEXT PRIMARY KEY, customer_name TEXT, image_path TEXT, source_group TEXT, timestamp DATETIME, forwarded BOOLEAN);")
    conn.execute("CREATE TABLE IF NOT EXISTS queries (id TEXT PRIMARY KEY, customer_name TEXT, query_group TEXT, timestamp DATETIME, matched_receipt_id TEXT, status TEXT);")
    conn.execute("CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, action TEXT, metadata TEXT, timestamp DATETIME);")
    conn.commit()
    conn.close()

# Add other database functions here (e.g., store_receipt, log_query, find_match, etc.)