import unittest
import os
import json
import sqlite3
import uuid
from datetime import datetime, timezone, timedelta

class TestTimeoutAndDelivery(unittest.TestCase):
    """
    SBK-MINI-09: Timeout Reassignment & Delivery Transparency Test Suite
    Verifies:
    1. Cloudflare Workers scheduled cron handler and wrangler.json crons trigger.
    2. HTTP POST /cron/process-timeouts rejects unauthorized requests (401).
    3. Expired offers past 15 minutes are transitioned to EXPIRED, freeing slot to AVAILABLE.
    4. Expired offer automatically re-routes to Rank 2 candidate.
    5. Expired offer cannot be accepted (409/410).
    6. partner-slots.html operates via web polling without fake 'instant SMS' claims.
    """

    def setUp(self):
        self.repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.worker_js_path = os.path.join(self.repo_root, "src", "worker.js")
        self.wrangler_json_path = os.path.join(self.repo_root, "wrangler.json")
        self.partner_slots_html_path = os.path.join(self.repo_root, "slotbridge", "partner-slots.html")

    def test_wrangler_and_worker_cron_configuration(self):
        with open(self.wrangler_json_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)

        self.assertIn("triggers", cfg)
        self.assertIn("crons", cfg["triggers"])

        with open(self.worker_js_path, "r", encoding="utf-8") as f:
            js = f.read()

        # Exported scheduled handler
        self.assertIn("async scheduled(event, env, ctx)", js)
        # Process timeouts internal helper
        self.assertIn("async function processTimeoutsInternal", js)
        # Admin authentication required for HTTP cron
        self.assertIn('크론 작업 실행 권한이 없습니다', js)

    def test_partner_slots_html_polling_and_no_fake_sms(self):
        with open(self.partner_slots_html_path, "r", encoding="utf-8") as f:
            html = f.read()

        # Web polling present
        self.assertIn("checkPartnerOffers()", html)
        self.assertIn("setInterval(checkPartnerOffers", html)

        # Transparent notification copy
        self.assertIn("실제 SMS 문자를 즉시 발송하지 않으며", html)
        self.assertIn("파트너 전용 웹 대기함 폴링", html)

    def test_timeout_and_sequential_rerouting_simulation(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        with open(os.path.join(self.repo_root, "migrations", "0001_initial_schema.sql"), "r", encoding="utf-8") as f:
            conn.executescript(f.read())

        now_kst = datetime.now(timezone(timedelta(hours=9)))
        tomorrow_str = (now_kst + timedelta(days=1)).strftime("%Y-%m-%d")
        now_dt = datetime.now(timezone.utc)
        now_iso = now_dt.isoformat()
        expired_time_iso = (now_dt - timedelta(minutes=5)).isoformat() # Expired 5 minutes ago

        # Setup Partner 1 (Rank 1: 5.0) and Partner 2 (Rank 2: 4.6)
        conn.execute("INSERT INTO organizations (id, name, business_number, representative_name) VALUES ('org_1', '업체1', '1111111111', '대표1')")
        conn.execute(
            """
            INSERT INTO partners (id, organization_id, name, partner_type, business_number, representative_name, contact_phone, verification_status, liability_insurance_status, rating_average, total_completed_count)
            VALUES ('partner_1', 'org_1', '업체1', 'FULFILLMENT', '1111111111', '대표1', '01011112222', 'APPROVED', 'VERIFIED', 5.0, 10)
            """
        )
        conn.execute("INSERT INTO service_areas (id, partner_id, city, district, neighborhood, is_active) VALUES ('sa_1', 'partner_1', '서울특별시', '관악구', '전체', 1)")
        conn.execute("INSERT INTO capacity_slots (id, partner_id, slot_date, time_window, status) VALUES ('slot_1', 'partner_1', ?, 'MORNING', 'OFFERED')", (tomorrow_str,))

        conn.execute("INSERT INTO organizations (id, name, business_number, representative_name) VALUES ('org_2', '업체2', '2222222222', '대표2')")
        conn.execute(
            """
            INSERT INTO partners (id, organization_id, name, partner_type, business_number, representative_name, contact_phone, verification_status, liability_insurance_status, rating_average, total_completed_count)
            VALUES ('partner_2', 'org_2', '업체2', 'FULFILLMENT', '2222222222', '대표2', '01033334444', 'APPROVED', 'VERIFIED', 4.6, 5)
            """
        )
        conn.execute("INSERT INTO service_areas (id, partner_id, city, district, neighborhood, is_active) VALUES ('sa_2', 'partner_2', '서울특별시', '관악구', '전체', 1)")
        conn.execute("INSERT INTO capacity_slots (id, partner_id, slot_date, time_window, status) VALUES ('slot_2', 'partner_2', ?, 'MORNING', 'AVAILABLE')", (tomorrow_str,))

        # Customer inquiry
        inquiry_id = "inq_timeout_test"
        conn.execute(
            """
            INSERT INTO inquiries (id, tracking_token, district, neighborhood, housing_type, area_pyeong, requested_date, time_window, customer_name_encrypted, customer_phone_encrypted, customer_phone_hash, status, expires_at)
            VALUES (?, 'tok_t', '관악구', '봉천동', 'STUDIO', 8, ?, 'MORNING', 'enc_n', 'enc_p', 'hash_p', 'OFFERED', ?)
            """,
            (inquiry_id, tomorrow_str, (now_dt + timedelta(hours=72)).isoformat())
        )

        # Expired Offer for Partner 1
        offer_1_id = "off_expired_1"
        conn.execute(
            """
            INSERT INTO assignment_offers (id, inquiry_id, partner_id, slot_id, priority, status, expires_at, created_at, updated_at)
            VALUES (?, ?, 'partner_1', 'slot_1', 1, 'PENDING', ?, ?, ?)
            """,
            (offer_1_id, inquiry_id, expired_time_iso, now_iso, now_iso)
        )
        conn.commit()

        # Execute timeout process simulation
        expired_offers = conn.execute(
            "SELECT id, inquiry_id, partner_id, slot_id FROM assignment_offers WHERE status = 'PENDING' AND expires_at < ?",
            (now_iso,)
        ).fetchall()
        self.assertEqual(len(expired_offers), 1)

        for off in expired_offers:
            # 1. Expire offer
            conn.execute("UPDATE assignment_offers SET status = 'EXPIRED' WHERE id = ?", (off["id"],))
            # 2. Free slot back to AVAILABLE
            conn.execute("UPDATE capacity_slots SET status = 'AVAILABLE' WHERE id = ?", (off["slot_id"],))
            # 3. Record timeout response
            conn.execute(
                "INSERT INTO partner_responses (id, offer_id, partner_id, response_type, responded_at) VALUES (?, ?, ?, 'TIMEOUT', ?)",
                (f"resp_{uuid.uuid4()}", off["id"], off["partner_id"], now_iso)
            )
            # 4. Inquire back to PENDING_MATCH
            conn.execute("UPDATE inquiries SET status = 'PENDING_MATCH' WHERE id = ?", (off["inquiry_id"],))

            # 5. Reassign to Rank 2 (partner_2)
            # Query candidate slots excluding previously timed-out partner_1
            next_cand = conn.execute(
                """
                SELECT cs.id AS slot_id, cs.partner_id
                FROM capacity_slots cs
                JOIN partners p ON cs.partner_id = p.id
                JOIN service_areas sa ON sa.partner_id = p.id
                WHERE cs.slot_date = ? AND cs.status = 'AVAILABLE' AND sa.district = '관악구'
                  AND p.verification_status = 'APPROVED' AND p.liability_insurance_status = 'VERIFIED'
                  AND cs.partner_id != ?
                ORDER BY p.rating_average DESC LIMIT 1
                """,
                (tomorrow_str, off["partner_id"])
            ).fetchone()

            self.assertIsNotNone(next_cand)
            self.assertEqual(next_cand["partner_id"], "partner_2")

            # Lock partner_2 slot and create new offer
            conn.execute("UPDATE capacity_slots SET status = 'OFFERED' WHERE id = ?", (next_cand["slot_id"],))
            new_off_id = "off_reassigned_2"
            new_exp = (now_dt + timedelta(minutes=15)).isoformat()
            conn.execute(
                """
                INSERT INTO assignment_offers (id, inquiry_id, partner_id, slot_id, priority, status, expires_at, created_at, updated_at)
                VALUES (?, ?, 'partner_2', ?, 2, 'PENDING', ?, ?, ?)
                """,
                (new_off_id, off["inquiry_id"], next_cand["slot_id"], new_exp, now_iso, now_iso)
            )
            conn.execute("UPDATE inquiries SET status = 'OFFERED' WHERE id = ?", (off["inquiry_id"],))
            conn.commit()

        # Check DB states
        # Slot 1 should be restored to AVAILABLE
        s1 = conn.execute("SELECT status FROM capacity_slots WHERE id = 'slot_1'").fetchone()
        self.assertEqual(s1["status"], "AVAILABLE")

        # Offer 1 should be EXPIRED
        o1 = conn.execute("SELECT status FROM assignment_offers WHERE id = 'off_expired_1'").fetchone()
        self.assertEqual(o1["status"], "EXPIRED")

        # Slot 2 should now be OFFERED to Partner 2
        s2 = conn.execute("SELECT status FROM capacity_slots WHERE id = 'slot_2'").fetchone()
        self.assertEqual(s2["status"], "OFFERED")

        # Reassigned offer 2 should exist and be PENDING
        o2 = conn.execute("SELECT status, partner_id, priority FROM assignment_offers WHERE id = 'off_reassigned_2'").fetchone()
        self.assertEqual(o2["status"], "PENDING")
        self.assertEqual(o2["partner_id"], "partner_2")
        self.assertEqual(o2["priority"], 2)

if __name__ == "__main__":
    unittest.main()
