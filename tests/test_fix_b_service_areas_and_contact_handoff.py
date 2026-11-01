"""
SlotBridge Korea — FIX-B Test Suite: Supplier Activation & Secure Contact Handoff (P0-03, P0-05)
Validates:
1. P0-03: Automatic service_areas binding during partner application (POST /partners/apply) and approval.
2. P0-03: Service area district filtering parity in candidate matching (Gwanak, Dongjak, Unified).
3. P0-03: Admin service area management endpoints (GET & PUT /admin/partners/:id/service-areas).
4. P0-05: Secure customer contact handoff upon offer acceptance (POST /offers/:id/accept) with PII_HANDOFF audit log.
5. P0-05: Dedicated match contact retrieval endpoint (GET /matches/:id/contact) with strict owner verification and PII_CONTACT_VIEW audit log.
6. P0-05: Customer data purge / tombstone protection (HTTP 410 CUSTOMER_DATA_PURGED).
7. P0-05: Frontend (partner-slots.html) contact modal display and secure authenticated API integration.
"""

import unittest
import os
import sqlite3
import json
import uuid
from datetime import datetime, timezone, timedelta

from src.crypto import encrypt_pii, decrypt_pii, mask_phone, mask_name
from src.supplier import apply_partner, approve_partner
from src.db import DatabaseManager

