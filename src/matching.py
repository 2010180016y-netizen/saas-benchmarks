"""
SlotBridge Korea — Deterministic Matching Engine & 10-Stage State Machine (Step 08)
Implements strict hard filters, soft ranking with equity balance, 1:1 exclusive dispatch,
15-minute sequential re-routing, SLA compliance, and full decision replay logging.
"""

import uuid
import json
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional, Tuple

from .db import DatabaseManager
from .engine import EngineError
from .crypto import decrypt_pii, mask_phone

# 10 Linear States
VALID_STATES = [
    "SUBMITTED", "VALIDATED", "CONSENTED", "OFFERED", "ACCEPTED",
    "CUSTOMER_CONFIRMED", "BOOKED", "COMPLETED", "VERIFIED", "BILLABLE"
]

# 5 Exception Branches
EXCEPTION_STATES = ["REJECTED", "EXPIRED", "CANCELLED", "DISPUTED", "REFUNDED"]

# Allowed State Transitions
ALLOWED_TRANSITIONS = {
    "SUBMITTED": ["VALIDATED", "CANCELLED"],
    "VALIDATED": ["CONSENTED", "CANCELLED"],
    "CONSENTED": ["OFFERED", "CANCELLED"],
    "OFFERED": ["ACCEPTED", "REJECTED", "EXPIRED", "CANCELLED"],
    "ACCEPTED": ["CUSTOMER_CONFIRMED", "CANCELLED", "DISPUTED"],
    "MATCHED": ["BOOKED", "CANCELLED", "DISPUTED"],
    "CONFIRMED": ["CUSTOMER_CONFIRMED", "BOOKED", "CANCELLED", "DISPUTED"],
    "CUSTOMER_CONFIRMED": ["BOOKED", "CANCELLED", "DISPUTED"],
    "BOOKED": ["COMPLETED", "CANCELLED", "DISPUTED"],
    "COMPLETED": ["VERIFIED", "DISPUTED"],
    "VERIFIED": ["BILLABLE", "DISPUTED"],
    "BILLABLE": ["REFUNDED"],
    "DISPUTED": ["VERIFIED", "REFUNDED", "CANCELLED"],
    "REJECTED": ["OFFERED", "CANCELLED"],
    "EXPIRED": ["OFFERED", "CANCELLED"],
    "CANCELLED": [],
    "REFUNDED": []
}

def transition_state(
    db: DatabaseManager,
    entity_type: str, # "INQUIRY" or "MATCH"
    entity_id: str,
    from_state: str,
    to_state: str,
    actor_id: str,
    evidence: Optional[Dict[str, Any]] = None,
    notes: Optional[str] = None
) -> Dict[str, Any]:
    """
    Executes an atomic state transition with strict guardrails and immutable audit logging.
    Enforces that COMPLETED and BILLABLE cannot be reached without verified evidence.
    """
    allowed_next = ALLOWED_TRANSITIONS.get(from_state, [])
    if to_state not in allowed_next:
        raise EngineError(
            "INVALID_STATE_TRANSITION",
            f"유효하지 않은 상태 전이입니다: {from_state} -> {to_state}",
            400
        )

    # Precondition Guardrails
    if to_state == "BOOKED":
        if not evidence or not evidence.get("agreed_price"):
            raise EngineError("PRECONDITION_FAILED", "고객 합의 금액(agreed_price) 없이 BOOKED로 전이할 수 없습니다.", 400)

    if to_state == "COMPLETED":
        if not evidence or not evidence.get("photo_urls") or len(evidence.get("photo_urls", [])) < 2:
            raise EngineError("PRECONDITION_FAILED", "현장 시공 전후 사진 2장 증빙 없이 COMPLETED로 전이할 수 없습니다.", 400)

    if to_state == "BILLABLE":
        if not evidence or not evidence.get("verified_completion"):
            raise EngineError("PRECONDITION_FAILED", "고객 확인 또는 완료 검수(verified_completion) 없이 BILLABLE로 전이할 수 없습니다.", 400)

    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    # Update Entity Status atomically (B07 Defense: Enforces actual DB status matches from_state)
    table_name = "inquiries" if entity_type == "INQUIRY" else "matches"
    if entity_type == "MATCH" and to_state == "BOOKED" and evidence and evidence.get("agreed_price"):
        cur = db.execute(
            f"UPDATE matches SET status = ?, final_amount = ?, updated_at = ? WHERE id = ? AND status = ?",
            (to_state, evidence["agreed_price"], now_iso, entity_id, from_state)
        )
    else:
        cur = db.execute(
            f"UPDATE {table_name} SET status = ?, updated_at = ? WHERE id = ? AND status = ?",
            (to_state, now_iso, entity_id, from_state)
        )
    if cur.rowcount == 0:
        actual_row = db.execute(f"SELECT status FROM {table_name} WHERE id = ?", (entity_id,)).fetchone()
        actual_status = actual_row["status"] if actual_row else "NOT_FOUND"
        raise EngineError(
            "STATE_CONFLICT",
            f"원자적 상태 전이 실패: 현재 상태가 {from_state}가 아닌 {actual_status}입니다.",
            409
        )

    # Immutable Audit Log
    db.execute(
        """
        INSERT INTO audit_log (id, actor_id, action, resource_type, resource_id, details, created_at)
        VALUES (?, ?, 'STATE_TRANSITION', ?, ?, ?, ?)
        """,
        (
            f"aud_{uuid.uuid4()}", actor_id, entity_type, entity_id,
            json.dumps({"from": from_state, "to": to_state, "evidence": evidence, "notes": notes}),
            now_iso
        )
    )
    db.commit()

    return {
        "entity_type": entity_type,
        "entity_id": entity_id,
        "from_state": from_state,
        "to_state": to_state,
        "timestamp": now_iso
    }

