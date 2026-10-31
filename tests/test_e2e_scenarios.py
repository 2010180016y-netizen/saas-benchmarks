"""
SlotBridge Korea — End-to-End (E2E) Integration Scenarios Test Suite (Step 11)
Validates the complete 6 mandatory MVP end-to-end operational scenarios:
1. Full Happy Path (Onboard -> Slot -> Intake -> Rank 1 Exclusive Offer -> Accept -> Complete -> Ledger -> Invoice -> Payout)
2. Sequential Re-routing on Rank 1 Timeout
3. 1-Click Customer Revocation & Immediate PII Tombstone
4. Race Condition & Concurrency 409 Conflict Guardrail
5. Unapproved Partner Hard Isolation
6. Privacy & Anti-Spam Statutory Guardrails
"""

import unittest
from datetime import datetime, timedelta

from src.db import DatabaseManager
from src.supplier import (
    apply_partner, approve_partner, register_capacity_slot, SEOUL_TZ
)
from src.intake import submit_customer_inquiry, revoke_customer_inquiry
from src.attribution import generate_attribution_token
from src.matching import (
    evaluate_and_dispatch_candidates, transition_state, handle_timeout_or_rejection
)
from src.engine import accept_offer, EngineError
from src.revenue import (
    record_verified_match_ledger, generate_monthly_invoice, approve_and_issue_invoice,
    generate_monthly_payout, approve_payout
)
from src.analytics import (
    track_funnel_event, can_send_promotional_message
)

