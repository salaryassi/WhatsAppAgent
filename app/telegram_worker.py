"""
telegram_worker.py

This file runs a persistent Pyrogram Client in a background thread and exposes simple
synchronous functions to enqueue sending documents/messages to Telegram. This avoids
starting/stopping the client per message (which is expensive).
"""

import threading
import queue
import time
import logging
import json
from pyrogram import Client
from .config import TELEGRAM_API_ID, TELEGRAM_API_HASH, FORWARD_TO_BOT_USERNAME, ADMIN_CHAT_ID

logger = logging.getLogger("app.telegram_worker")

class TelegramWorker:
    def __init__(self, session_name="whatsapp_agent_session"):
        self.session_name = session_name
        self._q = queue.Queue()
        self._client = Client(self.session_name, api_id=TELEGRAM_API_ID, api_hash=TELEGRAM_API_HASH, bot_token=TELEGRAM_BOT_TOKEN)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._running = False

    def start(self):
        logger.info("Starting Telegram worker thread")
        self._running = True
        self._thread.start()

    def stop(self):
        logger.info("Stopping Telegram worker")
        self._running = False
        self._q.put(None)
        self._thread.join(timeout=5)
        try:
            self._client.stop()
        except Exception:
            logger.exception("Error stopping pyrogram client")

    def _run(self):
        try:
            logger.info("Starting pyrogram client (blocking start) in worker")
            self._client.start()
            logger.info("Pyrogram client started")
            while self._running:
                try:
                    task = self._q.get(timeout=1)
                except queue.Empty:
                    continue
                if task is None:
                    break
                typ = task.get("type")
                if typ == "document":
                    self._send_document(task)
                elif typ == "message":
                    self._send_message(task)
                else:
                    logger.warning("Unknown task type: %s", typ)
            logger.info("Telegram worker loop exiting")
        except Exception:
            logger.exception("Telegram worker crashed")
        finally:
            try:
                self._client.stop()
            except Exception:
                pass

    def _send_document(self, task):
        chat_id = task.get("chat_id") or FORWARD_TO_BOT_USERNAME
        document_path = task["document_path"]
        caption = task.get("caption") or ""
        logger.info("Sending document %s to %s", document_path, chat_id)
        try:
            self._client.send_document(chat_id=chat_id, document=document_path, caption=caption)
            logger.info("Document sent to %s", chat_id)
        except Exception:
            logger.exception("Failed to send document %s to %s", document_path, chat_id)

    def _send_message(self, task):
        chat_id = task.get("chat_id") or ADMIN_CHAT_ID
        message = task.get("message", "")
        logger.info("Sending message to %s: %s", chat_id, message)
        try:
            self._client.send_message(chat_id=chat_id, text=message)
        except Exception:
            logger.exception("Failed to send message")

    # Public API: enqueue
    def enqueue_document(self, document_path, caption=None, chat_id=None):
        self._q.put({"type": "document", "document_path": document_path, "caption": caption, "chat_id": chat_id})

    def enqueue_message(self, message, chat_id=None):
        self._q.put({"type": "message", "message": message, "chat_id": chat_id})

# instantiate a worker in module scope so main.py can import and start it
telegram_worker = TelegramWorker()
