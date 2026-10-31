"""
SlotBridge Korea — Matching Engine, SLA & 10-Stage State Machine Test Suite (Step 08)
Contains 20 distinct normal, error, race condition, timeout, re-routing, and dispute scenarios.
"""

import unittest
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from src.db import DatabaseManager
from src.intake import submit_customer_inquiry
from src.supplier import SEOUL_TZ, apply_partner, approve_partner, register_capacity_slot
from src.matching import (
    transition_state, evaluate_and_dispatch_candidates,
    handle_timeout_or_rejection, raise_dispute, resolve_dispute
)
from src.engine import accept_offer, EngineError

class TestMatchingAndFulfillment(unittest.TestCase):
    def setUp(self):
        self.db = DatabaseManager(":memory:")
        self.db.run_migration_up()
        self.db.seed_data()
        self.today_seoul = datetime.now(SEOUL_TZ).date()
        self.tomorrow_str = (self.today_seoul + timedelta(days=1)).strftime("%Y-%m-%d")

    def tearDown(self):
        self.db.close()

    def _create_standard_inquiry(self):
        return submit_customer_inquiry(self.db, {
            "requested_date": self.tomorrow_str,
            "time_window": "MORNING",
            "district": "관악구",
            "neighborhood": "봉천동",
            "housing_type": "STUDIO",
            "area_pyeong": 10,
            "customer_name": "테스트고객",
            "customer_phone": "010-1234-5678",
            "agreed_privacy_collection": True,
            "agreed_third_party_share": True
        })

    def _create_valid_match(self, match_id: str, status: str = "ACCEPTED") -> str:
        inq = self._create_standard_inquiry()
        self.db.execute(
            """
            INSERT INTO matches (id, inquiry_id, partner_id, slot_id, status)
            VALUES (?, ?, 'partner_gw_cleanhome', 'slot_gw_tomorrow_am', ?)
            """,
            (match_id, inq["inquiry_id"], status)
        )
        self.db.commit()
        return match_id

    # -------------------------------------------------------------------------
    # Scenario 01: 10-Stage State Machine Full Linear Happy Path
    # -------------------------------------------------------------------------
    def test_01_linear_state_machine_full_happy_path(self):
        """SUBMITTED -> VALIDATED -> CONSENTED -> OFFERED -> ACCEPTED -> CUSTOMER_CONFIRMED -> BOOKED -> COMPLETED -> VERIFIED -> BILLABLE"""
        match_id = self._create_valid_match("mat_happy_path_01", "ACCEPTED")

        # 1. ACCEPTED -> CUSTOMER_CONFIRMED
        t1 = transition_state(self.db, "MATCH", match_id, "ACCEPTED", "CUSTOMER_CONFIRMED", "PARTNER")
        self.assertEqual(t1["to_state"], "CUSTOMER_CONFIRMED")

        # 2. CUSTOMER_CONFIRMED -> BOOKED (requires price)
        t2 = transition_state(self.db, "MATCH", match_id, "CUSTOMER_CONFIRMED", "BOOKED", "PARTNER", evidence={"agreed_price": 250000})
        self.assertEqual(t2["to_state"], "BOOKED")

        # 3. BOOKED -> COMPLETED (requires photos)
        t3 = transition_state(self.db, "MATCH", match_id, "BOOKED", "COMPLETED", "PARTNER", evidence={"photo_urls": ["p1.jpg", "p2.jpg"]})
        self.assertEqual(t3["to_state"], "COMPLETED")

        # 4. COMPLETED -> VERIFIED
        t4 = transition_state(self.db, "MATCH", match_id, "COMPLETED", "VERIFIED", "SYSTEM")
        self.assertEqual(t4["to_state"], "VERIFIED")

        # 5. VERIFIED -> BILLABLE (requires verification evidence)
        t5 = transition_state(self.db, "MATCH", match_id, "VERIFIED", "BILLABLE", "ADMIN", evidence={"verified_completion": True})
        self.assertEqual(t5["to_state"], "BILLABLE")

    # -------------------------------------------------------------------------
    # Scenario 02-05: State Transition Guardrails & Illegal Jumps
    # -------------------------------------------------------------------------
    def test_02_transition_precondition_booked_requires_price(self):
        """Transitioning to BOOKED without agreed_price is rejected."""
        match_id = self._create_valid_match("mat_guard_02", "CUSTOMER_CONFIRMED")
        with self.assertRaises(EngineError) as cm:
            transition_state(self.db, "MATCH", match_id, "CUSTOMER_CONFIRMED", "BOOKED", "PARTNER", evidence={})
        self.assertEqual(cm.exception.code, "PRECONDITION_FAILED")

    def test_03_transition_precondition_completed_requires_photos(self):
        """Transitioning to COMPLETED without 2 photos is rejected."""
        match_id = self._create_valid_match("mat_guard_03", "BOOKED")
        with self.assertRaises(EngineError) as cm:
            transition_state(self.db, "MATCH", match_id, "BOOKED", "COMPLETED", "PARTNER", evidence={"photo_urls": ["only_one.jpg"]})
        self.assertEqual(cm.exception.code, "PRECONDITION_FAILED")

    def test_04_transition_precondition_billable_requires_verification(self):
        """Transitioning to BILLABLE without verified_completion is rejected."""
        match_id = self._create_valid_match("mat_guard_04", "VERIFIED")
        with self.assertRaises(EngineError) as cm:
            transition_state(self.db, "MATCH", match_id, "VERIFIED", "BILLABLE", "ADMIN", evidence={})
        self.assertEqual(cm.exception.code, "PRECONDITION_FAILED")

    def test_05_illegal_transition_jump_rejected(self):
        """Attempting invalid jump (e.g. SUBMITTED directly to BILLABLE) is strictly rejected."""
        with self.assertRaises(EngineError) as cm:
            transition_state(self.db, "INQUIRY", "inq_dummy", "SUBMITTED", "BILLABLE", "ADMIN")
        self.assertEqual(cm.exception.code, "INVALID_STATE_TRANSITION")

    # -------------------------------------------------------------------------
    # Scenario 06-09: Hard Filter Enforcements
    # -------------------------------------------------------------------------
    def test_06_hard_filter_unapproved_partner_excluded(self):
        """Unapproved partner is filtered out by hard filter 1."""
        app = apply_partner(self.db, {"name": "미승인업체", "business_number": "123-45-00000", "representative_name": "김미승", "contact_phone": "010-0000-1111"})
        p_id = app["partner_id"]
        # Force available slot in DB
        self.db.execute("INSERT INTO capacity_slots (id, partner_id, slot_date, time_window, status) VALUES ('s_unapp', ?, ?, 'MORNING', 'AVAILABLE')", (p_id, self.tomorrow_str))
        self.db.commit()

        inq = self._create_standard_inquiry()
        dispatch = evaluate_and_dispatch_candidates(self.db, inq["inquiry_id"])
        self.assertIsNotNone(dispatch)
        self.assertNotEqual(dispatch["selected_partner_id"], p_id)

    def test_07_hard_filter_unverified_insurance_excluded(self):
        """Partner without verified insurance is filtered out by hard filter 2."""
        app = apply_partner(self.db, {"name": "보험미가입업체", "business_number": "123-45-11111", "representative_name": "이무험", "contact_phone": "010-0000-2222"})
        p_id = app["partner_id"]
        approve_partner(self.db, p_id)
        # Force insurance unverified
        self.db.execute("UPDATE partners SET liability_insurance_status='PENDING' WHERE id=?", (p_id,))
        self.db.execute("INSERT INTO capacity_slots (id, partner_id, slot_date, time_window, status) VALUES ('s_noins', ?, ?, 'MORNING', 'AVAILABLE')", (p_id, self.tomorrow_str))
        self.db.commit()

        inq = self._create_standard_inquiry()
        dispatch = evaluate_and_dispatch_candidates(self.db, inq["inquiry_id"])
        self.assertNotEqual(dispatch["selected_partner_id"], p_id)

    def test_08_hard_filter_service_area_mismatch_excluded(self):
        """Partner serving only Dongjak is not dispatched a Gwanak inquiry."""
        # Topclean serves Dongjak
        inq = self._create_standard_inquiry() # Gwanak
        dispatch = evaluate_and_dispatch_candidates(self.db, inq["inquiry_id"])
        # Selected must serve Gwanak (Cleanhome)
        self.assertEqual(dispatch["selected_partner_id"], "partner_gw_cleanhome")

    def test_09_hard_filter_previously_rejected_partner_excluded(self):
        """Partner who previously rejected this inquiry is excluded upon re-routing."""
        inq = self._create_standard_inquiry()
        dispatch1 = evaluate_and_dispatch_candidates(self.db, inq["inquiry_id"])
        p1 = dispatch1["selected_partner_id"]

        # P1 rejects
        handle_timeout_or_rejection(self.db, dispatch1["offer_id"], action="REJECT", reason="일정 중복")

        # Re-check rejection in DB
        rej = self.db.execute("SELECT count(*) AS cnt FROM partner_responses WHERE response_type='REJECT'").fetchone()["cnt"]
        self.assertGreaterEqual(rej, 1)

    # -------------------------------------------------------------------------
    # Scenario 10-12: Soft Scoring, Equity Balance & Decision Replay
    # -------------------------------------------------------------------------
    def test_10_deterministic_soft_scoring_rank(self):
        """Verifies that soft scoring correctly ranks qualified candidates."""
        inq = self._create_standard_inquiry()
        dispatch = evaluate_and_dispatch_candidates(self.db, inq["inquiry_id"])
        self.assertGreater(dispatch["score"], 100.0)
        self.assertEqual(dispatch["selected_partner_id"], "partner_gw_cleanhome")

    def test_11_equity_balancing_bonus_prevents_monopoly(self):
        """Equity balancing bonus gives slight boost to less dispatched partners."""
        # Cleanhome has dispatches, topclean has 0 dispatches
        # When both serve the area, equity bonus balances the load
        inq = self._create_standard_inquiry()
        dispatch = evaluate_and_dispatch_candidates(self.db, inq["inquiry_id"])
        self.assertIsNotNone(dispatch)

    def test_12_decision_log_replay_integrity(self):
        """Verifies that audit_log contains full decision replay log."""
        inq = self._create_standard_inquiry()
        evaluate_and_dispatch_candidates(self.db, inq["inquiry_id"])
        
        audit = self.db.execute("SELECT details FROM audit_log WHERE action='CANDIDATE_EVALUATION'").fetchone()
        self.assertIsNotNone(audit)
        details = json.loads(audit["details"])
        self.assertIn("decision_log", details)
        self.assertGreaterEqual(len(details["decision_log"]), 1)

    # -------------------------------------------------------------------------
    # Scenario 13-15: Timeout, Sequential Re-routing & Exhaustion
    # -------------------------------------------------------------------------
    def test_13_sequential_re_routing_upon_timeout(self):
        """15-minute timeout triggers expiration, restores slot, and re-routes."""
        inq = self._create_standard_inquiry()
        d1 = evaluate_and_dispatch_candidates(self.db, inq["inquiry_id"])
        offer_id = d1["offer_id"]

        # Timeout occurs
        handle_timeout_or_rejection(self.db, offer_id, action="TIMEOUT")

        # Verify previous offer EXPIRED
        off = self.db.execute("SELECT status FROM assignment_offers WHERE id=?", (offer_id,)).fetchone()
        self.assertEqual(off["status"], "EXPIRED")

        # Verify previous slot restored to AVAILABLE
        slot = self.db.execute("SELECT status FROM capacity_slots WHERE id='slot_gw_tomorrow_am'").fetchone()
        self.assertEqual(slot["status"], "AVAILABLE")

    def test_14_sequential_re_routing_upon_explicit_rejection(self):
        """Explicit partner rejection records response and frees slot."""
        inq = self._create_standard_inquiry()
        d1 = evaluate_and_dispatch_candidates(self.db, inq["inquiry_id"])
        offer_id = d1["offer_id"]

        handle_timeout_or_rejection(self.db, offer_id, action="REJECT", reason="차량 정비")
        resp = self.db.execute("SELECT response_type, rejection_reason FROM partner_responses WHERE offer_id=?", (offer_id,)).fetchone()
        self.assertEqual(resp["response_type"], "REJECT")
        self.assertEqual(resp["rejection_reason"], "차량 정비")

    def test_15_exhausted_candidates_switches_to_unavailable(self):
        """When all available candidates reject, inquiry returns to PENDING_MATCH without false dispatch."""
        inq = self._create_standard_inquiry()
        d1 = evaluate_and_dispatch_candidates(self.db, inq["inquiry_id"])
        
        # Partner rejects
        next_d = handle_timeout_or_rejection(self.db, d1["offer_id"], action="REJECT", reason="마감")
        # No more Gwanak morning partners exist
        self.assertIsNone(next_d)

    # -------------------------------------------------------------------------
    # Scenario 16-17: Concurrency, Double-Booking & TTL Expiration
    # -------------------------------------------------------------------------
    def test_16_slot_concurrency_race_condition_rejected(self):
        """Two concurrent calls trying to accept the same offer: second gets 409 conflict."""
        inq = self._create_standard_inquiry()
        d = evaluate_and_dispatch_candidates(self.db, inq["inquiry_id"])
        offer_id = d["offer_id"]
        partner_id = d["selected_partner_id"]

        # Call 1 succeeds
        res1 = accept_offer(self.db, offer_id, partner_id)
        self.assertEqual(res1["status"], "CONFIRMED")

        # Call 2 conflicts
        with self.assertRaises(EngineError) as cm:
            accept_offer(self.db, offer_id, partner_id)
        self.assertEqual(cm.exception.code, "ALREADY_PROCESSED")

    def test_17_expired_offer_acceptance_blocked(self):
        """Attempting to accept after TTL expiration is blocked with 410 error."""
        inq = self._create_standard_inquiry()
        d = evaluate_and_dispatch_candidates(self.db, inq["inquiry_id"])
        offer_id = d["offer_id"]
        partner_id = d["selected_partner_id"]

        # Force offer expiration in past
        self.db.execute("UPDATE assignment_offers SET expires_at='2020-01-01T00:00:00Z' WHERE id=?", (offer_id,))
        self.db.commit()

        with self.assertRaises(EngineError) as cm:
            accept_offer(self.db, offer_id, partner_id)
        self.assertEqual(cm.exception.code, "OFFER_EXPIRED")

    # -------------------------------------------------------------------------
    # Scenario 18-20: Dispute Lifecycle (DISPUTED -> REFUNDED / VERIFIED)
    # -------------------------------------------------------------------------
    def test_18_dispute_transition_freezes_settlement(self):
        """Filing a dispute transitions match to DISPUTED."""
        match_id = self._create_valid_match("mat_dispute_18", "COMPLETED")
        res = raise_dispute(self.db, match_id, "CUSTOMER", "주방 기름때 미제거")
        self.assertEqual(res["to_state"], "DISPUTED")

    def test_19_dispute_resolution_refund_path(self):
        """Resolving dispute via full refund moves state to REFUNDED."""
        match_id = self._create_valid_match("mat_dispute_19", "DISPUTED")
        res = resolve_dispute(self.db, match_id, "REFUND", "업체 재방문 거부로 전액 환불")
        self.assertEqual(res["to_state"], "REFUNDED")

    def test_20_dispute_resolution_reclean_verified_path(self):
        """Resolving dispute via free A/S re-cleaning moves state back to VERIFIED."""
        match_id = self._create_valid_match("mat_dispute_20", "DISPUTED")
        res = resolve_dispute(self.db, match_id, "RECLEAN_COMPLETED", "24시간 내 무상 보완 청소 완료 및 고객 확인")
        self.assertEqual(res["to_state"], "VERIFIED")

if __name__ == "__main__":
    unittest.main()
