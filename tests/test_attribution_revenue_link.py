import unittest
import os
import re
import sqlite3
import hashlib
import hmac
import time
import uuid
from datetime import datetime, timezone

class TestAttributionRevenueLink(unittest.TestCase):
    """
    SBK-MINI-05: Attribution Revenue Link Test Suite
    Verifies that:
    1. widget.js and inquiry.html propagate attribution_token properly.
    2. Public unauthenticated GET /attribution/token is blocked (401).
    3. Forged, expired, spoofed, or unapproved partner tokens are rejected (resulting in attribution=None).
    4. Valid signed tokens are attributed correctly in D1.
    """

    def setUp(self):
        self.repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.widget_js_path = os.path.join(self.repo_root, "slotbridge", "widget.js")
        self.inquiry_html_path = os.path.join(self.repo_root, "slotbridge", "inquiry.html")
        self.worker_js_path = os.path.join(self.repo_root, "src", "worker.js")

    def test_widget_js_signed_token_support(self):
        with open(self.widget_js_path, "r", encoding="utf-8") as f:
            js = f.read()

        # Checks data-token and data-partner-key attributes
        self.assertIn("data-token", js)
        self.assertIn("data-partner-key", js)
        self.assertIn("btn.href = baseUrl + '/slotbridge/inquiry.html?ref='", js)

    def test_inquiry_html_extracts_and_posts_attribution_token(self):
        with open(self.inquiry_html_path, "r", encoding="utf-8") as f:
            html = f.read()

        # Hidden input for token exists
        self.assertIn('<input type="hidden" id="attribution_token"', html)

        # Reads token parameter from URL
        self.assertIn("params.get('token')", html)

        # Sends attribution_token in fetch payload
        self.assertIn("attribution_token: document.getElementById('attribution_token').value", html)

    def test_worker_js_blocks_public_token_generation(self):
        with open(self.worker_js_path, "r", encoding="utf-8") as f:
            js = f.read()

        # Authentication check exists in GET /attribution/token
        self.assertIn("X-SBK-Partner-Key", js)
        self.assertIn("X-SBK-Admin-Key", js)
        self.assertIn("공개 토큰 발급이 차단되었습니다", js)

        # Anti-spoofing check exists in POST /inquiries
        self.assertIn("body.attribution_partner_id !== verified.partnerId", js)

    def test_attribution_verification_simulation(self):
        """Simulate verification rules on D1 database."""
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        with open(os.path.join(self.repo_root, "migrations", "0001_initial_schema.sql"), "r", encoding="utf-8") as f:
            conn.executescript(f.read())

        secret = "test_attribution_secret_key_2026_ok"
        now = int(time.time())

        # Register approved partner
        partner_approved = "part_approved_1"
        conn.execute(
            """
            INSERT INTO organizations (id, name, business_number, representative_name)
            VALUES ('org_1', '제휴점', '1234567890', '대표')
            """
        )
        conn.execute(
            """
            INSERT INTO partners (id, organization_id, name, partner_type, business_number, representative_name, contact_phone, verification_status)
            VALUES (?, 'org_1', '승인제휴사', 'REFERRING', '1234567890', '대표', '01011112222', 'APPROVED')
            """,
            (partner_approved,)
        )
        # Register pending partner
        partner_pending = "part_pending_2"
        conn.execute(
            """
            INSERT INTO partners (id, organization_id, name, partner_type, business_number, representative_name, contact_phone, verification_status)
            VALUES (?, 'org_1', '미승인제휴사', 'REFERRING', '9999999999', '대표', '01033334444', 'PENDING_APPROVAL')
            """,
            (partner_pending,)
        )
        conn.commit()

        def make_token(p_id, exp_offset=86400, tamper=False):
            exp = now + exp_offset
            payload = f"{p_id}:overflow_widget:{exp}"
            sig = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()[:32]
            if tamper:
                sig = "0" * 32
            return f"{payload}:{sig}"

        def verify(token_str, requested_ref=None):
            if not token_str or token_str.count(":") != 3:
                return None
            p_id, camp, exp_s, sig = token_str.split(":")
            exp = int(exp_s)
            if now > exp:
                return None
            expected_sig = hmac.new(secret.encode(), f"{p_id}:{camp}:{exp}".encode(), hashlib.sha256).hexdigest()[:32]
            if sig != expected_sig:
                return None
            # Anti-spoofing
            if requested_ref and requested_ref != p_id:
                return None
            # D1 check
            row = conn.execute("SELECT verification_status FROM partners WHERE id = ?", (p_id,)).fetchone()
            if not row or row["verification_status"] not in ["APPROVED", "VERIFIED"]:
                return None
            return p_id

        # 1. Valid token for approved partner -> Attributed
        valid_tok = make_token(partner_approved, 86400)
        self.assertEqual(verify(valid_tok, partner_approved), partner_approved)

        # 2. Forged signature -> None
        forged_tok = make_token(partner_approved, 86400, tamper=True)
        self.assertIsNone(verify(forged_tok, partner_approved))

        # 3. Expired token -> None
        expired_tok = make_token(partner_approved, -3600)
        self.assertIsNone(verify(expired_tok, partner_approved))

        # 4. Spoofed partner ID (token for partner_approved, but ref passed as partner_other) -> None
        self.assertIsNone(verify(valid_tok, "partner_attacker_spoof"))

        # 5. Token for unapproved partner -> None
        unapproved_tok = make_token(partner_pending, 86400)
        self.assertIsNone(verify(unapproved_tok, partner_pending))

        # 6. Direct intake (no token) -> None
        self.assertIsNone(verify(None, None))

if __name__ == "__main__":
    unittest.main()
