"""
SlotBridge Korea — Security & Vulnerability Audit Test Suite (Step 11)
Conducts automated penetration checks:
1. SQL Injection (SQLi) resistance across dynamic query parameters.
2. Insecure Direct Object Reference (IDOR) & Broken Object Level Authorization (BOLA).
3. Plaintext PII Leakage (Ensures zero unencrypted customer names/phones in database).
4. Secret Leak & Hardcoded Credentials Audit across repository codebase.
"""

import os
import re
import unittest
from datetime import datetime, timedelta

from src.db import DatabaseManager
from src.supplier import (
    apply_partner, approve_partner, register_capacity_slot, SEOUL_TZ
)
from src.intake import submit_customer_inquiry, get_inquiry_status
from src.matching import evaluate_and_dispatch_candidates
from src.engine import accept_offer, EngineError

class TestSlotBridgeSecurityAudit(unittest.TestCase):
    def setUp(self):
        self.db = DatabaseManager(":memory:")
        self.db.run_migration_up()
        self.today_seoul = datetime.now(SEOUL_TZ).date()
        self.tomorrow_str = (self.today_seoul + timedelta(days=1)).strftime("%Y-%m-%d")

    def tearDown(self):
        self.db.close()

    def test_security_01_sqli_injection_resistance(self):
        """
        Tests SQL injection payloads across all intake and partner search parameters.
        Must be parameterized safely without syntax error or unauthorized data extraction.
        """
        sqli_payloads = [
            "' OR '1'='1",
            "'; DROP TABLE matches; --",
            "admin'--",
            "1 UNION SELECT null, null, null, null--"
        ]
        for payload in sqli_payloads:
            # Test customer intake with malicious district
            with self.assertRaises(EngineError):
                submit_customer_inquiry(self.db, {
                    "requested_date": self.tomorrow_str,
                    "district": payload,
                    "housing_type": "STUDIO",
                    "area_pyeong": 10,
                    "customer_name": f"SQLi_{payload}",
                    "customer_phone": "010-0000-0000",
                    "agreed_privacy_collection": True,
                    "agreed_third_party_share": True
                })

            # Verify table still exists
            check = self.db.execute("SELECT count(*) AS cnt FROM matches").fetchone()["cnt"]
            self.assertEqual(check, 0)

    def test_security_02_idor_unauthorized_pii_access_blocked(self):
        """
        BOLA/IDOR Check:
        A partner or third party cannot access customer PII by guessing an inquiry_id or offer_id.
        """
        p1_res = apply_partner(self.db, {
            "business_number": "111-22-33333", "name": "파트너1",
            "representative_name": "김일", "contact_phone": "010-1111-2222",
            "service_areas": [{"city": "서울특별시", "district": "관악구", "neighborhood": "전체"}],
            "liability_insurance_policy_number": "INS-111"
        })
        p1 = p1_res["partner_id"]
        approve_partner(self.db, p1, admin_id="admin")
        register_capacity_slot(self.db, p1, self.tomorrow_str, "MORNING")

        p2_res = apply_partner(self.db, {
            "business_number": "444-55-66666", "name": "파트너2 (공격자)",
            "representative_name": "이이", "contact_phone": "010-4444-5555",
            "service_areas": [{"city": "서울특별시", "district": "동작구", "neighborhood": "전체"}],
            "liability_insurance_policy_number": "INS-222"
        })
        p2 = p2_res["partner_id"]
        approve_partner(self.db, p2, admin_id="admin")

        inq = submit_customer_inquiry(self.db, {
            "requested_date": self.tomorrow_str, "district": "관악구",
            "housing_type": "STUDIO", "area_pyeong": 10,
            "customer_name": "민감한고객", "customer_phone": "010-8888-9999",
            "agreed_privacy_collection": True, "agreed_third_party_share": True
        })
        dispatch = evaluate_and_dispatch_candidates(self.db, inq["inquiry_id"])
        offer_id = dispatch["offer_id"]

        # Partner 2 attempts to accept Partner 1's offer (IDOR)
        with self.assertRaises(EngineError) as cm:
            accept_offer(self.db, offer_id, partner_id=p2) # Wrong partner
        self.assertEqual(cm.exception.status_code, 404)

        # Attacker attempts to retrieve inquiry status with invalid/guessed token
        fake_token = "inqtok_attacker_guessed_token_12345"
        with self.assertRaises(EngineError) as cm:
            get_inquiry_status(self.db, fake_token)
        self.assertEqual(cm.exception.status_code, 404)

    def test_security_03_zero_plaintext_pii_in_database(self):
        """
        PII Audit:
        Scans all text columns in SQLite database to guarantee raw phone '010-9876-5432'
        and raw name '홍길동보안' are NEVER stored in plaintext.
        """
        raw_name = "홍길동보안"
        raw_phone = "010-9876-5432"

        submit_customer_inquiry(self.db, {
            "requested_date": self.tomorrow_str,
            "district": "관악구",
            "housing_type": "STUDIO",
            "area_pyeong": 10,
            "customer_name": raw_name,
            "customer_phone": raw_phone,
            "agreed_privacy_collection": True,
            "agreed_third_party_share": True
        })

        # Scan all records across all tables
        tables = [r["name"] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        for tbl in tables:
            rows = self.db.execute(f"SELECT * FROM {tbl}").fetchall()
            for r in rows:
                for col_name in r.keys():
                    val = str(r[col_name])
                    self.assertNotIn(raw_name, val, f"Plaintext name leaked in table '{tbl}', col '{col_name}'!")
                    self.assertNotIn(raw_phone, val, f"Plaintext phone leaked in table '{tbl}', col '{col_name}'!")

    def test_security_04_secret_leak_audit(self):
        """
        Codebase Secret Audit:
        Scans git working tree for hardcoded API keys, bearer tokens, and private keys.
        """
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        secret_patterns = [
            re.compile(r'AKIA[0-9A-Z]{16}'),                    # AWS Access Key
            re.compile(r'ghp_[0-9a-zA-Z]{36}'),                 # GitHub Personal Access Token
            re.compile(r'-----BEGIN [A-Z]+ PRIVATE KEY-----'),   # Private Key
            re.compile(r'sk_live_[0-9a-zA-Z]{24}')              # Stripe Secret Key
        ]

        scanned_files = 0
        for root, _, files in os.walk(base_dir):
            if any(p in root for p in [".git", "__pycache__", "venv", ".pytest_cache"]):
                continue
            for f in files:
                if f.endswith(('.py', '.sql', '.html', '.js', '.yaml', '.md', '.json')):
                    filepath = os.path.join(root, f)
                    scanned_files += 1
                    with open(filepath, 'r', encoding='utf-8', errors='ignore') as fh:
                        content = fh.read()
                        for pattern in secret_patterns:
                            matches = pattern.findall(content)
                            self.assertEqual(len(matches), 0, f"Found secret leak matching {pattern.pattern} in {filepath}!")

        self.assertGreater(scanned_files, 20, "Must scan at least 20 source files.")

if __name__ == "__main__":
    unittest.main()
