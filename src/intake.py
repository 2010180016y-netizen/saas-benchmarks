"""
SlotBridge Korea — Customer Intake & Statutory Consent Controller
Handles mobile-first customer inquiries, statutory consent recording (PIPC Act Art. 17),
attribution verification, honest availability checks, and 1-click self-service revocation.
"""

import uuid
import re
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional

from .db import DatabaseManager
from .engine import EngineError, create_inquiry, revoke_inquiry
from .crypto import hash_phone, mask_phone, mask_name, decrypt_pii

TERMS_VERSION = "v1.0.0"

def check_district_availability(db: DatabaseManager, requested_date: str, district: str) -> int:
    """Returns actual count of verified available slots. NEVER fakes availability."""
    query = """
        SELECT count(cs.id) AS cnt
        FROM capacity_slots cs
        JOIN partners p ON cs.partner_id = p.id
        JOIN service_areas sa ON sa.partner_id = p.id
        WHERE cs.slot_date = ? 
          AND cs.status = 'AVAILABLE'
          AND p.verification_status = 'APPROVED'
          AND sa.district = ?
          AND sa.is_active = 1
    """
    row = db.execute(query, (requested_date, district)).fetchone()
    return row["cnt"] if row else 0

def submit_customer_inquiry(
    db: DatabaseManager,
    data: Dict[str, Any],
    client_ip: str = "127.0.0.1",
    user_agent: str = "Mozilla/5.0"
) -> Dict[str, Any]:
    """
    Submits customer inquiry with strict guardrails:
    1. Verifies attribution partner exists and is approved (prevents tampering).
    2. Enforces mandatory consent for collection and 3rd-party provision.
    3. Performs honest availability check (if 0 slots, returns UNAVAILABLE without fake numbers).
    4. Encrypts PII and records immutable consent event.
    """
    # 1. Attribution Partner Server-Side Verification
    attribution_id = data.get("attribution_partner_id")
    if not attribution_id and data.get("attribution_token"):
        from .attribution import verify_attribution_token
        is_valid, verified_payload = verify_attribution_token(data["attribution_token"])
        if is_valid and verified_payload:
            attribution_id = verified_payload.get("partner_id")

    verified_attribution = None
    if attribution_id:
        partner = db.execute(
            "SELECT id, verification_status FROM partners WHERE id = ?",
            (attribution_id,)
        ).fetchone()
        if partner and partner["verification_status"] == "APPROVED":
            verified_attribution = partner["id"]
        else:
            # Drop invalid/tampered attribution gracefully
            verified_attribution = None

    sanitized_data = dict(data)
    sanitized_data["attribution_partner_id"] = verified_attribution

    # 2. Honest Availability Check
    requested_date = sanitized_data.get("requested_date", "")
    district = sanitized_data.get("district", "관악구")
    available_slots_count = check_district_availability(db, requested_date, district)

    # 3. Create Inquiry through Core Engine
    inquiry_res = create_inquiry(
        db,
        data=sanitized_data,
        client_ip=client_ip,
        user_agent=user_agent
    )

    inquiry_id = inquiry_res["inquiry_id"]
    tracking_token = inquiry_res["tracking_token"]

    if available_slots_count == 0:
        # Update status to UNAVAILABLE_WAITING
        now_iso = datetime.now(timezone.utc).isoformat()
        db.execute(
            "UPDATE inquiries SET status = 'UNAVAILABLE_WAITING', updated_at = ? WHERE id = ?",
            (now_iso, inquiry_id)
        )
        db.commit()
        return {
            "inquiry_id": inquiry_id,
            "tracking_token": tracking_token,
            "status": "UNAVAILABLE_WAITING",
            "has_available_slots": False,
            "available_slots_count": 0,
            "message": "현재 선택하신 일시에 관내 잔여 슬롯이 마감되었습니다. 취소 슬롯 발생 시 즉시 연락을 위해 대기 접수되었습니다.",
            "status_url": f"/slotbridge/status.html?token={tracking_token}"
        }

    return {
        "inquiry_id": inquiry_id,
        "tracking_token": tracking_token,
        "status": "PENDING_MATCH",
        "has_available_slots": True,
        "available_slots_count": available_slots_count,
        "message": f"관내 {available_slots_count}개 팀의 가용 슬롯이 확인되었습니다. 최적의 청소팀으로 즉시 배타적 오퍼를 전송합니다.",
        "status_url": f"/slotbridge/status.html?token={tracking_token}"
    }

def get_inquiry_status(db: DatabaseManager, tracking_token: str) -> Dict[str, Any]:
    """Retrieves inquiry status by token without revealing raw PII."""
    inq = db.execute("SELECT * FROM inquiries WHERE tracking_token = ?", (tracking_token,)).fetchone()
    if not inq:
        raise EngineError("INQUIRY_NOT_FOUND", "신청 내역을 찾을 수 없습니다.", 404)

    is_cancelled = inq["status"] == "CANCELLED"
    matched_partner_name = None

    if inq["status"] in ["MATCHED", "FULFILLED"]:
        match = db.execute(
            """
            SELECT p.name AS partner_name
            FROM matches m
            JOIN partners p ON m.partner_id = p.id
            WHERE m.inquiry_id = ?
            """,
            (inq["id"],)
        ).fetchone()
        if match:
            matched_partner_name = match["partner_name"]

    return {
        "inquiry_id": inq["id"],
        "status": inq["status"],
        "requested_date": inq["requested_date"],
        "district": inq["district"],
        "housing_type": inq["housing_type"],
        "area_pyeong": inq["area_pyeong"],
        "matched_partner_name": matched_partner_name,
        "is_pii_erased": is_cancelled,
        "created_at": inq["created_at"]
    }

def revoke_customer_inquiry(db: DatabaseManager, tracking_token: str) -> Dict[str, Any]:
    """1-Click Customer Self-Service Revocation."""
    return revoke_inquiry(db, tracking_token)
