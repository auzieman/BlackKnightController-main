import base64
import json
import os
from copy import deepcopy
from pathlib import Path

try:
    from cryptography.fernet import Fernet, InvalidToken
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
except ImportError:  # pragma: no cover - runtime dependent
    Fernet = None
    InvalidToken = RuntimeError
    hashes = None
    PBKDF2HMAC = None


BASE_DIR = Path(__file__).resolve().parents[1]
SECRETS_META_PATH = BASE_DIR / "dictionaries" / "secrets_meta.json"
SECRETS_KEY_PATH = BASE_DIR / "keys" / "bkc_master_key"

SECRET_FIELDS = {
    "password",
    "controller_password",
    "token_value",
}

ENCRYPTED_PREFIX = "bkc$enc$"
DEFAULT_META = {
    "version": 1,
    "kdf": "pbkdf2-sha256",
    "iterations": 390000,
    "salt": "",
}


def _load_meta() -> dict:
    if SECRETS_META_PATH.exists():
        with SECRETS_META_PATH.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        merged = deepcopy(DEFAULT_META)
        merged.update(data)
        if merged.get("salt"):
            return merged

    meta = deepcopy(DEFAULT_META)
    meta["salt"] = base64.urlsafe_b64encode(os.urandom(16)).decode("ascii")
    SECRETS_META_PATH.parent.mkdir(parents=True, exist_ok=True)
    with SECRETS_META_PATH.open("w", encoding="utf-8") as handle:
        json.dump(meta, handle, indent=2, sort_keys=True)
        handle.write("\n")
    return meta


def _load_master_secret() -> bytes:
    override = os.getenv("BKC_MASTER_SECRET", "")
    if override:
        return override.encode("utf-8")

    if not SECRETS_KEY_PATH.exists():
        SECRETS_KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
        secret = base64.urlsafe_b64encode(os.urandom(32))
        SECRETS_KEY_PATH.write_bytes(secret + b"\n")
        os.chmod(SECRETS_KEY_PATH, 0o600)
        return secret

    return SECRETS_KEY_PATH.read_bytes().strip()


def _fernet() -> Fernet:
    if Fernet is None or hashes is None or PBKDF2HMAC is None:
        raise RuntimeError("cryptography is required for encrypted secret storage. Install requirements.txt first.")
    meta = _load_meta()
    salt = base64.urlsafe_b64decode(meta["salt"].encode("ascii"))
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=int(meta["iterations"]),
    )
    derived = base64.urlsafe_b64encode(kdf.derive(_load_master_secret()))
    return Fernet(derived)


def encrypt_secret(value: str) -> str:
    if not value:
        return value
    if value.startswith(ENCRYPTED_PREFIX):
        return value
    token = _fernet().encrypt(value.encode("utf-8")).decode("ascii")
    return f"{ENCRYPTED_PREFIX}{token}"


def decrypt_secret(value: str) -> str:
    if not value or not isinstance(value, str):
        return value
    if not value.startswith(ENCRYPTED_PREFIX):
        return value
    if Fernet is None or hashes is None or PBKDF2HMAC is None:
        # Keep older environments able to load inventory/state even if they cannot
        # decrypt live secrets until dependencies are rebuilt.
        return ""
    token = value[len(ENCRYPTED_PREFIX):]
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise RuntimeError(
            "Unable to decrypt stored secrets. Restore keys/bkc_master_key and dictionaries/secrets_meta.json, "
            "or set BKC_MASTER_SECRET to the original master secret."
        ) from exc


def encrypt_structure(data):
    if isinstance(data, dict):
        encrypted = {}
        for key, value in data.items():
            if key in SECRET_FIELDS and isinstance(value, str):
                encrypted[key] = encrypt_secret(value)
            else:
                encrypted[key] = encrypt_structure(value)
        return encrypted
    if isinstance(data, list):
        return [encrypt_structure(item) for item in data]
    return data


def decrypt_structure(data):
    if isinstance(data, dict):
        decrypted = {}
        for key, value in data.items():
            if key in SECRET_FIELDS and isinstance(value, str):
                decrypted[key] = decrypt_secret(value)
            else:
                decrypted[key] = decrypt_structure(value)
        return decrypted
    if isinstance(data, list):
        return [decrypt_structure(item) for item in data]
    return data
