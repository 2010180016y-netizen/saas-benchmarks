"""
SlotBridge Korea — Revenue, Reconciliation & Double-Entry Ledger Test Suite (Step 09)
Validates exact KRW integer math, 10% VAT separation, immutable double-entry ledger,
4 distinct KPIs, human-in-the-loop invoice/payout approvals, and month-end reconciliation.
"""

import unittest
from datetime import datetime, timedelta

from src.db import DatabaseManager
from src.revenue import (
    calculate_commission, get_financial_kpis,
    record_verified_match_ledger, record_ledger_reversal,
    generate_monthly_invoice, approve_and_issue_invoice,
    generate_monthly_payout, approve_payout, reconcile_billing_period
)
from src.engine import EngineError
from src.intake import submit_customer_inquiry
from src.supplier import SEOUL_TZ

class TestRevenueAndReconciliation(unittest.TestCase):
    def setUp(self):
        self.db = DatabaseManager(":memory:")
        self.db.run_migration_up()
        self.db.seed_data()
        self.today_seoul = datetime.now(SEOUL_TZ).date()
        self.tomorrow_str = (self.today_seoul + timedelta(days=1)).strftime("%Y-%m-%d")

    def tearDown(self):
        self.db.close()

    def _create_verified_match_with_attribution(self, match_id="mat_rev_01"):
        inq = submit_customer_inquiry(self.db, {
            "attribution_partner_id": "partner_sl_master",
            "requested_date": self.tomorrow_str,
            "district": "관악구",
            "housing_type": "STUDIO",
            "area_pyeong": 10,
            "customer_name": "정산고객",
            "customer_phone": "010-9999-1111",
            "agreed_privacy_collection": True,
            "agreed_third_party_share": True
        })
        self.db.execute(
            """
            INSERT INTO matches (id, inquiry_id, partner_id, slot_id, status, estimated_amount, final_amount)
            VALUES (?, ?, 'partner_gw_cleanhome', 'slot_gw_tomorrow_am', 'VERIFIED', 250000, 250000)
            """,
            (match_id, inq["inquiry_id"])
        )
        self.db.commit()
        return match_id

    def test_01_commission_integer_math_and_vat(self):
        """Calculates 8% platform fee, 35% referral cut, and 10% VAT with zero floating point drift."""
        comm = calculate_commission(250000)
        self.assertEqual(comm["platform_fee"], 20000) # 250k * 0.08
        self.assertEqual(comm["platform_vat"], 2000)   # 20k * 0.10
        self.assertEqual(comm["total_invoice_amount"], 22000)
        self.assertEqual(comm["referral_fee"], 7000)  # 20k * 0.35
        self.assertEqual(comm["referral_vat"], 700)    # 7k * 0.10
        self.assertEqual(comm["total_payout_amount"], 7700)

    def test_02_four_distinct_kpis_separation(self):
        """Verifies 4 distinct KPIs: estimated does NOT mix with verified, invoiced, or settled."""
        # Unconfirmed inquiry
        submit_customer_inquiry(self.db, {
            "requested_date": self.tomorrow_str,
            "district": "관악구",
            "housing_type": "STUDIO",
            "area_pyeong": 10,
            "customer_name": "미확정",
            "customer_phone": "010-1111-2222",
            "agreed_privacy_collection": True,
            "agreed_third_party_share": True
        })
        kpis = get_financial_kpis(self.db, "2026-11")
        self.assertGreater(kpis["estimated_revenue_krw"], 0)
        self.assertEqual(kpis["verified_billable_krw"], 0)
        self.assertEqual(kpis["invoiced_revenue_krw"], 0)
        self.assertEqual(kpis["settled_cash_krw"], 0)

    def test_03_double_entry_ledger_recording(self):
        """Match verification records balanced debit and credit entries in ledger_events."""
        match_id = self._create_verified_match_with_attribution("mat_ledger_03")
        events = record_verified_match_ledger(self.db, match_id)
        self.assertEqual(len(events), 4)

        # Check DB rows
        rows = self.db.execute("SELECT debit_account, credit_account, amount FROM ledger_events WHERE match_id=?", (match_id,)).fetchall()
        accounts = [(r["debit_account"], r["credit_account"], r["amount"]) for r in rows]
        
        self.assertIn(("ACCOUNTS_RECEIVABLE_PARTNER", "PLATFORM_COMMISSION_REVENUE", 20000), accounts)
        self.assertIn(("ACCOUNTS_RECEIVABLE_PARTNER", "VAT_OUTPUT_PAYABLE", 2000), accounts)
        self.assertIn(("REFERRAL_COMMISSION_EXPENSE", "ACCOUNTS_PAYABLE_REFERRER", 7000), accounts)
        self.assertIn(("VAT_INPUT_RECEIVABLE", "ACCOUNTS_PAYABLE_REFERRER", 700), accounts)

    def test_04_ledger_reversal_on_dispute(self):
        """Ledger reversal appends contra entries without deleting or mutating original entries."""
        match_id = self._create_verified_match_with_attribution("mat_rev_04")
        record_verified_match_ledger(self.db, match_id)
        count_before = self.db.execute("SELECT count(*) AS cnt FROM ledger_events WHERE match_id=?", (match_id,)).fetchone()["cnt"]

        # Dispute occurs -> Reversal
        rev_ids = record_ledger_reversal(self.db, match_id, reason="고객 클레임 전액 환불")
        self.assertEqual(len(rev_ids), count_before)

        # Total entries doubled (original + contra)
        count_after = self.db.execute("SELECT count(*) AS cnt FROM ledger_events WHERE match_id=?", (match_id,)).fetchone()["cnt"]
        self.assertEqual(count_after, count_before * 2)

    def test_05_monthly_invoice_generation_draft(self):
        """Generates DRAFT monthly invoice for fulfillment partner with 8% fee and 10% VAT."""
        match_id = self._create_verified_match_with_attribution("mat_inv_05")
        inv = generate_monthly_invoice(self.db, "partner_gw_cleanhome", "2026-11")
        self.assertEqual(inv["status"], "DRAFT")
        self.assertEqual(inv["total_platform_fee"], 20000)
        self.assertEqual(inv["vat_amount"], 2000)
        self.assertEqual(inv["total_invoice_amount"], 22000)

    def test_06_admin_approve_and_issue_invoice(self):
        """Admin reviews and issues invoice with tax invoice number."""
        self._create_verified_match_with_attribution("mat_issue_06")
        inv = generate_monthly_invoice(self.db, "partner_gw_cleanhome", "2026-11")
        issued = approve_and_issue_invoice(self.db, inv["invoice_id"], admin_id="ADMIN_HOEGI")
        self.assertEqual(issued["status"], "ISSUED")
        self.assertTrue(issued["tax_invoice_number"].startswith("TX-"))

    def test_07_prevent_duplicate_monthly_invoice(self):
        """Prevents duplicate invoice generation for the same partner and period."""
        self._create_verified_match_with_attribution("mat_dup_07")
        generate_monthly_invoice(self.db, "partner_gw_cleanhome", "2026-11")
        with self.assertRaises(EngineError) as cm:
            generate_monthly_invoice(self.db, "partner_gw_cleanhome", "2026-11")
        self.assertEqual(cm.exception.code, "INVOICE_ALREADY_EXISTS")

    def test_08_monthly_payout_generation(self):
        """Generates PENDING payout record for referring partner (35% referral cut)."""
        self._create_verified_match_with_attribution("mat_pay_08")
        pay = generate_monthly_payout(self.db, "partner_sl_master", "2026-11")
        self.assertEqual(pay["status"], "PENDING")
        self.assertEqual(pay["payout_amount"], 7000)
        self.assertEqual(pay["vat_amount"], 700)

    def test_09_payout_approval_blocked_without_tax_invoice(self):
        """Approving payout strictly fails if tax invoice is not verified."""
        self._create_verified_match_with_attribution("mat_pay_09")
        pay = generate_monthly_payout(self.db, "partner_sl_master", "2026-11")
        with self.assertRaises(EngineError) as cm:
            approve_payout(self.db, pay["payout_id"], tax_invoice_verified=False)
        self.assertEqual(cm.exception.code, "TAX_INVOICE_REQUIRED")

        # Now approve with verified tax invoice
        app = approve_payout(self.db, pay["payout_id"], tax_invoice_verified=True)
        self.assertEqual(app["status"], "APPROVED")

    def test_10_month_end_reconciliation(self):
        """Month-end reconciliation checks matches, invoices, payouts and creates BALANCED audit event after settlement."""
        self._create_verified_match_with_attribution("mat_rec_10")
        inv = generate_monthly_invoice(self.db, "partner_gw_cleanhome", "2026-11")
        generate_monthly_payout(self.db, "partner_sl_master", "2026-11")

        # Prior to cash settlement, reconciliation is strictly UNRECONCILED (Phantom Cash Banned)
        rec_unsettled = reconcile_billing_period(self.db, "2026-11", reconciled_by="ADMIN_HOEGI")
        self.assertEqual(rec_unsettled["status"], "UNRECONCILED")
        self.assertEqual(rec_unsettled["discrepancy_amount"], 22000)

        # Settle invoice via bank transfer
        approve_and_issue_invoice(self.db, inv["invoice_id"], admin_id="ADMIN_HOEGI")
        from src.revenue import record_invoice_payment
        record_invoice_payment(self.db, inv["invoice_id"], paid_amount=22000, bank_transfer_ref="KB_TX_REC_10")

        # After settlement, reconciliation reaches BALANCED with 0 discrepancy
        rec = reconcile_billing_period(self.db, "2026-11", reconciled_by="ADMIN_HOEGI")
        self.assertEqual(rec["status"], "BALANCED")
        self.assertEqual(rec["discrepancy_amount"], 0)
        self.assertGreater(rec["total_revenue_expected"], 0)
        self.assertEqual(rec["total_revenue_collected"], 22000)

if __name__ == "__main__":
    unittest.main()
