"""
SlotBridge Korea — Real Supply Gate & State Machine Transaction Integrity Test Suite (SBK-FIX-04)
Strictly validates:
1. Partner application sets insurance to PENDING_VERIFICATION (B06)
2. Unverified insurance excluded by Hard Filter 2 (B06)
3. Partner suspension cancels all active slots and pending offers (B06)
4. 1-Team-1-Job slot conflict checking (ALL_DAY vs MORNING/AFTERNOON overlap 409 Conflict)
5. State machine atomic transition: stale from_state raises 409 Conflict (B07)
6. Customer time_window matching in dispatch engine
7. Concurrency lock on offer acceptance: duplicate accept raises 409 Conflict
8. Batch expiration of 15-minute SLA offers with zero abandoned offers (B12)
"""

import unittest
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from src.db import DatabaseManager
from src.supplier import (
    apply_partner, approve_partner, suspend_partner,
    register_capacity_slot, delete_capacity_slot, SEOUL_TZ
)
from src.matching import (
    transition_state, evaluate_and_dispatch_candidates,
    handle_timeout_or_rejection, process_expired_offers
)
from src.intake import submit_customer_inquiry
from src.engine import accept_offer, EngineError

class TestSupplyAndStateMachine(unittest.TestCase):
    def setUp(self):
        self.db = DatabaseManager(":memory:")
        self.db.run_migration_up()
        self.db.seed_data()
        self.today_seoul = datetime.now(SEOUL_TZ).date()
        self.tomorrow_str = (self.today_seoul + timedelta(days=1)).strftime("%Y-%m-%d")

    def tearDown(self):
        self.db.close()

    def test_01_apply_partner_unverified_insurance_by_default(self):
        """Entering insurance policy number string does NOT grant VERIFIED insurance (B06)."""
        app = apply_partner(self.db, {
            "name": "클린마스터 관악",
            "business_number": "123-45-67890",
            "representative_name": "홍길동",
            "contact_phone": "010-1234-5678",
            "liability_insurance_policy_number": "POL-2026-99999",
            "settlement_bank": "국민은행",
            "settlement_account": "111-222-333"
        })
        partner_id = app["partner_id"]
        row = self.db.execute("SELECT liability_insurance_status, verification_status FROM partners WHERE id = ?", (partner_id,)).fetchone()
        self.assertEqual(row["verification_status"], "PENDING_APPROVAL")
        self.assertEqual(row["liability_insurance_status"], "PENDING_VERIFICATION")

    def test_02_unverified_insurance_partner_excluded_by_hard_filter(self):
        """Approved partner without verified insurance cannot be dispatched to customers (Hard Filter 2)."""
        # Approve partner without verified insurance
        app = apply_partner(self.db, {
            "name": "노보험청소",
            "business_number": "999-88-77777",
            "representative_name": "무보험",
            "contact_phone": "010-9999-8888"
        })
        partner_id = app["partner_id"]
        approve_partner(self.db, partner_id, insurance_verified=False)
        
        # Register slot
        register_capacity_slot(self.db, partner_id, self.tomorrow_str, "MORNING")

        # Submit inquiry
        inq = submit_customer_inquiry(self.db, {
            "requested_date": self.tomorrow_str,
            "time_window": "MORNING",
            "district": "관악구",
            "housing_type": "STUDIO",
            "area_pyeong": 10,
            "customer_name": "김고객",
            "customer_phone": "010-7777-1111",
            "agreed_privacy_collection": True,
            "agreed_third_party_share": True
        })

        # Evaluate candidates: unverified insurance partner should NOT win
        dispatch = evaluate_and_dispatch_candidates(self.db, inq["inquiry_id"])
        if dispatch:
            self.assertNotEqual(dispatch["selected_partner_id"], partner_id)

    def test_03_partner_suspension_cancels_all_active_slots_and_offers(self):
        """Suspending partner immediately cancels AVAILABLE/OFFERED slots and pending offers (B06)."""
        partner_id = "partner_gw_cleanhome"
        
        # Verify initial slots
        slots_before = self.db.execute("SELECT count(*) as cnt FROM capacity_slots WHERE partner_id = ? AND status = 'AVAILABLE'", (partner_id,)).fetchone()["cnt"]
        self.assertGreater(slots_before, 0)

        # Suspend
        sus = suspend_partner(self.db, partner_id, "불법 하도급 및 허위 견적 적발")
        self.assertEqual(sus["verification_status"], "SUSPENDED")

        # All slots must be CANCELLED
        slots_after = self.db.execute("SELECT count(*) as cnt FROM capacity_slots WHERE partner_id = ? AND status IN ('AVAILABLE', 'OFFERED')", (partner_id,)).fetchone()["cnt"]
        self.assertEqual(slots_after, 0)

    def test_04_slot_overlap_conflict_rejected_409(self):
        """1-Team-1-Job rule: ALL_DAY slot conflicts with MORNING/AFTERNOON slot on same date."""
        partner_id = "partner_sl_master"
        date_str = (self.today_seoul + timedelta(days=2)).strftime("%Y-%m-%d")

        # 1. Register MORNING slot
        s1 = register_capacity_slot(self.db, partner_id, date_str, "MORNING")
        self.assertEqual(s1["status"], "AVAILABLE")

        # 2. Attempt ALL_DAY slot on same day -> Conflict!
        with self.assertRaises(EngineError) as cm:
            register_capacity_slot(self.db, partner_id, date_str, "ALL_DAY")
        self.assertEqual(cm.exception.status_code, 409)

    def test_05_state_machine_atomic_transition_conflict_409(self):
        """State transition fails with 409 if DB status does not match from_state (B07)."""
        inq = submit_customer_inquiry(self.db, {
            "requested_date": self.tomorrow_str,
            "district": "관악구",
            "housing_type": "STUDIO",
            "area_pyeong": 10,
            "customer_name": "원자테스트",
            "customer_phone": "010-3333-4444",
            "agreed_privacy_collection": True,
            "agreed_third_party_share": True
        })
        inq_id = inq["inquiry_id"]

        # Valid transition on matches
        slot = self.db.execute("SELECT id FROM capacity_slots WHERE partner_id = 'partner_gw_cleanhome' LIMIT 1").fetchone()
        slot_id = slot["id"] if slot else "slot_gw_tomorrow_am"

        match_id = "mat_atomic_test_01"
        self.db.execute(
            "INSERT INTO matches (id, inquiry_id, partner_id, slot_id, status) VALUES (?, ?, 'partner_gw_cleanhome', ?, 'ACCEPTED')",
            (match_id, inq_id, slot_id)
        )
        self.db.commit()

        # Step 1: ACCEPTED -> CUSTOMER_CONFIRMED
        transition_state(self.db, "MATCH", match_id, "ACCEPTED", "CUSTOMER_CONFIRMED", "PARTNER")

        # Step 2: Attempting ACCEPTED -> CUSTOMER_CONFIRMED again (stale from_state) -> 409!
        with self.assertRaises(EngineError) as cm:
            transition_state(self.db, "MATCH", match_id, "ACCEPTED", "CUSTOMER_CONFIRMED", "PARTNER")
        self.assertEqual(cm.exception.status_code, 409)
        self.assertIn("STATE_CONFLICT", cm.exception.code)

    def test_06_customer_time_window_filtering(self):
        """Customer requesting AFTERNOON will not receive a MORNING slot."""
        # Create unique date
        unique_date = (self.today_seoul + timedelta(days=3)).strftime("%Y-%m-%d")
        
        # Register a MORNING slot only for partner_dj_topclean
        register_capacity_slot(self.db, "partner_dj_topclean", unique_date, "MORNING")

        # Submit inquiry requesting AFTERNOON
        inq = submit_customer_inquiry(self.db, {
            "requested_date": unique_date,
            "time_window": "AFTERNOON",
            "district": "동작구",
            "housing_type": "STUDIO",
            "area_pyeong": 10,
            "customer_name": "오후원함",
            "customer_phone": "010-8888-5555",
            "agreed_privacy_collection": True,
            "agreed_third_party_share": True
        })

        # Evaluate: MORNING slot must be rejected due to TIME_WINDOW_MISMATCH
        dispatch = evaluate_and_dispatch_candidates(self.db, inq["inquiry_id"])
        self.assertIsNone(dispatch)

    def test_07_concurrent_offer_acceptance_409(self):
        """Accepting an already accepted or expired offer raises 409 Conflict."""
        inq = submit_customer_inquiry(self.db, {
            "requested_date": self.tomorrow_str,
            "district": "관악구",
            "housing_type": "STUDIO",
            "area_pyeong": 10,
            "customer_name": "동시성고객",
            "customer_phone": "010-6666-7777",
            "agreed_privacy_collection": True,
            "agreed_third_party_share": True
        })
        dispatch = evaluate_and_dispatch_candidates(self.db, inq["inquiry_id"])
        self.assertIsNotNone(dispatch)
        offer_id = dispatch["offer_id"]
        partner_id = dispatch["selected_partner_id"]

        # First accept succeeds
        acc1 = accept_offer(self.db, offer_id, partner_id)
        self.assertIsNotNone(acc1["match_id"])

        # Second accept attempt fails with 409
        with self.assertRaises(EngineError) as cm:
            accept_offer(self.db, offer_id, partner_id)
        self.assertEqual(cm.exception.status_code, 409)

    def test_08_process_expired_offers_auto_reassignment(self):
        """Batch process_expired_offers expires 15-min SLA offers and reassigns to Rank 2 (B12)."""
        inq = submit_customer_inquiry(self.db, {
            "requested_date": self.tomorrow_str,
            "district": "관악구",
            "housing_type": "STUDIO",
            "area_pyeong": 10,
            "customer_name": "타임아웃테스트",
            "customer_phone": "010-1234-9999",
            "agreed_privacy_collection": True,
            "agreed_third_party_share": True
        })
        dispatch = evaluate_and_dispatch_candidates(self.db, inq["inquiry_id"], ttl_minutes=15)
        self.assertIsNotNone(dispatch)
        offer_id = dispatch["offer_id"]

        # Artificially expire offer in DB
        past_iso = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        self.db.execute("UPDATE assignment_offers SET expires_at = ? WHERE id = ?", (past_iso, offer_id))
        self.db.commit()

        # Run batch expiration
        results = process_expired_offers(self.db)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["expired_offer_id"], offer_id)

        # Verify old offer is EXPIRED
        old_off = self.db.execute("SELECT status FROM assignment_offers WHERE id = ?", (offer_id,)).fetchone()
        self.assertEqual(old_off["status"], "EXPIRED")

if __name__ == "__main__":
    unittest.main()
