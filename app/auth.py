"""Tiny PoC password hashing (PBKDF2-SHA256, stdlib only — avoids passlib/bcrypt version issues)."""
import hashlib, os, hmac, base64

_ITER = 100_000


def hash_password(pw: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, _ITER)
    return "pbkdf2$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(dk).decode()


def verify_password(pw: str, stored: str) -> bool:
    try:
        _, salt_b64, dk_b64 = stored.split("$")
        test = hashlib.pbkdf2_hmac("sha256", pw.encode(), base64.b64decode(salt_b64), _ITER)
        return hmac.compare_digest(test, base64.b64decode(dk_b64))
    except Exception:
        return False
