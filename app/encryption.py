from cryptography.fernet import Fernet
from .config import ENCRYPTION_KEY

cipher_suite = Fernet(ENCRYPTION_KEY.encode())

def encrypt_image(image_data):
    return cipher_suite.encrypt(image_data)

def decrypt_image(encrypted_data):
    return cipher_suite.decrypt(encrypted_data)