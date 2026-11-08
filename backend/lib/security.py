"""Password hashing, credential encryption and per-league access tokens."""

import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone

import jwt
from cryptography.fernet import Fernet, InvalidToken

_ITERATIONS = 200_000
TOKEN_DAYS = 365


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITERATIONS)
    return f"pbkdf2${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt_hex, dk_hex = stored.split("$")
    except ValueError:
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), _ITERATIONS)
    return hmac.compare_digest(dk.hex(), dk_hex)


def password_lookup(password: str) -> str:
    """Deterministic keyed digest so a league can be found (and kept unique) by its access password."""
    return hmac.new(os.environ["LEAGUE_TOKEN_SECRET"].encode(), password.encode(), hashlib.sha256).hexdigest()


def _fernet() -> Fernet:
    return Fernet(os.environ["CREDENTIALS_KEY"].encode())


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt(value: str) -> str | None:
    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken:
        return None


def make_league_token(league_id: str) -> str:
    payload = {"lid": league_id, "exp": datetime.now(timezone.utc) + timedelta(days=TOKEN_DAYS)}
    return jwt.encode(payload, os.environ["LEAGUE_TOKEN_SECRET"], algorithm="HS256")


def read_league_token(token: str | None) -> str | None:
    if not token:
        return None
    try:
        data = jwt.decode(token, os.environ["LEAGUE_TOKEN_SECRET"], algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
    return data.get("lid")
