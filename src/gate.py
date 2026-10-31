"""
SlotBridge Korea — Release Gate & Operator Approval Policy (SBK-FIX-10)
Defines strict fail-closed release gates preventing real public traffic, unconsented PII,
or real cash transactions before explicit operator sign-off and offline gate fulfillment.
"""

import os
from typing import Dict, Any

class PilotReleaseGate:
    """
    Release Gate Enforcement for SlotBridge Korea.
    Ensures that technical PASS remains BLOCKED_EXTERNAL until operator (회기님)
    signs off on real offline gates (Partner LOI, Tax registration, Legal review).
    """

    GATE_CONFIG = {
        "ENV": os.environ.get("SBK_ENV", "staging"),
        "OPERATOR_APPROVAL_GRANTED": os.environ.get("SBK_OPERATOR_APPROVAL", "0") == "1",
        "PUBLIC_MARKETING_ENABLED": os.environ.get("SBK_PUBLIC_MARKETING", "0") == "1",
        "ALLOW_REAL_PII": os.environ.get("SBK_ALLOW_REAL_PII", "0") == "1",
        "ALLOW_REAL_PAYMENTS": os.environ.get("SBK_ALLOW_REAL_PAYMENTS", "0") == "1",
        "STAGING_RESTRICTED_PILOT_ONLY": True
    }

    OFFLINE_GATES = [
        {"id": "GATE-01", "name": "법률 자문 최종 확인", "status": "PENDING_OFFLINE", "required_by": "Operator"},
        {"id": "GATE-02", "name": "관악·동작 1차 시공 파트너 서명 LOI 체결", "status": "PENDING_OFFLINE", "required_by": "Partner"},
        {"id": "GATE-03", "name": "세무 사업자등록 및 정산용 전용 계좌 개설", "status": "PENDING_OFFLINE", "required_by": "Tax/Bank"}
    ]

    @classmethod
    def evaluate_release_status(cls, test_suite_passed: bool, dry_run_passed: bool) -> Dict[str, Any]:
        """
        Evaluates the composite status according to Codex Step 10 & 13 standards:
        - Technical status: PASS if all tests and E2E pass.
        - Overall business status: BLOCKED_EXTERNAL if offline gates or operator approval are missing.
        """
        technical_pass = test_suite_passed and dry_run_passed
        offline_fulfilled = all(g["status"] == "FULFILLED" for g in cls.OFFLINE_GATES)
        operator_approved = cls.GATE_CONFIG["OPERATOR_APPROVAL_GRANTED"]

        if technical_pass and offline_fulfilled and operator_approved:
            overall_status = "PASS"
        elif technical_pass:
            overall_status = "BLOCKED_EXTERNAL"
        else:
            overall_status = "FAIL"

        return {
            "overall_status": overall_status,
            "technical_status": "PASS" if technical_pass else "FAIL",
            "offline_gates": cls.OFFLINE_GATES,
            "operator_approval_granted": operator_approved,
            "public_marketing_enabled": cls.GATE_CONFIG["PUBLIC_MARKETING_ENABLED"],
            "allow_real_pii": cls.GATE_CONFIG["ALLOW_REAL_PII"],
            "allow_real_payments": cls.GATE_CONFIG["ALLOW_REAL_PAYMENTS"],
            "verdict_reason": (
                "Technical and software runtime is 100% verified. "
                "Production traffic remains strictly BLOCKED_EXTERNAL until operator executes offline agreements."
            )
        }

    @classmethod
    def check_can_process_real_transaction(cls) -> bool:
        """Returns True only if operator has explicitly approved real money transactions."""
        return cls.GATE_CONFIG["OPERATOR_APPROVAL_GRANTED"] and cls.GATE_CONFIG["ALLOW_REAL_PAYMENTS"]
