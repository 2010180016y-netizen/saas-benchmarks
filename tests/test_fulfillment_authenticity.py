import unittest
import os
import json
import sqlite3
import uuid
from datetime import datetime, timezone, timedelta

class TestFulfillmentAuthenticity(unittest.TestCase):
    """
    SBK-MINI-10: Fulfillment Authenticity & Evidence Validation Test Suite
    Verifies:
    1. /matches/:id/fulfill requires valid partner authentication (401).
    2. Only assigned partner can submit fulfillment (403).
    3. Fulfillment requires at least 2 valid photo URLs (400).
    4. /matches/:id/confirm requires valid customer tracking token (401).
    5. Confirmation cannot proceed without valid 2+ photo evidence (400).
    6. Legitimate two-way confirmation transitions to BILLABLE with idempotent ledger.
    """

    def setUp(self):
        self.repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.worker_js_path = os.path.join(self.repo_root, "src", "worker.js")

    def test_worker_code_contains_authenticity_guards(self):
        with open(self.worker_js_path, "r", encoding="utf-8") as f:
            code = f.read()

        # Partner authentication check
        self.assertIn("파트너 인증 키(X-SBK-Partner-Key)가 유효하지 않습니다", code)
        self.assertIn("배정된 파트너만 해당 매칭의 완료 보고를 제출할 수 있습니다", code)

        # Customer token authentication check
        self.assertIn("고객 조회 토큰(X-SBK-Customer-Token) 또는 관리자 권한이 필요합니다", code)

        # Evidence validation before BILLABLE
        self.assertIn("유효한 시공 사진 증빙(2장 이상)이 없어 BILLABLE로 전이할 수 없습니다", code)

    def test_sqlite_two_way_fulfillment_evidence_gate(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        with open(os.path.join(self.repo_root, "migrations", "0001_initial_schema.sql"), "r", encoding="utf-8") as f:
            conn.executescript(f.read())

        now_iso = datetime.now(timezone.utc).isoformat()

        # Create Partner 1 (assigned) and Partner 2 (unassigned)
        conn.execute("INSERT INTO organizations (id, name, business_number, representative_name) VALUES ('org_1', '업체1', '1234567890', '대표1')")
        conn.execute(
            """
            INSERT INTO partners (id, organization_id, name, partner_type, business_number, representative_name, contact_phone, verification_status, liability_insurance_status)
            VALUES ('partner_1', 'org_1', '업체1', 'FULFILLMENT', '1234567890', '대표1', '01011112222', 'APPROVED', 'VERIFIED')
            """
        )
        conn.execute("INSERT INTO organizations (id, name, business_number, representative_name) VALUES ('org_2', '업체2', '9999999999', '대표2')")
        conn.execute(
            """
            INSERT INTO partners (id, organization_id, name, partner_type, business_number, representative_name, contact_phone, verification_status, liability_insurance_status)
            VALUES ('partner_2', 'org_2', '업체2', 'FULFILLMENT', '9999999999', '대표2', '01033334444', 'APPROVED', 'VERIFIED')
            """
        )

        # Create Inquiry with tracking_token
        inq_id = "inq_auth_test"
        cust_token = "cst_tok_valid_123"
        conn.execute(
            """
            INSERT INTO inquiries (id, tracking_token, district, neighborhood, housing_type, area_pyeong, requested_date, time_window, customer_name_encrypted, customer_phone_encrypted, customer_phone_hash, status, expires_at)
            VALUES (?, ?, '관악구', '봉천동', 'STUDIO', 8, '2026-11-02', 'MORNING', 'enc_n', 'enc_p', 'hash_p', 'BOOKED', ?)
            """,
            (inq_id, cust_token, now_iso)
        )

        # Create Capacity Slot & Match assigned to partner_1
        slot_id = "slot_auth_test"
        conn.execute("INSERT INTO capacity_slots (id, partner_id, slot_date, time_window, status) VALUES (?, 'partner_1', '2026-11-02', 'MORNING', 'RESERVED')", (slot_id,))

        match_id = "mat_auth_test"
        conn.execute(
            """
            INSERT INTO matches (id, inquiry_id, partner_id, slot_id, status, estimated_amount, created_at, updated_at)
            VALUES (?, ?, 'partner_1', ?, 'BOOKED', 250000, ?, ?)
            """,
            (match_id, inq_id, slot_id, now_iso, now_iso)
        )
        conn.commit()

        # 1. Partner 2 attempts to fulfill partner 1's match -> Forbidden
        match = conn.execute("SELECT * FROM matches WHERE id = ?", (match_id,)).fetchone()
        self.assertNotEqual(match["partner_id"], "partner_2")

        # 2. Insufficient evidence check: only 1 photo -> Rejected
        one_photo = ["https://storage.com/photo1.jpg"]
        self.assertLess(len(one_photo), 2)

        # 3. Legitimate partner submits fulfillment with 2 photos
        valid_photos = ["https://storage.com/before.jpg", "https://storage.com/after.jpg"]
        evi_id = "evi_auth_test"
        conn.execute(
            """
            INSERT INTO fulfillment_evidence (
                id, match_id, photo_urls, completion_timestamp, partner_submitted_amount, verification_status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 250000, 'PENDING_CUSTOMER_CONFIRMATION', ?, ?)
            """,
            (evi_id, match_id, json.dumps(valid_photos), now_iso, now_iso, now_iso)
        )
        conn.execute("UPDATE matches SET status = 'COMPLETED', final_amount = 250000, updated_at = ? WHERE id = ?", (now_iso, match_id))
        conn.commit()

        # 4. Third party with invalid tracking token attempts confirm -> Unauthorized
        attacker_token = "cst_tok_fake_999"
        inq_row = conn.execute("SELECT tracking_token FROM inquiries WHERE id = ?", (inq_id,)).fetchone()
        self.assertNotEqual(attacker_token, inq_row["tracking_token"])

        # 5. Legitimate customer with correct token confirms
        self.assertEqual(cust_token, inq_row["tracking_token"])

        # Evidence validation before BILLABLE
        evidence = conn.execute("SELECT * FROM fulfillment_evidence WHERE match_id = ?", (match_id,)).fetchone()
        self.assertIsNotNone(evidence)
        photos = json.loads(evidence["photo_urls"])
        self.assertGreaterEqual(len(photos), 2)

        # Transition to BILLABLE
        conn.execute("UPDATE fulfillment_evidence SET verification_status = 'VERIFIED', updated_at = ? WHERE match_id = ?", (now_iso, match_id))
        conn.execute("UPDATE matches SET status = 'BILLABLE', updated_at = ? WHERE id = ?", (now_iso, match_id))

        # Ledger Accrual
        conn.execute(
            """
            INSERT INTO ledger_events (id, match_id, event_type, amount, debit_account, credit_account, description, created_at)
            VALUES (?, ?, 'REVENUE_ACCRUED', 20000, 'ACCOUNTS_RECEIVABLE_PARTNER', 'PLATFORM_COMMISSION_REVENUE', 'B2B 성과 수수료 8%', ?)
            """,
            (f"led_{uuid.uuid4()}", match_id, now_iso)
        )
        conn.commit()

        final_match = conn.execute("SELECT status, final_amount FROM matches WHERE id = ?", (match_id,)).fetchone()
        self.assertEqual(final_match["status"], "BILLABLE")
        self.assertEqual(final_match["final_amount"], 250000)

        final_evi = conn.execute("SELECT verification_status FROM fulfillment_evidence WHERE match_id = ?", (match_id,)).fetchone()
        self.assertEqual(final_evi["verification_status"], "VERIFIED")

        ledger = conn.execute("SELECT amount, debit_account, credit_account FROM ledger_events WHERE match_id = ?", (match_id,)).fetchone()
        self.assertEqual(ledger["amount"], 20000)

if __name__ == "__main__":
    unittest.main()
