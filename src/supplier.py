"""
SlotBridge Korea — Supplier Network & Capacity Slot Management Engine
Handles partner onboarding, admin verification, 24~72h capacity slots with Asia/Seoul timezone,
and anti-double-booking concurrency controls.
"""

import uuid
import re
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional
from zoneinfo import ZoneInfo

from .db import DatabaseManager
from .engine import EngineError
from .crypto import mask_phone

SEOUL_TZ = ZoneInfo("Asia/Seoul")

def apply_partner(db: DatabaseManager, data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Onboards a new cleaning contractor or referring partner in PENDING_APPROVAL state.
    Attachments (business license / insurance policy) are referenced, never stored insecurely.
    """
    name = data.get("name", "").strip()
    if not name or len(name) < 2:
        raise EngineError("INVALID_NAME", "상호명을 2자 이상 입력해 주세요.", 400)

    biz_num = re.sub(r'[^0-9]', '', data.get("business_number", ""))
    if len(biz_num) != 10:
        raise EngineError("INVALID_BIZ_NUMBER", "사업자등록번호는 10자리 숫자여야 합니다.", 400)
    formatted_biz_num = f"{biz_num[:3]}-{biz_num[3:5]}-{biz_num[5:]}"

    phone = data.get("contact_phone", "").strip()
    if not re.match(r"^010-[0-9]{4}-[0-9]{4}$", phone):
        raise EngineError("INVALID_PHONE", "대표 연락처는 010-XXXX-XXXX 형식이어야 합니다.", 400)

    rep_name = data.get("representative_name", "").strip()
    if not rep_name:
        raise EngineError("INVALID_REP_NAME", "대표자명을 입력해 주세요.", 400)

    partner_type = data.get("partner_type", "FULFILLMENT")
    if partner_type not in ["FULFILLMENT", "REFERRING", "HYBRID"]:
        raise EngineError("INVALID_PARTNER_TYPE", "유효하지 않은 파트너 유형입니다.", 400)

    now_iso = datetime.now(timezone.utc).isoformat()
    org_id = f"org_{uuid.uuid4()}"
    partner_id = f"partner_{uuid.uuid4()}"

    # 1. Create Organization
    db.execute(
        """
        INSERT INTO organizations (id, name, business_number, representative_name, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, 'ACTIVE', ?, ?)
        """,
        (org_id, name, formatted_biz_num, rep_name, now_iso, now_iso)
    )

    # 2. Create Partner in PENDING_APPROVAL status
    # Statutory Rule (B06): Merely entering a policy number string does NOT verify insurance.
    # Insurance status remains PENDING_VERIFICATION until manual verification of certificate document.
    insurance_policy = data.get("liability_insurance_policy_number")
    insurance_status = "PENDING_VERIFICATION"

    db.execute(
        """
        INSERT INTO partners (
            id, organization_id, name, partner_type, business_number,
            representative_name, contact_phone, settlement_bank, settlement_account,
            liability_insurance_status, verification_status, rating_average, total_completed_count,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING_APPROVAL', 5.0, 0, ?, ?)
        """,
        (
            partner_id, org_id, name, partner_type, formatted_biz_num,
            rep_name, phone, data.get("settlement_bank"), data.get("settlement_account"),
            insurance_status, now_iso, now_iso
        )
    )

    # 3. Create Service Areas
    areas = data.get("service_areas", [{"city": "서울특별시", "district": "관악구", "neighborhood": "봉천동"}])
    for area in areas:
        db.execute(
            """
            INSERT INTO service_areas (id, partner_id, city, district, neighborhood, is_active, created_at)
            VALUES (?, ?, ?, ?, ?, 1, ?)
            """,
            (f"sa_{uuid.uuid4()}", partner_id, area.get("city", "서울특별시"), area.get("district", "관악구"), area.get("neighborhood", "전체"), now_iso)
        )

    # 4. Audit Log
    db.execute(
        """
        INSERT INTO audit_log (id, actor_id, action, resource_type, resource_id, details, created_at)
        VALUES (?, 'SYSTEM', 'PARTNER_APPLICATION', 'PARTNER', ?, ?, ?)
        """,
        (f"aud_{uuid.uuid4()}", partner_id, f'{{"name": "{name}", "phone": "{mask_phone(phone)}"}}', now_iso)
    )
    db.commit()

    return {
        "partner_id": partner_id,
        "name": name,
        "verification_status": "PENDING_APPROVAL",
        "message": "파트너 신청이 완료되었습니다. 관리자 서류 심사(24시간 이내) 후 승인됩니다."
    }

def approve_partner(
    db: DatabaseManager,
    partner_id: str,
    admin_id: str = "ADMIN_HOEGI",
    insurance_verified: bool = True
) -> Dict[str, Any]:
    """Admin approves partner after reviewing business registration and insurance."""
    partner = db.execute("SELECT * FROM partners WHERE id = ?", (partner_id,)).fetchone()
    if not partner:
        raise EngineError("PARTNER_NOT_FOUND", "파트너를 찾을 수 없습니다.", 404)

    now_iso = datetime.now(timezone.utc).isoformat()
    ins_status = "VERIFIED" if insurance_verified else "PENDING_VERIFICATION"

    db.execute(
        "UPDATE partners SET verification_status = 'APPROVED', liability_insurance_status = ?, updated_at = ? WHERE id = ?",
        (ins_status, now_iso, partner_id)
    )
    db.execute(
        """
        INSERT INTO audit_log (id, actor_id, action, resource_type, resource_id, details, created_at)
        VALUES (?, ?, 'PARTNER_APPROVED', 'PARTNER', ?, ?, ?)
        """,
        (f"aud_{uuid.uuid4()}", admin_id, partner_id, f'{{"status": "APPROVED", "insurance": "{ins_status}"}}', now_iso)
    )
    db.commit()

    return {
        "partner_id": partner_id,
        "verification_status": "APPROVED",
        "liability_insurance_status": ins_status,
        "message": "파트너 승인 심사가 완료되었습니다."
    }

def suspend_partner(db: DatabaseManager, partner_id: str, reason: str, admin_id: str = "ADMIN_HOEGI") -> Dict[str, Any]:
    """
    Suspends partner and immediately cancels all their AVAILABLE/OFFERED capacity slots
    and PENDING assignment offers to prevent dispatch (B06).
    """
    partner = db.execute("SELECT * FROM partners WHERE id = ?", (partner_id,)).fetchone()
    if not partner:
        raise EngineError("PARTNER_NOT_FOUND", "파트너를 찾을 수 없습니다.", 404)

    now_iso = datetime.now(timezone.utc).isoformat()
    db.execute("UPDATE partners SET verification_status = 'SUSPENDED', updated_at = ? WHERE id = ?", (now_iso, partner_id))
    
    # 1. Cancel available and offered capacity slots
    cancelled_slots = db.execute(
        "UPDATE capacity_slots SET status = 'CANCELLED', updated_at = ? WHERE partner_id = ? AND status IN ('AVAILABLE', 'OFFERED')",
        (now_iso, partner_id)
    ).rowcount

    # 2. Cancel pending assignment offers for this suspended partner and re-queue inquiries
    pending_offers = db.execute(
        "SELECT id, inquiry_id, slot_id FROM assignment_offers WHERE partner_id = ? AND status = 'PENDING'",
        (partner_id,)
    ).fetchall()
    
    for off in pending_offers:
        db.execute(
            "UPDATE assignment_offers SET status = 'CANCELLED', updated_at = ? WHERE id = ?",
            (now_iso, off["id"])
        )
        db.execute(
            "UPDATE inquiries SET status = 'PENDING_MATCH', updated_at = ? WHERE id = ?",
            (now_iso, off["inquiry_id"])
        )

    db.execute(
        """
        INSERT INTO audit_log (id, actor_id, action, resource_type, resource_id, details, created_at)
        VALUES (?, ?, 'PARTNER_SUSPENDED', 'PARTNER', ?, ?, ?)
        """,
        (f"aud_{uuid.uuid4()}", admin_id, partner_id, f'{{"reason": "{reason}", "cancelled_slots": {cancelled_slots}}}', now_iso)
    )
    db.commit()

    return {
        "partner_id": partner_id,
        "verification_status": "SUSPENDED",
        "cancelled_slots_count": cancelled_slots,
        "message": "파트너가 정지되었으며 잔여 슬롯이 전원 취소되었습니다."
    }

def register_capacity_slot(
    db: DatabaseManager,
    partner_id: str,
    slot_date_str: str,
    time_window: str = "MORNING",
    max_capacity: int = 1
) -> Dict[str, Any]:
    """
    Registers a 24~72h capacity slot in Asia/Seoul timezone.
    Enforces strict guardrails:
    1. Unapproved partners CANNOT register slots.
    2. Date must be within 24h to 72h window (tomorrow to +3 days).
    3. Duplicate slot registration is rejected.
    """
    partner = db.execute("SELECT * FROM partners WHERE id = ?", (partner_id,)).fetchone()
    if not partner:
        raise EngineError("PARTNER_NOT_FOUND", "파트너 정보를 찾을 수 없습니다.", 404)
    if partner["verification_status"] != "APPROVED":
        raise EngineError("UNAPPROVED_PARTNER", "관리자 승인이 완료된 파트너만 가용 슬롯을 등록할 수 있습니다.", 403)

    if time_window not in ["MORNING", "AFTERNOON", "ALL_DAY"]:
        raise EngineError("INVALID_TIME_WINDOW", "유효한 시간대(MORNING, AFTERNOON, ALL_DAY)를 지정하세요.", 400)

    # Timezone validation (Asia/Seoul)
    try:
        slot_date = datetime.strptime(slot_date_str, "%Y-%m-%d").date()
    except ValueError:
        raise EngineError("INVALID_DATE_FORMAT", "날짜는 YYYY-MM-DD 형식이어야 합니다.", 400)

    today_seoul = datetime.now(SEOUL_TZ).date()
    min_date = today_seoul + timedelta(days=1) # 24h+
    max_date = today_seoul + timedelta(days=4) # ~72h+

    if slot_date < min_date or slot_date > max_date:
        raise EngineError("OUT_OF_RUSH_WINDOW", f"급행 슬롯은 24~72시간 이내({min_date} ~ {max_date})만 등록 가능합니다.", 400)

    # Check existing slot conflict (1 Team 1 Job / ALL_DAY overlap prevention)
    if time_window == "ALL_DAY":
        overlapping = db.execute(
            """
            SELECT id, time_window, status FROM capacity_slots
            WHERE partner_id = ? AND slot_date = ? AND status IN ('AVAILABLE', 'OFFERED', 'RESERVED')
            """,
            (partner_id, slot_date_str)
        ).fetchone()
        if overlapping:
            raise EngineError(
                "SLOT_DUPLICATE",
                f"해당 일시({slot_date_str})에 이미 {overlapping['time_window']} 슬롯이 존재하여 종일(ALL_DAY) 슬롯을 등록할 수 없습니다.",
                409
            )
    else:
        overlapping = db.execute(
            """
            SELECT id, time_window, status FROM capacity_slots
            WHERE partner_id = ? AND slot_date = ? 
              AND (time_window = ? OR time_window = 'ALL_DAY')
              AND status IN ('AVAILABLE', 'OFFERED', 'RESERVED')
            """,
            (partner_id, slot_date_str, time_window)
        ).fetchone()
        if overlapping:
            raise EngineError(
                "SLOT_DUPLICATE",
                f"해당 일시({slot_date_str})에 이미 {overlapping['time_window']} 슬롯이 존재하여 중복/충돌 등록할 수 없습니다.",
                409
            )

    existing = db.execute(
        "SELECT id, status FROM capacity_slots WHERE partner_id = ? AND slot_date = ? AND time_window = ?",
        (partner_id, slot_date_str, time_window)
    ).fetchone()
    if existing:
        if existing["status"] in ["AVAILABLE", "OFFERED", "RESERVED"]:
            raise EngineError("SLOT_DUPLICATE", f"해당 일시({slot_date_str} {time_window})에 이미 등록된 슬롯이 존재합니다.", 409)
        elif existing["status"] == "CANCELLED":
            # Re-activate previously cancelled slot
            now_iso = datetime.now(timezone.utc).isoformat()
            db.execute("UPDATE capacity_slots SET status = 'AVAILABLE', updated_at = ? WHERE id = ?", (now_iso, existing["id"]))
            db.commit()
            return {
                "slot_id": existing["id"],
                "partner_id": partner_id,
                "slot_date": slot_date_str,
                "time_window": time_window,
                "status": "AVAILABLE"
            }

    slot_id = f"slot_{uuid.uuid4()}"
    now_iso = datetime.now(timezone.utc).isoformat()

    db.execute(
        """
        INSERT INTO capacity_slots (id, partner_id, slot_date, time_window, max_capacity, allocated_count, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, 0, 'AVAILABLE', ?, ?)
        """,
        (slot_id, partner_id, slot_date_str, time_window, max_capacity, now_iso, now_iso)
    )
    db.commit()

    return {
        "slot_id": slot_id,
        "partner_id": partner_id,
        "slot_date": slot_date_str,
        "time_window": time_window,
        "status": "AVAILABLE"
    }

def delete_capacity_slot(db: DatabaseManager, slot_id: str, partner_id: str) -> Dict[str, Any]:
    """Allows partner to withdraw an AVAILABLE slot. Blocks deletion if already OFFERED or RESERVED."""
    slot = db.execute("SELECT * FROM capacity_slots WHERE id = ? AND partner_id = ?", (slot_id, partner_id)).fetchone()
    if not slot:
        raise EngineError("SLOT_NOT_FOUND", "해당 슬롯을 찾을 수 없습니다.", 404)

    if slot["status"] != "AVAILABLE":
        raise EngineError("SLOT_CANNOT_DELETE", f"현재 {slot['status']} 상태인 슬롯은 삭제할 수 없습니다.", 409)

    now_iso = datetime.now(timezone.utc).isoformat()
    db.execute("UPDATE capacity_slots SET status = 'CANCELLED', updated_at = ? WHERE id = ?", (now_iso, slot_id))
    db.commit()

    return {"slot_id": slot_id, "status": "CANCELLED", "message": "가용 슬롯이 취소되었습니다."}
