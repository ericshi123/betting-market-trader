import base64
import os
import time

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from dotenv import load_dotenv

load_dotenv()

KALSHI_API_BASE = "https://api.elections.kalshi.com/trade-api/v2"
_API_PATH_PREFIX = "/trade-api/v2"

_SESSION = requests.Session()
_SESSION.headers.update({"Accept": "application/json"})

_private_key = None


def _get_private_key():
    global _private_key
    if _private_key is None:
        key_path = os.getenv("KALSHI_PRIVATE_KEY_PATH")
        if not key_path:
            raise EnvironmentError("KALSHI_PRIVATE_KEY_PATH not set in .env")
        with open(key_path, "rb") as f:
            _private_key = serialization.load_pem_private_key(f.read(), password=None)
    return _private_key


def get_auth_headers(method: str, path: str) -> dict:
    """
    Generate RSA-PSS signed auth headers for a Kalshi API request.
    path must be the full URL path including /trade-api/v2 prefix (no query string).
    """
    key_id = os.getenv("KALSHI_API_KEY_ID")
    if not key_id:
        raise EnvironmentError("KALSHI_API_KEY_ID not set in .env")

    ts_ms = str(int(time.time() * 1000))
    msg = (ts_ms + method.upper() + path).encode("utf-8")

    private_key = _get_private_key()
    sig = private_key.sign(
        msg,
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=32,
        ),
        hashes.SHA256(),
    )

    return {
        "KALSHI-ACCESS-KEY": key_id,
        "KALSHI-ACCESS-SIGNATURE": base64.b64encode(sig).decode("utf-8"),
        "KALSHI-ACCESS-TIMESTAMP": ts_ms,
        "Content-Type": "application/json",
    }


def kalshi_get(path: str, params: dict = None) -> dict:
    """Authenticated GET. path is relative to API base (e.g. '/markets')."""
    full_path = _API_PATH_PREFIX + path
    headers = get_auth_headers("GET", full_path)
    url = KALSHI_API_BASE + path
    resp = _SESSION.get(url, headers=headers, params=params, timeout=15)
    resp.raise_for_status()
    return resp.json()


def kalshi_post(path: str, body: dict) -> dict:
    """Authenticated POST."""
    full_path = _API_PATH_PREFIX + path
    headers = get_auth_headers("POST", full_path)
    url = KALSHI_API_BASE + path
    resp = _SESSION.post(url, headers=headers, json=body, timeout=15)
    resp.raise_for_status()
    return resp.json()


def kalshi_delete(path: str) -> dict:
    """Authenticated DELETE."""
    full_path = _API_PATH_PREFIX + path
    headers = get_auth_headers("DELETE", full_path)
    url = KALSHI_API_BASE + path
    resp = _SESSION.delete(url, headers=headers, timeout=15)
    resp.raise_for_status()
    try:
        return resp.json()
    except Exception:
        return {}
