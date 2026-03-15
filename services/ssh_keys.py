from pathlib import Path

from paramiko import RSAKey


BASE_DIR = Path(__file__).resolve().parents[1]
KEYS_DIR = BASE_DIR / "keys"


def _resolve_key_path(path_str: str) -> Path:
    path = Path(path_str)
    if path.is_absolute():
        return path
    return BASE_DIR / path


def ensure_key_pair(private_key_path: str, public_key_path: str, bits: int = 4096) -> dict:
    private_path = _resolve_key_path(private_key_path)
    public_path = _resolve_key_path(public_key_path)

    private_path.parent.mkdir(parents=True, exist_ok=True)
    public_path.parent.mkdir(parents=True, exist_ok=True)

    if not private_path.exists():
        key = RSAKey.generate(bits=bits)
        key.write_private_key_file(str(private_path))
        public_path.write_text(
            f"{key.get_name()} {key.get_base64()}\n",
            encoding="utf-8",
        )
        private_path.chmod(0o600)
        public_path.chmod(0o644)

    public_key = public_path.read_text(encoding="utf-8").strip() if public_path.exists() else ""
    return {
        "private_key_path": str(private_path),
        "public_key_path": str(public_path),
        "public_key": public_key,
    }


def read_key_pair(private_key_path: str, public_key_path: str) -> dict:
    private_path = _resolve_key_path(private_key_path)
    public_path = _resolve_key_path(public_key_path)
    public_key = public_path.read_text(encoding="utf-8").strip() if public_path.exists() else ""
    return {
        "private_key_path": str(private_path),
        "public_key_path": str(public_path),
        "public_key": public_key,
    }