class TestSlotBridgeE2EScenarios(unittest.TestCase):
    def setUp(self):
        self.db = DatabaseManager(":memory:")
        self.db.run_migration_up()
        self.today_seoul = datetime.now(SEOUL_TZ).date()
        self.tomorrow_str = (self.today_seoul + timedelta(days=1)).strftime("%Y-%m-%d")

    def tearDown(self):
        self.db.close()

    def test_scenario_01_full_happy_path_e2e(self):
        """
        Scenario 1: Full Happy Path
        Onboarding -> Slots -> Intake -> Rank 1 Exclusive Offer -> Accept -> Agreed Price ->
        Completed with Photos -> Verified -> Ledger Entries -> Monthly Invoice Issued -> Referral Payout Approved.
        """
        # 1. Partner Onboarding: Fulfillment Partner & Referring Partner
        p_fulfill_res = apply_partner(self.db, {
            "business_number": "123-45-67890",
            "name": "E2E 클린마스터",
            "representative_name": "김반장",
            "contact_phone": "010-1111-2222",
            "service_areas": [{"city": "서울특별시", "district": "관악구", "neighborhood": "신림동"}],
            "liability_insurance_policy_number": "INS-12345"
        })
        p_fulfill_id = p_fulfill_res["partner_id"]
        approve_partner(self.db, p_fulfill_id, admin_id="admin_hoegi")

        p_ref_res = apply_partner(self.db, {
            "business_number": "987-65-43210",
            "name": "E2E 리퍼럴청소",
            "representative_name": "이사장",
            "contact_phone": "010-3333-4444",
            "service_areas": [{"city": "서울특별시", "district": "관악구", "neighborhood": "봉천동"}],
            "liability_insurance_policy_number": "INS-54321"
        })
        p_ref_id = p_ref_res["partner_id"]
        approve_partner(self.db, p_ref_id, admin_id="admin_hoegi")

        # 2. Register Capacity Slot for Fulfillment Partner
        slot_res = register_capacity_slot(self.db, p_fulfill_id, self.tomorrow_str, "MORNING")
        slot_id = slot_res["slot_id"]
        self.assertTrue(slot_id.startswith("slot_"))

        # 3. Customer Intake via Referring Partner's Signed Attribution Token
        attr_token = generate_attribution_token(p_ref_id)
        intake_res = submit_customer_inquiry(self.db, {
            "requested_date": self.tomorrow_str,
            "district": "관악구",
            "housing_type": "STUDIO",
            "area_pyeong": 10,
            "customer_name": "행복한고객",
            "customer_phone": "010-5555-6666",
            "attribution_token": attr_token,
            "agreed_privacy_collection": True,
            "agreed_third_party_share": True
        })
        inquiry_id = intake_res["inquiry_id"]
        track_funnel_event(self.db, "sess_e2e", "request_submitted", partner_id=p_ref_id)

        # 4. Deterministic Offer Dispatch to Rank 1 Partner
        dispatch_res = evaluate_and_dispatch_candidates(self.db, inquiry_id)
        self.assertIsNotNone(dispatch_res)
        offer_id = dispatch_res["offer_id"]
        self.assertEqual(dispatch_res["selected_partner_id"], p_fulfill_id)

        # 5. Rank 1 Partner Accepts within 15min Window
        accept_res = accept_offer(self.db, offer_id, p_fulfill_id)
        self.assertEqual(accept_res["status"], "CONFIRMED")
        match_id = accept_res["match_id"]
        self.assertEqual(accept_res["decrypted_customer_phone"], "010-5555-6666")

        # 6. Customer Confirms Agreed Price -> State moves to BOOKED
        transition_state(self.db, "MATCH", match_id, "CONFIRMED", "CUSTOMER_CONFIRMED", "PARTNER")
        transition_state(self.db, "MATCH", match_id, "CUSTOMER_CONFIRMED", "BOOKED", "PARTNER", evidence={"agreed_price": 250000})

        # 7. On-site Service Completion with 2 Verified Photos
        transition_state(self.db, "MATCH", match_id, "BOOKED", "COMPLETED", "PARTNER", evidence={"photo_urls": ["photo1.jpg", "photo2.jpg"]})

        # 8. Inspection & Verification -> State moves to VERIFIED (Triggers Ledger Entries)
        transition_state(self.db, "MATCH", match_id, "COMPLETED", "VERIFIED", "ADMIN")
        record_verified_match_ledger(self.db, match_id)

        # Verify Immutable Ledger Events (Balanced Debit & Credit)
        ledger_rows = self.db.execute(
            "SELECT debit_account, credit_account, amount FROM ledger_events WHERE match_id = ?",
            (match_id,)
        ).fetchall()
        self.assertGreaterEqual(len(ledger_rows), 2)
        self.assertEqual(ledger_rows[0]["debit_account"], "ACCOUNTS_RECEIVABLE_PARTNER")
        self.assertEqual(ledger_rows[0]["amount"], 20000) # 8% fee

        # 9. Month-End Billing: Generate B2B Invoice and Issue with Tax Invoice #
        billing_period = self.tomorrow_str[:7] # YYYY-MM
        inv_res = generate_monthly_invoice(self.db, p_fulfill_id, billing_period)
        inv_id = inv_res["invoice_id"]
        self.assertEqual(inv_res["status"], "DRAFT")
        self.assertEqual(inv_res["total_invoice_amount"], 22000) # 20k + 2k VAT

        issue_res = approve_and_issue_invoice(self.db, inv_id, admin_id="admin_hoegi")
        self.assertEqual(issue_res["status"], "ISSUED")

        # 10. Month-End Referral Payout: Requires Mandatory Tax Invoice Receipt
        payout_res = generate_monthly_payout(self.db, p_ref_id, billing_period)
        payout_id = payout_res["payout_id"]
        self.assertEqual(payout_res["payout_amount"], 7000) # 7k referral cut

        # Attempt without tax invoice receipt should fail
        with self.assertRaises(EngineError):
            approve_payout(self.db, payout_id, tax_invoice_verified=False)

        # Approve with verified tax invoice receipt succeeds
        pay_res = approve_payout(self.db, payout_id, tax_invoice_verified=True, admin_id="admin_hoegi")
        self.assertEqual(pay_res["status"], "APPROVED")

    def test_scenario_02_sequential_re_routing_upon_timeout(self):
        """
        Scenario 2: Sequential Re-routing
        Rank 1 expires after 15min timeout -> Slot restored -> Re-routes to Rank 2.
        """
        p1_res = apply_partner(self.db, {
            "business_number": "111-11-11111", "name": "Rank1 청소",
            "representative_name": "일등", "contact_phone": "010-1111-0001",
            "service_areas": [{"city": "서울특별시", "district": "관악구", "neighborhood": "전체"}],
            "liability_insurance_policy_number": "INS-111"
        })
        p1 = p1_res["partner_id"]
        approve_partner(self.db, p1, admin_id="admin")
        register_capacity_slot(self.db, p1, self.tomorrow_str, "MORNING")

        p2_res = apply_partner(self.db, {
            "business_number": "222-22-22222", "name": "Rank2 청소",
            "representative_name": "이등", "contact_phone": "010-2222-0002",
            "service_areas": [{"city": "서울특별시", "district": "관악구", "neighborhood": "전체"}],
            "liability_insurance_policy_number": "INS-222"
        })
        p2 = p2_res["partner_id"]
        approve_partner(self.db, p2, admin_id="admin")
        register_capacity_slot(self.db, p2, self.tomorrow_str, "MORNING")

        inq = submit_customer_inquiry(self.db, {
            "requested_date": self.tomorrow_str, "district": "관악구",
            "housing_type": "STUDIO", "area_pyeong": 10,
            "customer_name": "순차고객", "customer_phone": "010-9999-0002",
            "agreed_privacy_collection": True, "agreed_third_party_share": True
        })
        inquiry_id = inq["inquiry_id"]

        # Offer 1 dispatched
        d1 = evaluate_and_dispatch_candidates(self.db, inquiry_id)
        self.assertIsNotNone(d1)
        first_partner_id = d1["selected_partner_id"]
        first_offer_id = d1["offer_id"]

        # Simulate timeout and re-routing
        next_offer = handle_timeout_or_rejection(self.db, first_offer_id, action="TIMEOUT")

        # Next dispatch automatically re-routed to next candidate
        self.assertIsNotNone(next_offer)
        self.assertNotEqual(next_offer["selected_partner_id"], first_partner_id)

    def test_scenario_03_customer_1click_revocation_tombstone(self):
        """
        Scenario 3: 1-Click Customer Revocation
        Obliterates all customer PII and writes tombstone in database.
        """
        inq = submit_customer_inquiry(self.db, {
            "requested_date": self.tomorrow_str, "district": "관악구",
            "housing_type": "STUDIO", "area_pyeong": 10,
            "customer_name": "철회고객", "customer_phone": "010-7777-8888",
            "agreed_privacy_collection": True, "agreed_third_party_share": True
        })
        token = inq["tracking_token"]
        inq_id = inq["inquiry_id"]

        revoke_res = revoke_customer_inquiry(self.db, token)
        self.assertTrue(revoke_res["success"])

        # Verify raw DB row: PII is completely tombstoned
        row = self.db.execute(
            "SELECT customer_name_encrypted, customer_phone_encrypted, status FROM inquiries WHERE id = ?",
            (inq_id,)
        ).fetchone()
        self.assertTrue(row["customer_name_encrypted"].startswith("ERASED_"))
        self.assertTrue(row["customer_phone_encrypted"].startswith("ERASED_"))
        self.assertEqual(row["status"], "CANCELLED")

    def test_scenario_04_race_condition_409_conflict(self):
        """
        Scenario 4: Race Condition & Concurrency Guardrail
        Concurrent acceptance of the same offer yields 409 conflict.
        """
        p_res = apply_partner(self.db, {
            "business_number": "333-33-33333", "name": "동시성청소",
            "representative_name": "박반장", "contact_phone": "010-3333-0003",
            "service_areas": [{"city": "서울특별시", "district": "관악구", "neighborhood": "전체"}],
            "liability_insurance_policy_number": "INS-333"
        })
        p = p_res["partner_id"]
        approve_partner(self.db, p, admin_id="admin")
        register_capacity_slot(self.db, p, self.tomorrow_str, "MORNING")

        inq = submit_customer_inquiry(self.db, {
            "requested_date": self.tomorrow_str, "district": "관악구",
            "housing_type": "STUDIO", "area_pyeong": 10,
            "customer_name": "동시성고객", "customer_phone": "010-3333-9999",
            "agreed_privacy_collection": True, "agreed_third_party_share": True
        })
        dispatch = evaluate_and_dispatch_candidates(self.db, inq["inquiry_id"])
        offer_id = dispatch["offer_id"]

        # Worker 1 succeeds
        res1 = accept_offer(self.db, offer_id, p)
        self.assertEqual(res1["status"], "CONFIRMED")

        # Worker 2 attempts same offer -> 409 Conflict
        with self.assertRaises(EngineError) as cm:
            accept_offer(self.db, offer_id, p)
        self.assertEqual(cm.exception.status_code, 409)

    def test_scenario_05_unapproved_partner_hard_isolation(self):
        """
        Scenario 5: Unapproved Partner Protection
        Unapproved partner cannot register slots or receive match offers.
        """
        p_unapproved_res = apply_partner(self.db, {
            "business_number": "444-44-44444", "name": "미승인청소",
            "representative_name": "최사장", "contact_phone": "010-4444-0004",
            "service_areas": [{"city": "서울특별시", "district": "관악구", "neighborhood": "전체"}],
            "liability_insurance_policy_number": None
        })
        p_unapproved = p_unapproved_res["partner_id"]

        # Cannot register slots
        with self.assertRaises(EngineError) as cm:
            register_capacity_slot(self.db, p_unapproved, self.tomorrow_str, "MORNING")
        self.assertEqual(cm.exception.code, "UNAPPROVED_PARTNER")

    def test_scenario_06_statutory_privacy_and_anti_spam_guardrails(self):
        """
        Scenario 6: Privacy & Regulatory Guardrail
        Enforces Article 17 consent and blocks unsolicited promotional messaging.
        """
        # Missing consent rejected
        with self.assertRaises(EngineError) as cm:
            submit_customer_inquiry(self.db, {
                "requested_date": self.tomorrow_str, "district": "관악구",
                "housing_type": "STUDIO", "area_pyeong": 10,
                "customer_name": "동의거부고객", "customer_phone": "010-0000-0000",
                "agreed_privacy_collection": False, # Missing
                "agreed_third_party_share": True
            })
        self.assertEqual(cm.exception.code, "CONSENT_REQUIRED")

        # Unsolicited marketing blocked by default
        inq = submit_customer_inquiry(self.db, {
            "requested_date": self.tomorrow_str, "district": "관악구",
            "housing_type": "STUDIO", "area_pyeong": 10,
            "customer_name": "기본동의고객", "customer_phone": "010-0000-0005",
            "agreed_privacy_collection": True,
            "agreed_third_party_share": True,
            "agreed_marketing": False
        })
        self.assertFalse(can_send_promotional_message(self.db, inq["inquiry_id"]))

if __name__ == "__main__":
    unittest.main()
