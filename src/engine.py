"""
SlotBridge Korea — Core Domain Engine
Implements business rules, 1:1 exclusive matching, atomic concurrency,
immutable ledger accounting, and statutory privacy enforcement.
"""

import uuid
import json
import re
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, List

from .crypto import encrypt_pii, decrypt_pii, hash_phone, mask_phone, mask_name
from .db import DatabaseManager

class EngineError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code

def create_inquiry(
    db: DatabaseManager,
    data: Dict[str, Any],
    idempotency_key: Optional[str] = None,
    client_ip: str = "127.0.0.1",
    user_agent: str = "Mozilla/5.0"
) -> Dict[str, Any]:
    """
    FR-01, FR-02: Validates input, enforces mandatory consent, encrypts PII,
    and creates immutable consent event.
    """
    # 1. Mandatory Consent Verification
    if not data.get("agreed_privacy_collection"):
        raise EngineError("CONSENT_REQUIRED", "개인정보 수집 및 이용 동의는 필수입니다.", 400)
    if not data.get("agreed_third_party_share"):
        raise EngineError("CONSENT_REQUIRED", "개인정보 제3자 제공 동의는 필수입니다.", 400)

    # 2. Field Validations
    phone = data.get("customer_phone", "").strip()
    if not re.match(r"^010-[0-9]{4}-[0-9]{4}$", phone):
        raise EngineError("INVALID_PHONE", "휴대전화번호는 010-XXXX-XXXX 형식이어야 합니다.", 400)
    
    name = data.get("customer_name", "").strip()
    if not name or len(name) < 2:
        raise EngineError("INVALID_NAME", "신청자 성명을 2자 이상 입력해 주세요.", 400)

    district = data.get("district", "").strip()
    if district not in ["관악구", "동작구"]:
        raise EngineError("UNSUPPORTED_AREA", "현재 관악구와 동작구 권역만 서비스 지원됩니다.", 400)

    area_pyeong = int(data.get("area_pyeong", 0))
    if area_pyeong < 5 or area_pyeong > 60:
        raise EngineError("INVALID_AREA", "전용면적은 5평 이상 60평 이하만 접수 가능합니다.", 400)

    # 3. Deduplication Check via Phone Hash
    phone_hash = hash_phone(phone)
    requested_date = data.get("requested_date")
    
    cursor = db.execute(
        "SELECT id, tracking_token FROM inquiries WHERE customer_phone_hash = ? AND requested_date = ? AND status != 'CANCELLED'",
        (phone_hash, requested_date)
    )
    existing = cursor.fetchone()
    if existing:
        return {
            "inquiry_id": existing["id"],
            "tracking_token": existing["tracking_token"],
            "status": "DUPLICATE_RETRIEVED",
            "message": "이미 동일 일자로 접수된 신청 건이 존재합니다."
        }

    # 4. Encrypt PII
    enc_name = encrypt_pii(name)
    enc_phone = encrypt_pii(phone)
    
    inquiry_id = f"inq_{uuid.uuid4()}"
    tracking_token = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    expires_at = (now + timedelta(hours=72)).isoformat()
    now_iso = now.isoformat()

    add_ons_json = json.dumps(data.get("add_ons", []))
    attribution_id = data.get("attribution_partner_id")

    # 5. Insert Inquiry
    db.execute(
        """
        INSERT INTO inquiries (
            id, tracking_token, attribution_partner_id, campaign_id,
            requested_date, time_window, district, neighborhood,
            housing_type, area_pyeong, add_ons,
            customer_name_encrypted, customer_phone_encrypted, customer_phone_hash,
            status, expires_at, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING_MATCH', ?, ?, ?)
        """,
        (
            inquiry_id, tracking_token, attribution_id, data.get("campaign_id"),
            requested_date, data.get("time_window", "MORNING"), district, data.get("neighborhood", ""),
            data.get("housing_type", "STUDIO"), area_pyeong, add_ons_json,
            enc_name, enc_phone, phone_hash,
            expires_at, now_iso, now_iso
        )
    )

    # 6. Insert Immutable Consent Event
    consent_id = f"cst_{uuid.uuid4()}"
    db.execute(
        """
        INSERT INTO consent_events (
            id, inquiry_id, terms_version, agreed_privacy_collection,
            agreed_third_party_share, agreed_marketing, client_ip_hash, user_agent, consented_at
        ) VALUES (?, ?, 'v1.0.0', 1, 1, ?, ?, ?, ?)
        """,
        (
            consent_id, inquiry_id,
            1 if data.get("agreed_marketing") else 0,
            hash_phone(client_ip), user_agent, now_iso
        )
    )

    # 7. Audit Log
    db.execute(
        """
        INSERT INTO audit_log (id, actor_id, action, resource_type, resource_id, ip_address, details, created_at)
        VALUES (?, 'CUSTOMER', 'INQUIRY_CREATED', 'INQUIRY', ?, ?, ?, ?)
        """,
        (
            f"aud_{uuid.uuid4()}", inquiry_id, client_ip,
            json.dumps({"masked_phone": mask_phone(phone), "masked_name": mask_name(name)}),
            now_iso
        )
    )
    db.commit()

    return {
        "inquiry_id": inquiry_id,
        "tracking_token": tracking_token,
        "status": "PENDING_MATCH",
        "masked_name": mask_name(name),
        "status_url": f"/slotbridge/status?token={tracking_token}"
    }

