"""
SlotBridge Korea — Zero-Paid Growth & Analytics Test Suite (Step 10)
Validates 11-stage conversion funnel tracking, realized unit economics,
rejection reasons aggregation, AB testing framework, and promotional privacy guardrails.
"""

import unittest
from datetime import datetime, timedelta

from src.db import DatabaseManager
from src.analytics import (
    track_funnel_event, compute_funnel_metrics,
    compute_unit_economics_realized, get_rejection_reasons_summary,
    assign_ab_test_variant, record_ab_event, evaluate_ab_test,
    can_send_promotional_message, FUNNEL_STAGES
)
from src.intake import submit_customer_inquiry
from src.engine import EngineError
from src.supplier import SEOUL_TZ

class TestAnalyticsAndGrowth(unittest.TestCase):
    def setUp(self):
        self.db = DatabaseManager(":memory:")
        self.db.run_migration_up()
        self.db.seed_data()
        self.today_seoul = datetime.now(SEOUL_TZ).date()
        self.tomorrow_str = (self.today_seoul + timedelta(days=1)).strftime("%Y-%m-%d")

    def tearDown(self):
        self.db.close()

    def test_01_track_all_11_funnel_stages(self):
        """Records zero-PII events across all 11 stages of the conversion funnel."""
        session_id = "sess_test_11_stages"
        for stage in FUNNEL_STAGES:
            evt_id = track_funnel_event(self.db, session_id, stage, partner_id="partner_gw_cleanhome")
            self.assertTrue(evt_id.startswith("evt_"))

        # Verify DB counts
        count = self.db.execute("SELECT count(*) AS cnt FROM growth_events WHERE session_id=?", (session_id,)).fetchone()["cnt"]
        self.assertEqual(count, 11)

    def test_02_invalid_funnel_event_rejected(self):
        """Invalid funnel event names are strictly rejected."""
        with self.assertRaises(EngineError) as cm:
            track_funnel_event(self.db, "sess_err", "unsupported_event_name")
        self.assertEqual(cm.exception.code, "INVALID_EVENT")

    def test_03_compute_funnel_metrics(self):
        """Computes conversion percentages across views, form starts, submissions, and completions."""
        # 10 views, 5 form starts, 3 submits, 1 completion
        for _ in range(10): track_funnel_event(self.db, "s", "page_view")
        for _ in range(5): track_funnel_event(self.db, "s", "form_start")
        for _ in range(3): track_funnel_event(self.db, "s", "request_submitted")
        track_funnel_event(self.db, "s", "completed")

        metrics = compute_funnel_metrics(self.db)
        self.assertEqual(metrics["form_start_rate_pct"], 50.0) # 5/10
        self.assertEqual(metrics["submit_rate_pct"], 60.0)     # 3/5
        self.assertEqual(metrics["end_to_end_conversion_pct"], 20.0) # 1/5

    def test_04_realized_unit_economics_cac_zero(self):
        """Calculates realized unit economics with CAC = 0, 13,000 KRW net profit, and 65% margin."""
        # Insert 2 separate completed matches
        inq1 = submit_customer_inquiry(self.db, {
            "requested_date": self.tomorrow_str,
            "district": "관악구",
            "housing_type": "STUDIO",
            "area_pyeong": 10,
            "customer_name": "경제성1",
            "customer_phone": "010-1234-9991",
            "agreed_privacy_collection": True,
            "agreed_third_party_share": True
        })
        inq2 = submit_customer_inquiry(self.db, {
            "requested_date": self.tomorrow_str,
            "district": "동작구",
            "housing_type": "STUDIO",
            "area_pyeong": 10,
            "customer_name": "경제성2",
            "customer_phone": "010-1234-9992",
            "agreed_privacy_collection": True,
            "agreed_third_party_share": True
        })
        self.db.execute(
            """
            INSERT INTO matches (id, inquiry_id, partner_id, slot_id, status, estimated_amount, final_amount)
            VALUES ('mat_ue_1', ?, 'partner_gw_cleanhome', 'slot_gw_tomorrow_am', 'VERIFIED', 250000, 250000),
                   ('mat_ue_2', ?, 'partner_dj_topclean', 'slot_dj_tomorrow_am', 'VERIFIED', 250000, 250000)
            """,
            (inq1["inquiry_id"], inq2["inquiry_id"])
        )
        self.db.commit()

        ue = compute_unit_economics_realized(self.db)
        self.assertEqual(ue["completed_count"], 2)
        self.assertEqual(ue["average_order_value_krw"], 250000)
        self.assertEqual(ue["customer_acquisition_cost_krw"], 0) # CAC 0 KRW
        self.assertEqual(ue["platform_gross_fee_krw"], 20000)   # 8%
        self.assertEqual(ue["referral_payout_krw"], 7000)        # 35%
        self.assertEqual(ue["net_contribution_margin_krw"], 13000) # 13,000 KRW
        self.assertEqual(ue["margin_percentage"], 65.0)         # 65% margin
        self.assertEqual(ue["net_take_rate_pct"], 5.20)         # 5.2% take rate

    def test_05_rejection_reasons_summary(self):
        """Aggregates partner rejection and timeout reasons."""
        inq = submit_customer_inquiry(self.db, {
            "requested_date": self.tomorrow_str,
            "district": "관악구",
            "housing_type": "STUDIO",
            "area_pyeong": 10,
            "customer_name": "거절집계",
            "customer_phone": "010-1234-8888",
            "agreed_privacy_collection": True,
            "agreed_third_party_share": True
        })
        # Insert 3 offers with valid foreign keys
        self.db.execute(
            """
            INSERT INTO assignment_offers (id, inquiry_id, partner_id, slot_id, priority, status, expires_at)
            VALUES ('off_rej_1', ?, 'partner_gw_cleanhome', 'slot_gw_tomorrow_am', 1, 'REJECTED', '2026-11-01'),
                   ('off_rej_2', ?, 'partner_dj_topclean', 'slot_dj_tomorrow_am', 1, 'REJECTED', '2026-11-01'),
                   ('off_rej_3', ?, 'partner_gw_cleanhome', 'slot_gw_tomorrow_am', 1, 'EXPIRED', '2026-11-01')
            """,
            (inq["inquiry_id"], inq["inquiry_id"], inq["inquiry_id"])
        )
        self.db.execute(
            """
            INSERT INTO partner_responses (id, offer_id, partner_id, response_type, rejection_reason)
            VALUES ('r1', 'off_rej_1', 'partner_gw_cleanhome', 'REJECT', '일정 중복'),
                   ('r2', 'off_rej_2', 'partner_dj_topclean', 'REJECT', '일정 중복'),
                   ('r3', 'off_rej_3', 'partner_gw_cleanhome', 'TIMEOUT', NULL)
            """
        )
        self.db.commit()

        reasons = get_rejection_reasons_summary(self.db)
        self.assertEqual(reasons["REJECT: 일정 중복"], 2)
        self.assertEqual(reasons["TIMEOUT: 무응답 타임아웃"], 1)

    def test_06_ab_test_deterministic_assignment(self):
        """Assigns consistent variant based on MD5 hash of experiment and user."""
        v1 = assign_ab_test_variant("EXP-01", "user_123")
        v2 = assign_ab_test_variant("EXP-01", "user_123")
        self.assertEqual(v1, v2)
        self.assertIn(v1, ["variant_a", "variant_b"])

    def test_07_ab_test_evaluation_and_stop_condition(self):
        """Tracks exposures/conversions and triggers stop condition when min sample reached."""
        exp_id = "EXP-TEST-01"
        # Record 60 exposures for A (18 conv) and 60 exposures for B (24 conv) = 120 total
        for i in range(60):
            record_ab_event(self.db, exp_id, "variant_a", f"u_a_{i}", "exposure")
            if i < 18:
                record_ab_event(self.db, exp_id, "variant_a", f"u_a_{i}", "conversion")
        for i in range(60):
            record_ab_event(self.db, exp_id, "variant_b", f"u_b_{i}", "exposure")
            if i < 24:
                record_ab_event(self.db, exp_id, "variant_b", f"u_b_{i}", "conversion")

        res = evaluate_ab_test(self.db, exp_id, min_sample=100)
        self.assertEqual(res["total_exposures"], 120)
        self.assertTrue(res["stop_condition_met"])
        self.assertEqual(res["variants"]["variant_a"]["cvr_pct"], 30.0)
        self.assertEqual(res["variants"]["variant_b"]["cvr_pct"], 40.0)

    def test_08_promotional_messaging_blocked_without_explicit_consent(self):
        """Promotional messaging is strictly blocked unless agreed_marketing == 1."""
        # Inquiry without marketing consent
        inq_no_mkt = submit_customer_inquiry(self.db, {
            "requested_date": self.tomorrow_str,
            "district": "관악구",
            "housing_type": "STUDIO",
            "area_pyeong": 10,
            "customer_name": "노마케팅",
            "customer_phone": "010-0000-0001",
            "agreed_privacy_collection": True,
            "agreed_third_party_share": True,
            "agreed_marketing": False # No marketing
        })
        self.assertFalse(can_send_promotional_message(self.db, inq_no_mkt["inquiry_id"]))

        # Inquiry with explicit marketing consent
        inq_with_mkt = submit_customer_inquiry(self.db, {
            "requested_date": self.tomorrow_str,
            "district": "관악구",
            "housing_type": "STUDIO",
            "area_pyeong": 10,
            "customer_name": "마케팅동의",
            "customer_phone": "010-0000-0002",
            "agreed_privacy_collection": True,
            "agreed_third_party_share": True,
            "agreed_marketing": True # Agreed
        })
        self.assertTrue(can_send_promotional_message(self.db, inq_with_mkt["inquiry_id"]))

if __name__ == "__main__":
    unittest.main()
