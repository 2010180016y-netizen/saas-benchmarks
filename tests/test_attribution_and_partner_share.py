"""
tests/test_attribution_and_partner_share.py
SBK-FIX-08 Verification Suite: Real Attribution, Tamper Protection & Self-Referral Prevention.
"""

import unittest
import json
import time
import os

from src.db import DatabaseManager
from src.attribution import (
    generate_attribution_token,
    verify_attribution_token,
    validate_attribution_for_match,
    get_attribution_secret,
    AttributionError
)
from src.intake import submit_customer_inquiry
from src.supplier import SEOUL_TZ

class TestAttributionAndPartnerShare(unittest.TestCase):
    def setUp(self):
        self.db = DatabaseManager(":memory:")
        self.db.run_migration_up()
        self.db.seed_data()

        # Seed approved referral partner
        self.db.execute(
            """
            INSERT OR IGNORE INTO organizations (id, name, business_number, representative_name)
            VALUES ('org_ref_approved', '공인리퍼럴', '1234567899', '이대표')
            """
        )
        self.db.execute(
            """
            INSERT OR IGNORE INTO partners (id, organization_id, name, partner_type, business_number, representative_name, contact_phone, verification_status, settlement_bank, settlement_account)
            VALUES ('partner_ref_approved', 'org_ref_approved', '공인리퍼럴', 'REFERRAL', '1234567899', '이대표', '010-8888-7777', 'APPROVED', '신한은행', '110-222-333333')
            """
        )
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_01_tampered_token_rejected(self):
        """Tampered partner ID or forged signature is strictly rejected by verify_attribution_token."""
        token = generate_attribution_token("partner_ref_approved", "campaign_test")
        parts = token.split(":")
        
        # Tamper partner ID
        tampered_partner = f"partner_attacker:{parts[1]}:{parts[2]}:{parts[3]}"
        is_valid, payload = verify_attribution_token(tampered_partner)
        self.assertFalse(is_valid)
        self.assertIsNone(payload)

        # Tamper signature
        tampered_sig = f"{parts[0]}:{parts[1]}:{parts[2]}:0123456789abcdef0123456789abcdef"
        is_valid, payload = verify_attribution_token(tampered_sig)
        self.assertFalse(is_valid)
        self.assertIsNone(payload)

    def test_02_expired_token_rejected(self):
        """Token with past expiration timestamp is strictly rejected."""
        # Expired token with negative TTL
        expired_token = generate_attribution_token("partner_ref_approved", "campaign_test", ttl_seconds=-100)
        is_valid, payload = verify_attribution_token(expired_token)
        self.assertFalse(is_valid)
        self.assertIsNone(payload)

    def test_03_raw_ref_without_token_sanitized_in_strict_mode(self):
        """Inquiry with ?ref=PARTNER_ID alone without valid signed token is sanitized to None under strict attribution."""
        os.environ["SBK_STRICT_ATTRIBUTION"] = "1"
        try:
            inq = submit_customer_inquiry(self.db, {
                "attribution_partner_id": "partner_ref_approved", # Raw ref without token
                "requested_date": "2026-11-10",
                "district": "관악구",
                "housing_type": "STUDIO",
                "area_pyeong": 10,
                "customer_name": "무단변조고객",
                "customer_phone": "010-9999-0001",
                "agreed_privacy_collection": True,
                "agreed_third_party_share": True
            })
            # Check DB row: attribution_partner_id MUST be None
            row = self.db.execute("SELECT attribution_partner_id FROM inquiries WHERE id = ?", (inq["inquiry_id"],)).fetchone()
            self.assertIsNone(row["attribution_partner_id"])
        finally:
            os.environ.pop("SBK_STRICT_ATTRIBUTION", None)

    def test_04_valid_signed_token_grants_approved_attribution(self):
        """Inquiry submitted with valid signed HMAC token successfully grants attribution to approved partner."""
        token = generate_attribution_token("partner_ref_approved", "campaign_clean")
        inq = submit_customer_inquiry(self.db, {
            "attribution_token": token,
            "requested_date": "2026-11-10",
            "district": "관악구",
            "housing_type": "STUDIO",
            "area_pyeong": 10,
            "customer_name": "정상제휴고객",
            "customer_phone": "010-9999-0002",
            "agreed_privacy_collection": True,
            "agreed_third_party_share": True
        })
        row = self.db.execute("SELECT attribution_partner_id FROM inquiries WHERE id = ?", (inq["inquiry_id"],)).fetchone()
        self.assertEqual(row["attribution_partner_id"], "partner_ref_approved")

    def test_05_self_referral_wash_trading_blocked(self):
        """If attributing partner is identical to fulfillment partner, referral cut is dropped and audit logged."""
        # When fulfilling partner is partner_gw_cleanhome and referring partner is also partner_gw_cleanhome
        valid_attr = validate_attribution_for_match(
            self.db,
            attribution_partner_id="partner_gw_cleanhome",
            fulfillment_partner_id="partner_gw_cleanhome"
        )
        self.assertIsNone(valid_attr)

        # Verify audit log was recorded
        audit = self.db.execute(
            "SELECT * FROM audit_log WHERE action = 'SELF_REFERRAL_BLOCKED' AND resource_id = 'partner_gw_cleanhome'"
        ).fetchone()
        self.assertIsNotNone(audit)
        self.assertIn("자가 귀속 차단", audit["details"])

    def test_06_unapproved_partner_attribution_rejected(self):
        """Unapproved/unverified partner attribution is rejected by validate_attribution_for_match."""
        self.db.execute(
            """
            INSERT OR IGNORE INTO organizations (id, name, business_number, representative_name)
            VALUES ('org_pending', '대기파트너', '1111111111', '정대표')
            """
        )
        self.db.execute(
            """
            INSERT OR IGNORE INTO partners (id, organization_id, name, partner_type, business_number, representative_name, contact_phone, verification_status)
            VALUES ('partner_pending', 'org_pending', '대기파트너', 'REFERRAL', '1111111111', '정대표', '010-1111-9999', 'PENDING_APPROVAL')
            """
        )
        self.db.commit()

        valid_attr = validate_attribution_for_match(
            self.db,
            attribution_partner_id="partner_pending",
            fulfillment_partner_id="partner_gw_cleanhome"
        )
        self.assertIsNone(valid_attr)

    def test_07_fail_closed_missing_secret(self):
        """When HMAC secret key is empty, get_attribution_secret strictly raises AttributionError."""
        with self.assertRaises(AttributionError):
            get_attribution_secret(secret="")

    def test_08_widget_js_safe_dom_and_zero_cookies(self):
        """Verifies slotbridge/widget.js uses safe DOM APIs, no dangerous innerHTML interpolation, and zero cookies."""
        with open("slotbridge/widget.js", "r", encoding="utf-8") as f:
            code = f.read()

        # Zero document.cookie writes
        self.assertNotIn("document.cookie", code)
        self.assertNotIn("localStorage", code)

        # Zero innerHTML string interpolations
        self.assertNotIn("innerHTML", code)

        # Safe DOM API methods present
        self.assertIn("createElement", code)
        self.assertIn("textContent", code)
        self.assertIn("attribution/token", code)

if __name__ == "__main__":
    unittest.main()