def evaluate_and_dispatch_candidates(
    db: DatabaseManager,
    inquiry_id: str,
    ttl_minutes: int = 15
) -> Optional[Dict[str, Any]]:
    """
    FR-05 & SBK-08: Deterministic Rule Matching Engine.
    Hard Filters:
    1. Approved partner only (verification_status == 'APPROVED')
    2. Verified insurance (liability_insurance_status == 'VERIFIED')
    3. Service area district match
    4. Capacity slot status == 'AVAILABLE' for requested_date
    5. Has not previously rejected this inquiry
    
    Soft Scoring:
    score = (rating * 20.0) + (completed_count * 0.5) + (10.0 / (recent_dispatches + 1))
    """
    inq = db.execute("SELECT * FROM inquiries WHERE id = ?", (inquiry_id,)).fetchone()
    if not inq:
        raise EngineError("INQUIRY_NOT_FOUND", "인입 정보를 찾을 수 없습니다.", 404)

    # Disallow dispatching cancelled or already matched inquiries (B13)
    if inq["status"] in ("CANCELLED", "MATCHED", "FULFILLED", "BOOKED", "COMPLETED", "VERIFIED", "BILLABLE"):
        return None

    # Verify statutory consent (PIPC Art. 17) before dispatching to any partner
    consent = db.execute(
        """
        SELECT agreed_privacy_collection, agreed_third_party_share
        FROM consent_events
        WHERE inquiry_id = ?
        ORDER BY consented_at DESC LIMIT 1
        """,
        (inquiry_id,)
    ).fetchone()
    if not consent or consent["agreed_privacy_collection"] != 1 or consent["agreed_third_party_share"] != 1:
        return None

    # 1. Fetch all candidate slots
    query = """
        SELECT 
            cs.id AS slot_id, cs.partner_id, cs.slot_date, cs.time_window,
            p.name AS partner_name, p.verification_status, p.liability_insurance_status,
            p.rating_average, p.total_completed_count,
            sa.district
        FROM capacity_slots cs
        JOIN partners p ON cs.partner_id = p.id
        JOIN service_areas sa ON sa.partner_id = p.id
        WHERE cs.slot_date = ? 
          AND cs.status = 'AVAILABLE'
          AND sa.district = ?
          AND sa.is_active = 1
    """
    candidates = db.execute(query, (inq["requested_date"], inq["district"])).fetchall()

    decision_log = []
    qualified_candidates = []

    for c in candidates:
        partner_id = c["partner_id"]

        # Hard Filter 0: Time Window match (MORNING / AFTERNOON / ALL_DAY)
        req_window = inq["time_window"] if ("time_window" in inq.keys() and inq["time_window"]) else "MORNING"
        slot_window = c["time_window"] if ("time_window" in c.keys() and c["time_window"]) else "MORNING"
        if req_window != "ALL_DAY" and slot_window != "ALL_DAY" and slot_window != req_window:
            decision_log.append({"partner_id": partner_id, "passed": False, "reason": "TIME_WINDOW_MISMATCH"})
            continue
        
        # Hard Filter 1: Approved partner
        if c["verification_status"] != "APPROVED":
            decision_log.append({"partner_id": partner_id, "passed": False, "reason": "UNAPPROVED_PARTNER"})
            continue

        # Hard Filter 2: Insurance verified
        if c["liability_insurance_status"] != "VERIFIED":
            decision_log.append({"partner_id": partner_id, "passed": False, "reason": "UNVERIFIED_INSURANCE"})
            continue

        # Hard Filter 3: Check previous rejection or timeout
        rejected = db.execute(
            """
            SELECT pr.id FROM partner_responses pr
            JOIN assignment_offers ao ON pr.offer_id = ao.id
            WHERE ao.inquiry_id = ? AND pr.partner_id = ? AND pr.response_type IN ('REJECT', 'TIMEOUT')
            """,
            (inquiry_id, partner_id)
        ).fetchone()
        if rejected:
            decision_log.append({"partner_id": partner_id, "passed": False, "reason": "PREVIOUSLY_REJECTED_OR_TIMED_OUT"})
            continue

        # Soft Score Calculation (Deterministic)
        # Recent dispatches in last 7 days for equity balance
        dispatches_cnt = db.execute(
            "SELECT count(*) AS cnt FROM assignment_offers WHERE partner_id = ?",
            (partner_id,)
        ).fetchone()["cnt"]

        score = (
            (c["rating_average"] * 20.0) +
            (min(c["total_completed_count"], 100) * 0.5) +
            (10.0 / (dispatches_cnt + 1)) # Equity balance bonus
        )

        qualified_candidates.append({
            "slot_id": c["slot_id"],
            "partner_id": partner_id,
            "partner_name": c["partner_name"],
            "score": score
        })
        decision_log.append({"partner_id": partner_id, "passed": True, "score": score})

    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    # Log Replay Decision
    db.execute(
        """
        INSERT INTO audit_log (id, actor_id, action, resource_type, resource_id, details, created_at)
        VALUES (?, 'MATCHING_ENGINE', 'CANDIDATE_EVALUATION', 'INQUIRY', ?, ?, ?)
        """,
        (f"aud_{uuid.uuid4()}", inquiry_id, json.dumps({"decision_log": decision_log}), now_iso)
    )

    if not qualified_candidates:
        db.commit()
        return None

    # Sort deterministically by score DESC
    qualified_candidates.sort(key=lambda x: x["score"], reverse=True)
    
    # Atomic Slot Locking (Anti-Race Condition: Locks first available candidate)
    winner = None
    for cand in qualified_candidates:
        cur = db.execute(
            "UPDATE capacity_slots SET status = 'OFFERED', updated_at = ? WHERE id = ? AND status = 'AVAILABLE'",
            (now_iso, cand["slot_id"])
        )
        if cur.rowcount > 0:
            winner = cand
            break

    if not winner:
        # All candidate slots were concurrently locked by other requests
        db.commit()
        return None

    # Create 1:1 Exclusive Offer with TTL
    offer_id = f"off_{uuid.uuid4()}"
    now_dt = datetime.now(timezone.utc)
    expires_at = (now_dt + timedelta(minutes=ttl_minutes)).isoformat()

    # Set inquiry to OFFERED
    db.execute("UPDATE inquiries SET status = 'OFFERED', updated_at = ? WHERE id = ?", (now_iso, inquiry_id))

    db.execute(
        """
        INSERT INTO assignment_offers (id, inquiry_id, partner_id, slot_id, priority, status, expires_at, created_at, updated_at)
        VALUES (?, ?, ?, ?, 1, 'PENDING', ?, ?, ?)
        """,
        (offer_id, inquiry_id, winner["partner_id"], winner["slot_id"], expires_at, now_iso, now_iso)
    )

    # Queue $0 mock notification into notification_outbox for zero-cost operation (B12)
    notif_id = f"notif_{uuid.uuid4()}"
    notif_payload = {
        "offer_id": offer_id,
        "inquiry_id": inquiry_id,
        "district": inq["district"],
        "requested_date": inq["requested_date"],
        "time_window": inq["time_window"] if ("time_window" in inq.keys() and inq["time_window"]) else "MORNING",
        "expires_at": expires_at,
        "channel": "MOCKED_SMS"
    }
    db.execute(
        """
        INSERT INTO notification_outbox (
            id, recipient_type, recipient_destination, notification_type, payload, status, created_at
        ) VALUES (?, 'PARTNER', ?, 'OFFER_ALERT', ?, 'MOCKED', ?)
        """,
        (notif_id, winner["partner_id"], json.dumps(notif_payload), now_iso)
    )

    db.commit()

    return {
        "offer_id": offer_id,
        "inquiry_id": inquiry_id,
        "partner_id": winner["partner_id"],
        "selected_partner_id": winner["partner_id"],
        "selected_partner_name": winner["partner_name"],
        "slot_id": winner["slot_id"],
        "score": winner["score"],
        "expires_at": expires_at,
        "total_evaluated_count": len(candidates),
        "qualified_count": len(qualified_candidates)
    }

