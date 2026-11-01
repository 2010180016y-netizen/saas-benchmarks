"""
tests/test_fix_d_verified_revenue_and_accounting.py
===================================================
Comprehensive Verification Suite for [SBK-FIX-D: Verified Revenue & Accounting Parity].

Validates:
1. P0-06: Customer verification gate: COMPLETED status (partner self-fulfillment only)
   strictly excluded from invoicing, payouts, billable KPIs, and ledger events.
2. P0-07: Elimination of phantom cash: Reconciliations without full bank deposit
   unconditionally report UNRECONCILED with exact positive discrepancy.
3. P0-08: Admin UI and API payment recording schema parity, full payment enforcement,
   duplicate bank transaction reference defense, and atomic double-entry ledger event.
4. P1-01: No false NTS verification claims without live NTS API connection (nts_verified: false).
5. P1-02: Referral payout approval strictly requires document_ref (>=4 chars) and prevents
   duplicate approvals on non-PENDING status.
6. P1-03: KPI schema alignment with slotbridge/admin-billing.html (data.kpis binding) and
   accurate period/verification filtering.
7. Worker JS Static Integrity: Verifies strict_cash bypass removal, batch atomicity,
   and elimination of unverified status in src/worker.js.
"""

import os
import unittest
import json
from datetime import datetime, timezone, timedelta

