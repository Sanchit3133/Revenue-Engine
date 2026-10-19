"""Small, side-effect-explicit helpers for Razorpay checkout order creation."""

from __future__ import annotations

import base64
from decimal import Decimal, InvalidOperation
import hashlib
import hmac
import json
from typing import Any

import requests

RAZORPAY_ORDER_URL = "https://api.razorpay.com/v1/orders"


def amount_to_minor_units(raw: str, *, currency: str = "INR") -> int:
    """Convert a trusted owner's rupee quote to paise without float rounding."""
    if currency.upper() != "INR":
        raise ValueError("Only INR quotes are enabled in Phase 1.")
    try:
        amount = Decimal(str(raw).strip())
    except (InvalidOperation, ValueError):
        raise ValueError("Enter a valid amount in rupees.") from None
    if not amount.is_finite() or amount < Decimal("1.00"):
        raise ValueError("The minimum checkout amount is ₹1.00.")
    quantized = amount.quantize(Decimal("0.01"))
    if quantized != amount:
        raise ValueError("Enter no more than two decimal places.")
    minor = int(quantized * 100)
    if minor > 100_000_000_00:
        raise ValueError("The quoted amount exceeds the configured safety limit.")
    return minor


def checkout_token(*, secret: str, request_public_id: str, order_id: str, expires_at: str) -> str:
    """Create a deterministic capability token; only its hash is stored in the database."""
    if not secret or not request_public_id or not order_id or not expires_at:
        raise ValueError("Checkout token inputs are incomplete.")
    message = f"{request_public_id}:{order_id}:{expires_at}".encode("utf-8")
    digest = hmac.new(secret.encode("utf-8"), message, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def verify_checkout_signature(
    *, order_id: str, payment_id: str, signature: str | None, key_secret: str | None
) -> bool:
    """Verify the signature returned by Razorpay Checkout; it does not alone mark paid."""
    if not order_id or not payment_id or not signature or not key_secret:
        return False
    message = f"{order_id}|{payment_id}".encode("utf-8")
    expected = hmac.new(key_secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature.strip())


def create_razorpay_order(
    *,
    key_id: str,
    key_secret: str,
    amount_minor: int,
    currency: str,
    receipt: str,
    request_public_id: str,
    session: Any = requests,
) -> dict[str, Any]:
    """Create an order through Razorpay's server API; never logs keys or raw responses.

    A transport error has an ambiguous outcome: the provider may have created the
    order. The caller must not automatically retry it.
    """
    if not key_id or not key_secret:
        return {"success": False, "uncertain": False, "reason": "razorpay_api_credentials_missing"}
    if not isinstance(amount_minor, int) or amount_minor < 100:
        return {"success": False, "uncertain": False, "reason": "invalid_amount_minor_units"}
    currency = (currency or "").upper()
    if currency != "INR":
        return {"success": False, "uncertain": False, "reason": "unsupported_currency"}
    payload = {
        "amount": amount_minor,
        "currency": currency,
        "receipt": receipt[:40],
        "partial_payment": False,
        "notes": {"revenue_engine_request": request_public_id[:64]},
    }
    try:
        response = session.post(
            RAZORPAY_ORDER_URL,
            auth=(key_id, key_secret),
            json=payload,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            timeout=12,
        )
    except requests.RequestException as exc:
        return {
            "success": False,
            "uncertain": True,
            "reason": f"razorpay_transport_error_check_dashboard_before_retry:{type(exc).__name__}",
        }

    try:
        data = response.json()
    except (ValueError, AttributeError):
        data = None
    if response.status_code not in {200, 201} or not isinstance(data, dict):
        return {
            "success": False,
            "uncertain": False,
            "reason": f"razorpay_order_http_{response.status_code}",
        }

    order_id = str(data.get("id") or "").strip()
    try:
        returned_amount = int(data.get("amount"))
    except (TypeError, ValueError):
        returned_amount = 0
    returned_currency = str(data.get("currency") or "").upper()
    if not order_id or returned_amount != amount_minor or returned_currency != currency:
        return {
            "success": False,
            "uncertain": True,
            "reason": "razorpay_order_response_mismatch_check_dashboard",
        }
    return {
        "success": True,
        "uncertain": False,
        "order_id": order_id,
        "amount": returned_amount,
        "currency": returned_currency,
    }