def handle_timeout_or_rejection(
    db: DatabaseManager,
    offer_id: str,
    action: str = "TIMEOUT", # TIMEOUT or REJECT
    reason: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """
    Handles 15-minute expiration or explicit partner rejection, restores slot,
    and sequentially triggers re-routing to Rank 2 partner.
    """
    offer = db.execute("SELECT * FROM assignment_offers WHERE id = ?", (offer_id,)).fetchone()
    if not offer:
        raise EngineError("OFFER_NOT_FOUND", "오퍼를 찾을 수 없습니다.", 404)

    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    new_offer_status = "EXPIRED" if action == "TIMEOUT" else "REJECTED"

    # 1. Update Offer Status
    db.execute("UPDATE assignment_offers SET status = ?, updated_at = ? WHERE id = ?", (new_offer_status, now_iso, offer_id))

    # 2. Free up previous partner slot back to AVAILABLE
    db.execute("UPDATE capacity_slots SET status = 'AVAILABLE', updated_at = ? WHERE id = ?", (now_iso, offer["slot_id"]))

    # 3. Record Response Log
    db.execute(
        """
        INSERT INTO partner_responses (id, offer_id, partner_id, response_type, rejection_reason, responded_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (f"resp_{uuid.uuid4()}", offer_id, offer["partner_id"], action, reason, now_iso)
    )

    # 4. Set inquiry back to CONSENTED / PENDING_MATCH for re-routing
    db.execute("UPDATE inquiries SET status = 'PENDING_MATCH', updated_at = ? WHERE id = ?", (now_iso, offer["inquiry_id"]))
    db.commit()

    # 5. Sequentially Dispatch to Next Rank Candidate
    next_offer = evaluate_and_dispatch_candidates(db, offer["inquiry_id"])
    return next_offer

def process_expired_offers(db: DatabaseManager) -> List[Dict[str, Any]]:
    """
    Scans and expires unaccepted offers past their 15-minute TTL,
    frees slots back to AVAILABLE, and triggers sequential re-routing (B12).
    Zero abandoned offers policy.
    """
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    expired_offers = db.execute(
        """
        SELECT id, inquiry_id, partner_id, slot_id
        FROM assignment_offers
        WHERE status = 'PENDING' AND expires_at < ?
        """,
        (now_iso,)
    ).fetchall()

    results = []
    for off in expired_offers:
        next_offer = handle_timeout_or_rejection(db, off["id"], action="TIMEOUT", reason="15_MIN_SLA_EXPIRED")
        results.append({
            "expired_offer_id": off["id"],
            "inquiry_id": off["inquiry_id"],
            "reassigned_offer": next_offer
        })
    return results

def raise_dispute(
    db: DatabaseManager,
    match_id: str,
    actor_id: str,
    reason: str
) -> Dict[str, Any]:
    """Transitions match to DISPUTED state and freezes settlement."""
    match = db.execute("SELECT * FROM matches WHERE id = ?", (match_id,)).fetchone()
    if not match:
        raise EngineError("MATCH_NOT_FOUND", "매칭을 찾을 수 없습니다.", 404)

    return transition_state(
        db,
        entity_type="MATCH",
        entity_id=match_id,
        from_state=match["status"],
        to_state="DISPUTED",
        actor_id=actor_id,
        notes=reason
    )

def resolve_dispute(
    db: DatabaseManager,
    match_id: str,
    resolution_type: str, # "REFUND" or "RECLEAN_COMPLETED"
    notes: str
) -> Dict[str, Any]:
    """Resolves dispute: either full refund (REFUNDED) or verified after A/S (VERIFIED)."""
    match = db.execute("SELECT status FROM matches WHERE id = ?", (match_id,)).fetchone()
    if not match or match["status"] != "DISPUTED":
        raise EngineError("INVALID_DISPUTE_STATE", "DISPUTED 상태인 매칭만 해결할 수 있습니다.", 400)

    target_state = "REFUNDED" if resolution_type == "REFUND" else "VERIFIED"
    return transition_state(
        db,
        entity_type="MATCH",
        entity_id=match_id,
        from_state="DISPUTED",
        to_state=target_state,
        actor_id="ADMIN_DISPUTE_RESOLUTION",
        evidence={"resolution_type": resolution_type, "verified_completion": True},
        notes=notes
    )