def dispatch_1to1_offer(db: DatabaseManager, inquiry_id: str) -> Optional[Dict[str, Any]]:
    """
    FR-05 & SBK-FIX-05: Unified Single Dispatch Entrypoint.
    Eliminates duplicated dispatch logic (B11) and delegates to deterministic matching engine.
    """
    from .matching import evaluate_and_dispatch_candidates
    return evaluate_and_dispatch_candidates(db, inquiry_id)

def accept_offer(db: DatabaseManager, offer_id: str, partner_id: str) -> Dict[str, Any]:
    """
    FR-06: Atomic acceptance with concurrency lock. Decrypts customer PII only for accepted partner.
    """
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()

    # Atomic check and update
    offer = db.execute("SELECT * FROM assignment_offers WHERE id = ? AND partner_id = ?", (offer_id, partner_id)).fetchone()
    if not offer:
        raise EngineError("OFFER_NOT_FOUND", "오퍼를 찾을 수 없습니다.", 404)
    if offer["status"] != "PENDING":
        raise EngineError("ALREADY_PROCESSED", f"오퍼가 이미 {offer['status']} 상태입니다.", 409)

    # Check expiration
    if now.isoformat() > offer["expires_at"]:
        db.execute("UPDATE assignment_offers SET status = 'EXPIRED', updated_at = ? WHERE id = ?", (now_iso, offer_id))
        db.execute("UPDATE capacity_slots SET status = 'AVAILABLE', updated_at = ? WHERE id = ?", (now_iso, offer["slot_id"]))
        db.execute("UPDATE inquiries SET status = 'PENDING_MATCH', updated_at = ? WHERE id = ?", (now_iso, offer["inquiry_id"]))
        db.commit()
        raise EngineError("OFFER_EXPIRED", "15분 수락 제한시간이 만료되었습니다.", 410)

    # Lock slot
    slot_res = db.execute(
        "UPDATE capacity_slots SET status = 'RESERVED', allocated_count = 1, updated_at = ? WHERE id = ? AND status = 'OFFERED'",
        (now_iso, offer["slot_id"])
    )
    if slot_res.rowcount == 0:
        raise EngineError("CONCURRENCY_CONFLICT", "슬롯이 이미 다른 요청에 배정되었습니다.", 409)

    # Atomic Update offer with condition WHERE status = 'PENDING'
    offer_cur = db.execute(
        "UPDATE assignment_offers SET status = 'ACCEPTED', updated_at = ? WHERE id = ? AND status = 'PENDING'",
        (now_iso, offer_id)
    )
    if offer_cur.rowcount == 0:
        raise EngineError("ALREADY_PROCESSED", "오퍼가 이미 처리되었거나 만료되었습니다.", 409)

    # Update inquiry
    db.execute("UPDATE inquiries SET status = 'MATCHED', updated_at = ? WHERE id = ?", (now_iso, offer["inquiry_id"]))

    # Record partner response
    db.execute(
        "INSERT INTO partner_responses (id, offer_id, partner_id, response_type, responded_at) VALUES (?, ?, ?, 'ACCEPT', ?)",
        (f"resp_{uuid.uuid4()}", offer_id, partner_id, now_iso)
    )

    # Create Match
    match_id = f"mat_{uuid.uuid4()}"
    db.execute(
        """
        INSERT INTO matches (id, inquiry_id, partner_id, slot_id, status, estimated_amount, created_at, updated_at)
        VALUES (?, ?, ?, ?, 'CONFIRMED', 250000, ?, ?)
        """,
        (match_id, offer["inquiry_id"], partner_id, offer["slot_id"], now_iso, now_iso)
    )

    # Fetch and decrypt customer PII
    inq = db.execute("SELECT customer_name_encrypted, customer_phone_encrypted FROM inquiries WHERE id = ?", (offer["inquiry_id"],)).fetchone()
    raw_name = decrypt_pii(inq["customer_name_encrypted"])
    raw_phone = decrypt_pii(inq["customer_phone_encrypted"])

    # Audit PII Decrypt
    db.execute(
        """
        INSERT INTO audit_log (id, actor_id, action, resource_type, resource_id, details, created_at)
        VALUES (?, ?, 'PII_DECRYPT', 'INQUIRY', ?, ?, ?)
        """,
        (
            f"aud_{uuid.uuid4()}", partner_id, offer["inquiry_id"],
            json.dumps({"match_id": match_id, "masked_phone": mask_phone(raw_phone)}),
            now_iso
        )
    )
    db.commit()

    return {
        "match_id": match_id,
        "status": "CONFIRMED",
        "decrypted_customer_name": raw_name,
        "decrypted_customer_phone": raw_phone,
        "instructions": "30분 이내에 고객님께 유선 연락하여 세부 일정과 주소지를 확인하세요."
    }

