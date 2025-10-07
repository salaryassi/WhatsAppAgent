# app/main.py

from flask import Flask, request, jsonify
import os
import logging

# Configure basic logging to see output in the terminal
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

app = Flask(__name__)

# Load the secret from environment variables
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")

@app.route('/')
def index():
    return "<h1>Flask App is Running!</h1>"

@app.route('/whatsapp_webhook', methods=['POST'])
def webhook():
    logging.info("--- Webhook Connection Attempt Received ---")

    # Verify the secret to ensure it's from the Evolution API
    received_secret = request.headers.get('X-Webhook-Secret')
    if received_secret != WEBHOOK_SECRET:
        logging.error(f"!!! UNAUTHORIZED ATTEMPT! Wrong secret key received: {received_secret}")
        return jsonify({"status": "unauthorized"}), 401

    logging.info("+++ Webhook Secret Verified Successfully! +++")
    data = request.json
    logging.info(f"Received Data: {data}")

    return jsonify({"status": "ok"}), 200

if __name__ == '__main__':
    # Using app.run() for development
    app.run(host='0.0.0.0', port=5000, debug=True)