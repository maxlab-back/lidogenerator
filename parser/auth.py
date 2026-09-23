"""Пользователи и пароли для командного режима.

Пароли храним как PBKDF2-SHA256 с солью (stdlib, без зависимостей).
Пользователей заводит администратор из консоли: python app.py --add-user ivan
"""
from __future__ import annotations

import hashlib
import hmac
import secrets

_ITER = 240_000


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), _ITER)
    return f"pbkdf2${_ITER}${salt}${dk.hex()}"


def check_password(password: str, stored: str | None) -> bool:
    if not stored:
        # тратим то же время, чтобы не выдавать существование пользователя по таймингу
        hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), b"0" * 16, _ITER)
        return False
    try:
        _, it, salt, hexdk = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), int(it))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(dk.hex(), hexdk)


def new_token() -> str:
    return secrets.token_urlsafe(32)
