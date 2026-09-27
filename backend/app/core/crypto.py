"""Symmetric encryption for secrets (VK token, etc.) with Fernet."""
from cryptography.fernet import Fernet, InvalidToken

from app.config import settings

_fernet = Fernet(settings.FERNET_KEY.encode("utf-8"))


def encrypt_secret(plaintext: str) -> str:
    return _fernet.encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_secret(ciphertext: str) -> str:
    try:
        return _fernet.decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        return ""


def mask_email(email: str) -> str:
    if "@" not in email:
        return "***"
    local, domain = email.split("@", 1)
    if len(local) <= 2:
        return f"{local[0]}***@{domain}"
    return f"{local[:2]}***@{domain}"