def submit_fulfillment(
    db: DatabaseManager,
    match_id: str,
    partner_id: str,
    final_amount: int,
    photo_urls: List[str],
    auto_accrue: bool = True
) -> Dict[str, Any]:
    """
    FR-07, FR-08 & SBK-FIX-06: Verified Fulfillment & Two-Way Completion Gateway.
    Guardrails:
    1. Match must exist and partner must match (prevents unassigned fraud).
    2. Minimum 2 photo URLs required (before & after photos).
    3. Final amount must be strictly positive (> 0).
    4. Cancelled inquiry cannot be fulfilled (blocks work after customer revocation).
    5. Disputed match cannot be fulfilled/accrued (freezes disputed settlement).
    """
    match = db.execute("SELECT * FROM matches WHERE id = ? AND partner_id = ?", (match_id, partner_id)).fetchone()
    if not match:
        raise EngineError("MATCH_NOT_FOUND", "일치하는 매칭 정보를 찾을 수 없습니다.", 404)

    # Guardrail 2: Photo evidence check (B07)
    if not photo_urls or len(photo_urls) < 2:
        raise EngineError("INSUFFICIENT_EVIDENCE", "현장 시공 전후 사진 2장 이상의 증빙이 필수적입니다.", 400)

    # Guardrail 3: Positive amount check
    if not final_amount or final_amount <= 0:
        raise EngineError("INVALID_AMOUNT", "최종 정산 금액은 0원보다 커야 합니다.", 400)

    # Guardrail 4: Inquiry cancellation check (B13)
    inq = db.execute("SELECT * FROM inquiries WHERE id = ?", (match["inquiry_id"],)).fetchone()
    if inq and inq["status"] == "CANCELLED":
        raise EngineError("INQUIRY_CANCELLED", "고객이 철회한 요청 건에 대해서는 시공 완료를 접수할 수 없습니다.", 400)

    # Guardrail 5: Dispute status check
    if match["status"] == "DISPUTED":
        raise EngineError("MATCH_DISPUTED", "분쟁 진행 중인 매칭에 대해서는 정산 청구를 진행할 수 없습니다.", 400)

    now_iso = datetime.now(timezone.utc).isoformat()
    evidence_id = f"evi_{uuid.uuid4()}"
    ver_status = "VERIFIED" if auto_accrue else "PENDING_CUSTOMER_CONFIRMATION"

    # Insert Fulfillment Evidence
    db.execute(
        """
        INSERT INTO fulfillment_evidence (
            id, match_id, photo_urls, completion_timestamp, partner_submitted_amount, verification_status, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (evidence_id, match_id, json.dumps(photo_urls), now_iso, final_amount, ver_status, now_iso, now_iso)
    )

    # Update Match & Slot
    target_match_status = "COMPLETED" if not auto_accrue else "VERIFIED"
    db.execute("UPDATE matches SET status = ?, final_amount = ?, updated_at = ? WHERE id = ?", (target_match_status, final_amount, now_iso, match_id))
    db.execute("UPDATE capacity_slots SET status = 'COMPLETED', updated_at = ? WHERE id = ?", (now_iso, match["slot_id"]))
    db.execute("UPDATE inquiries SET status = 'FULFILLED', updated_at = ? WHERE id = ?", (now_iso, match["inquiry_id"]))
    db.execute("UPDATE partners SET total_completed_count = total_completed_count + 1 WHERE id = ?", (partner_id,))

    # Ledger Accounting (Only accrued if verified!)
    platform_fee = int(final_amount * 0.08) # 8%
    referral_fee = int(platform_fee * 0.35) # 35% of 8%

    if auto_accrue:
        # 1. Platform Revenue Event
        db.execute(
            """
            INSERT INTO ledger_events (id, match_id, event_type, amount, debit_account, credit_account, description, created_at)
            VALUES (?, ?, 'REVENUE_ACCRUED', ?, 'ACCOUNTS_RECEIVABLE_PARTNER', 'PLATFORM_COMMISSION_REVENUE', 'B2B 성과 수수료 8%', ?)
            """,
            (f"led_{uuid.uuid4()}", match_id, platform_fee, now_iso)
        )

        # 2. Referral Payout Allocation (if inquiry has attribution)
        if inq and inq["attribution_partner_id"]:
            db.execute(
                """
                INSERT INTO ledger_events (id, match_id, event_type, amount, debit_account, credit_account, description, created_at)
                VALUES (?, ?, 'REFERRAL_ALLOCATED', ?, 'REFERRAL_COMMISSION_EXPENSE', 'ACCOUNTS_PAYABLE_REFERRER', '원 유입업체 리퍼럴 배분 35%', ?)
                """,
                (f"led_{uuid.uuid4()}", match_id, referral_fee, now_iso)
            )

    db.commit()

    return {
        "match_id": match_id,
        "final_amount": final_amount,
        "platform_fee": platform_fee,
        "referral_fee": referral_fee,
        "status": "FULFILLED" if auto_accrue else "COMPLETED",
        "verification_status": ver_status
    }

def confirm_customer_completion(
    db: DatabaseManager,
    match_id: str,
    customer_rating: int = 5,
    customer_notes: Optional[str] = None
) -> Dict[str, Any]:
    """
    SBK-FIX-06: Customer explicitly confirms completed work.
    Transitions match to BILLABLE and writes immutable ledger events.
    """
    match = db.execute("SELECT * FROM matches WHERE id = ?", (match_id,)).fetchone()
    if not match:
        raise EngineError("MATCH_NOT_FOUND", "매칭을 찾을 수 없습니다.", 404)
    if match["status"] == "DISPUTED":
        raise EngineError("MATCH_DISPUTED", "분쟁 진행 중인 건은 완료 확인할 수 없습니다.", 400)
    if match["status"] not in ("COMPLETED", "VERIFIED"):
        raise EngineError("INVALID_MATCH_STATUS", f"완료 확인 대기 상태(COMPLETED)가 아닙니다 (현재: {match['status']}).", 400)

    now_iso = datetime.now(timezone.utc).isoformat()

    # Update evidence to VERIFIED
    db.execute(
        "UPDATE fulfillment_evidence SET verification_status = 'VERIFIED', updated_at = ? WHERE match_id = ?",
        (now_iso, match_id)
    )

    # Transition match to BILLABLE
    db.execute("UPDATE matches SET status = 'BILLABLE', updated_at = ? WHERE id = ?", (now_iso, match_id))

    final_amount = match["final_amount"] or 250000
    platform_fee = int(final_amount * 0.08)
    referral_fee = int(platform_fee * 0.35)

    # Check if ledger already exists
    existing_ledger = db.execute("SELECT id FROM ledger_events WHERE match_id = ? AND event_type = 'REVENUE_ACCRUED'", (match_id,)).fetchone()
    if not existing_ledger:
        db.execute(
            """
            INSERT INTO ledger_events (id, match_id, event_type, amount, debit_account, credit_account, description, created_at)
            VALUES (?, ?, 'REVENUE_ACCRUED', ?, 'ACCOUNTS_RECEIVABLE_PARTNER', 'PLATFORM_COMMISSION_REVENUE', 'B2B 성과 수수료 8%', ?)
            """,
            (f"led_{uuid.uuid4()}", match_id, platform_fee, now_iso)
        )
        inq = db.execute("SELECT attribution_partner_id FROM inquiries WHERE id = ?", (match["inquiry_id"],)).fetchone()
        if inq and inq["attribution_partner_id"]:
            db.execute(
                """
                INSERT INTO ledger_events (id, match_id, event_type, amount, debit_account, credit_account, description, created_at)
                VALUES (?, ?, 'REFERRAL_ALLOCATED', ?, 'REFERRAL_COMMISSION_EXPENSE', 'ACCOUNTS_PAYABLE_REFERRER', '원 유입업체 리퍼럴 배분 35%', ?)
                """,
                (f"led_{uuid.uuid4()}", match_id, referral_fee, now_iso)
            )

    # Audit log
    db.execute(
        """
        INSERT INTO audit_log (id, actor_id, action, resource_type, resource_id, details, created_at)
        VALUES (?, 'CUSTOMER', 'COMPLETION_CONFIRMED', 'MATCH', ?, ?, ?)
        """,
        (f"aud_{uuid.uuid4()}", match_id, json.dumps({"rating": customer_rating, "notes": customer_notes}), now_iso)
    )
    db.commit()

    return {
        "match_id": match_id,
        "status": "BILLABLE",
        "verification_status": "VERIFIED",
        "platform_fee": platform_fee,
        "referral_fee": referral_fee
    }

def admin_override_completion(
    db: DatabaseManager,
    match_id: str,
    admin_id: str,
    reason: str
) -> Dict[str, Any]:
    """
    SBK-FIX-06: Admin exception override for completion verification.
    Mandatory audit reason required.
    """
    if not reason or len(reason.strip()) < 5:
        raise EngineError("REASON_REQUIRED", "관리자 예외 승인 시 명확한 승인 근거 사유(5자 이상)가 필수입니다.", 400)

    match = db.execute("SELECT * FROM matches WHERE id = ?", (match_id,)).fetchone()
    if not match:
        raise EngineError("MATCH_NOT_FOUND", "매칭을 찾을 수 없습니다.", 404)

    now_iso = datetime.now(timezone.utc).isoformat()
    db.execute(
        "UPDATE fulfillment_evidence SET verification_status = 'VERIFIED', updated_at = ? WHERE match_id = ?",
        (now_iso, match_id)
    )
    db.execute("UPDATE matches SET status = 'BILLABLE', updated_at = ? WHERE id = ?", (now_iso, match_id))

    final_amount = match["final_amount"] or 250000
    platform_fee = int(final_amount * 0.08)
    referral_fee = int(platform_fee * 0.35)

    existing_ledger = db.execute("SELECT id FROM ledger_events WHERE match_id = ? AND event_type = 'REVENUE_ACCRUED'", (match_id,)).fetchone()
    if not existing_ledger:
        db.execute(
            """
            INSERT INTO ledger_events (id, match_id, event_type, amount, debit_account, credit_account, description, created_at)
            VALUES (?, ?, 'REVENUE_ACCRUED', ?, 'ACCOUNTS_RECEIVABLE_PARTNER', 'PLATFORM_COMMISSION_REVENUE', 'B2B 성과 수수료 8%', ?)
            """,
            (f"led_{uuid.uuid4()}", match_id, platform_fee, now_iso)
        )

    db.execute(
        """
        INSERT INTO audit_log (id, actor_id, action, resource_type, resource_id, details, created_at)
        VALUES (?, ?, 'ADMIN_OVERRIDE_COMPLETION', 'MATCH', ?, ?, ?)
        """,
        (f"aud_{uuid.uuid4()}", admin_id, match_id, json.dumps({"reason": reason}, ensure_ascii=False), now_iso)
    )
    db.commit()

    return {
        "match_id": match_id,
        "status": "BILLABLE",
        "verification_status": "VERIFIED",
        "override_reason": reason
    }

def revoke_inquiry(db: DatabaseManager, tracking_token: str) -> Dict[str, Any]:
    """
    Privacy Right to Erasure (1-Click deletion). Overwrites PII with tombstone and cancels pending matching.
    """
    inq = db.execute("SELECT id, status FROM inquiries WHERE tracking_token = ?", (tracking_token,)).fetchone()
    if not inq:
        raise EngineError("NOT_FOUND", "신청 정보를 찾을 수 없습니다.", 404)

    now_iso = datetime.now(timezone.utc).isoformat()
    tombstone = "ERASED_" + uuid.uuid4().hex[:12]

    # Overwrite PII
    db.execute(
        """
        UPDATE inquiries 
        SET customer_name_encrypted = ?, customer_phone_encrypted = ?, customer_phone_hash = ?, status = 'CANCELLED', updated_at = ?
        WHERE id = ?
        """,
        (tombstone, tombstone, tombstone, now_iso, inq["id"])
    )

    # Cancel any pending offers
    db.execute(
        "UPDATE assignment_offers SET status = 'CANCELLED', updated_at = ? WHERE inquiry_id = ? AND status = 'PENDING'",
        (now_iso, inq["id"])
    )

    # Record Erasure Request
    db.execute(
        """
        INSERT INTO data_erasure_requests (id, inquiry_id, requested_by, status, erased_fields, erased_at)
        VALUES (?, ?, 'CUSTOMER_TOKEN', 'COMPLETED', 'customer_name,customer_phone', ?)
        """,
        (f"era_{uuid.uuid4()}", inq["id"], now_iso)
    )

    # Audit Log
    db.execute(
        """
        INSERT INTO audit_log (id, actor_id, action, resource_type, resource_id, details, created_at)
        VALUES (?, 'CUSTOMER', 'DATA_ERASURE', 'INQUIRY', ?, '{"reason": "Customer 1-click self-service revocation"}', ?)
        """,
        (f"aud_{uuid.uuid4()}", inq["id"], now_iso)
    )
    db.commit()

    return {
        "success": True,
        "message": "개인정보가 완전히 영구 파기되었으며 매칭 신청이 취소되었습니다."
    }
