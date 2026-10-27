"""API keys typed in Admin -> AI providers, encrypted at rest.

Fernet with Config.AI_KEYS_ENCRYPTION_KEY - a key of its own, not
SECRET_KEY, so rotating SECRET_KEY (which ends sessions and resets two-step
sign-in) can't make provider keys unreadable. Either a Fernet key (what
Fernet.generate_key() prints) or any long random string, which is stretched
into one.

Keys are write-only: the app stores them and uses them, but no page or API
ever returns one - only its last 4 characters.
"""

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from config import Config


class KeysUnavailable(RuntimeError):
    """No AI_KEYS_ENCRYPTION_KEY is set, so keys can't be stored or read."""


def ready() -> bool:
    return bool(Config.AI_KEYS_ENCRYPTION_KEY)


def _fernet() -> Fernet:
    raw = Config.AI_KEYS_ENCRYPTION_KEY
    if not raw:
        raise KeysUnavailable("Set AI_KEYS_ENCRYPTION_KEY in the server settings to store API keys in Admin.")
    try:
        return Fernet(raw.encode())
    except ValueError:  # not a Fernet key: derive one from the text
        return Fernet(base64.urlsafe_b64encode(hashlib.sha256(("ai-keys:" + raw).encode()).digest()))


def encrypt(api_key: str) -> tuple[str, str]:
    """-> (token to store, last 4 characters to show)."""
    api_key = (api_key or "").strip()
    if not api_key:
        raise ValueError("The API key is empty.")
    return _fernet().encrypt(api_key.encode()).decode(), api_key[-4:]


def decrypt(token: str | None) -> str | None:
    """The key, or None when there is none or it can't be read (the
    encryption key changed or is missing)."""
    if not token:
        return None
    try:
        return _fernet().decrypt(token.encode()).decode()
    except (InvalidToken, KeysUnavailable):
        return None
