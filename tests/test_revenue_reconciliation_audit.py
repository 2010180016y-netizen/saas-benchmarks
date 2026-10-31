"""
tests/test_revenue_reconciliation_audit.py
SBK-FIX-07 Verification Suite: Revenue Reconciliation, Cash Receipt & No Phantom Cash Audit.
Tests all 9+ scenarios specified in SBK-FIX-07:
1. Cross-month transaction isolation (전월/익월 거래 분리)
2. Duplicate ledger accrual blocked (1건 중복 원장 차단)
3. Unverified/NULL amount blocked without phantom assumption (무증빙 금액 차단)
4. Uncollected cash leads to UNRECONCILED (미입금 대사 불일치 판정)
5. Partial payment rejected (부분 입금 거부)
6. Payout withheld without tax invoice (정산 보류)
7. Duplicate reversal blocked (중복 역분개 차단)
8. Multi-partner attribution isolated (3명 파트너 개별 귀속)
9. Zero-volume invoice handled cleanly (0원 인보이스)
10. Full cash settlement reaches BALANCED with 0 discrepancy (실증빙 대사 완결)
"""

import unittest
import json
from datetime import datetime, timedelta

from src.db import DatabaseManager
from src.revenue import (
    calculate_commission,
    get_financial_kpis,
    record_verified_match_ledger,
    record_ledger_reversal,
    generate_monthly_invoice,
    approve_and_issue_invoice,
    record_invoice_payment,
    generate_monthly_payout,
    approve_payout,
    reconcile_billing_period
)
from src.engine import EngineError
from src.intake import submit_customer_inquiry
from src.supplier import SEOUL_TZ

