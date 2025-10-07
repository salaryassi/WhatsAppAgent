import logging
from cryptography.fernet import Fernet, InvalidToken
from .config import ENCRYPTION_KEY

logger = logging.getLogger("app.encryption")

if not ENCRYPTION_KEY:
    logger.warning("ENCRYPTION_KEY not set. Images will not be encrypted!")
    cipher_suite = None
else:
    cipher_suite = Fernet(ENCRYPTION_KEY.encode())

def encrypt_bytes(data: bytes) -> bytes:
    if not cipher_suite:
        return data
    return cipher_suite.encrypt(data)

def decrypt_bytes(token: bytes) -> bytes:
    if not cipher_suite:
        return token
    try:
        return cipher_suite.decrypt(token)
    except InvalidToken:
        logger.exception("Failed to decrypt data - invalid token")
        raise
