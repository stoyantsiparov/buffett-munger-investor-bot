"""Fernet symmetric encryption helper for storing user Trading 212 API keys safely in the DB."""

import os
from pathlib import Path
from cryptography.fernet import Fernet


def get_or_create_fernet() -> Fernet:
    """Return a Fernet instance using BOT_ENCRYPTION_KEY from env or .env file."""
    key = os.environ.get("BOT_ENCRYPTION_KEY", "").strip()
    env_path = Path(__file__).resolve().parent.parent / ".env"

    if not key and env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s.startswith("BOT_ENCRYPTION_KEY="):
                key = s.split("=", 1)[1].strip().strip('"').strip("'")
                break

    if not key:
        key = Fernet.generate_key().decode("utf-8")
        os.environ["BOT_ENCRYPTION_KEY"] = key
        # Persist to .env if it exists so encrypted keys survive restarts
        if env_path.exists():
            existing = env_path.read_text(encoding="utf-8")
            if "BOT_ENCRYPTION_KEY=" not in existing:
                sep = "" if existing.endswith("\n") else "\n"
                env_path.write_text(
                    existing + f"{sep}BOT_ENCRYPTION_KEY={key}\n",
                    encoding="utf-8",
                )

    return Fernet(key.encode("utf-8"))


def encrypt_secret(plain_text: str | None) -> str | None:
    """Encrypt a plaintext string and return URL-safe base64 ciphertext."""
    if not plain_text:
        return None
    f = get_or_create_fernet()
    return f.encrypt(plain_text.encode("utf-8")).decode("utf-8")


def decrypt_secret(cipher_text: str | None) -> str | None:
    """Decrypt a Fernet ciphertext back to plaintext."""
    if not cipher_text:
        return None
    f = get_or_create_fernet()
    return f.decrypt(cipher_text.encode("utf-8")).decode("utf-8")
