"""
Named GPU-server profiles — persisted to gpu_servers.json next to this file.

A profile holds the connection settings for one GPU box (host, user, key_path,
remote_path, optional predict_remote_path and passphrase). Projects reference a
profile by NAME (config.json: "server_profile"); resolution happens on the
instance that executes the job, because the same logical server needs different
key paths from different machines (the PC vs the Azure VM's container).

The file is PER-INSTANCE and git-ignored — it may contain a stored key
passphrase (plaintext, next to the key it unlocks; protects against a copied
key file, not a stolen machine).
"""
from __future__ import annotations
import json
from pathlib import Path

_PROFILES_PATH = Path(__file__).parent / "gpu_servers.json"

FIELDS = ("host", "user", "key_path", "remote_path",
          "predict_remote_path", "passphrase")


def load_all() -> dict:
    if _PROFILES_PATH.exists():
        try:
            return json.loads(_PROFILES_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def get(name: str) -> dict | None:
    return load_all().get(name)


def save(name: str, server: dict) -> dict:
    profiles = load_all()
    clean = {k: v for k, v in server.items() if k in FIELDS and v}
    profiles[name] = clean
    _PROFILES_PATH.write_text(json.dumps(profiles, indent=2, ensure_ascii=False),
                              encoding="utf-8")
    return profiles


def delete(name: str) -> dict:
    profiles = load_all()
    profiles.pop(name, None)
    _PROFILES_PATH.write_text(json.dumps(profiles, indent=2, ensure_ascii=False),
                              encoding="utf-8")
    return profiles
