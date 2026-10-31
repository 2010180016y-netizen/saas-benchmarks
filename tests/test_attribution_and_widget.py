"""
SlotBridge Korea — Distribution & Attribution Test Suite (Step 07)
Validates cryptographic attribution tokens, fraud prevention (self-referral blocking,
tampering rejection, expiration), campaign click recording, and mock notification outbox.
"""

import unittest
import time
from datetime import datetime, timedelta

from src.db import DatabaseManager
from src.attribution import (
    generate_attribution_token, verify_attribution_token,
    record_widget_click, validate_attribution_for_match,
    queue_mock_notification, SECRET_KEY
)
from src.engine import EngineError

class TestAttributionAndWidget(unittest.TestCase):
    def setUp(self):
        self.db = DatabaseManager(":memory:")
        self.db.run_migration_up()
        self.db.seed_data()

    def tearDown(self):
        self.db.close()

    def test_01_token_generation_and_verification(self):
        """Generates valid signed token and verifies payload."""
        token = generate_attribution_token("partner_sl_master", "overflow_widget", ttl_seconds=3600)
        is_valid, data = verify_attribution_token(token)
        self.assertTrue(is_valid)
        self.assertEqual(data["partner_id"], "partner_sl_master")
        self.assertEqual(data["campaign_id"], "overflow_widget")

    def test_02_tampered_token_rejection(self):
        """Tampered partner_id or signature strictly fails verification."""
        token = generate_attribution_token("partner_sl_master", "overflow_widget")
        
        # Tamper payload
        tampered_token = token.replace("partner_sl_master", "partner_gw_cleanhome")
        is_valid, data = verify_attribution_token(tampered_token)
        self.assertFalse(is_valid)
        self.assertIsNone(data)

        # Tamper signature
        parts = token.split(":")
        tampered_sig = f"{parts[0]}:{parts[1]}:{parts[2]}:ffffffffffffffff"
        is_valid2, data2 = verify_attribution_token(tampered_sig)
        self.assertFalse(is_valid2)
        self.assertIsNone(data2)

    def test_03_expired_token_rejection(self):
        """Expired token (TTL < 0) strictly fails verification."""
        # Generate token with negative TTL
        token = generate_attribution_token("partner_sl_master", "overflow_widget", ttl_seconds=-10)
        is_valid, data = verify_attribution_token(token)
        self.assertFalse(is_valid)
        self.assertIsNone(data)

    def test_04_self_referral_wash_trading_blocked(self):
        """Self-referral is detected and stripped to prevent fee evasion."""
        # Case 1: Partner A refers and Partner A fulfills -> Dropped!
        res1 = validate_attribution_for_match(
            self.db,
            attribution_partner_id="partner_gw_cleanhome",
            fulfillment_partner_id="partner_gw_cleanhome"
        )
        self.assertIsNone(res1)

        # Case 2: Partner A refers and Partner B fulfills -> Valid!
        res2 = validate_attribution_for_match(
            self.db,
            attribution_partner_id="partner_sl_master",
            fulfillment_partner_id="partner_gw_cleanhome"
        )
        self.assertEqual(res2, "partner_sl_master")

    def test_05_record_widget_click_updates_campaign(self):
        """Recording widget click increments campaign clicks count in DB."""
        res = record_widget_click(self.db, "partner_sl_master", "overflow_banner")
        self.assertEqual(res["partner_id"], "partner_sl_master")
        self.assertIn("partner_sl_master", res["attribution_token"])
        self.assertIn("token=", res["referral_url"])

        # Check campaign_attributions table in DB
        camp = self.db.execute(
            "SELECT clicks_count FROM campaign_attributions WHERE partner_id='partner_sl_master' AND campaign_name='overflow_banner'"
        ).fetchone()
        self.assertGreaterEqual(camp["clicks_count"], 1)

    def test_06_unapproved_partner_widget_click_rejected(self):
        """Unapproved partner ID cannot record clicks or generate attribution."""
        with self.assertRaises(EngineError) as cm:
            record_widget_click(self.db, "fake_partner_id", "overflow_banner")
        self.assertEqual(cm.exception.code, "INVALID_PARTNER")

    def test_07_mock_notification_outbox(self):
        """Queues notifications into notification_outbox with MOCKED status for $0 cost."""
        notif_id = queue_mock_notification(
            self.db,
            recipient_type="CUSTOMER",
            recipient_destination="010-****-1234",
            notification_type="STATUS_UPDATE",
            payload={"status": "OFFERED", "token": "uuid"}
        )
        self.assertIsNotNone(notif_id)

        # Check DB
        row = self.db.execute("SELECT status, notification_type FROM notification_outbox WHERE id=?", (notif_id,)).fetchone()
        self.assertEqual(row["status"], "MOCKED")
        self.assertEqual(row["notification_type"], "STATUS_UPDATE")

if __name__ == "__main__":
    unittest.main()
