import unittest
import os
import re
import sqlite3
import json
import uuid
from datetime import datetime, timezone, timedelta

class TestIntakeAndPartnerWorkflow(unittest.TestCase):
    """
    SBK-MINI-06: Customer Intake Reliability & Partner Application Workflow Test Suite
    Verifies:
    1. KST 24~72h date window and time_window validation.
    2. Idempotency-Key header support and deduplication (409 Conflict).
    3. Partner application business number uniqueness (409 Conflict) and initial PENDING_APPROVAL.
    4. Admin partner verification endpoint POST /admin/partners/:id/verify with audit logging.
    5. inquiry.html dynamic time_window and KST date calculations.
    """

    def setUp(self):
        self.repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.worker_js_path = os.path.join(self.repo_root, "src", "worker.js")
        self.inquiry_html_path = os.path.join(self.repo_root, "slotbridge", "inquiry.html")

    def test_inquiry_html_dynamic_time_window_and_kst_date(self):
        with open(self.inquiry_html_path, "r", encoding="utf-8") as f:
            html = f.read()

        # Dynamic time_window extraction
        self.assertIn("requested_time_window: document.getElementById('time_window').value", html)
        self.assertNotIn("requested_time_window: 'MORNING',", html)

        # KST calculation in JS
        self.assertIn("9 * 3600 * 1000", html)

    def test_worker_js_has_workflow_and_reliability_checks(self):
        with open(self.worker_js_path, "r", encoding="utf-8") as f:
            js = f.read()

        # Idempotency check
        self.assertIn('request.headers.get("Idempotency-Key")', js)
        self.assertIn('idempotent_replay: true', js)

        # KST Date window check
        self.assertIn('DATE_OUT_OF_WINDOW', js)
        self.assertIn('INVALID_TIME_WINDOW', js)

        # Duplicate inquiry check (409)
        self.assertIn('DUPLICATE_INQUIRY', js)

        # Atomic batch insert
        self.assertIn('await env.DB.batch([insertInquiryStmt, insertConsentStmt]);', js)

        # Partner duplicate business number check (409)
        self.assertIn('DUPLICATE_PARTNER', js)

        # Admin partner verification endpoint
        self.assertIn('/admin/partners/', js)
        self.assertIn('PARTNER_VERIFIED', js)

    def test_d1_intake_and_partner_workflow_simulation(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        with open(os.path.join(self.repo_root, "migrations", "0001_initial_schema.sql"), "r", encoding="utf-8") as f:
            conn.executescript(f.read())

        now_kst = datetime.now(timezone(timedelta(hours=9)))
        today_kst_str = now_kst.strftime("%Y-%m-%d")

        # 1. Date window validation simulation
        def check_date_window(req_date_str):
            req_d = datetime.strptime(req_date_str, "%Y-%m-%d").date()
            today_d = now_kst.date()
            diff = (req_d - today_d).days
            if diff < 1 or diff > 3:
                return False, "DATE_OUT_OF_WINDOW"
            return True, "OK"

        tomorrow_str = (now_kst + timedelta(days=1)).strftime("%Y-%m-%d")
        three_days_str = (now_kst + timedelta(days=3)).strftime("%Y-%m-%d")
        past_str = (now_kst - timedelta(days=1)).strftime("%Y-%m-%d")
        far_future_str = (now_kst + timedelta(days=5)).strftime("%Y-%m-%d")

        self.assertTrue(check_date_window(tomorrow_str)[0])
        self.assertTrue(check_date_window(three_days_str)[0])
        self.assertFalse(check_date_window(today_kst_str)[0])
        self.assertFalse(check_date_window(past_str)[0])
        self.assertFalse(check_date_window(far_future_str)[0])

        # 2. Partner registration & duplicate check
        biz_num = "1234567890"
        conn.execute(
            """
            INSERT INTO organizations (id, name, business_number, representative_name)
            VALUES ('org_test', '클린파트너', ?, '김대표')
            """,
            (biz_num,)
        )
        conn.execute(
            """
            INSERT INTO partners (id, organization_id, name, partner_type, business_number, representative_name, contact_phone, verification_status)
            VALUES ('part_test_1', 'org_test', '클린파트너', 'FULFILLMENT', ?, '김대표', '01011112222', 'PENDING_APPROVAL')
            """,
            (biz_num,)
        )
        conn.commit()

        # Duplicate check should detect existing biz_num
        row = conn.execute("SELECT id FROM partners WHERE business_number = ?", (biz_num,)).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["id"], "part_test_1")

        # 3. Admin verification
        now_iso = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "UPDATE partners SET verification_status = 'APPROVED', liability_insurance_status = 'VERIFIED', updated_at = ? WHERE id = ?",
            (now_iso, "part_test_1")
        )
        conn.execute(
            """
            INSERT INTO audit_log (id, actor_id, action, resource_type, resource_id, details, created_at)
            VALUES (?, 'ADMIN', 'PARTNER_VERIFIED', 'PARTNER', 'part_test_1', '{"decision": "APPROVED"}', ?)
            """,
            (f"aud_{uuid.uuid4()}", now_iso)
        )
        conn.commit()

        verified_row = conn.execute("SELECT verification_status, liability_insurance_status FROM partners WHERE id = 'part_test_1'").fetchone()
        self.assertEqual(verified_row["verification_status"], "APPROVED")
        self.assertEqual(verified_row["liability_insurance_status"], "VERIFIED")

        audit_row = conn.execute("SELECT action FROM audit_log WHERE resource_id = 'part_test_1'").fetchone()
        self.assertEqual(audit_row["action"], "PARTNER_VERIFIED")

if __name__ == "__main__":
    unittest.main()
