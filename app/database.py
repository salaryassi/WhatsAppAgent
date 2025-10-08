# database.py
import sqlite3
import uuid
from datetime import datetime
import json
import os
from .config import DB_PATH

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def setup_database():
    conn = get_db_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS receipts (
            id TEXT PRIMARY KEY,
            customer_name TEXT,
            image_path TEXT,
            source_group TEXT,
            timestamp DATETIME,
            forwarded BOOLEAN DEFAULT 0
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

def store_receipt(customer_name, image_path, source_group):
    conn = get_db_connection()
    rid = uuid.uuid4().hex
    now = datetime.utcnow().isoformat()
    conn.execute(
        "INSERT INTO receipts (id, customer_name, image_path, source_group, timestamp, forwarded) VALUES (?, ?, ?, ?, ?, ?)",
        (rid, customer_name, image_path, source_group, now, False)
    )
    conn.commit()
    conn.close()
    return rid

def get_unforwarded_receipts():
    conn = get_db_connection()
    cur = conn.execute("SELECT * FROM receipts WHERE forwarded = 0")
    rows = cur.fetchall()
    conn.close()
    return rows

def get_receipt_by_id(rid):
    conn = get_db_connection()
    cur = conn.execute("SELECT * FROM receipts WHERE id = ?", (rid,))
    row = cur.fetchone()
    conn.close()
    return row

def mark_receipt_forwarded(rid):
    conn = get_db_connection()
    conn.execute("UPDATE receipts SET forwarded = 1 WHERE id = ?", (rid,))
    conn.commit()
    conn.close()

def log_query(customer_name, query_group, matched_receipt_id=None, status="created"):
    conn = get_db_connection()
    qid = uuid.uuid4().hex
    now = datetime.utcnow().isoformat()
    conn.execute(
        "INSERT INTO queries (id, customer_name, query_group, timestamp, matched_receipt_id, status) VALUES (?, ?, ?, ?, ?, ?)",
        (qid, customer_name, query_group, now, matched_receipt_id, status)
    )
    conn.commit()
    conn.close()
    return qid

def log_event(action, metadata: dict):
    conn = get_db_connection()
    now = datetime.utcnow().isoformat()
    conn.execute(
        "INSERT INTO events (action, metadata, timestamp) VALUES (?, ?, ?)",
        (action, json.dumps(metadata, default=str), now)
    )
    conn.commit()
    conn.close()