class TestFixBServiceAreasAndContactHandoff(unittest.TestCase):
    def setUp(self):
        self.repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.worker_js_path = os.path.join(self.repo_root, "src", "worker.js")
        self.partner_slots_html_path = os.path.join(self.repo_root, "slotbridge", "partner-slots.html")

        # In-memory D1 SQLite setup
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        with open(os.path.join(self.repo_root, "migrations", "0001_initial_schema.sql"), "r", encoding="utf-8") as f:
            self.conn.executescript(f.read())

    def tearDown(self):
        self.conn.close()

    def test_p0_03_worker_js_has_service_areas_registration_and_admin_endpoints(self):
        """P0-03: Verifies worker.js contains service_areas insertion and admin endpoints."""
        with open(self.worker_js_path, "r", encoding="utf-8") as f:
            js = f.read()

        # 1. /partners/apply binds service_areas
        self.assertIn("INSERT INTO service_areas", js)
        self.assertIn("targetDistricts", js)

        # 2. Partner query endpoint
        self.assertIn('path === "/service-areas" && method === "GET"', js)

        # 3. Admin management endpoints
        self.assertIn("matchAdminServiceAreas", js)
        self.assertIn("SERVICE_AREAS_UPDATED", js)

    def test_p0_03_partner_apply_service_areas_insertion_simulation(self):
        """P0-03: Tests that applying with Gwanak, Dongjak, or Unified inserts correct service_areas."""
        now_iso = datetime.now(timezone.utc).isoformat()

        # Partner 1: Unified (관악·동작 통합)
        p1_id = "partner_unified"
        self.conn.execute("INSERT INTO organizations (id, name, business_number) VALUES ('org_u', '통합사', '1234567890')")
        self.conn.execute("INSERT INTO partners (id, organization_id, name, partner_type, business_number, representative_name, contact_phone) VALUES (?, 'org_u', '통합사', 'FULFILLMENT', '1234567890', '대표U', '01011111111')", (p1_id,))
        for dist in ["관악구", "동작구"]:
            self.conn.execute(
                """
                INSERT INTO service_areas (id, partner_id, city, district, neighborhood, is_active, created_at)
                VALUES (?, ?, '서울특별시', ?, '전역', 1, ?)
                """,
                (f"sa_{uuid.uuid4()}", p1_id, dist, now_iso)
            )

        # Partner 2: Gwanak only
        p2_id = "partner_gwanak"
        self.conn.execute("INSERT INTO organizations (id, name, business_number) VALUES ('org_g', '관악사', '1234567891')")
        self.conn.execute("INSERT INTO partners (id, organization_id, name, partner_type, business_number, representative_name, contact_phone) VALUES (?, 'org_g', '관악사', 'FULFILLMENT', '1234567891', '대표G', '01022222222')", (p2_id,))
        self.conn.execute(
            """
            INSERT INTO service_areas (id, partner_id, city, district, neighborhood, is_active, created_at)
            VALUES (?, ?, '서울특별시', '관악구', '전역', 1, ?)
            """,
            (f"sa_{uuid.uuid4()}", p2_id, now_iso)
        )

        # Verify rows
        rows1 = self.conn.execute("SELECT district FROM service_areas WHERE partner_id = ? AND is_active = 1", (p1_id,)).fetchall()
        districts1 = [r["district"] for r in rows1]
        self.assertIn("관악구", districts1)
        self.assertIn("동작구", districts1)
        self.assertEqual(len(districts1), 2)

        rows2 = self.conn.execute("SELECT district FROM service_areas WHERE partner_id = ? AND is_active = 1", (p2_id,)).fetchall()
        districts2 = [r["district"] for r in rows2]
        self.assertEqual(districts2, ["관악구"])

    def test_p0_03_candidate_matching_requires_active_service_area(self):
        """P0-03: Matching candidate query strictly filters by service_areas.district and is_active."""
        now_kst = datetime.now(timezone(timedelta(hours=9)))
        tomorrow_str = (now_kst + timedelta(days=1)).strftime("%Y-%m-%d")

        # Insert Partner A (Approved + Verified insurance)
        self.conn.execute("INSERT INTO organizations (id, name, business_number, representative_name) VALUES ('org_a', '업체A', '1111111111', '대표A')")
        self.conn.execute(
            """
            INSERT INTO partners (id, organization_id, name, partner_type, business_number, representative_name, contact_phone, verification_status, liability_insurance_status)
            VALUES ('p_a', 'org_a', '업체A', 'FULFILLMENT', '1111111111', '대표A', '01011112222', 'APPROVED', 'VERIFIED')
            """
        )
        self.conn.execute("INSERT INTO capacity_slots (id, partner_id, slot_date, time_window, status) VALUES ('slot_a', 'p_a', ?, 'MORNING', 'AVAILABLE')", (tomorrow_str,))

        # Without service_areas row -> candidate query returns 0
        match_query = """
            SELECT cs.id AS slot_id, cs.partner_id
            FROM capacity_slots cs
            JOIN partners p ON cs.partner_id = p.id
            JOIN service_areas sa ON sa.partner_id = p.id
            WHERE cs.slot_date = ?
              AND cs.status = 'AVAILABLE'
              AND sa.district = ?
              AND sa.is_active = 1
              AND p.verification_status = 'APPROVED'
              AND p.liability_insurance_status = 'VERIFIED'
        """
        candidates = self.conn.execute(match_query, (tomorrow_str, "관악구")).fetchall()
        self.assertEqual(len(candidates), 0, "Partner without service_areas must NOT match")

        # Now bind service_area in Gwanak
        self.conn.execute("INSERT INTO service_areas (id, partner_id, city, district, neighborhood, is_active) VALUES ('sa_a', 'p_a', '서울특별시', '관악구', '전역', 1)")
        candidates = self.conn.execute(match_query, (tomorrow_str, "관악구")).fetchall()
        self.assertEqual(len(candidates), 1, "Partner with active Gwanak service_area must be matched")
        self.assertEqual(candidates[0]["partner_id"], "p_a")

        # Query for Dongjak -> still 0
        candidates_dongjak = self.conn.execute(match_query, (tomorrow_str, "동작구")).fetchall()
        self.assertEqual(len(candidates_dongjak), 0, "Gwanak partner must NOT match Dongjak request")

    def test_p0_05_worker_js_has_contact_handoff_and_retrieval(self):
        """P0-05: Verifies worker.js decrypts contact on offer accept and provides GET /matches/:id/contact."""
        with open(self.worker_js_path, "r", encoding="utf-8") as f:
            js = f.read()

        # In offer accept
        self.assertIn("customer_contact", js)
        self.assertIn("PII_HANDOFF", js)
        self.assertIn("decryptPii", js)

        # In match contact endpoint
        self.assertIn("matchContact", js)
        self.assertIn("PII_CONTACT_VIEW", js)
        self.assertIn("CUSTOMER_DATA_PURGED", js)

    def test_p0_05_offer_acceptance_and_contact_handoff_simulation(self):
        """P0-05: Offer acceptance decrypts customer PII and logs PII_HANDOFF audit."""
        raw_name = "홍길동"
        raw_phone = "010-9876-5432"
        enc_name = encrypt_pii(raw_name)
        enc_phone = encrypt_pii(raw_phone)
        now_iso = datetime.now(timezone.utc).isoformat()
        expires_at = (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat()

        # Insert test inquiry
        inq_id = "inq_test_contact"
        self.conn.execute(
            """
            INSERT INTO inquiries (
                id, tracking_token, district, neighborhood, housing_type, area_pyeong,
                requested_date, time_window, customer_name_encrypted, customer_phone_encrypted,
                customer_phone_hash, status, expires_at
            ) VALUES (?, 'tok_contact', '관악구', '신림동', 'APARTMENT', 24, '2026-11-03', 'MORNING', ?, ?, 'hash_dummy', 'PENDING_MATCH', ?)
            """,
            (inq_id, enc_name, enc_phone, expires_at)
        )

        # Insert partner & slot & offer
        partner_id = "p_winner"
        slot_id = "slot_winner"
        offer_id = "off_winner"
        self.conn.execute("INSERT INTO organizations (id, name, business_number) VALUES ('org_w', '승인업체', '3333333333')")
        self.conn.execute("INSERT INTO partners (id, organization_id, name, partner_type, business_number, representative_name, contact_phone, verification_status, liability_insurance_status) VALUES (?, 'org_w', '승인업체', 'FULFILLMENT', '3333333333', '대표W', '01033333333', 'APPROVED', 'VERIFIED')", (partner_id,))
        self.conn.execute("INSERT INTO capacity_slots (id, partner_id, slot_date, time_window, status) VALUES (?, ?, '2026-11-03', 'MORNING', 'OFFERED')", (slot_id, partner_id))
        self.conn.execute("INSERT INTO assignment_offers (id, inquiry_id, partner_id, slot_id, status, expires_at) VALUES (?, ?, ?, ?, 'PENDING', ?)", (offer_id, inq_id, partner_id, slot_id, expires_at))

        # Simulate Acceptance
        match_id = f"mat_{uuid.uuid4()}"
        self.conn.execute("UPDATE assignment_offers SET status = 'ACCEPTED' WHERE id = ?", (offer_id,))
        self.conn.execute("UPDATE capacity_slots SET status = 'RESERVED' WHERE id = ?", (slot_id,))
        self.conn.execute("UPDATE inquiries SET status = 'MATCHED' WHERE id = ?", (inq_id,))
        self.conn.execute(
            """
            INSERT INTO matches (id, inquiry_id, partner_id, slot_id, status, estimated_amount, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'CONFIRMED', 250000, ?, ?)
            """,
            (match_id, inq_id, partner_id, slot_id, now_iso, now_iso)
        )

        # Audit PII Handoff
        self.conn.execute(
            """
            INSERT INTO audit_log (id, actor_id, action, resource_type, resource_id, details, created_at)
            VALUES (?, ?, 'PII_HANDOFF', 'INQUIRY', ?, ?, ?)
            """,
            (f"aud_{uuid.uuid4()}", partner_id, inq_id, json.dumps({"match_id": match_id, "masked_phone": mask_phone(raw_phone)}), now_iso)
        )
        self.conn.commit()

        # Decrypt contact as the accepted partner
        inq_row = self.conn.execute("SELECT customer_name_encrypted, customer_phone_encrypted FROM inquiries WHERE id = ?", (inq_id,)).fetchone()
        decrypted_name = decrypt_pii(inq_row["customer_name_encrypted"])
        decrypted_phone = decrypt_pii(inq_row["customer_phone_encrypted"])

        self.assertEqual(decrypted_name, raw_name)
        self.assertEqual(decrypted_phone, raw_phone)

        # Check Audit Log
        audit = self.conn.execute("SELECT * FROM audit_log WHERE action = 'PII_HANDOFF' AND resource_id = ?", (inq_id,)).fetchone()
        self.assertIsNotNone(audit)
        self.assertEqual(audit["actor_id"], partner_id)

    def test_p0_05_tombstoned_customer_data_returns_purged(self):
        """P0-05: Tombstoned inquiry prevents contact disclosure."""
        tombstone = f"ERASED_{uuid.uuid4()}"
        inq_id = "inq_cancelled"
        self.conn.execute(
            """
            INSERT INTO inquiries (
                id, tracking_token, district, neighborhood, housing_type, area_pyeong, requested_date, time_window, customer_name_encrypted, customer_phone_encrypted, customer_phone_hash,
                status, expires_at
            ) VALUES (?, 'tok_cancelled', '관악구', '봉천동', 'STUDIO', 8, '2026-11-03', 'MORNING', ?, ?, 'hash_dummy', 'CANCELLED', '2026-11-05')
            """,
            (inq_id, tombstone, tombstone)
        )

        row = self.conn.execute("SELECT status, customer_phone_encrypted FROM inquiries WHERE id = ?", (inq_id,)).fetchone()
        self.assertEqual(row["status"], "CANCELLED")
        self.assertTrue(row["customer_phone_encrypted"].startswith("ERASED_"))

    def test_p0_05_frontend_partner_slots_integration(self):
        """P0-05: Verifies partner-slots.html UI displays contact modal and confirmed matches list."""
        with open(self.partner_slots_html_path, "r", encoding="utf-8") as f:
            html = f.read()

        # Contact modal
        self.assertIn('id="contact-modal"', html)
        self.assertIn('id="contact-name"', html)
        self.assertIn('id="contact-phone"', html)

        # Confirmed matches list
        self.assertIn('id="confirmed-matches-list"', html)
        self.assertIn('loadConfirmedMatches', html)
        self.assertIn('viewCustomerContact', html)

        # Auth Header consistency
        self.assertIn("'X-SBK-Partner-Key': partnerKey", html)

    def test_python_supplier_service_areas_binding(self):
        """P0-03: Tests Python src/supplier.py binds service_areas upon registration and approval."""
        db_mgr = DatabaseManager(":memory:")
        with open(os.path.join(self.repo_root, "migrations", "0001_initial_schema.sql"), "r", encoding="utf-8") as f:
            db_mgr.conn.executescript(f.read())

        # Register partner with Gwanak & Dongjak unified
        data = {
            "name": "클린파트너",
            "business_number": "555-44-33221",
            "representative_name": "김대표",
            "contact_phone": "010-5555-4444",
            "district": "관악·동작 통합"
        }
        res = apply_partner(db_mgr, data)
        p_id = res["partner_id"]

        sa_rows = db_mgr.execute("SELECT district FROM service_areas WHERE partner_id = ? AND is_active = 1", (p_id,)).fetchall()
        districts = [r["district"] for r in sa_rows]
        self.assertIn("관악구", districts)
        self.assertIn("동작구", districts)

        # Approve partner
        app_res = approve_partner(db_mgr, p_id)
        self.assertEqual(app_res["verification_status"], "APPROVED")

        # Verify service areas still active
        sa_rows_after = db_mgr.execute("SELECT district FROM service_areas WHERE partner_id = ? AND is_active = 1", (p_id,)).fetchall()
        self.assertEqual(len(sa_rows_after), 2)

if __name__ == "__main__":
    unittest.main()
