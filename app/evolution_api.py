import requests
from .config import EVOLUTION_API_URL, EVOLUTION_API_KEY

def send_whatsapp_message(group_jid, message):
    url = f"{EVOLUTION_API_URL}/message/sendText"
    headers = {"apikey": EVOLUTION_API_KEY}
    payload = {"number": group_jid, "options": {"delay": 1200}, "textMessage": {"text": message}}
    response = requests.post(url, json=payload, headers=headers)
    response.raise_for_status()
    return response.json()