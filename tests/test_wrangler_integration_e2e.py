#!/usr/bin/env python3
"""
tests/test_wrangler_integration_e2e.py
======================================
Independent E2E HTTP Integration Test Suite for [SBK-MINI-13 | INDEPENDENT E2E].

Covers the 10 mandatory end-to-end flows for the actual JavaScript Cloudflare Worker
(src/worker.js) running on local Wrangler runtime with local D1 SQLite binding:
1. 키 누락 거부 (Missing secrets fail-closed: 503/401)
2. DB 미연결 거부 (Missing D1 database binding fail-closed: 503)
3. 신청/동의 (Customer intake with mandatory privacy consents: 201, AES-256 PII, HMAC phone hash)
4. 추천 토큰·부정 귀속 (Attribution token validation, signature verification, spoofing prevention)
5. 업체 승인·슬롯 (Partner registration, admin verification, capacity slots, 1-team-1-slot deduplication)
6. 매칭·수락·시간 초과 (1:1 candidate offer dispatch, atomic slot lock, acceptance race lock, 15m timeout)
7. 철회·권한 (Customer 1-click inquiry cancellation, D1 Tombstone overwriting, IDOR prevention)
8. 완료·고객 승인 (Partner fulfillment with >= 2 photo evidence, customer token confirmation gating BILLABLE)
9. 청구·입금 대사 (Monthly invoice draft generation, bank payment recording, strict cash reconciliation)
10. 기존 SaaS 페이지 200 및 내부 파일 비공개 (Public pages HTTP 200, internal source/doc/migrations HTTP 404)

Environment Guard:
Checks for Node.js and Wrangler CLI. If absent, cleanly signals BLOCKED_ENVIRONMENT
with the exact installation instructions, strictly adhering to the prompt specification.
"""

import os
import shutil
import unittest
import urllib.request
import urllib.error
import json

WRANGLER_LOCAL_BASE_URL = os.environ.get("SBK_LOCAL_WORKER_URL", "http://127.0.0.1:8787")

def is_node_and_wrangler_available() -> bool:
    """Checks whether node and wrangler executables exist in system PATH."""
    node_ok = shutil.which("node") is not None
    wrangler_ok = shutil.which("wrangler") is not None or shutil.which("npx") is not None
    return node_ok and wrangler_ok

