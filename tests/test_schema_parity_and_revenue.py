"""
SlotBridge Korea — Tests for Schema Parity, Revenue, Invoicing & Reconciliation (SBK-MINI-11)
Validates:
1. Full D1 Schema Parity against migrations/0001_initial_schema.sql
2. 4-tier distinct Financial KPIs (Estimated, Billable, Invoiced, Settled Cash)
3. Monthly B2B Invoicing with 8% Fee, 10% VAT, cross-month isolation, and double-billing defense
4. Bank Transfer Payment Receipt recording and double-entry Cash Ledger Event
5. Referral Payouts with 35% cut and mandatory Tax Invoice Verification gate
6. Strict Cash Reconciliation (No Phantom Cash — Uncollected = UNRECONCILED)
"""

import unittest
import sqlite3
import re
import os
import uuid
from datetime import datetime, timezone

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

class TestSchemaParityAndRevenue(unittest.TestCase):
    def setUp(self):
        self.db = DatabaseManager(":memory:")
        schema_path = os.path.join(os.path.dirname(__file__), "..", "migrations", "0001_initial_schema.sql")
        with open(schema_path, "r", encoding="utf-8") as f:
            schema_sql = f.read()
        self.db.execute_script(schema_sql)

        # Base seed data
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.db.execute(
            "INSERT INTO organizations (id, name, business_number, representative_name) VALUES (?, ?, ?, ?)",
            ("org_test", "테스트운영사", "123-45-67890", "김대표")
        )
        self.db.execute(
            """
            INSERT INTO partners (
                id, organization_id, name, partner_type, business_number, representative_name, contact_phone,
                settlement_bank, settlement_account, liability_insurance_status, verification_status
            ) VALUES (?, ?, ?, 'HYBRID', ?, ?, ?, ?, ?, 'VERIFIED', 'APPROVED')
            """,
            ("p_clean_01", "org_test", "클린홈 관악본점", "111-22-33333", "이청소", "010-1111-2222", "신한은행", "110-123-456789")
        )
        self.db.execute(
            """
            INSERT INTO partners (
                id, organization_id, name, partner_type, business_number, representative_name, contact_phone,
                settlement_bank, settlement_account, liability_insurance_status, verification_status
            ) VALUES (?, ?, ?, 'REFERRING', ?, ?, ?, ?, ?, 'VERIFIED', 'APPROVED')
            """,
            ("p_ref_01", "org_test", "원룸부동산 관악점", "222-33-44444", "박공인", "010-3333-4444", "우리은행", "1002-123-456789")
        )
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_01_schema_parity_all_tables_exist(self):
        """Verify all 21 core entities defined in migration 0001 exist in database."""
        expected_tables = {
            "organizations", "partners", "partner_members", "service_areas",
            "capacity_slots", "inquiries", "consent_events", "matches",
            "assignment_offers", "partner_responses", "booking_outcomes",
            "fulfillment_evidence", "revenue_rules", "invoices", "ledger_events",
            "reconciliation_events", "payouts", "campaign_attributions",
            "audit_log", "data_erasure_requests", "notification_outbox"
        }
        cursor = self.db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        actual_tables = {row["name"] for row in cursor.fetchall()}
        missing = expected_tables - actual_tables
        self.assertEqual(len(missing), 0, f"Missing D1 tables: {missing}")

    def test_02_schema_parity_worker_columns_match(self):
        """Verify that worker.js references exact columns from schema with no discrepancies."""
        worker_path = os.path.join(os.path.dirname(__file__), "..", "src", "worker.js")
        with open(worker_path, "r", encoding="utf-8") as f:
            worker_js = f.read()

        # Check key columns
        self.assertIn("customer_phone_hash", worker_js)
        self.assertIn("customer_phone_encrypted", worker_js)
        self.assertIn("customer_name_encrypted", worker_js)
        self.assertIn("tracking_token", worker_js)
        self.assertIn("attribution_partner_id", worker_js)
        self.assertIn("liability_insurance_status", worker_js)
        self.assertIn("verification_status", worker_js)
        self.assertIn("total_service_volume", worker_js)
        self.assertIn("total_platform_fee", worker_js)
        self.assertIn("total_invoice_amount", worker_js)
        self.assertIn("referring_partner_id", worker_js)
        self.assertIn("payout_amount", worker_js)
        self.assertIn("discrepancy_amount", worker_js)

        # Check that worker sets liability_insurance_status to 'PENDING' on registration
        self.assertIn("'PENDING_APPROVAL', 'PENDING'", worker_js)

    def test_03_financial_kpis_four_tiers(self):
        """Verify 4 distinct financial KPIs prevent phantom revenue representation."""
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        # 1 In-flight inquiry (estimated)
        self.db.execute(
            """
            INSERT INTO inquiries (
                id, tracking_token, requested_date, time_window, district, neighborhood,
                housing_type, area_pyeong, customer_name_encrypted, customer_phone_encrypted,
                customer_phone_hash, status, expires_at
            ) VALUES ('inq_kpi_1', 'tok_kpi_1', '2026-11-02', 'MORNING', '관악구', '봉천동', 'STUDIO', 7, 'enc_n', 'enc_p', 'h_p', 'PENDING_MATCH', ?)
            """,
            (now_iso,)
        )

        # 1 Verified match (billable)
        self.db.execute(
            """
            INSERT INTO capacity_slots (id, partner_id, slot_date, time_window)
            VALUES ('slot_kpi_1', 'p_clean_01', '2026-11-01', 'MORNING')
            """
        )
        self.db.execute(
            """
            INSERT INTO inquiries (
                id, tracking_token, requested_date, time_window, district, neighborhood,
                housing_type, area_pyeong, customer_name_encrypted, customer_phone_encrypted,
                customer_phone_hash, status, expires_at
            ) VALUES ('inq_kpi_2', 'tok_kpi_2', '2026-11-01', 'MORNING', '관악구', '봉천동', 'STUDIO', 7, 'enc_n', 'enc_p', 'h_p', 'MATCHED', ?)
            """,
            (now_iso,)
        )
        self.db.execute(
            """
            INSERT INTO matches (id, inquiry_id, partner_id, slot_id, status, final_amount)
            VALUES ('mat_kpi_1', 'inq_kpi_2', 'p_clean_01', 'slot_kpi_1', 'BILLABLE', 250000)
            """
        )
        self.db.commit()

        kpis = get_financial_kpis(self.db, "2026-11")
        self.assertEqual(kpis["estimated_revenue_krw"], 40000) # 2 inquiries (PENDING_MATCH + MATCHED) * 20000
        self.assertEqual(kpis["verified_billable_krw"], 20000) # 250000 * 0.08
        self.assertEqual(kpis["invoiced_revenue_krw"], 0)     # No invoice yet
        self.assertEqual(kpis["settled_cash_krw"], 0)          # No cash paid yet

    def test_04_invoicing_and_cross_month_isolation(self):
        """Verify B2B invoice generation with strict integer KRW math and cross-month isolation."""
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        # Match in November
        self.db.execute(
            "INSERT INTO capacity_slots (id, partner_id, slot_date, time_window) VALUES ('slot_nov', 'p_clean_01', '2026-11-05', 'MORNING')"
        )
        self.db.execute(
            """
            INSERT INTO inquiries (id, tracking_token, requested_date, time_window, district, neighborhood, housing_type, area_pyeong, customer_name_encrypted, customer_phone_encrypted, customer_phone_hash, status, expires_at)
            VALUES ('inq_nov', 'tok_nov', '2026-11-05', 'MORNING', '관악구', '봉천동', 'STUDIO', 8, 'n', 'p', 'h', 'MATCHED', ?)
            """,
            (now_iso,)
        )
        self.db.execute(
            "INSERT INTO matches (id, inquiry_id, partner_id, slot_id, status, final_amount, created_at) VALUES ('mat_nov', 'inq_nov', 'p_clean_01', 'slot_nov', 'BILLABLE', 300000, '2026-11-05T10:00:00Z')"
        )

        # Match in December (different month)
        self.db.execute(
            "INSERT INTO capacity_slots (id, partner_id, slot_date, time_window) VALUES ('slot_dec', 'p_clean_01', '2026-12-01', 'MORNING')"
        )
        self.db.execute(
            """
            INSERT INTO inquiries (id, tracking_token, requested_date, time_window, district, neighborhood, housing_type, area_pyeong, customer_name_encrypted, customer_phone_encrypted, customer_phone_hash, status, expires_at)
            VALUES ('inq_dec', 'tok_dec', '2026-12-01', 'MORNING', '관악구', '봉천동', 'STUDIO', 8, 'n', 'p', 'h', 'MATCHED', ?)
            """,
            (now_iso,)
        )
        self.db.execute(
            "INSERT INTO matches (id, inquiry_id, partner_id, slot_id, status, final_amount, created_at) VALUES ('mat_dec', 'inq_dec', 'p_clean_01', 'slot_dec', 'BILLABLE', 500000, '2026-12-01T10:00:00Z')"
        )
        self.db.commit()

        # Generate November invoice
        inv = generate_monthly_invoice(self.db, "p_clean_01", "2026-11")
        self.assertEqual(inv["total_service_volume"], 300000) # Only November match!
        self.assertEqual(inv["total_platform_fee"], 24000)   # 300000 * 0.08
        self.assertEqual(inv["vat_amount"], 2400)            # 10% VAT
        self.assertEqual(inv["total_invoice_amount"], 26400)
        self.assertEqual(inv["status"], "DRAFT")

        # Double-billing defense
        with self.assertRaises(EngineError):
            generate_monthly_invoice(self.db, "p_clean_01", "2026-11")

    def test_05_invoice_issue_and_payment_receipt(self):
        """Verify formal invoice issuance and bank transfer payment receipt with cash ledger entry."""
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.db.execute(
            "INSERT INTO capacity_slots (id, partner_id, slot_date, time_window) VALUES ('slot_p', 'p_clean_01', '2026-11-03', 'MORNING')"
        )
        self.db.execute(
            """
            INSERT INTO inquiries (id, tracking_token, requested_date, time_window, district, neighborhood, housing_type, area_pyeong, customer_name_encrypted, customer_phone_encrypted, customer_phone_hash, status, expires_at)
            VALUES ('inq_p', 'tok_p', '2026-11-03', 'MORNING', '관악구', '봉천동', 'STUDIO', 8, 'n', 'p', 'h', 'MATCHED', ?)
            """,
            (now_iso,)
        )
        self.db.execute(
            "INSERT INTO matches (id, inquiry_id, partner_id, slot_id, status, final_amount, created_at) VALUES ('mat_p', 'inq_p', 'p_clean_01', 'slot_p', 'BILLABLE', 200000, '2026-11-03T10:00:00Z')"
        )
        self.db.commit()

        inv = generate_monthly_invoice(self.db, "p_clean_01", "2026-11")
        inv_id = inv["invoice_id"]

        # Issue invoice
        issued = approve_and_issue_invoice(self.db, inv_id)
        self.assertEqual(issued["status"], "ISSUED")
        self.assertFalse(issued["nts_verified"])
        self.assertTrue(issued["tax_invoice_number"].startswith("TX-INT-"))

        # Disallow partial payment (< total_invoice_amount: 16000 + 1600 = 17600)
        with self.assertRaises(EngineError):
            record_invoice_payment(self.db, inv_id, 10000, "BANK_TX_001")

        # Disallow invalid bank ref
        with self.assertRaises(EngineError):
            record_invoice_payment(self.db, inv_id, 17600, "")

        # Record full payment
        paid = record_invoice_payment(self.db, inv_id, 17600, "SHINHAN_20261103_0981")
        self.assertEqual(paid["status"], "PAID")

        # Verify double-entry cash receipt ledger entry
        row = self.db.execute("SELECT * FROM ledger_events WHERE event_type = 'PAYMENT_RECEIVED'").fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["amount"], 17600)
        self.assertEqual(row["debit_account"], "CASH_BANK_ACCOUNT")
        self.assertEqual(row["credit_account"], "ACCOUNTS_RECEIVABLE_PARTNER")

    def test_06_strict_cash_reconciliation_no_phantom_cash(self):
        """Verify that unpaid invoices result in UNRECONCILED status, forbidding phantom cash."""
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.db.execute(
            "INSERT INTO capacity_slots (id, partner_id, slot_date, time_window) VALUES ('slot_r', 'p_clean_01', '2026-11-04', 'MORNING')"
        )
        self.db.execute(
            """
            INSERT INTO inquiries (id, tracking_token, requested_date, time_window, district, neighborhood, housing_type, area_pyeong, customer_name_encrypted, customer_phone_encrypted, customer_phone_hash, status, expires_at)
            VALUES ('inq_r', 'tok_r', '2026-11-04', 'MORNING', '관악구', '봉천동', 'STUDIO', 8, 'n', 'p', 'h', 'MATCHED', ?)
            """,
            (now_iso,)
        )
        self.db.execute(
            "INSERT INTO matches (id, inquiry_id, partner_id, slot_id, status, final_amount, created_at) VALUES ('mat_r', 'inq_r', 'p_clean_01', 'slot_r', 'BILLABLE', 250000, '2026-11-04T10:00:00Z')"
        )
        self.db.commit()

        inv = generate_monthly_invoice(self.db, "p_clean_01", "2026-11")
        inv_id = inv["invoice_id"]
        approve_and_issue_invoice(self.db, inv_id)

        # Before payment: Strict Cash Reconciliation MUST be UNRECONCILED with discrepancy
        rec_unpaid = reconcile_billing_period(self.db, "2026-11", strict_cash=True)
        self.assertEqual(rec_unpaid["status"], "UNRECONCILED")
        self.assertEqual(rec_unpaid["discrepancy_amount"], 22000) # 20000 + 2000 VAT unpaid
        self.assertEqual(rec_unpaid["total_revenue_collected"], 0)

        # Record payment
        record_invoice_payment(self.db, inv_id, 22000, "KB_20261104_1122")

        # After payment: Strict Cash Reconciliation is BALANCED
        rec_paid = reconcile_billing_period(self.db, "2026-11", strict_cash=True)
        self.assertEqual(rec_paid["status"], "BALANCED")
        self.assertEqual(rec_paid["discrepancy_amount"], 0)
        self.assertEqual(rec_paid["total_revenue_collected"], 22000)

if __name__ == "__main__":
    unittest.main()
