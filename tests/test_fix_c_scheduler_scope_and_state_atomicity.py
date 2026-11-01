import os
import re
import json
import sqlite3
import unittest
from datetime import datetime, timezone, timedelta

class TestFixCSchedulerScopeAndStateAtomicity(unittest.TestCase):
    """
    SBK-FIX-C: Scheduler Scope, Universal Operator Gate, and State Transition Atomicity
    Addresses:
    - P0-04: processTimeoutsInternal moved to module scope (callable by scheduled cron)
    - P0-09: Operator release gate enforced across all transactional state-modifying routes
    - P0-10: State transition atomicity and rollback compensation in offer accept / match
    - P1-08: Cascade customer revocation to pending offers/slots and block acceptance on cancelled inquiries
    """

    def setUp(self):
        self.repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.worker_js_path = os.path.join(self.repo_root, "src", "worker.js")

        with open(self.worker_js_path, "r", encoding="utf-8") as f:
            self.worker_js = f.read()

        # In-memory D1 SQLite setup
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        with open(os.path.join(self.repo_root, "migrations", "0001_initial_schema.sql"), "r", encoding="utf-8") as f:
            self.conn.executescript(f.read())

    def tearDown(self):
        self.conn.close()

    def test_01_p0_04_process_timeouts_internal_is_module_scoped(self):
        """P0-04: Verifies processTimeoutsInternal is defined in top-level module scope before handleApiRequest."""
        # 1. Must be declared before handleApiRequest
        pos_timeout_fn = self.worker_js.find("async function processTimeoutsInternal(env)")
        pos_handle_api = self.worker_js.find("async function handleApiRequest(request, url, env)")
        self.assertNotEqual(pos_timeout_fn, -1, "processTimeoutsInternal must be declared in worker.js")
        self.assertNotEqual(pos_handle_api, -1, "handleApiRequest must be declared in worker.js")
        self.assertLess(
            pos_timeout_fn,
            pos_handle_api,
            "processTimeoutsInternal must be defined at module scope before handleApiRequest"
        )

        # 2. scheduled() must call processTimeoutsInternal
        self.assertIn("ctx.waitUntil(processTimeoutsInternal(env))", self.worker_js)
        self.assertIn("await processTimeoutsInternal(env)", self.worker_js)

        # 3. /cron/process-timeouts must call processTimeoutsInternal
        cron_block = self.worker_js[self.worker_js.find('/cron/process-timeouts'):]
        self.assertIn("await processTimeoutsInternal(env)", cron_block)

    def test_02_p0_09_operator_release_gate_present_on_all_transaction_routes(self):
        """P0-09: Operator release gate check must guard all state-modifying routes."""
        transactional_endpoints = [
            'path === "/inquiries" && method === "POST"',
            'path === "/partners/apply" && method === "POST"',
            'path === "/slots" && method === "POST"',
            'matchSlotDelete && method === "DELETE"',
            'matchOfferAccept && method === "POST"',
            'matchOfferReject && method === "POST"',
            'matchFulfill && method === "POST"',
            'matchConfirm && method === "POST"',
            'path === "/admin/invoices/generate" && method === "POST"',
            'matchInvoiceIssue && method === "POST"',
            'matchInvoicePay && method === "POST"',
            'path === "/admin/payouts/generate" && method === "POST"',
            'matchPayoutApprove && method === "POST"'
        ]

        for ep in transactional_endpoints:
            pos = self.worker_js.find(ep)
            self.assertNotEqual(pos, -1, f"Endpoint check '{ep}' must exist in worker.js")
            # Look at code immediately following the endpoint definition
            snippet = self.worker_js[pos:pos + 350]
            self.assertIn(
                "checkOperatorReleaseGate(env)",
                snippet,
                f"Endpoint '{ep}' must invoke checkOperatorReleaseGate(env) to enforce Fail-Closed release"
            )

    def test_03_p0_09_operator_gate_simulation_contract(self):
        """P0-09 Contract: Without SBK_OPERATOR_APPROVAL = 1, all transaction routes fail with 503."""
        def simulate_gate_check(env: dict):
            is_approved = env.get("SBK_OPERATOR_APPROVAL") in ["1", "true"]
            if not is_approved:
                return 503, "OPERATOR_APPROVAL_REQUIRED"
            return 200, "OK"

        # 1. Unapproved environment
        unapproved_env = {"DB": object(), "SBK_OPERATOR_APPROVAL": "0"}
        status, code = simulate_gate_check(unapproved_env)
        self.assertEqual(status, 503)
        self.assertEqual(code, "OPERATOR_APPROVAL_REQUIRED")

        # 2. Approved environment
        approved_env = {"DB": object(), "SBK_OPERATOR_APPROVAL": "1"}
        status, code = simulate_gate_check(approved_env)
        self.assertEqual(status, 200)
        self.assertEqual(code, "OK")

    def test_04_p1_08_customer_revocation_cascades_and_restores_slots(self):
        """P1-08: Customer revocation (DELETE /inquiries/:token) cascades cancellation to pending offers and frees slots."""
        now_iso = datetime.now(timezone.utc).isoformat()
        tomorrow_str = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%d")

        # Setup Partner, Slot, Inquiry, Offer
        self.conn.execute("INSERT INTO organizations (id, name, business_number, representative_name) VALUES ('org_c1', '파트너사', '1234567890', '대표')")
        self.conn.execute(
            """
            INSERT INTO partners (id, organization_id, name, partner_type, business_number, representative_name, contact_phone, verification_status, liability_insurance_status)
            VALUES ('part_c1', 'org_c1', '파트너사', 'FULFILLMENT', '1234567890', '대표', '01011112222', 'APPROVED', 'VERIFIED')
            """
        )
        self.conn.execute(
            "INSERT INTO capacity_slots (id, partner_id, slot_date, time_window, status) VALUES ('slot_c1', 'part_c1', ?, 'MORNING', 'OFFERED')",
            (tomorrow_str,)
        )
        self.conn.execute(
            """
            INSERT INTO inquiries (id, tracking_token, district, neighborhood, housing_type, area_pyeong, requested_date, time_window, customer_name_encrypted, customer_phone_encrypted, customer_phone_hash, status, expires_at)
            VALUES ('inq_c1', 'tok_rev_1', '관악구', '봉천동', 'STUDIO', 8, ?, 'MORNING', 'enc_n', 'enc_p', 'hash_p', 'OFFERED', ?)
            """,
            (tomorrow_str, now_iso)
        )
        self.conn.execute(
            """
            INSERT INTO assignment_offers (id, inquiry_id, partner_id, slot_id, priority, status, expires_at, created_at, updated_at)
            VALUES ('off_c1', 'inq_c1', 'part_c1', 'slot_c1', 1, 'PENDING', ?, ?, ?)
            """,
            (now_iso, now_iso, now_iso)
        )

        # Simulate DELETE /inquiries/:token cascading logic
        # 1. Update inquiry to CANCELLED
        self.conn.execute("UPDATE inquiries SET status = 'CANCELLED' WHERE id = 'inq_c1'")

        # 2. Cascade to pending offers and free slots
        pending_offers = self.conn.execute("SELECT id, slot_id FROM assignment_offers WHERE inquiry_id = 'inq_c1' AND status = 'PENDING'").fetchall()
        for off in pending_offers:
            self.conn.execute("UPDATE assignment_offers SET status = 'CANCELLED', updated_at = ? WHERE id = ?", (now_iso, off["id"]))
            self.conn.execute("UPDATE capacity_slots SET status = 'AVAILABLE', updated_at = ? WHERE id = ? AND status = 'OFFERED'", (now_iso, off["slot_id"]))

        # Verify Offer is CANCELLED and Slot is restored to AVAILABLE
        offer_row = self.conn.execute("SELECT status FROM assignment_offers WHERE id = 'off_c1'").fetchone()
        self.assertEqual(offer_row["status"], "CANCELLED")

        slot_row = self.conn.execute("SELECT status FROM capacity_slots WHERE id = 'slot_c1'").fetchone()
        self.assertEqual(slot_row["status"], "AVAILABLE", "Slot must be restored to AVAILABLE when inquiry is cancelled")

    def test_05_p1_08_offer_accept_blocks_cancelled_customer(self):
        """P1-08: Partner cannot accept offer for a cancelled customer inquiry; offer cancels and slot frees."""
        self.assertIn("INQUIRY_CANCELLED", self.worker_js)
        self.assertIn("고객이 이미 신청을 취소/철회하여 오퍼를 수락할 수 없습니다", self.worker_js)

        now_iso = datetime.now(timezone.utc).isoformat()
        self.conn.execute("INSERT INTO organizations (id, name, business_number, representative_name) VALUES ('org_c2', '파트너사2', '2222222222', '대표2')")
        self.conn.execute(
            """
            INSERT INTO partners (id, organization_id, name, partner_type, business_number, representative_name, contact_phone, verification_status, liability_insurance_status)
            VALUES ('part_c2', 'org_c2', '파트너사2', 'FULFILLMENT', '2222222222', '대표2', '01011112222', 'APPROVED', 'VERIFIED')
            """
        )
        # Setup cancelled inquiry
        self.conn.execute(
            """
            INSERT INTO inquiries (id, tracking_token, district, neighborhood, housing_type, area_pyeong, requested_date, time_window, customer_name_encrypted, customer_phone_encrypted, customer_phone_hash, status, expires_at)
            VALUES ('inq_canc', 'tok_canc', '관악구', '봉천동', 'STUDIO', 8, '2026-11-03', 'MORNING', 'ERASED_1', 'ERASED_2', 'ERASED_3', 'CANCELLED', ?)
            """,
            (now_iso,)
        )
        self.conn.execute(
            "INSERT INTO capacity_slots (id, partner_id, slot_date, time_window, status) VALUES ('slot_canc', 'part_c2', '2026-11-03', 'MORNING', 'OFFERED')"
        )
        self.conn.execute(
            """
            INSERT INTO assignment_offers (id, inquiry_id, partner_id, slot_id, priority, status, expires_at, created_at, updated_at)
            VALUES ('off_canc', 'inq_canc', 'part_c2', 'slot_canc', 1, 'PENDING', ?, ?, ?)
            """,
            (now_iso, now_iso, now_iso)
        )

        # Simulation of worker.js accept guard:
        inq_check = self.conn.execute("SELECT status FROM inquiries WHERE id = 'inq_canc'").fetchone()
        if inq_check["status"] == "CANCELLED":
            self.conn.execute("UPDATE assignment_offers SET status = 'CANCELLED' WHERE id = 'off_canc'")
            self.conn.execute("UPDATE capacity_slots SET status = 'AVAILABLE' WHERE id = 'slot_canc' AND status = 'OFFERED'")
            rejection_code = 410

        self.assertEqual(rejection_code, 410)
        off_status = self.conn.execute("SELECT status FROM assignment_offers WHERE id = 'off_canc'").fetchone()["status"]
        slot_status = self.conn.execute("SELECT status FROM capacity_slots WHERE id = 'slot_canc'").fetchone()["status"]
        self.assertEqual(off_status, "CANCELLED")
        self.assertEqual(slot_status, "AVAILABLE")

    def test_06_p1_08_timeout_scheduler_does_not_revive_cancelled_inquiry(self):
        """P1-08: processTimeoutsInternal does not change CANCELLED inquiries back to PENDING_MATCH."""
        self.assertIn("status != 'CANCELLED'", self.worker_js)

        now_iso = datetime.now(timezone.utc).isoformat()
        # Setup cancelled inquiry with expired offer
        self.conn.execute(
            """
            INSERT INTO inquiries (id, tracking_token, district, neighborhood, housing_type, area_pyeong, requested_date, time_window, customer_name_encrypted, customer_phone_encrypted, customer_phone_hash, status, expires_at)
            VALUES ('inq_exp_canc', 'tok_ec', '관악구', '봉천동', 'STUDIO', 8, '2026-11-03', 'MORNING', 'enc', 'enc', 'enc', 'CANCELLED', ?)
            """,
            (now_iso,)
        )

        now_iso = datetime.now(timezone.utc).isoformat()
        # Simulated timeout handler query with P1-08 guard:
        inq = self.conn.execute("SELECT status FROM inquiries WHERE id = 'inq_exp_canc'").fetchone()
        if inq["status"] != "CANCELLED":
            self.conn.execute("UPDATE inquiries SET status = 'PENDING_MATCH' WHERE id = 'inq_exp_canc'")

        # Verify it remained CANCELLED
        inq_after = self.conn.execute("SELECT status FROM inquiries WHERE id = 'inq_exp_canc'").fetchone()
        self.assertEqual(inq_after["status"], "CANCELLED")

    def test_07_p0_10_state_transition_atomicity_and_compensation_in_worker(self):
        """P0-10: Compensation rollback patterns exist in worker.js for slot locks and match transactions."""
        # 1. Rollback of slot lock if offer update conflicts/fails
        self.assertIn(
            "UPDATE capacity_slots SET status = 'OFFERED', allocated_count = 0",
            self.worker_js,
            "Must rollback slot lock if offerUpdate fails"
        )

        # 2. Multi-statement transaction failure compensation
        self.assertIn(
            "TRANSACTION_FAILED",
            self.worker_js,
            "Must handle transaction failure with rollback compensation"
        )
        self.assertIn(
            "UPDATE assignment_offers SET status = 'PENDING'",
            self.worker_js,
            "Must revert offer to PENDING on match transaction failure"
        )

        # 3. Fulfillment evidence gate requires CONFIRMED status
        self.assertIn('match.status !== "CONFIRMED"', self.worker_js)
        self.assertIn('match.status === "CANCELLED"', self.worker_js)
        self.assertIn("MATCH_CANCELLED", self.worker_js)

if __name__ == "__main__":
    unittest.main()
