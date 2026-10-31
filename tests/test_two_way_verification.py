"""
tests/test_two_way_verification.py
SBK-FIX-06 Verification Suite: Two-Way Fulfillment, Evidence Gates & Dispute Protection.
"""

import unittest
import json
from src.db import DatabaseManager
from src.engine import (
    create_inquiry,
    accept_offer,
    submit_fulfillment,
    confirm_customer_completion,
    admin_override_completion,
    revoke_inquiry,
    EngineError
)
from src.matching import (
    evaluate_and_dispatch_candidates,
    raise_dispute,
    transition_state
)

class TestTwoWayVerification(unittest.TestCase):
    def setUp(self):
        self.db = DatabaseManager(":memory:")
        self.db.run_migration_up()
        self.db.seed_data()

        # Find an available slot from seed
        slot = self.db.execute("SELECT * FROM capacity_slots WHERE status='AVAILABLE' LIMIT 1").fetchone()
        self.slot_date = slot["slot_date"]

        # Create Baseline Inquiry & Dispatched Offer
        self.inq = create_inquiry(self.db, {
            "requested_date": self.slot_date,
            "requested_time_window": "MORNING",
            "district": "관악구",
            "neighborhood": "봉천동",
            "housing_type": "STUDIO",
            "area_pyeong": 9,
            "customer_name": "이지은",
            "customer_phone": "010-9876-5432",
            "agreed_privacy_collection": True,
            "agreed_third_party_share": True
        })

        offer = evaluate_and_dispatch_candidates(self.db, self.inq["inquiry_id"])
        self.offer_id = offer["offer_id"]
        self.assigned_partner = offer["partner_id"]
        self.acc = accept_offer(self.db, self.offer_id, self.assigned_partner)
        self.match_id = self.acc["match_id"]

    def test_01_insufficient_photos_rejected(self):
        """Guardrail: Missing or < 2 photos must be strictly rejected with 400."""
        # 0 photos
        with self.assertRaises(EngineError) as ctx1:
            submit_fulfillment(
                self.db,
                match_id=self.match_id,
                partner_id=self.assigned_partner,
                final_amount=250000,
                photo_urls=[]
            )
        self.assertEqual(ctx1.exception.status_code, 400)
        self.assertEqual(ctx1.exception.code, "INSUFFICIENT_EVIDENCE")

        # 1 photo only
        with self.assertRaises(EngineError) as ctx2:
            submit_fulfillment(
                self.db,
                match_id=self.match_id,
                partner_id=self.assigned_partner,
                final_amount=250000,
                photo_urls=["https://r2.mock/before.jpg"]
            )
        self.assertEqual(ctx2.exception.status_code, 400)
        self.assertEqual(ctx2.exception.code, "INSUFFICIENT_EVIDENCE")

    def test_02_invalid_amount_rejected(self):
        """Guardrail: Non-positive final amount must be rejected."""
        with self.assertRaises(EngineError) as ctx:
            submit_fulfillment(
                self.db,
                match_id=self.match_id,
                partner_id=self.assigned_partner,
                final_amount=0,
                photo_urls=["https://r2.mock/before.jpg", "https://r2.mock/after.jpg"]
            )
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(ctx.exception.code, "INVALID_AMOUNT")

    def test_03_unassigned_partner_rejected(self):
        """Guardrail: Partner mismatch must be rejected with 404."""
        with self.assertRaises(EngineError) as ctx:
            submit_fulfillment(
                self.db,
                match_id=self.match_id,
                partner_id="partner_unauthorized",
                final_amount=250000,
                photo_urls=["https://r2.mock/before.jpg", "https://r2.mock/after.jpg"]
            )
        self.assertEqual(ctx.exception.status_code, 404)
        self.assertEqual(ctx.exception.code, "MATCH_NOT_FOUND")

    def test_04_revoked_inquiry_fulfillment_blocked(self):
        """Guardrail: Fulfillment attempt after customer revocation must be blocked."""
        # Customer revokes inquiry (1-click deletion)
        revoke_inquiry(self.db, self.inq["tracking_token"])

        with self.assertRaises(EngineError) as ctx:
            submit_fulfillment(
                self.db,
                match_id=self.match_id,
                partner_id=self.assigned_partner,
                final_amount=250000,
                photo_urls=["https://r2.mock/before.jpg", "https://r2.mock/after.jpg"]
            )
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(ctx.exception.code, "INQUIRY_CANCELLED")

    def test_05_unilateral_submission_not_accrued_without_confirmation(self):
        """Core Rule: Partner submission alone sets status COMPLETED but does NOT accrue to ledger."""
        res = submit_fulfillment(
            self.db,
            match_id=self.match_id,
            partner_id=self.assigned_partner,
            final_amount=250000,
            photo_urls=["https://r2.mock/before.jpg", "https://r2.mock/after.jpg"],
            auto_accrue=False # Unilateral submission
        )
        self.assertEqual(res["status"], "COMPLETED")
        self.assertEqual(res["verification_status"], "PENDING_CUSTOMER_CONFIRMATION")

        # Verify no ledger events accrued yet
        ledgers = self.db.execute("SELECT * FROM ledger_events WHERE match_id = ?", (self.match_id,)).fetchall()
        self.assertEqual(len(ledgers), 0)

        # Now Customer explicitly confirms completion
        conf = confirm_customer_completion(self.db, self.match_id, customer_rating=5, customer_notes="정말 깨끗합니다")
        self.assertEqual(conf["status"], "BILLABLE")
        self.assertEqual(conf["verification_status"], "VERIFIED")

        # Now ledger events MUST be accrued
        ledgers_after = self.db.execute("SELECT event_type, amount FROM ledger_events WHERE match_id = ?", (self.match_id,)).fetchall()
        self.assertEqual(len(ledgers_after), 1)
        self.assertEqual(ledgers_after[0]["event_type"], "REVENUE_ACCRUED")
        self.assertEqual(ledgers_after[0]["amount"], 20000) # 8% of 250k

    def test_06_disputed_match_blocks_fulfillment_and_confirmation(self):
        """Guardrail: Disputed match freezes settlement and rejects fulfillment/confirmation."""
        # Partner submits work
        submit_fulfillment(
            self.db,
            match_id=self.match_id,
            partner_id=self.assigned_partner,
            final_amount=250000,
            photo_urls=["https://r2.mock/before.jpg", "https://r2.mock/after.jpg"],
            auto_accrue=False
        )

        # Customer raises dispute due to unclean window frames
        raise_dispute(self.db, self.match_id, actor_id="CUSTOMER", reason="창틀 분진이 여전히 남아있습니다.")

        # Attempting customer confirmation during dispute must be blocked
        with self.assertRaises(EngineError) as ctx:
            confirm_customer_completion(self.db, self.match_id)
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(ctx.exception.code, "MATCH_DISPUTED")

    def test_07_admin_override_requires_reason(self):
        """Guardrail: Admin override requires explicit audit reason >= 5 chars."""
        submit_fulfillment(
            self.db,
            match_id=self.match_id,
            partner_id=self.assigned_partner,
            final_amount=250000,
            photo_urls=["https://r2.mock/before.jpg", "https://r2.mock/after.jpg"],
            auto_accrue=False
        )

        # Empty reason rejected
        with self.assertRaises(EngineError) as ctx1:
            admin_override_completion(self.db, self.match_id, admin_id="admin_01", reason="")
        self.assertEqual(ctx1.exception.status_code, 400)
        self.assertEqual(ctx1.exception.code, "REASON_REQUIRED")

        # Short reason (< 5 chars) rejected
        with self.assertRaises(EngineError) as ctx2:
            admin_override_completion(self.db, self.match_id, admin_id="admin_01", reason="ok")
        self.assertEqual(ctx2.exception.status_code, 400)
        self.assertEqual(ctx2.exception.code, "REASON_REQUIRED")

        # Valid reason succeeds
        override = admin_override_completion(
            self.db,
            self.match_id,
            admin_id="admin_01",
            reason="고객 유선 통화 녹취로 시공 완료 및 만족 확인"
        )
        self.assertEqual(override["status"], "BILLABLE")
        self.assertEqual(override["verification_status"], "VERIFIED")

        # Verify audit log exists
        audit = self.db.execute(
            "SELECT * FROM audit_log WHERE resource_id = ? AND action = 'ADMIN_OVERRIDE_COMPLETION'",
            (self.match_id,)
        ).fetchone()
        self.assertIsNotNone(audit)
        details = json.loads(audit["details"])
        self.assertEqual(details["reason"], "고객 유선 통화 녹취로 시공 완료 및 만족 확인")

if __name__ == "__main__":
    unittest.main()