class TestRevenueReconciliationAudit(unittest.TestCase):
    def setUp(self):
        self.db = DatabaseManager(":memory:")
        self.db.run_migration_up()
        self.db.seed_data()
        self.today_seoul = datetime.now(SEOUL_TZ).date()

    def tearDown(self):
        self.db.close()

    def _create_match(self, match_id, partner_id, slot_date, final_amount, attribution_partner_id=None, status="VERIFIED"):
        inq = submit_customer_inquiry(self.db, {
            "requested_date": slot_date,
            "district": "관악구",
            "housing_type": "STUDIO",
            "area_pyeong": 10,
            "customer_name": f"고객_{match_id}",
            "customer_phone": f"010-{abs(hash(match_id)) % 9000 + 1000:04d}-{abs(hash(match_id[::-1])) % 9000 + 1000:04d}",
            "attribution_partner_id": attribution_partner_id,
            "agreed_privacy_collection": True,
            "agreed_third_party_share": True
        })
        # Check if slot already exists for partner, date, time_window
        slot = self.db.execute(
            "SELECT id FROM capacity_slots WHERE partner_id = ? AND slot_date = ? AND time_window = 'MORNING'",
            (partner_id, slot_date)
        ).fetchone()
        if slot:
            slot_id = slot["id"]
        else:
            slot_id = f"slot_{match_id}"
            self.db.execute(
                """
                INSERT INTO capacity_slots (id, partner_id, slot_date, time_window, max_capacity, allocated_count, status)
                VALUES (?, ?, ?, 'MORNING', 1, 1, 'COMPLETED')
                """,
                (slot_id, partner_id, slot_date)
            )
        self.db.execute(
            """
            INSERT INTO matches (id, inquiry_id, partner_id, slot_id, status, estimated_amount, final_amount)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (match_id, inq["inquiry_id"], partner_id, slot_id, status, final_amount or 250000, final_amount)
        )
        self.db.commit()
        return match_id

    def test_01_cross_month_transaction_isolation(self):
        """Matches in October (2026-10) must NOT be included in November (2026-11) invoice or payout."""
        # October match
        self._create_match("mat_oct_01", "partner_gw_cleanhome", "2026-10-25", 200000, "partner_sl_master")
        # November match
        self._create_match("mat_nov_01", "partner_gw_cleanhome", "2026-11-02", 300000, "partner_sl_master")

        # Generate November Invoice
        nov_inv = generate_monthly_invoice(self.db, "partner_gw_cleanhome", "2026-11")
        self.assertEqual(nov_inv["total_service_volume"], 300000) # Only November match!
        self.assertEqual(nov_inv["total_platform_fee"], 24000)   # 8% of 300k
        self.assertEqual(nov_inv["matched_count"], 1)

        # Generate November Payout
        nov_pay = generate_monthly_payout(self.db, "partner_sl_master", "2026-11")
        self.assertEqual(nov_pay["payout_amount"], 8400) # 35% of 24k
        self.assertEqual(nov_pay["matched_count"], 1)

    def test_02_duplicate_ledger_accrual_blocked(self):
        """Calling record_verified_match_ledger twice on the same match strictly raises DUPLICATE_LEDGER_EVENT (409)."""
        m_id = self._create_match("mat_dup_ledger", "partner_gw_cleanhome", "2026-11-03", 250000)
        events = record_verified_match_ledger(self.db, m_id)
        self.assertGreater(len(events), 0)

        with self.assertRaises(EngineError) as ctx:
            record_verified_match_ledger(self.db, m_id)
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.code, "DUPLICATE_LEDGER_EVENT")

    def test_03_null_amount_blocked_without_assumption(self):
        """Attempting to ledger a match with NULL final_amount raises INVALID_AMOUNT without guessing 250k."""
        m_id = self._create_match("mat_null_amt", "partner_gw_cleanhome", "2026-11-03", None) # NULL final_amount
        with self.assertRaises(EngineError) as ctx:
            record_verified_match_ledger(self.db, m_id)
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(ctx.exception.code, "INVALID_AMOUNT")

    def test_04_uncollected_cash_reconciliation_unreconciled(self):
        """When invoice is issued but cash is uncollected, strict reconciliation reports UNRECONCILED with exact discrepancy."""
        self._create_match("mat_uncoll", "partner_gw_cleanhome", "2026-11-04", 250000)
        inv = generate_monthly_invoice(self.db, "partner_gw_cleanhome", "2026-11")
        approve_and_issue_invoice(self.db, inv["invoice_id"], admin_id="ADMIN_HOEGI")

        # Strict Cash Reconciliation (No payment made yet)
        rec = reconcile_billing_period(self.db, "2026-11", reconciled_by="ADMIN_HOEGI", strict_cash=True)
        self.assertEqual(rec["status"], "UNRECONCILED")
        self.assertEqual(rec["total_revenue_expected"], 22000) # 20k + 2k VAT
        self.assertEqual(rec["total_revenue_collected"], 0)
        self.assertEqual(rec["discrepancy_amount"], 22000)

    def test_05_partial_payment_rejected(self):
        """Partial payment on an invoice strictly fails with PARTIAL_PAYMENT (400)."""
        self._create_match("mat_part_pay", "partner_gw_cleanhome", "2026-11-05", 250000)
        inv = generate_monthly_invoice(self.db, "partner_gw_cleanhome", "2026-11")
        approve_and_issue_invoice(self.db, inv["invoice_id"], admin_id="ADMIN_HOEGI")

        # Attempt to pay 10,000 KRW instead of 22,000 KRW
        with self.assertRaises(EngineError) as ctx:
            record_invoice_payment(self.db, inv["invoice_id"], paid_amount=10000, bank_transfer_ref="KB_PARTIAL_01")
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(ctx.exception.code, "PARTIAL_PAYMENT")

    def test_06_payout_withheld_without_tax_invoice(self):
        """Payout approval remains PENDING and blocked if tax invoice receipt is unverified."""
        self._create_match("mat_tax_withhold", "partner_gw_cleanhome", "2026-11-06", 250000, "partner_sl_master")
        pay = generate_monthly_payout(self.db, "partner_sl_master", "2026-11")

        with self.assertRaises(EngineError) as ctx:
            approve_payout(self.db, pay["payout_id"], tax_invoice_verified=False)
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(ctx.exception.code, "TAX_INVOICE_REQUIRED")

        # Check DB status is still PENDING
        p_row = self.db.execute("SELECT status FROM payouts WHERE id = ?", (pay["payout_id"],)).fetchone()
        self.assertEqual(p_row["status"], "PENDING")

    def test_07_duplicate_reversal_blocked(self):
        """Calling record_ledger_reversal twice on the same match raises ALREADY_REVERSED (409)."""
        m_id = self._create_match("mat_dup_rev", "partner_gw_cleanhome", "2026-11-07", 250000)
        record_verified_match_ledger(self.db, m_id)

        # First reversal succeeds
        record_ledger_reversal(self.db, m_id, reason="고객 불만족 취소")

        # Second reversal MUST be rejected
        with self.assertRaises(EngineError) as ctx:
            record_ledger_reversal(self.db, m_id, reason="중복 취소 시도")
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.code, "ALREADY_REVERSED")

    def test_08_multi_partner_attribution_isolated(self):
        """3 distinct referring partners each receive exactly their respective 35% cut without cross-bleeding."""
        # Seed 3 referring partners and organizations
        for p_idx in [1, 2, 3]:
            self.db.execute(
                f"""
                INSERT OR IGNORE INTO organizations (id, name, business_number, representative_name)
                VALUES ('org_ref_{p_idx}', '리퍼럴_{p_idx}', '100000000{p_idx}', '대표_{p_idx}')
                """
            )
            self.db.execute(
                f"""
                INSERT OR IGNORE INTO partners (id, organization_id, name, partner_type, business_number, representative_name, contact_phone, verification_status, settlement_bank, settlement_account)
                VALUES ('partner_ref_{p_idx}', 'org_ref_{p_idx}', '리퍼럴_{p_idx}', 'REFERRAL', '100000000{p_idx}', '대표_{p_idx}', '010-0000-000{p_idx}', 'APPROVED', '국민은행', '111-22-33333{p_idx}')
                """
            )
        self.db.commit()

        # Partner 1: 1 match of 200,000 KRW (8% = 16,000, 35% = 5,600)
        self._create_match("mat_p1", "partner_gw_cleanhome", "2026-11-08", 200000, "partner_ref_1")
        # Partner 2: 1 match of 300,000 KRW (8% = 24,000, 35% = 8,400)
        self._create_match("mat_p2", "partner_gw_cleanhome", "2026-11-08", 300000, "partner_ref_2")
        # Partner 3: 1 match of 400,000 KRW (8% = 32,000, 35% = 11,200)
        self._create_match("mat_p3", "partner_gw_cleanhome", "2026-11-08", 400000, "partner_ref_3")

        pay1 = generate_monthly_payout(self.db, "partner_ref_1", "2026-11")
        pay2 = generate_monthly_payout(self.db, "partner_ref_2", "2026-11")
        pay3 = generate_monthly_payout(self.db, "partner_ref_3", "2026-11")

        self.assertEqual(pay1["payout_amount"], 5600)
        self.assertEqual(pay2["payout_amount"], 8400)
        self.assertEqual(pay3["payout_amount"], 11200)

    def test_09_zero_volume_invoice(self):
        """When a partner has zero matches in the period, invoice generates with 0 volume cleanly."""
        inv = generate_monthly_invoice(self.db, "partner_gw_cleanhome", "2026-12")
        self.assertEqual(inv["total_service_volume"], 0)
        self.assertEqual(inv["total_platform_fee"], 0)
        self.assertEqual(inv["vat_amount"], 0)
        self.assertEqual(inv["total_invoice_amount"], 0)
        self.assertEqual(inv["matched_count"], 0)

    def test_10_full_cash_settlement_reaches_balanced(self):
        """Verified bank deposit marks invoice PAID, records cash ledger event, and achieves BALANCED reconciliation."""
        self._create_match("mat_cash_settle", "partner_gw_cleanhome", "2026-11-09", 250000)
        inv = generate_monthly_invoice(self.db, "partner_gw_cleanhome", "2026-11")
        approve_and_issue_invoice(self.db, inv["invoice_id"], admin_id="ADMIN_HOEGI")

        # Partner transfers full 22,000 KRW
        pay_res = record_invoice_payment(
            self.db,
            inv["invoice_id"],
            paid_amount=22000,
            bank_transfer_ref="KB-202611-098765",
            admin_id="ADMIN_HOEGI"
        )
        self.assertEqual(pay_res["status"], "PAID")

        # Verify Cash Ledger Event recorded
        cash_ledger = self.db.execute(
            "SELECT * FROM ledger_events WHERE debit_account = 'CASH_BANK_ACCOUNT' AND amount = 22000"
        ).fetchone()
        self.assertIsNotNone(cash_ledger)
        self.assertEqual(cash_ledger["credit_account"], "ACCOUNTS_RECEIVABLE_PARTNER")

        # Strict Cash Reconciliation now MUST be BALANCED with 0 discrepancy
        rec = reconcile_billing_period(self.db, "2026-11", reconciled_by="ADMIN_HOEGI", strict_cash=True)
        self.assertEqual(rec["status"], "BALANCED")
        self.assertEqual(rec["discrepancy_amount"], 0)
        self.assertEqual(rec["total_revenue_expected"], 22000)
        self.assertEqual(rec["total_revenue_collected"], 22000)

if __name__ == "__main__":
    unittest.main()