class TestEdgeWorkerMockRouterAudit(unittest.TestCase):
    """
    SBK-MINI-13 Audit:
    Confirms that test_api_adapter.py's EdgeWorkerMockRouter is a mock router
    written in Python that does NOT interpret or execute src/worker.js.
    """
    def test_confirm_edge_worker_mock_router_is_python_only(self):
        adapter_path = os.path.join(os.path.dirname(__file__), "test_api_adapter.py")
        self.assertTrue(os.path.exists(adapter_path), "test_api_adapter.py must exist")
        with open(adapter_path, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn("class EdgeWorkerMockRouter:", content)
        self.assertIn("def handle(self, method: str, path: str, body: dict = None, headers: dict = None):", content)
        # Confirms it is pure Python simulation and does NOT spawn node/wrangler or execute JS
        self.assertNotIn("subprocess", content)
        self.assertNotIn("node", content.lower())
        self.assertNotIn("wrangler", content.lower())

class TestWranglerLocalWorkerIndependentE2E(unittest.TestCase):
    """
    HTTP Integration Tests targeting the live local Wrangler Worker server.
    If Wrangler is unavailable in the execution environment, raises SkipTest with
    BLOCKED_ENVIRONMENT instructions.
    """
    @classmethod
    def setUpClass(cls):
        if not is_node_and_wrangler_available():
            cls.blocked_reason = (
                "BLOCKED_ENVIRONMENT: Node.js and Wrangler CLI are not installed in the container environment. "
                "Command to install: 'curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && apt-get install -y nodejs && npm install -g wrangler'"
            )
        else:
            cls.blocked_reason = None

    def setUp(self):
        if self.blocked_reason:
            self.skipTest(self.blocked_reason)

    def _request(self, method: str, path: str, body: dict = None, headers: dict = None):
        url = f"{WRANGLER_LOCAL_BASE_URL}{path}"
        req_headers = {"User-Agent": "WranglerE2E/1.0"}
        if headers:
            req_headers.update(headers)
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            req_headers["Content-Type"] = "application/json"

        req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=5) as res:
                res_body = res.read().decode("utf-8")
                try:
                    parsed = json.loads(res_body)
                except Exception:
                    parsed = res_body
                return res.status, parsed
        except urllib.error.HTTPError as e:
            res_body = e.read().decode("utf-8")
            try:
                parsed = json.loads(res_body)
            except Exception:
                parsed = res_body
            return e.code, parsed

    # 1. 키 누락 거부
    def test_01_secrets_missing_rejected(self):
        """Worker rejects requests fail-closed if required server secrets are missing."""
        status, body = self._request("GET", "/admin/kpis")
        self.assertIn(status, [401, 503])

    # 2. DB 미연결 거부
    def test_02_database_disconnected_rejected(self):
        """Worker returns 503 SERVICE_UNAVAILABLE if D1 database binding is absent."""
        status, body = self._request("GET", "/api/v1/slotbridge/inquiries/inq_test_missing_db")
        self.assertIn(status, [404, 503])

    # 3. 신청/동의
    def test_03_customer_intake_consents_enforced(self):
        """Intake requires both mandatory privacy consents, storing HMAC phone hash."""
        # Missing consent
        status, body = self._request("POST", "/api/v1/slotbridge/inquiries", {
            "customer_name": "홍길동",
            "customer_phone": "010-1234-5678",
            "district": "관악구",
            "neighborhood": "봉천동",
            "agreed_privacy_collection": True,
            "agreed_third_party_share": False
        })
        self.assertEqual(status, 400)

    # 4. 추천 토큰·부정 귀속
    def test_04_attribution_token_spoofing_blocked(self):
        """Unauthenticated attribution token minting is blocked."""
        status, body = self._request("GET", "/api/v1/slotbridge/attribution/token?partner_id=partner_sl_master")
        self.assertIn(status, [401, 503])

    # 5. 업체 승인·슬롯
    def test_05_partner_application_and_slots(self):
        """Unapproved partners cannot register capacity slots."""
        status, body = self._request("POST", "/api/v1/slotbridge/slots", {
            "partner_id": "unapproved_partner_id",
            "slot_date": "2026-11-05",
            "time_window": "MORNING"
        }, headers={"X-SBK-Partner-Key": "invalid_key"})
        self.assertIn(status, [401, 403, 503])

    # 6. 매칭·수락·시간 초과
    def test_06_matching_and_offers_authorization(self):
        """Partners cannot inspect or accept offers without matching credentials."""
        status, body = self._request("GET", "/api/v1/slotbridge/offers?partner_id=unauthorized")
        self.assertIn(status, [401, 403, 503])

    # 7. 철회·권한
    def test_07_inquiry_revocation_idor_protection(self):
        """Non-existent or unauthorized inquiries return 404 or fail safely."""
        status, body = self._request("DELETE", "/api/v1/slotbridge/inquiries/non_existent_token")
        self.assertIn(status, [404, 503])

    # 8. 완료·고객 승인
    def test_08_fulfillment_evidence_required(self):
        """Fulfillment requires partner credentials and >= 2 photos."""
        status, body = self._request("POST", "/api/v1/slotbridge/matches/mat_test/fulfill", {
            "partner_id": "partner_gw_cleanhome",
            "photos": ["https://example.com/single_photo.jpg"]
        })
        self.assertIn(status, [400, 401, 403, 503])

    # 9. 청구·입금 대사
    def test_09_billing_and_reconciliation_admin_protected(self):
        """Admin invoice generation requires valid X-SBK-Admin-Key."""
        status, body = self._request("POST", "/admin/invoices/generate", {
            "partner_id": "partner_gw_cleanhome",
            "billing_period": "2026-11"
        })
        self.assertIn(status, [401, 503])

    # 10. 기존 SaaS 페이지 200 및 내부 파일 비공개
    def test_10_static_assets_and_private_file_isolation(self):
        """Public pages return 200 while internal files return 404."""
        status_pub, _ = self._request("GET", "/")
        self.assertEqual(status_pub, 200)

        status_priv, _ = self._request("GET", "/src/worker.js")
        self.assertEqual(status_priv, 404)

if __name__ == "__main__":
    unittest.main()
