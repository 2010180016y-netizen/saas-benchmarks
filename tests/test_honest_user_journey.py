#!/usr/bin/env python3
"""
tests/test_honest_user_journey.py
==================================
Verification Suite for [SBK-MINI-12 | HONEST USER JOURNEY].

Verifies:
1. slotbridge/inquiry.html: Duplicate submission prevention, no fake demo fallback on API failure, honest error alert and button re-enable.
2. slotbridge/status.html: Strict separation of demo vs real token, D1 status loading, 404 handling, revocation fails honestly without demo fake-out.
3. slotbridge/partner-apply.html: Duplicate submission prevention, no fallback demo notice on API failure, real error rendering and button re-enable.
4. slotbridge/partner-slots.html: Dynamic D1 slot loading (GET /slots), real slot deletion (DELETE /slots/:id), honest error handling for offers and slots.
5. slotbridge/partner-dashboard.html: Clear demo isolation, honest link copy prompt.
6. slotbridge/admin-billing.html: Real admin API integration (GET /kpis, POST /invoices/generate, POST /invoices/:id/issue, POST /invoices/:id/record-payment, POST /payouts/:id/approve) with X-SBK-Admin-Key, no fake alerts.
"""

import os
import re
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SLOTBRIDGE_DIR = os.path.join(REPO_ROOT, "slotbridge")

class TestHonestUserJourney(unittest.TestCase):
    def setUp(self):
        self.inquiry_path = os.path.join(SLOTBRIDGE_DIR, "inquiry.html")
        self.status_path = os.path.join(SLOTBRIDGE_DIR, "status.html")
        self.partner_apply_path = os.path.join(SLOTBRIDGE_DIR, "partner-apply.html")
        self.partner_slots_path = os.path.join(SLOTBRIDGE_DIR, "partner-slots.html")
        self.partner_dash_path = os.path.join(SLOTBRIDGE_DIR, "partner-dashboard.html")
        self.admin_billing_path = os.path.join(SLOTBRIDGE_DIR, "admin-billing.html")

    def test_01_inquiry_honest_error_handling_and_no_fake_demo(self):
        """inquiry.html must prevent double-clicks and NEVER redirect to demo mode on API failure."""
        with open(self.inquiry_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Double-click prevention
        self.assertIn("submitBtn.disabled = true", content)
        self.assertIn("submitBtn.textContent = '신청 접수 중...'", content)

        # No fallthrough fake demo redirect on failure
        self.assertNotIn("status.html?demo=true&token=", content)

        # Re-enables button on error
        self.assertIn("submitBtn.disabled = false", content)
        self.assertIn("신청 접수 실패", content)

    def test_02_status_honest_isolation_and_revocation(self):
        """status.html must strictly isolate demo tokens, load real D1 status, and fail revocation honestly."""
        with open(self.status_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Demo token vs Real token separation
        self.assertIn("isDemo = params.get('demo') === 'true'", content)
        self.assertIn("demo-banner", content)
        self.assertIn("404", content)
        self.assertIn("503", content)

        # Real revocation must not fall back to demo success on API failure
        self.assertIn("fetch('/api/v1/slotbridge/inquiries/' + encodeURIComponent(token), { method: 'DELETE' }", content)
        self.assertIn("취소 처리 실패", content)
        self.assertIn("revokeBtn.disabled = false", content)

    def test_03_partner_apply_honest_error_and_no_fake_loi(self):
        """partner-apply.html must prevent double submit and never claim intent confirmed when API fails."""
        with open(self.partner_apply_path, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn("submitBtn.disabled = true", content)
        self.assertIn("submitBtn.textContent = '제휴 신청서 전송 중...'", content)

        # No fake fallback claiming intent confirmed on API failure
        self.assertNotIn("Fallback Demo Notice", content)
        self.assertNotIn("[사전 접수 의향 확인 — 시연 모드]", content)

        # Displays real failure message and re-enables button
        self.assertIn("파트너 신청 접수 실패", content)
        self.assertIn("submitBtn.disabled = false", content)

    def test_04_partner_slots_dynamic_d1_and_real_deletion(self):
        """partner-slots.html must dynamically fetch slots from D1 and delete via real API."""
        with open(self.partner_slots_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Dynamic slots loading from API
        self.assertIn("fetch(`/api/v1/slotbridge/slots?partner_id=${encodeURIComponent(partnerId)}`)", content)
        self.assertIn("loadSlots()", content)

        # Real delete API call
        self.assertIn("fetch('/api/v1/slotbridge/slots/' + encodeURIComponent(slotId)", content)
        self.assertIn("method: 'DELETE'", content)
        self.assertNotIn("onclick=\"alert('슬롯이 취소되었습니다.')\"", content)

        # Honest offer acceptance / rejection
        self.assertIn("오퍼 수락 실패", content)
        self.assertIn("오퍼 거절 실패", content)

    def test_05_partner_dashboard_mockup_isolation(self):
        """partner-dashboard.html must remain an isolated mockup without pretending live trading."""
        with open(self.partner_dash_path, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn("데모 모드", content)
        self.assertIn("시연용 예시", content)
        self.assertIn("시연 모드", content)

    def test_06_admin_billing_real_api_integration(self):
        """admin-billing.html must connect to real admin APIs with X-SBK-Admin-Key and eliminate fake alerts."""
        with open(self.admin_billing_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Fake alerts eliminated
        self.assertNotIn("alert('전자세금계산서 연동 및 청구서가 발행(ISSUED)되었습니다.')", content)
        self.assertNotIn("alert('입금 확인 처리되었습니다.')", content)
        self.assertNotIn("alert('세금계산서 대조 완료: 지급 승인(APPROVED) 처리되었습니다.')", content)
        self.assertNotIn("alert('[데모 모드] 실제 D1 API 연동(FIX-02/07) 전이므로 청구서가 생성되지 않습니다.')", content)

        # Real Admin API endpoints hooked up
        self.assertIn("X-SBK-Admin-Key", content)
        self.assertIn("/api/v1/slotbridge/admin/kpis", content)
        self.assertIn("/api/v1/slotbridge/admin/invoices/generate", content)
        self.assertIn("/api/v1/slotbridge/admin/invoices/${encodeURIComponent(invoiceId)}/issue", content)
        self.assertIn("/api/v1/slotbridge/admin/invoices/${encodeURIComponent(invoiceId)}/record-payment", content)
        self.assertIn("/api/v1/slotbridge/admin/payouts/${encodeURIComponent(payoutId)}/approve", content)

if __name__ == "__main__":
    unittest.main()
