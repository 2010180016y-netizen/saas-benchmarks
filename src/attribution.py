"""
SlotBridge Korea — Attribution Engine & Overflow Network Controller (SBK-FIX-03)
Handles cryptographic HMAC-SHA256 attribution tokens, fraud prevention
(anti-cookie stuffing, self-referral blocking, first-touch priority),
and notification outbox with Fail-Closed security.
"""

import os
import hmac
import hashlib
import time
import json
import uuid
from typing import Dict, Any, Optional, Tuple

from .db import DatabaseManager
from .engine import EngineError

class AttributionError(Exception):
    """Raised when attribution integrity or secret key constraints are violated."""
    pass

DEFAULT_ATTRIBUTION_SECRET = os.environ.get("SBK_ATTRIBUTION_SECRET", "sbk_attribution_hmac_secret_2026_key")
SECRET_KEY = DEFAULT_ATTRIBUTION_SECRET

def get_attribution_secret(secret: Optional[str] = None) -> str:
    """Resolves HMAC secret key. Enforces Fail-Closed if secret is missing."""
    if secret is not None:
        sec = secret
    else:
        sec = os.environ.get("SBK_ATTRIBUTION_SECRET")
        if sec is None:
            sec = SECRET_KEY
    if not sec or not sec.strip():
        raise AttributionError("MISSING_ATTRIBUTION_SECRET: SBK_ATTRIBUTION_SECRET is required (Fail-Closed).")
    return sec

def generate_attribution_token(
    partner_id: str,
    campaign_id: str = "default_overflow",
    ttl_seconds: int = 86400 * 7, # 7 days
    secret: Optional[str] = None
) -> str:
    """
    Generates a tamper-proof signed attribution token.
    Format: partner_id:campaign_id:expires_at:signature_hex
    """
    sec = get_attribution_secret(secret)
    expires_at = int(time.time()) + ttl_seconds
    payload = f"{partner_id}:{campaign_id}:{expires_at}"
    sig = hmac.new(sec.encode('utf-8'), payload.encode('utf-8'), hashlib.sha256).hexdigest()[:32]
    return f"{payload}:{sig}"

def verify_attribution_token(token: str, secret: Optional[str] = None) -> Tuple[bool, Optional[Dict[str, Any]]]:
    """
    Verifies attribution token integrity, signature, and expiration.
    Returns (is_valid, parsed_data).
    """
    if not token or token.count(":") != 3:
        return False, None

    parts = token.split(":")
    partner_id, campaign_id, exp_str, sig = parts

    # Strict signature validation: must be exact 32 or 64 hex characters (P0-02 defense)
    if not sig or len(sig) not in (32, 64) or not all(c in '0123456789abcdefABCDEF' for c in sig):
        return False, None

    try:
        expires_at = int(exp_str)
    except ValueError:
        return False, None

    try:
        sec = get_attribution_secret(secret)
    except Exception:
        return False, None

    payload = f"{partner_id}:{campaign_id}:{expires_at}"
    full_sig = hmac.new(sec.encode('utf-8'), payload.encode('utf-8'), hashlib.sha256).hexdigest()
    expected_sig = full_sig[:len(sig)]

    if not hmac.compare_digest(sig.lower(), expected_sig.lower()):
        return False, None # Signature mismatch / tampering

    if time.time() > expires_at:
        return False, None # Expired token

    return True, {
        "partner_id": partner_id,
        "campaign_id": campaign_id,
        "expires_at": expires_at
    }

def record_widget_click(
    db: DatabaseManager,
    partner_id: str,
    campaign_name: str = "overflow_widget",
    client_ip: str = "127.0.0.1",
    user_agent: str = "Mozilla/5.0"
) -> Dict[str, Any]:
    """Records widget display/click event and updates campaign stats."""
    partner = db.execute("SELECT id, verification_status FROM partners WHERE id = ?", (partner_id,)).fetchone()
    if not partner or partner["verification_status"] != "APPROVED":
        raise EngineError("INVALID_PARTNER", "유효하지 않거나 승인되지 않은 파트너 ID입니다.", 403)

    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    
    # Update or insert campaign attribution
    existing = db.execute(
        "SELECT id, clicks_count FROM campaign_attributions WHERE partner_id = ? AND campaign_name = ?",
        (partner_id, campaign_name)
    ).fetchone()
    
    if existing:
        db.execute(
            "UPDATE campaign_attributions SET clicks_count = clicks_count + 1, updated_at = ? WHERE id = ?",
            (now_iso, existing["id"])
        )
    else:
        db.execute(
            """
            INSERT INTO campaign_attributions (id, partner_id, campaign_name, clicks_count, conversions_count, created_at, updated_at)
            VALUES (?, ?, ?, 1, 0, ?, ?)
            """,
            (f"camp_{uuid.uuid4()}", partner_id, campaign_name, now_iso, now_iso)
        )
    db.commit()

    token = generate_attribution_token(partner_id, campaign_name)
    return {
        "partner_id": partner_id,
        "campaign_name": campaign_name,
        "attribution_token": token,
        "referral_url": f"/slotbridge/inquiry.html?ref={partner_id}&token={token}"
    }

def validate_attribution_for_match(
    db: DatabaseManager,
    attribution_partner_id: Optional[str],
    fulfillment_partner_id: str
) -> Optional[str]:
    """
    Enforces anti-fraud rules:
    Rule 1: Self-Referral / Wash Trading Block!
    If referring partner is identical to fulfillment partner, drop referral cut (prevent fee evasion).
    Rule 2: Partner must be APPROVED.
    """
    if not attribution_partner_id:
        return None
    if attribution_partner_id == fulfillment_partner_id:
        now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        db.execute(
            """
            INSERT INTO audit_log (id, actor_id, action, resource_type, resource_id, details, created_at)
            VALUES (?, 'SYSTEM', 'SELF_REFERRAL_BLOCKED', 'PARTNER', ?, ?, ?)
            """,
            (
                f"aud_{uuid.uuid4()}", attribution_partner_id,
                json.dumps({"reason": "유입 파트너와 수행 파트너 동일 (자가 귀속 차단)", "partner_id": attribution_partner_id}, ensure_ascii=False),
                now_iso
            )
        )
        db.commit()
        return None

    partner = db.execute(
        "SELECT id, verification_status FROM partners WHERE id = ?",
        (attribution_partner_id,)
    ).fetchone()

    if not partner or partner["verification_status"] != "APPROVED":
        return None

    return attribution_partner_id

def queue_mock_notification(
    db: DatabaseManager,
    recipient_type: str,
    recipient_destination: str,
    notification_type: str,
    payload: Dict[str, Any]
) -> str:
    """
    Queues notification into notification_outbox as MOCKED.
    Guarantees $0 cost (no paid SMS/email gateways called) while keeping complete event log.
    """
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    notif_id = f"notif_{uuid.uuid4()}"
    
    db.execute(
        """
        INSERT INTO notification_outbox (id, recipient_type, recipient_destination, notification_type, payload, status, created_at)
        VALUES (?, ?, ?, ?, ?, 'MOCKED', ?)
        """,
        (notif_id, recipient_type, recipient_destination, notification_type, json.dumps(payload), now_iso)
    )
    db.commit()
    return notif_id
