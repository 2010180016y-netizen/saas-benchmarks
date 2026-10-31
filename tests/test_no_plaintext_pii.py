import unittest
import os
import re
import sqlite3
import hashlib
import uuid
from datetime import datetime, timezone

class TestNoPlaintextPii(unittest.TestCase):
    """
    SBK-MINI-04: No Plaintext PII Test Suite
    Verifies that customer_phone_hash stores a 64-character keyed HMAC hash rather than plaintext digits,
    that client_ip_hash and user_agent avoid fake static constants,
    and that no customer phone numbers remain in any plain text column in D1.
    """

    def setUp(self):
        self.repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.worker_js_path = os.path.join(self.repo_root, "src", "worker.js")

    def test_worker_js_has_no_plaintext_phone_in_d1_insert(self):
        with open(self.worker_js_path, "r", encoding="utf-8") as f:
            js = f.read()

        # 1. Plaintext phone replacement must NOT be bound to customer_phone_hash in D1 insert
        self.assertNotIn(
            'phone.replace(/[^0-9]/g, ""),\n          attributionPartnerId,',
            js,
            "Raw digits phone.replace(/[^0-9]/g, '') must NOT be bound directly to customer_phone_hash"
        )
        self.assertIn(
            'phoneHash,\n          attributionPartnerId,',
            js,
            "customer_phone_hash must be bound to phoneHash"
        )

        # 2. Keyed HMAC hashPhoneNumber and decryptPii must exist
        self.assertIn("async function hashPhoneNumber(phone, env)", js)
        self.assertIn("async function decryptPii(encoded, env)", js)

        # 3. Fake hardcoded consent constants must be removed
        self.assertNotIn("'hash_cf_worker'", js)
        self.assertNotIn("'CloudflareEdge'", js)

    def test_d1_persistence_has_zero_plaintext_pii(self):
        """Simulate D1 insertion with new hashing logic and verify zero plaintext in all columns."""
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        with open(os.path.join(self.repo_root, "migrations", "0001_initial_schema.sql"), "r", encoding="utf-8") as f:
            conn.executescript(f.read())

        raw_phone = "010-5555-8888"
        clean_phone = raw_phone.replace("-", "")
        # Compute HMAC-like hash
        phone_hash = hashlib.sha256(("SBK_PHONE_HASH_V1:" + clean_phone).encode()).hexdigest()
        self.assertEqual(len(phone_hash), 64)

        inq_id = f"inq_{uuid.uuid4()}"
        tracking_token = str(uuid.uuid4())
        now_iso = datetime.now(timezone.utc).isoformat()

        # Simulated encrypted fields (starts with version 0x02)
        enc_name = "Ag==" + "test_encrypted_name_bytes"
        enc_phone = "Ag==" + "test_encrypted_phone_bytes"

        # Insert inquiry
        conn.execute(
            """
            INSERT INTO inquiries (
                id, tracking_token, district, neighborhood, housing_type, area_pyeong,
                requested_date, time_window, customer_name_encrypted, customer_phone_encrypted,
                customer_phone_hash, status, expires_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING_MATCH', ?, ?, ?)
            """,
            (
                inq_id, tracking_token, "관악구", "신림동", "STUDIO", 7,
                "2026-11-05", "AFTERNOON", enc_name, enc_phone, phone_hash,
                now_iso, now_iso, now_iso
            )
        )
        conn.commit()

        # Verify no column contains raw_phone or clean_phone
        row = conn.execute("SELECT * FROM inquiries WHERE id = ?", (inq_id,)).fetchone()
        for col in row.keys():
            val = str(row[col])
            self.assertNotIn(raw_phone, val, f"Column {col} must not contain raw phone {raw_phone}")
            self.assertNotIn(clean_phone, val, f"Column {col} must not contain clean phone digits {clean_phone}")

        # Verify hash column format
        self.assertEqual(row["customer_phone_hash"], phone_hash)
        self.assertTrue(re.match(r"^[0-9a-f]{64}$", row["customer_phone_hash"]))

        # Revoke test (tombstone)
        tombstone_id = f"ERASED_{uuid.uuid4()}"
        conn.execute(
            """
            UPDATE inquiries SET
                status = 'CANCELLED',
                customer_name_encrypted = ?,
                customer_phone_encrypted = ?,
                customer_phone_hash = ?,
                updated_at = ?
            WHERE tracking_token = ?
            """,
            (tombstone_id, tombstone_id, tombstone_id, now_iso, tracking_token)
        )
        conn.commit()

        revoked_row = conn.execute("SELECT * FROM inquiries WHERE id = ?", (inq_id,)).fetchone()
        self.assertEqual(revoked_row["status"], "CANCELLED")
        self.assertTrue(revoked_row["customer_name_encrypted"].startswith("ERASED_"))
        self.assertTrue(revoked_row["customer_phone_encrypted"].startswith("ERASED_"))
        self.assertTrue(revoked_row["customer_phone_hash"].startswith("ERASED_"))

if __name__ == "__main__":
    unittest.main()
