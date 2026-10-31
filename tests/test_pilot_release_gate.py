"""
Unit and integration tests for SBK-FIX-10: Restricted Pilot & Release Gate.
Verifies the complete 10-step consent-based pilot with 1 Buyer, 1 Referrer, 1 Contractor,
and asserts strict BLOCKED_EXTERNAL fail-closed gating before operator offline sign-off.
"""

import os
import unittest

from scripts.pilot_restricted_e2e import run_restricted_pilot
from src.gate import PilotReleaseGate

class TestPilotReleaseGate(unittest.TestCase):
    def test_10_step_restricted_pilot_e2e(self):
        """Verifies the complete 10-step single-transaction pilot execution."""
        res = run_restricted_pilot(":memory:")
        self.assertTrue(res["ok"])
        self.assertEqual(len(res["logs"]), 12, "Should execute all onboarding, matching, fulfill, and ledger steps")
        self.assertEqual(res["reconciliation"]["status"], "BALANCED")
        self.assertEqual(res["reconciliation"]["discrepancy_amount"], 0)

    def test_release_gate_blocks_real_transactions_without_operator_approval(self):
        """Ensures real transactions are blocked by default fail-closed policy."""
        can_transact = PilotReleaseGate.check_can_process_real_transaction()
        self.assertFalse(can_transact, "Real transactions must be blocked without explicit operator approval")

    def test_release_gate_evaluates_to_blocked_external(self):
        """Ensures overall business status is strictly BLOCKED_EXTERNAL until offline gates pass."""
        gate_res = PilotReleaseGate.evaluate_release_status(test_suite_passed=True, dry_run_passed=True)
        self.assertEqual(gate_res["technical_status"], "PASS")
        self.assertEqual(gate_res["overall_status"], "BLOCKED_EXTERNAL")
        self.assertFalse(gate_res["operator_approval_granted"])
        self.assertFalse(gate_res["public_marketing_enabled"])
        self.assertFalse(gate_res["allow_real_pii"])
        self.assertFalse(gate_res["allow_real_payments"])

    def test_offline_gates_presence(self):
        """Verifies all 3 statutory offline gates are tracked."""
        gate_res = PilotReleaseGate.evaluate_release_status(test_suite_passed=True, dry_run_passed=True)
        gate_ids = [g["id"] for g in gate_res["offline_gates"]]
        self.assertIn("GATE-01", gate_ids) # 법률 자문
        self.assertIn("GATE-02", gate_ids) # 관악 시공 파트너 LOI
        self.assertIn("GATE-03", gate_ids) # 세무 사업자등록 및 전용 통장

if __name__ == "__main__":
    unittest.main()
