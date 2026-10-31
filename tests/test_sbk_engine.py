"""
SlotBridge Korea — Automated Test Suite (Security, Database, Concurrency, Accounting)
Executes all acceptance criteria and security controls for Step 04.
"""

import unittest
import json
import sqlite3
from datetime import datetime, timezone

from src.db import DatabaseManager
from src.crypto import encrypt_pii, decrypt_pii, mask_phone, mask_name, hash_phone
from src.engine import (
    create_inquiry, dispatch_1to1_offer, accept_offer,
    submit_fulfillment, revoke_inquiry, EngineError
)

class TestSlotBridgeKorea(unittest.TestCase):
    def setUp(self):
        """Set up in-memory D1-compatible SQLite database for isolated test execution."""
        self.db = DatabaseManager(":memory:")
        self.db.run_migration_up()
        self.db.seed_data()

    def tearDown(self):
        self.db.close()

    def test_01_all_21_tables_exist(self):
        """Verify that all 21 core tables are created by migration 0001."""
        cursor = self.db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        tables = set(row["name"] for row in cursor.fetchall())
        expected_21_tables = {
            "organizations", "partners", "partner_members", "service_areas", "capacity_slots",
            "inquiries", "consent_events", "matches", "assignment_offers", "partner_responses",
            "booking_outcomes", "fulfillment_evidence", "revenue_rules", "invoices", "ledger_events",
            "reconciliation_events", "payouts", "campaign_attributions", "audit_log",
            "data_erasure_requests", "notification_outbox"
        }
        for table in expected_21_tables:
            self.assertIn(table, tables, f"Required table '{table}' is missing from schema")
        self.assertEqual(len(expected_21_tables.intersection(tables)), 21)

    def test_02_migration_rollback_and_replay(self):
        """Verify that rollback teardown drops all tables cleanly and re-applies."""
        self.db.run_migration_down()
        cursor = self.db.execute("SELECT count(*) AS cnt FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        self.assertEqual(cursor.fetchone()["cnt"], 0)
        # Re-apply
        self.db.run_migration_up()
        cursor = self.db.execute("SELECT count(*) AS cnt FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        self.assertEqual(cursor.fetchone()["cnt"], 21)

    def test_03_seed_data_integrity(self):
        """Verify that development seeds populate partners, service areas, slots, and revenue rules."""
        partners = self.db.execute("SELECT count(*) AS cnt FROM partners").fetchone()["cnt"]
        self.assertGreaterEqual(partners, 3)
        slots = self.db.execute("SELECT count(*) AS cnt FROM capacity_slots").fetchone()["cnt"]
        self.assertGreaterEqual(slots, 4)
        rule = self.db.execute("SELECT platform_take_rate, referral_share_rate FROM revenue_rules WHERE id='rev_rule_standard_2026'").fetchone()
        self.assertAlmostEqual(rule["platform_take_rate"], 0.08)
        self.assertAlmostEqual(rule["referral_share_rate"], 0.35)

    def test_04_pii_encryption_zero_plaintext_in_db(self):
        """Verify that raw customer name and phone are NEVER stored as plaintext in the database."""
        raw_name = "김관악"
        raw_phone = "010-9876-5432"

        # Calculate tomorrow date dynamically matching available seed slot
        cursor = self.db.execute("SELECT slot_date FROM capacity_slots WHERE status='AVAILABLE' LIMIT 1")
        target_date = cursor.fetchone()["slot_date"]

        res = create_inquiry(self.db, {
            "requested_date": target_date,
            "district": "관악구",
            "neighborhood": "봉천동",
            "housing_type": "STUDIO",
            "area_pyeong": 10,
            "customer_name": raw_name,
            "customer_phone": raw_phone,
            "agreed_privacy_collection": True,
            "agreed_third_party_share": True
        })
        self.assertEqual(res["status"], "PENDING_MATCH")

        # Direct raw SQL query to inspect columns
        row = self.db.execute("SELECT customer_name_encrypted, customer_phone_encrypted FROM inquiries WHERE id=?", (res["inquiry_id"],)).fetchone()
        self.assertNotEqual(row["customer_name_encrypted"], raw_name)
        self.assertNotEqual(row["customer_phone_encrypted"], raw_phone)
        self.assertNotIn(raw_phone, row["customer_phone_encrypted"])
        
        # Verify proper decryption works with correct key
        self.assertEqual(decrypt_pii(row["customer_name_encrypted"]), raw_name)
        self.assertEqual(decrypt_pii(row["customer_phone_encrypted"]), raw_phone)

    def test_05_pii_log_masking(self):
        """Verify phone and name masking utilities."""
        self.assertEqual(mask_phone("010-1234-5678"), "010-****-5678")
        self.assertEqual(mask_name("김관악"), "김*악")
        self.assertEqual(mask_name("이동"), "이*")

    def test_06_mandatory_consent_rejection(self):
        """Verify that missing privacy or 3rd-party consent strictly rejects submission."""
        with self.assertRaises(EngineError) as cm:
            create_inquiry(self.db, {
                "requested_date": "2026-11-01",
                "district": "관악구",
                "area_pyeong": 10,
                "customer_name": "홍길동",
                "customer_phone": "010-1111-2222",
                "agreed_privacy_collection": True,
                "agreed_third_party_share": False # Missing!
            })
        self.assertEqual(cm.exception.code, "CONSENT_REQUIRED")

    def test_07_1to1_exclusive_offer_and_atomic_acceptance(self):
        """Verify end-to-end 1:1 exclusive matching, atomic lock, and PII reveal upon acceptance."""
        cursor = self.db.execute("SELECT slot_date FROM capacity_slots WHERE status='AVAILABLE' LIMIT 1")
        target_date = cursor.fetchone()["slot_date"]

        # 1. Create Inquiry with attribution
        inq = create_inquiry(self.db, {
            "attribution_partner_id": "partner_sl_master",
            "requested_date": target_date,
            "time_window": "MORNING",
            "district": "관악구",
            "neighborhood": "봉천동",
            "housing_type": "STUDIO",
            "area_pyeong": 12,
            "customer_name": "이지은",
            "customer_phone": "010-4444-5555",
            "agreed_privacy_collection": True,
            "agreed_third_party_share": True
        })

        # 2. Dispatch 1:1 Offer
        offer = dispatch_1to1_offer(self.db, inq["inquiry_id"])
        self.assertIsNotNone(offer)
        self.assertEqual(offer["partner_id"], "partner_gw_cleanhome")

        # 3. Accept Offer by correct partner
        accept_res = accept_offer(self.db, offer["offer_id"], "partner_gw_cleanhome")
        self.assertEqual(accept_res["status"], "CONFIRMED")
        self.assertEqual(accept_res["decrypted_customer_name"], "이지은")
        self.assertEqual(accept_res["decrypted_customer_phone"], "010-4444-5555")

        # 4. Attempt duplicate acceptance (must fail)
        with self.assertRaises(EngineError) as cm:
            accept_offer(self.db, offer["offer_id"], "partner_gw_cleanhome")
        self.assertEqual(cm.exception.code, "ALREADY_PROCESSED")

    def test_08_fulfillment_and_immutable_ledger_accounting(self):
        """Verify completion reporting generates exact 8% platform revenue and 35% referral allocation."""
        cursor = self.db.execute("SELECT slot_date FROM capacity_slots WHERE status='AVAILABLE' LIMIT 1")
        target_date = cursor.fetchone()["slot_date"]

        # Create, dispatch, accept
        inq = create_inquiry(self.db, {
            "attribution_partner_id": "partner_sl_master",
            "requested_date": target_date,
            "district": "관악구",
            "housing_type": "STUDIO",
            "area_pyeong": 15,
            "customer_name": "박서준",
            "customer_phone": "010-7777-8888",
            "agreed_privacy_collection": True,
            "agreed_third_party_share": True
        })
        offer = dispatch_1to1_offer(self.db, inq["inquiry_id"])
        acc = accept_offer(self.db, offer["offer_id"], "partner_gw_cleanhome")
        match_id = acc["match_id"]

        # Submit fulfillment with 250,000 KRW
        ful = submit_fulfillment(
            self.db,
            match_id=match_id,
            partner_id="partner_gw_cleanhome",
            final_amount=250000,
            photo_urls=["https://r2.mock/photo1.jpg", "https://r2.mock/photo2.jpg"]
        )
        self.assertEqual(ful["final_amount"], 250000)
        self.assertEqual(ful["platform_fee"], 20000) # 8% of 250k
        self.assertEqual(ful["referral_fee"], 7000)  # 35% of 20k

        # Verify Ledger Events in Database
        ledgers = self.db.execute("SELECT event_type, amount FROM ledger_events WHERE match_id=?", (match_id,)).fetchall()
        ledger_map = {row["event_type"]: row["amount"] for row in ledgers}
        self.assertEqual(ledger_map["REVENUE_ACCRUED"], 20000)
        self.assertEqual(ledger_map["REFERRAL_ALLOCATED"], 7000)

    def test_09_1click_revocation_and_tombstone(self):
        """Verify 1-click revocation completely obliterates PII and cancels inquiry."""
        cursor = self.db.execute("SELECT slot_date FROM capacity_slots WHERE status='AVAILABLE' LIMIT 1")
        target_date = cursor.fetchone()["slot_date"]

        inq = create_inquiry(self.db, {
            "requested_date": target_date,
            "district": "관악구",
            "housing_type": "STUDIO",
            "area_pyeong": 10,
            "customer_name": "최동작",
            "customer_phone": "010-3333-2222",
            "agreed_privacy_collection": True,
            "agreed_third_party_share": True
        })
        token = inq["tracking_token"]

        # User clicks revoke
        res = revoke_inquiry(self.db, token)
        self.assertTrue(res["success"])

        # Check DB state
        row = self.db.execute("SELECT customer_name_encrypted, customer_phone_encrypted, status FROM inquiries WHERE id=?", (inq["inquiry_id"],)).fetchone()
        self.assertEqual(row["status"], "CANCELLED")
        self.assertTrue(row["customer_name_encrypted"].startswith("ERASED_"))
        self.assertTrue(row["customer_phone_encrypted"].startswith("ERASED_"))

        # Verify erasure request log
        era = self.db.execute("SELECT requested_by, status FROM data_erasure_requests WHERE inquiry_id=?", (inq["inquiry_id"],)).fetchone()
        self.assertEqual(era["status"], "COMPLETED")

    def test_10_circuit_breaker_fail_closed(self):
        """Verify fail-closed circuit breaker triggers at 80% quota threshold."""
        # Lower quota limit for testing
        self.db.max_daily_writes = 10
        self.db.daily_write_count = 8 # 80% reached!

        with self.assertRaises(RuntimeError) as cm:
            self.db.execute("INSERT INTO organizations (id, name) VALUES ('test_org', 'Test')")
        self.assertIn("Fail-Closed Circuit Breaker Triggered", str(cm.exception))

if __name__ == "__main__":
    unittest.main()