from src.db import DatabaseManager
from src.revenue import (
    calculate_commission,
    get_financial_kpis,
    record_verified_match_ledger,
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

class TestFixDVerifiedRevenueAndAccounting(unittest.TestCase):
    def setUp(self):
        self.db = DatabaseManager(":memory:")
        self.db.run_migration_up()
        self.db.seed_data()
        self.today_seoul = datetime.now(SEOUL_TZ).date()
        self.tomorrow_str = (self.today_seoul + timedelta(days=1)).strftime("%Y-%m-%d")

    def tearDown(self):
        self.db.close()

    def _create_match_with_status(self, match_id: str, status: str, final_amount: int = 250000, attr_partner: str = "partner_sl_master", partner_id: str = "partner_gw_cleanhome", slot_id: str = "slot_gw_tomorrow_am"):
        phone_mid = abs(hash(match_id)) % 9000 + 1000
        inq = submit_customer_inquiry(self.db, {
            "requested_date": self.tomorrow_str,
            "district": "관악구",
            "housing_type": "STUDIO",
            "area_pyeong": 10,
            "customer_name": f"고객_{match_id}",
            "customer_phone": f"010-{phone_mid:04d}-6666",
            "attribution_partner_id": attr_partner,
            "agreed_privacy_collection": True,
            "agreed_third_party_share": True
        })
        self.db.execute(
            """
            INSERT INTO matches (id, inquiry_id, partner_id, slot_id, status, estimated_amount, final_amount, created_at)
            VALUES (?, ?, ?, ?, ?, 250000, ?, ?)
            """,
            (match_id, inq["inquiry_id"], partner_id, slot_id, status, final_amount, f"{self.tomorrow_str}T10:00:00Z")
        )
        self.db.commit()
        return match_id

    # -------------------------------------------------------------------------
    # 1. P0-06: Customer verification gate (COMPLETED excluded from revenue/billing)
    # -------------------------------------------------------------------------
    def test_01_completed_status_excluded_from_invoices_and_ledger(self):
        """Matches with COMPLETED status (unconfirmed by customer) cannot be ledgered or invoiced."""
        m_comp = self._create_match_with_status("mat_comp_01", "COMPLETED", 300000)

        # 1. Ledger accrual must be rejected
        with self.assertRaises(EngineError) as ctx:
            record_verified_match_ledger(self.db, m_comp)
        self.assertEqual(ctx.exception.code, "UNVERIFIED_MATCH")

        # 2. Invoice generation must have 0 volume for COMPLETED
        inv = generate_monthly_invoice(self.db, "partner_gw_cleanhome", "2026-11")
        self.assertEqual(inv["total_service_volume"], 0)
        self.assertEqual(inv["total_platform_fee"], 0)
        self.assertEqual(inv["matched_count"], 0)

        # 3. Payout generation must have 0 volume for COMPLETED
        pay = generate_monthly_payout(self.db, "partner_sl_master", "2026-11")
        self.assertEqual(pay["payout_amount"], 0)
        self.assertEqual(pay["matched_count"], 0)

    def test_02_billable_status_accrues_and_invoices_properly(self):
        """Matches transitioned to BILLABLE (customer confirmed) are properly accrued and invoiced."""
        m_bill = self._create_match_with_status("mat_bill_02", "BILLABLE", 300000)

        # 1. Ledger accrual succeeds
        events = record_verified_match_ledger(self.db, m_bill)
        self.assertGreater(len(events), 0)

        # 2. Invoice includes 300,000 KRW volume (8% = 24,000, VAT = 2,400)
        inv = generate_monthly_invoice(self.db, "partner_gw_cleanhome", "2026-11")
        self.assertEqual(inv["total_service_volume"], 300000)
        self.assertEqual(inv["total_platform_fee"], 24000)
        self.assertEqual(inv["vat_amount"], 2400)
        self.assertEqual(inv["total_invoice_amount"], 26400)
        self.assertEqual(inv["matched_count"], 1)

    # -------------------------------------------------------------------------
    # 2. P0-07: Elimination of phantom cash in reconciliation
    # -------------------------------------------------------------------------
    def test_03_uncollected_cash_strictly_unreconciled_without_phantom_bypass(self):
        """Uncollected invoices strictly produce UNRECONCILED status; phantom cash is eliminated."""
        self._create_match_with_status("mat_uncoll_03", "BILLABLE", 250000)
        inv = generate_monthly_invoice(self.db, "partner_gw_cleanhome", "2026-11")
        approve_and_issue_invoice(self.db, inv["invoice_id"], admin_id="ADMIN_HOEGI")

        # Unconditionally unreconciled when bank deposit is missing
        rec = reconcile_billing_period(self.db, "2026-11", reconciled_by="ADMIN_HOEGI")
        self.assertEqual(rec["status"], "UNRECONCILED")
        self.assertEqual(rec["discrepancy_amount"], 22000)
        self.assertEqual(rec["total_revenue_expected"], 22000)
        self.assertEqual(rec["total_revenue_collected"], 0)

    def test_04_full_deposit_produces_balanced_reconciliation(self):
        """Full bank deposit receipt marks invoice PAID and achieves BALANCED reconciliation."""
        self._create_match_with_status("mat_settled_04", "BILLABLE", 250000)
        inv = generate_monthly_invoice(self.db, "partner_gw_cleanhome", "2026-11")
        approve_and_issue_invoice(self.db, inv["invoice_id"], admin_id="ADMIN_HOEGI")

        record_invoice_payment(self.db, inv["invoice_id"], paid_amount=22000, bank_transfer_ref="KB_SETTLE_04_CONFIRMED")

        rec = reconcile_billing_period(self.db, "2026-11", reconciled_by="ADMIN_HOEGI")
        self.assertEqual(rec["status"], "BALANCED")
        self.assertEqual(rec["discrepancy_amount"], 0)
        self.assertEqual(rec["total_revenue_collected"], 22000)

    # -------------------------------------------------------------------------
    # 3. P0-08: Admin payment recording schema, validation, and uniqueness
    # -------------------------------------------------------------------------
    def test_05_duplicate_bank_transfer_ref_blocked(self):
        """Reusing the same bank transfer reference across multiple payments is strictly rejected (409)."""
        self._create_match_with_status("mat_dup_pay_05a", "BILLABLE", 200000)
        inv_a = generate_monthly_invoice(self.db, "partner_gw_cleanhome", "2026-11")
        approve_and_issue_invoice(self.db, inv_a["invoice_id"], admin_id="ADMIN_HOEGI")
        record_invoice_payment(self.db, inv_a["invoice_id"], paid_amount=17600, bank_transfer_ref="SHINHAN_TX_UNIQUE_99")

        # Create second invoice for another partner
        self._create_match_with_status("mat_dup_pay_05b", "BILLABLE", 200000, partner_id="partner_dj_topclean", slot_id="slot_dj_tomorrow_am")
        inv_b = generate_monthly_invoice(self.db, "partner_dj_topclean", "2026-11")
        approve_and_issue_invoice(self.db, inv_b["invoice_id"], admin_id="ADMIN_HOEGI")

        # Attempt to reuse the exact same bank transfer reference
        with self.assertRaises(EngineError) as ctx:
            record_invoice_payment(self.db, inv_b["invoice_id"], paid_amount=17600, bank_transfer_ref="SHINHAN_TX_UNIQUE_99")
        self.assertEqual(ctx.exception.code, "DUPLICATE_PAYMENT_REF")

    def test_06_invalid_bank_ref_length_rejected(self):
        """Bank transfer reference shorter than 4 characters is rejected (400)."""
        self._create_match_with_status("mat_short_ref", "BILLABLE", 200000)
        inv = generate_monthly_invoice(self.db, "partner_gw_cleanhome", "2026-11")
        approve_and_issue_invoice(self.db, inv["invoice_id"], admin_id="ADMIN_HOEGI")

        with self.assertRaises(EngineError) as ctx:
            record_invoice_payment(self.db, inv["invoice_id"], paid_amount=17600, bank_transfer_ref="123")
        self.assertEqual(ctx.exception.code, "INVALID_BANK_REF")

    # -------------------------------------------------------------------------
    # 4. P1-01: No false NTS verification claims
    # -------------------------------------------------------------------------
    def test_07_nts_verified_remains_false_without_live_api(self):
        """Even with external HomeTax approval string, nts_verified remains False."""
        self._create_match_with_status("mat_nts_07", "BILLABLE", 200000)
        inv = generate_monthly_invoice(self.db, "partner_gw_cleanhome", "2026-11")
        issued = approve_and_issue_invoice(self.db, inv["invoice_id"], hometax_approval_number="20261101-4100021-99887766")

        self.assertEqual(issued["status"], "ISSUED")
        self.assertEqual(issued["tax_invoice_number"], "20261101-4100021-99887766")
        self.assertFalse(issued["nts_verified"], "nts_verified must strictly remain False without live government API")

    # -------------------------------------------------------------------------
    # 5. P1-02: Payout approval document_ref requirement and duplicate protection
    # -------------------------------------------------------------------------
    def test_08_payout_approval_requires_document_ref(self):
        """Approving a payout requires a valid document_ref (>=4 chars); duplicate approval is blocked."""
        self._create_match_with_status("mat_payout_08", "BILLABLE", 200000, "partner_sl_master")
        pay = generate_monthly_payout(self.db, "partner_sl_master", "2026-11")

        # 1. Short or empty document_ref fails
        with self.assertRaises(EngineError) as ctx:
            approve_payout(self.db, pay["payout_id"], tax_invoice_verified=True, document_ref="abc")
        self.assertEqual(ctx.exception.code, "DOCUMENT_REF_REQUIRED")

        # 2. Valid document_ref succeeds
        app = approve_payout(self.db, pay["payout_id"], tax_invoice_verified=True, document_ref="HOMETAX_DOC_98765")
        self.assertEqual(app["status"], "APPROVED")
        self.assertEqual(app["document_ref"], "HOMETAX_DOC_98765")

        # 3. Duplicate approval on already APPROVED payout fails
        with self.assertRaises(EngineError) as ctx:
            approve_payout(self.db, pay["payout_id"], tax_invoice_verified=True, document_ref="HOMETAX_DOC_98765")
        self.assertEqual(ctx.exception.code, "INVALID_PAYOUT_STATUS")

    # -------------------------------------------------------------------------
    # 6. P1-03: KPI schema alignment with admin UI
    # -------------------------------------------------------------------------
    def test_09_kpis_returns_ui_expected_schema_and_excludes_completed(self):
        """get_financial_kpis returns nested kpis matching slotbridge/admin-billing.html bindings."""
        # 1 match COMPLETED (should NOT be counted in billable)
        self._create_match_with_status("mat_kpi_comp", "COMPLETED", 200000)
        # 1 match BILLABLE (should be counted in billable)
        self._create_match_with_status("mat_kpi_bill", "BILLABLE", 300000)

        res = get_financial_kpis(self.db, "2026-11")
        self.assertIn("kpis", res)
        k = res["kpis"]

        # Only BILLABLE match (300,000 * 0.08 = 24,000 KRW) should be in billable pool
        self.assertEqual(k["billable_matches_count"], 1)
        self.assertEqual(k["verified_billable_pool"], 24000)
        self.assertEqual(res["verified_billable_krw"], 24000)

        # Check all UI data keys exist
        expected_keys = [
            "pipeline_projected_revenue", "pipeline_inquiries_count",
            "verified_billable_pool", "billable_matches_count",
            "official_issued_invoiced", "issued_invoices_count",
            "reconciled_paid_cash", "paid_invoices_count"
        ]
        for ek in expected_keys:
            self.assertIn(ek, k)

    # -------------------------------------------------------------------------
    # 7. Static Code Audit on Worker JS
    # -------------------------------------------------------------------------
    def test_10_worker_js_static_audit(self):
        """Audit src/worker.js for complete elimination of bypasses and schema parity."""
        worker_path = os.path.join(os.path.dirname(__file__), "..", "src", "worker.js")
        with open(worker_path, "r", encoding="utf-8") as f:
            code = f.read()

        # 1. No strict_cash bypass in worker.js
        self.assertNotIn("strictCash", code)
        self.assertNotIn("strict_cash", code)

        # 2. No unverified COMPLETED status in invoice/payout/kpi queries
        self.assertNotIn("'VERIFIED', 'BILLABLE', 'COMPLETED'", code)

        # 3. Duplicate payment ref check present
        self.assertIn("DUPLICATE_PAYMENT_REF", code)

        # 4. Batch atomicity on payment recording and payout approval
        self.assertIn("env.DB.batch", code)

        # 5. Nested kpis object returned in /admin/kpis
        self.assertIn("kpis: kpis", code)

if __name__ == "__main__":
    unittest.main()
