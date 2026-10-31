"""
SlotBridge Korea — Customer Intake & Statutory Consent Test Suite (Step 06)
Validates mobile-first customer inquiries, statutory consent recording (PIPC Act Art. 17),
attribution partner verification, honest availability checks, and 1-click revocation.
"""

import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from src.db import DatabaseManager
from src.intake import (
    submit_customer_inquiry, get_inquiry_status,
    revoke_customer_inquiry, check_district_availability, TERMS_VERSION
)
from src.engine import dispatch_1to1_offer, EngineError
from src.supplier import SEOUL_TZ

class TestCustomerIntake(unittest.TestCase):
    def setUp(self):
        self.db = DatabaseManager(":memory:")
        self.db.run_migration_up()
        self.db.seed_data()
        self.today_seoul = datetime.now(SEOUL_TZ).date()
        self.tomorrow_str = (self.today_seoul + timedelta(days=1)).strftime("%Y-%m-%d")

    def tearDown(self):
        self.db.close()

    def test_01_valid_intake_with_approved_attribution_partner(self):
        """Intake with valid approved attribution partner preserves attribution_id."""
        res = submit_customer_inquiry(self.db, {
            "attribution_partner_id": "partner_sl_master", # Approved seed partner
            "requested_date": self.tomorrow_str,
            "district": "관악구",
            "neighborhood": "봉천동",
            "housing_type": "STUDIO",
            "area_pyeong": 10,
            "customer_name": "정고객",
            "customer_phone": "010-1234-7777",
            "agreed_privacy_collection": True,
            "agreed_third_party_share": True
        })
        self.assertEqual(res["status"], "PENDING_MATCH")
        self.assertTrue(res["has_available_slots"])

        # Check DB attribution preserved
        inq = self.db.execute("SELECT attribution_partner_id FROM inquiries WHERE id=?", (res["inquiry_id"],)).fetchone()
        self.assertEqual(inq["attribution_partner_id"], "partner_sl_master")

    def test_02_tampered_attribution_partner_sanitized(self):
        """Intake with fake/unapproved attribution partner sanitizes attribution to None."""
        res = submit_customer_inquiry(self.db, {
            "attribution_partner_id": "fake_partner_id_123", # Fake!
            "requested_date": self.tomorrow_str,
            "district": "관악구",
            "housing_type": "STUDIO",
            "area_pyeong": 10,
            "customer_name": "강고객",
            "customer_phone": "010-8888-2222",
            "agreed_privacy_collection": True,
            "agreed_third_party_share": True
        })
        self.assertEqual(res["status"], "PENDING_MATCH")

        # DB must have NULL/None for attribution_partner_id
        inq = self.db.execute("SELECT attribution_partner_id FROM inquiries WHERE id=?", (res["inquiry_id"],)).fetchone()
        self.assertIsNone(inq["attribution_partner_id"])

    def test_03_statutory_consent_immutable_log(self):
        """Enforces terms version v1.0.0 and immutable consent event logging."""
        res = submit_customer_inquiry(self.db, {
            "requested_date": self.tomorrow_str,
            "district": "관악구",
            "housing_type": "STUDIO",
            "area_pyeong": 8,
            "customer_name": "윤고객",
            "customer_phone": "010-4321-8765",
            "agreed_privacy_collection": True,
            "agreed_third_party_share": True,
            "agreed_marketing": False
        }, client_ip="203.0.113.42", user_agent="Mozilla/5.0 iPhone")

        # Verify consent_events row
        consent = self.db.execute(
            "SELECT * FROM consent_events WHERE inquiry_id=?",
            (res["inquiry_id"],)
        ).fetchone()
        self.assertIsNotNone(consent)
        self.assertEqual(consent["terms_version"], TERMS_VERSION)
        self.assertEqual(consent["agreed_privacy_collection"], 1)
        self.assertEqual(consent["agreed_third_party_share"], 1)
        self.assertEqual(consent["agreed_marketing"], 0)
        self.assertEqual(consent["user_agent"], "Mozilla/5.0 iPhone")

    def test_04_missing_consent_strictly_rejected(self):
        """Missing either collection or 3rd party consent rejects request."""
        with self.assertRaises(EngineError) as cm:
            submit_customer_inquiry(self.db, {
                "requested_date": self.tomorrow_str,
                "district": "관악구",
                "housing_type": "STUDIO",
                "area_pyeong": 8,
                "customer_name": "문고객",
                "customer_phone": "010-1111-3333",
                "agreed_privacy_collection": True,
                "agreed_third_party_share": False # Missing!
            })
        self.assertEqual(cm.exception.code, "CONSENT_REQUIRED")

    def test_05_honest_availability_when_slots_exhausted(self):
        """When 0 slots exist for a date/district, returns UNAVAILABLE without fake counts."""
        far_date_str = (self.today_seoul + timedelta(days=3)).strftime("%Y-%m-%d")
        
        # Verify 0 slots exist for Dongjak on far date
        cnt = check_district_availability(self.db, far_date_str, "동작구")
        self.assertEqual(cnt, 0)

        res = submit_customer_inquiry(self.db, {
            "requested_date": far_date_str,
            "district": "동작구",
            "housing_type": "STUDIO",
            "area_pyeong": 10,
            "customer_name": "배고객",
            "customer_phone": "010-6666-9999",
            "agreed_privacy_collection": True,
            "agreed_third_party_share": True
        })
        self.assertEqual(res["status"], "UNAVAILABLE_WAITING")
        self.assertFalse(res["has_available_slots"])
        self.assertEqual(res["available_slots_count"], 0)
        self.assertIn("마감되었습니다", res["message"])

    def test_06_inquiry_status_retrieval(self):
        """Retrieves status by token without revealing raw customer PII."""
        res = submit_customer_inquiry(self.db, {
            "requested_date": self.tomorrow_str,
            "district": "관악구",
            "housing_type": "STUDIO",
            "area_pyeong": 10,
            "customer_name": "오고객",
            "customer_phone": "010-5555-7777",
            "agreed_privacy_collection": True,
            "agreed_third_party_share": True
        })
        token = res["tracking_token"]

        status = get_inquiry_status(self.db, token)
        self.assertEqual(status["inquiry_id"], res["inquiry_id"])
        self.assertEqual(status["district"], "관악구")
        self.assertFalse(status["is_pii_erased"])

    def test_07_customer_1click_revocation_erases_pii(self):
        """1-Click revocation completely obliterates PII with tombstone and cancels matching."""
        res = submit_customer_inquiry(self.db, {
            "requested_date": self.tomorrow_str,
            "district": "관악구",
            "housing_type": "STUDIO",
            "area_pyeong": 10,
            "customer_name": "송고객",
            "customer_phone": "010-9999-3333",
            "agreed_privacy_collection": True,
            "agreed_third_party_share": True
        })
        token = res["tracking_token"]

        # Customer clicks 1-Click Revocation
        rev_res = revoke_customer_inquiry(self.db, token)
        self.assertTrue(rev_res["success"])

        # Check status
        status = get_inquiry_status(self.db, token)
        self.assertEqual(status["status"], "CANCELLED")
        self.assertTrue(status["is_pii_erased"])

        # Attempt to dispatch offer on cancelled inquiry (must be None)
        offer = dispatch_1to1_offer(self.db, res["inquiry_id"])
        self.assertIsNone(offer)

if __name__ == "__main__":
    unittest.main()
