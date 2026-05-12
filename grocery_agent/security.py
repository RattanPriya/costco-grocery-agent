from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any


class ApprovalTokenError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ApprovalTokenPayload:
    cart_id: str
    action: str
    expires_at: int


class ApprovalTokenSigner:
    def __init__(self, secret: str, ttl_seconds: int = 60 * 60 * 24 * 7) -> None:
        if not secret:
            raise ValueError("Approval token secret is required.")
        self.secret = secret.encode("utf-8")
        self.ttl_seconds = ttl_seconds

    def create(self, cart_id: str, action: str, now: int | None = None) -> str:
        issued_at = int(now if now is not None else time.time())
        payload = {"cart_id": cart_id, "action": action, "expires_at": issued_at + self.ttl_seconds}
        body = _urlsafe_json(payload)
        signature = _sign(self.secret, body)
        return f"{body}.{signature}"

    def verify(self, token: str, expected_action: str, now: int | None = None) -> ApprovalTokenPayload:
        parts = token.split(".")
        if len(parts) != 2:
            raise ApprovalTokenError("Malformed approval token.")
        body, signature = parts
        expected_signature = _sign(self.secret, body)
        if not hmac.compare_digest(signature, expected_signature):
            raise ApprovalTokenError("Invalid approval token signature.")
        payload = _decode_urlsafe_json(body)
        action = str(payload.get("action", ""))
        if action != expected_action:
            raise ApprovalTokenError(f"Approval token is for {action!r}, not {expected_action!r}.")
        expires_at = int(payload.get("expires_at", 0))
        current_time = int(now if now is not None else time.time())
        if expires_at < current_time:
            raise ApprovalTokenError("Approval token has expired.")
        cart_id = str(payload.get("cart_id", ""))
        if not cart_id:
            raise ApprovalTokenError("Approval token is missing cart_id.")
        return ApprovalTokenPayload(cart_id=cart_id, action=action, expires_at=expires_at)


def _urlsafe_json(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_urlsafe_json(value: str) -> dict[str, Any]:
    padding = "=" * (-len(value) % 4)
    raw = base64.urlsafe_b64decode(value + padding)
    decoded = json.loads(raw.decode("utf-8"))
    if not isinstance(decoded, dict):
        raise ApprovalTokenError("Approval token payload must be an object.")
    return decoded


def _sign(secret: bytes, body: str) -> str:
    digest = hmac.new(secret, body.encode("ascii"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
