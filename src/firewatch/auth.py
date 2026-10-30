"""Optional signed operator sessions; mandatory in the production profile."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time

COOKIE = "firewatch_session"

def required() -> bool:
    return os.environ.get("FIREWATCH_AUTH_REQUIRED", "1" if os.environ.get("FIREWATCH_ENV") == "production" else "0") == "1"

def configuration_errors() -> list[str]:
    if not required():
        return []
    errors = []
    if len(os.environ.get("FIREWATCH_SESSION_SECRET", "")) < 32:
        errors.append("FIREWATCH_SESSION_SECRET must contain at least 32 characters")
    if len(os.environ.get("FIREWATCH_OPERATOR_TOKEN", "")) < 24:
        errors.append("FIREWATCH_OPERATOR_TOKEN must contain at least 24 characters")
    return errors

def issue_session(access_code: str) -> tuple[str, str] | None:
    if configuration_errors():
        return None
    role = None
    for candidate in ("admin", "operator"):
        configured = os.environ.get(f"FIREWATCH_{candidate.upper()}_TOKEN", "")
        if configured and hmac.compare_digest(access_code, configured):
            role = candidate
    if role is None:
        return None
    data = base64.urlsafe_b64encode(json.dumps({"role": role, "exp": int(time.time()) + 8 * 3600}).encode()).decode()
    signature = hmac.new(os.environ["FIREWATCH_SESSION_SECRET"].encode(), data.encode(), hashlib.sha256).hexdigest()
    return f"{data}.{signature}", role

def session_role(cookie: str | None) -> str | None:
    if not required():
        return "local_operator"
    if not cookie or configuration_errors():
        return None
    try:
        data, supplied = cookie.split(".", 1)
        expected = hmac.new(os.environ["FIREWATCH_SESSION_SECRET"].encode(), data.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(supplied, expected):
            return None
        payload = json.loads(base64.urlsafe_b64decode(data))
        if payload["exp"] <= time.time() or payload["role"] not in {"operator", "admin"}:
            return None
        return payload["role"]
    except (ValueError, KeyError, TypeError):
        return None
