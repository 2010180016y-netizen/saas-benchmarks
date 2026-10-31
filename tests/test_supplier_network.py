"""
SlotBridge Korea — Supplier Network & Capacity Slot Test Suite (Step 05)
Validates partner onboarding, admin verification, slot registration guardrails,
Asia/Seoul timezone validation, and anti-double-booking concurrency.
"""

import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from src.db import DatabaseManager
from src.supplier import (
    apply_partner, approve_partner, suspend_partner,
    register_capacity_slot, delete_capacity_slot, SEOUL_TZ
)
from src.engine import create_inquiry, dispatch_1to1_offer, EngineError

class TestSupplierNetwork(unittest.TestCase):
    def setUp(self):
        self.db = DatabaseManager(":memory:")
        self.db.run_migration_up()
        self.db.seed_data()
        self.today_seoul = datetime.now(SEOUL_TZ).date()

    def tearDown(self):
        self.db.close()

    def test_01_partner_onboarding_pending_approval(self):
        """New partner application starts in PENDING_APPROVAL status."""
        res = apply_partner(self.db, {
            "name": "번개클린 관악점",
            "business_number": "123-45-67890",
            "representative_name": "강번개",
            "contact_phone": "010-8888-9999",
            "partner_type": "FULFILLMENT",
            "settlement_bank": "카카오뱅크",
            "settlement_account": "3333-01-1234567"
        })
        self.assertEqual(res["verification_status"], "PENDING_APPROVAL")
        partner_id = res["partner_id"]

        # Check DB
        p = self.db.execute("SELECT verification_status, business_number FROM partners WHERE id=?", (partner_id,)).fetchone()
        self.assertEqual(p["verification_status"], "PENDING_APPROVAL")
        self.assertEqual(p["business_number"], "123-45-67890")

    def test_02_unapproved_partner_cannot_register_slots(self):
        """Unapproved partner is strictly blocked from registering capacity slots."""
        app = apply_partner(self.db, {
            "name": "미승인청소",
            "business_number": "999-88-77766",
            "representative_name": "김미승",
            "contact_phone": "010-1234-9999"
        })
        partner_id = app["partner_id"]
        tomorrow_str = (self.today_seoul + timedelta(days=1)).strftime("%Y-%m-%d")

        with self.assertRaises(EngineError) as cm:
            register_capacity_slot(self.db, partner_id, tomorrow_str, "MORNING")
        self.assertEqual(cm.exception.code, "UNAPPROVED_PARTNER")

    def test_03_unapproved_partner_never_receives_match_offers(self):
        """Unapproved partner slots (even if forced in DB) are never dispatched matching offers."""
        app = apply_partner(self.db, {
            "name": "가짜파트너",
            "business_number": "111-22-33344",
            "representative_name": "사칭범",
            "contact_phone": "010-5555-1111"
        })
        partner_id = app["partner_id"]
        tomorrow_str = (self.today_seoul + timedelta(days=1)).strftime("%Y-%m-%d")

        # Manually force insert an available slot in DB for unapproved partner
        self.db.execute(
            "INSERT INTO capacity_slots (id, partner_id, slot_date, time_window, status) VALUES ('slot_unapp', ?, ?, 'MORNING', 'AVAILABLE')",
            (partner_id, tomorrow_str)
        )
        self.db.commit()

        # Create inquiry matching that date and district
        inq = create_inquiry(self.db, {
            "requested_date": tomorrow_str,
            "district": "관악구",
            "housing_type": "STUDIO",
            "area_pyeong": 10,
            "customer_name": "고객테스트",
            "customer_phone": "010-9999-8888",
            "agreed_privacy_collection": True,
            "agreed_third_party_share": True
        })

        offer = dispatch_1to1_offer(self.db, inq["inquiry_id"])
        if offer:
            # Must NOT be the unapproved partner! Must be one of the approved seed partners
            self.assertNotEqual(offer["partner_id"], partner_id)

    def test_04_admin_approval_and_slot_registration(self):
        """Admin approves partner, allowing valid 24~72h slot registration."""
        app = apply_partner(self.db, {
            "name": "성실클린 동작점",
            "business_number": "555-66-77788",
            "representative_name": "박성실",
            "contact_phone": "010-3333-7777"
        })
        partner_id = app["partner_id"]

        # Admin Approve
        app_res = approve_partner(self.db, partner_id)
        self.assertEqual(app_res["verification_status"], "APPROVED")

        # Now register slot for tomorrow (24h+)
        tomorrow_str = (self.today_seoul + timedelta(days=1)).strftime("%Y-%m-%d")
        slot_res = register_capacity_slot(self.db, partner_id, tomorrow_str, "AFTERNOON")
        self.assertEqual(slot_res["status"], "AVAILABLE")
        self.assertEqual(slot_res["time_window"], "AFTERNOON")

    def test_05_slot_rush_window_validation_seoul_tz(self):
        """Enforces 24~72h rush window (rejects today/past or dates > 4 days out)."""
        partner_id = "partner_gw_cleanhome" # Approved seed partner

        # 1. Past date / Today (less than 24h)
        today_str = self.today_seoul.strftime("%Y-%m-%d")
        with self.assertRaises(EngineError) as cm:
            register_capacity_slot(self.db, partner_id, today_str, "MORNING")
        self.assertEqual(cm.exception.code, "OUT_OF_RUSH_WINDOW")

        # 2. Far future date (> 72h / 4 days)
        far_future_str = (self.today_seoul + timedelta(days=10)).strftime("%Y-%m-%d")
        with self.assertRaises(EngineError) as cm:
            register_capacity_slot(self.db, partner_id, far_future_str, "MORNING")
        self.assertEqual(cm.exception.code, "OUT_OF_RUSH_WINDOW")

    def test_06_slot_duplicate_prevention(self):
        """Prevents duplicate slot on the same partner, date, and time window."""
        partner_id = "partner_gw_cleanhome"
        target_date_str = (self.today_seoul + timedelta(days=3)).strftime("%Y-%m-%d")

        # Register first
        register_capacity_slot(self.db, partner_id, target_date_str, "MORNING")

        # Duplicate attempt
        with self.assertRaises(EngineError) as cm:
            register_capacity_slot(self.db, partner_id, target_date_str, "MORNING")
        self.assertEqual(cm.exception.code, "SLOT_DUPLICATE")

    def test_07_partner_suspension_cancels_available_slots(self):
        """Suspending a partner automatically cancels all their AVAILABLE slots."""
        partner_id = "partner_dj_topclean"
        
        # Verify active slots exist before suspension
        slots_before = self.db.execute("SELECT count(*) AS cnt FROM capacity_slots WHERE partner_id=? AND status='AVAILABLE'", (partner_id,)).fetchone()["cnt"]
        self.assertGreater(slots_before, 0)

        # Suspend
        sus_res = suspend_partner(self.db, partner_id, "고객 클레임 3회 누적")
        self.assertEqual(sus_res["verification_status"], "SUSPENDED")
        self.assertGreater(sus_res["cancelled_slots_count"], 0)

        # Verify slots cancelled in DB
        slots_after = self.db.execute("SELECT count(*) AS cnt FROM capacity_slots WHERE partner_id=? AND status='AVAILABLE'", (partner_id,)).fetchone()["cnt"]
        self.assertEqual(slots_after, 0)

    def test_08_slot_deletion_guardrail(self):
        """Partner can delete AVAILABLE slot, but cannot delete OFFERED or RESERVED slot."""
        partner_id = "partner_gw_cleanhome"
        target_date_str = (self.today_seoul + timedelta(days=2)).strftime("%Y-%m-%d")

        slot = register_capacity_slot(self.db, partner_id, target_date_str, "AFTERNOON")
        slot_id = slot["slot_id"]

        # 1. Normal delete works
        del_res = delete_capacity_slot(self.db, slot_id, partner_id)
        self.assertEqual(del_res["status"], "CANCELLED")

        # 2. Offered slot cannot be deleted
        slot2 = register_capacity_slot(self.db, partner_id, target_date_str, "AFTERNOON")
        self.db.execute("UPDATE capacity_slots SET status='OFFERED' WHERE id=?", (slot2["slot_id"],))
        self.db.commit()

        with self.assertRaises(EngineError) as cm:
            delete_capacity_slot(self.db, slot2["slot_id"], partner_id)
        self.assertEqual(cm.exception.code, "SLOT_CANNOT_DELETE")

if __name__ == "__main__":
    unittest.main()
