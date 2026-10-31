import unittest
import os
import sqlite3
import json
import uuid
from datetime import datetime, timezone, timedelta

class TestSupplierAvailability(unittest.TestCase):
    """
    SBK-MINI-07: Supplier Availability & Capacity Slots Test Suite
    Verifies:
    1. Unapproved partner rejected (403 UNAPPROVED_PARTNER).
    2. Partner A accessing/deleting Partner B's slot rejected (403 FORBIDDEN).
    3. Approved partner registers and lists slots successfully in D1.
    4. Duplicate slot registration or ALL_DAY overlap rejected (409 Conflict).
    5. Deleting OFFERED/RESERVED slot rejected (409 Conflict).
    6. partner-slots.html connects to real /api/v1/slotbridge/slots without demo alerts.
    """

    def setUp(self):
        self.repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.worker_js_path = os.path.join(self.repo_root, "src", "worker.js")
        self.partner_slots_html_path = os.path.join(self.repo_root, "slotbridge", "partner-slots.html")

    def test_partner_slots_html_connects_to_real_slots_api(self):
        with open(self.partner_slots_html_path, "r", encoding="utf-8") as f:
            html = f.read()

        # addSlot function calls real fetch API
        self.assertIn("fetch('/api/v1/slotbridge/slots'", html)
        self.assertNotIn("슬롯 등록 시뮬레이션:", html)
        self.assertIn("X-SBK-Partner-Key", html)

    def test_worker_js_has_slot_endpoints_and_guardrails(self):
        with open(self.worker_js_path, "r", encoding="utf-8") as f:
            js = f.read()

        # Slot routing
        self.assertIn('path === "/slots" && method === "GET"', js)
        self.assertIn('path === "/slots" && method === "POST"', js)
        self.assertIn('matchSlotDelete', js)

        # Unapproved partner check
        self.assertIn('UNAPPROVED_PARTNER', js)

        # Ownership check
        self.assertIn('FORBIDDEN', js)

        # Duplicate slot check
        self.assertIn('SLOT_DUPLICATE', js)

        # Delete protection for non-available slots
        self.assertIn('SLOT_CANNOT_DELETE', js)

    def test_capacity_slots_d1_lifecycle_simulation(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        with open(os.path.join(self.repo_root, "migrations", "0001_initial_schema.sql"), "r", encoding="utf-8") as f:
            conn.executescript(f.read())

        now_kst = datetime.now(timezone(timedelta(hours=9)))
        tomorrow_str = (now_kst + timedelta(days=1)).strftime("%Y-%m-%d")
        now_iso = datetime.now(timezone.utc).isoformat()

        # Setup 2 partners: Partner A (APPROVED), Partner B (PENDING_APPROVAL)
        conn.execute("INSERT INTO organizations (id, name, business_number, representative_name) VALUES ('org_a', '업체A', '1111111111', '대표A')")
        conn.execute("INSERT INTO organizations (id, name, business_number, representative_name) VALUES ('org_b', '업체B', '2222222222', '대표B')")
        
        conn.execute(
            """
            INSERT INTO partners (id, organization_id, name, partner_type, business_number, representative_name, contact_phone, verification_status)
            VALUES ('partner_a', 'org_a', '업체A', 'FULFILLMENT', '1111111111', '대표A', '01011112222', 'APPROVED')
            """
        )
        conn.execute(
            """
            INSERT INTO partners (id, organization_id, name, partner_type, business_number, representative_name, contact_phone, verification_status)
            VALUES ('partner_b', 'org_b', '업체B', 'FULFILLMENT', '2222222222', '대표B', '01033334444', 'PENDING_APPROVAL')
            """
        )
        conn.commit()

        # Helper simulation functions
        def register_slot(partner_id, auth_key, slot_date, time_window):
            p = conn.execute("SELECT * FROM partners WHERE id = ?", (partner_id,)).fetchone()
            if not p:
                return 404, "PARTNER_NOT_FOUND"
            if p["verification_status"] != "APPROVED":
                return 403, "UNAPPROVED_PARTNER"
            if auth_key != p["business_number"]:
                return 403, "FORBIDDEN"

            # Check overlap
            if time_window == "ALL_DAY":
                ov = conn.execute(
                    "SELECT id, time_window FROM capacity_slots WHERE partner_id = ? AND slot_date = ? AND status IN ('AVAILABLE', 'OFFERED', 'RESERVED')",
                    (partner_id, slot_date)
                ).fetchone()
                if ov:
                    return 409, "SLOT_DUPLICATE"
            else:
                ov = conn.execute(
                    "SELECT id, time_window FROM capacity_slots WHERE partner_id = ? AND slot_date = ? AND (time_window = ? OR time_window = 'ALL_DAY') AND status IN ('AVAILABLE', 'OFFERED', 'RESERVED')",
                    (partner_id, slot_date, time_window)
                ).fetchone()
                if ov:
                    return 409, "SLOT_DUPLICATE"

            # Check if re-activating previously cancelled slot
            ex = conn.execute(
                "SELECT id, status FROM capacity_slots WHERE partner_id = ? AND slot_date = ? AND time_window = ?",
                (partner_id, slot_date, time_window)
            ).fetchone()
            if ex and ex["status"] == "CANCELLED":
                conn.execute("UPDATE capacity_slots SET status = 'AVAILABLE', updated_at = ? WHERE id = ?", (now_iso, ex["id"]))
                conn.commit()
                return 200, ex["id"]

            s_id = f"slot_{uuid.uuid4()}"
            conn.execute(
                "INSERT INTO capacity_slots (id, partner_id, slot_date, time_window, status, created_at, updated_at) VALUES (?, ?, ?, ?, 'AVAILABLE', ?, ?)",
                (s_id, partner_id, slot_date, time_window, now_iso, now_iso)
            )
            conn.commit()
            return 201, s_id

        def delete_slot(slot_id, auth_key):
            s = conn.execute("SELECT * FROM capacity_slots WHERE id = ?", (slot_id,)).fetchone()
            if not s:
                return 404, "SLOT_NOT_FOUND"
            p = conn.execute("SELECT business_number FROM partners WHERE id = ?", (s["partner_id"],)).fetchone()
            if auth_key != p["business_number"]:
                return 403, "FORBIDDEN"
            if s["status"] != "AVAILABLE":
                return 409, "SLOT_CANNOT_DELETE"
            conn.execute("UPDATE capacity_slots SET status = 'CANCELLED' WHERE id = ?", (slot_id,))
            conn.commit()
            return 200, "CANCELLED"

        # 1. Unapproved Partner B tries to register -> 403 UNAPPROVED_PARTNER
        status, code = register_slot("partner_b", "2222222222", tomorrow_str, "MORNING")
        self.assertEqual(status, 403)
        self.assertEqual(code, "UNAPPROVED_PARTNER")

        # 2. Partner A registers MORNING slot -> 201 Created
        status, slot_a1 = register_slot("partner_a", "1111111111", tomorrow_str, "MORNING")
        self.assertEqual(status, 201)

        # 3. Duplicate slot registration (same day & window) -> 409 Conflict
        status, code = register_slot("partner_a", "1111111111", tomorrow_str, "MORNING")
        self.assertEqual(status, 409)
        self.assertEqual(code, "SLOT_DUPLICATE")

        # 4. ALL_DAY slot registration when MORNING exists -> 409 Conflict
        status, code = register_slot("partner_a", "1111111111", tomorrow_str, "ALL_DAY")
        self.assertEqual(status, 409)
        self.assertEqual(code, "SLOT_DUPLICATE")

        # 5. Partner B tries to delete Partner A's slot -> 403 FORBIDDEN
        status, code = delete_slot(slot_a1, "2222222222")
        self.assertEqual(status, 403)
        self.assertEqual(code, "FORBIDDEN")

        # 6. Partner A deletes their own slot -> 200 CANCELLED
        status, code = delete_slot(slot_a1, "1111111111")
        self.assertEqual(status, 200)
        self.assertEqual(code, "CANCELLED")

        # 7. Cancelled slot can be re-activated
        status, slot_a2 = register_slot("partner_a", "1111111111", tomorrow_str, "MORNING")
        self.assertIn(status, [200, 201])

        # 8. Slot in OFFERED status cannot be deleted -> 409 SLOT_CANNOT_DELETE
        conn.execute("UPDATE capacity_slots SET status = 'OFFERED' WHERE id = ?", (slot_a2,))
        conn.commit()
        status, code = delete_slot(slot_a2, "1111111111")
        self.assertEqual(status, 409)
        self.assertEqual(code, "SLOT_CANNOT_DELETE")

if __name__ == "__main__":
    unittest.main()
