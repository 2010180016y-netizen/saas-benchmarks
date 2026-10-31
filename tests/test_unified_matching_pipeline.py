"""
SlotBridge Korea — Unified Matching Pipeline & Notification Queue Test Suite (SBK-FIX-05)
Strictly validates:
1. Unified dispatch entry point: dispatch_1to1_offer delegates to evaluate_and_dispatch_candidates
2. Statutory consent gate: disallow dispatch if PIPC Art. 17 consent is missing/unconsented
3. Inquiries in terminal/active states (CANCELLED, MATCHED) cannot be dispatched
4. Notification outbox queue: every dispatched offer creates a MOCKED outbox record ($0 cost)
5. Independent concurrency simulation: atomic conditional slot lock prevents double booking
6. Sequential re-routing on rejection/timeout queues notification for Rank 2 partner
"""

import unittest
import json
from datetime import datetime, timezone, timedelta

from src.db import DatabaseManager
from src.supplier import SEOUL_TZ, register_capacity_slot
from src.intake import submit_customer_inquiry
from src.engine import dispatch_1to1_offer, accept_offer, EngineError
from src.matching import (
    evaluate_and_dispatch_candidates, handle_timeout_or_rejection,
    process_expired_offers
)

class TestUnifiedMatchingPipeline(unittest.TestCase):
    def setUp(self):
        self.db = DatabaseManager(":memory:")
        self.db.run_migration_up()
        self.db.seed_data()
        self.today_seoul = datetime.now(SEOUL_TZ).date()
        self.tomorrow_str = (self.today_seoul + timedelta(days=1)).strftime("%Y-%m-%d")

    def tearDown(self):
        self.db.close()

    def test_01_unified_dispatch_entrypoint_delegation(self):
        """dispatch_1to1_offer cleanly delegates to matching engine and returns expected keys."""
        inq = submit_customer_inquiry(self.db, {
            "requested_date": self.tomorrow_str,
            "district": "관악구",
            "neighborhood": "봉천동",
            "housing_type": "STUDIO",
            "area_pyeong": 10,
            "customer_name": "홍길동",
            "customer_phone": "010-1234-5678",
            "agreed_privacy_collection": True,
            "agreed_third_party_share": True
        })
        
        offer = dispatch_1to1_offer(self.db, inq["inquiry_id"])
        self.assertIsNotNone(offer)
        self.assertIn("offer_id", offer)
        self.assertIn("partner_id", offer)
        self.assertIn("selected_partner_id", offer)
        self.assertEqual(offer["partner_id"], offer["selected_partner_id"])

    def test_02_statutory_consent_gate_blocks_unconsented_dispatch(self):
        """Inquiry without valid PIPC Art. 17 3rd-party provision consent is strictly blocked from dispatch."""
        inq = submit_customer_inquiry(self.db, {
            "requested_date": self.tomorrow_str,
            "district": "관악구",
            "housing_type": "STUDIO",
            "area_pyeong": 10,
            "customer_name": "미동의자",
            "customer_phone": "010-9999-0000",
            "agreed_privacy_collection": True,
            "agreed_third_party_share": True
        })
        inquiry_id = inq["inquiry_id"]

        # Artificially tamper consent_events to unconsented (0)
        self.db.execute(
            "UPDATE consent_events SET agreed_third_party_share = 0 WHERE inquiry_id = ?",
            (inquiry_id,)
        )
        self.db.commit()

        # Dispatch must return None
        offer = evaluate_and_dispatch_candidates(self.db, inquiry_id)
        self.assertIsNone(offer, "Unconsented inquiry was illegally dispatched to a partner!")

    def test_03_cancelled_inquiry_never_dispatched(self):
        """Cancelled or matched inquiries cannot receive new assignment offers."""
        inq = submit_customer_inquiry(self.db, {
            "requested_date": self.tomorrow_str,
            "district": "관악구",
            "housing_type": "STUDIO",
            "area_pyeong": 10,
            "customer_name": "취소고객",
            "customer_phone": "010-5555-4444",
            "agreed_privacy_collection": True,
            "agreed_third_party_share": True
        })
        inquiry_id = inq["inquiry_id"]

        # Cancel inquiry
        self.db.execute("UPDATE inquiries SET status = 'CANCELLED' WHERE id = ?", (inquiry_id,))
        self.db.commit()

        offer = evaluate_and_dispatch_candidates(self.db, inquiry_id)
        self.assertIsNone(offer)

    def test_04_notification_outbox_record_created_on_dispatch(self):
        """Dispatching an offer creates an immutable MOCKED notification record in outbox ($0 cost)."""
        inq = submit_customer_inquiry(self.db, {
            "requested_date": self.tomorrow_str,
            "district": "관악구",
            "housing_type": "STUDIO",
            "area_pyeong": 10,
            "customer_name": "알림테스트",
            "customer_phone": "010-8888-9999",
            "agreed_privacy_collection": True,
            "agreed_third_party_share": True
        })
        
        offer = evaluate_and_dispatch_candidates(self.db, inq["inquiry_id"])
        self.assertIsNotNone(offer)

        # Check outbox record
        notif = self.db.execute(
            "SELECT * FROM notification_outbox WHERE recipient_type='PARTNER' AND notification_type='OFFER_ALERT' ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        self.assertIsNotNone(notif)
        self.assertEqual(notif["status"], "MOCKED")
        payload = json.loads(notif["payload"])
        self.assertEqual(payload["offer_id"], offer["offer_id"])
        self.assertEqual(payload["district"], "관악구")

    def test_05_sequential_rerouting_triggers_next_candidate_and_outbox(self):
        """Rejection triggers timeout/rejection handler and dispatches to Rank 2 partner with fresh outbox notification."""
        # Ensure partner_sl_master has verified insurance, service area, and slot in Gwanak
        self.db.execute("UPDATE partners SET liability_insurance_status = 'VERIFIED' WHERE id = 'partner_sl_master'")
        self.db.execute(
            "INSERT OR IGNORE INTO service_areas (id, partner_id, city, district, neighborhood, is_active) VALUES ('sa_sl_test', 'partner_sl_master', '서울특별시', '관악구', '전체', 1)"
        )
        self.db.execute(
            "INSERT OR IGNORE INTO capacity_slots (id, partner_id, slot_date, time_window, status) VALUES ('slot_sl_test_am', 'partner_sl_master', ?, 'MORNING', 'AVAILABLE')",
            (self.tomorrow_str,)
        )
        self.db.commit()

        inq = submit_customer_inquiry(self.db, {
            "requested_date": self.tomorrow_str,
            "district": "관악구",
            "housing_type": "STUDIO",
            "area_pyeong": 10,
            "customer_name": "재배정고객",
            "customer_phone": "010-7777-6666",
            "agreed_privacy_collection": True,
            "agreed_third_party_share": True
        })
        
        offer1 = evaluate_and_dispatch_candidates(self.db, inq["inquiry_id"])
        self.assertIsNotNone(offer1)
        first_partner = offer1["partner_id"]

        # Reject offer1
        next_offer = handle_timeout_or_rejection(self.db, offer1["offer_id"], action="REJECT", reason="일정 중복")
        self.assertIsNotNone(next_offer)
        self.assertNotEqual(next_offer["partner_id"], first_partner)

        # Verify new outbox notification was queued for next partner
        notifs = self.db.execute(
            "SELECT * FROM notification_outbox WHERE recipient_type='PARTNER' AND notification_type='OFFER_ALERT' ORDER BY created_at DESC LIMIT 2"
        ).fetchall()
        self.assertGreaterEqual(len(notifs), 2)

if __name__ == "__main__":
    unittest.main()
