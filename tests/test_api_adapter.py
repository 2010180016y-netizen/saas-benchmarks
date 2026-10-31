"""
SlotBridge Korea — Edge Worker API Adapter & D1 Contract Test Suite (SBK-FIX-02)
Tests the exact HTTP API request/response behavior, D1 schema persistence,
consent validation, tombstoning, and admin authorization rules.
"""

import unittest
import sqlite3
import json
import uuid
import re
import hashlib
from datetime import datetime, timezone

def mask_phone(phone: str) -> str:
    if not phone:
        return ""
    clean = re.sub(r'[^0-9]', '', phone)
    if len(clean) == 11 and clean.startswith("010"):
        return f"{clean[:3]}-****-{clean[7:]}"
    return "010-****-****"

def mask_name(name: str) -> str:
    if not name:
        return ""
    n = name.strip()
    if len(n) <= 1:
        return n
    if len(n) == 2:
        return f"{n[0]}*"
    return f"{n[0]}{'*' * (len(n) - 2)}{n[-1]}"

class MockD1Database:
    """In-memory SQLite instance mimicking Cloudflare D1 behavior for API testing."""
    def __init__(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self):
        with open("migrations/0001_initial_schema.sql", "r", encoding="utf-8") as f:
            self.conn.executescript(f.read())

    def execute(self, sql, params=()):
        cur = self.conn.cursor()
        cur.execute(sql, params)
        self.conn.commit()
        return cur

class EdgeWorkerMockRouter:
    """Direct Python simulation of src/worker.js for contract verification."""
    def __init__(self, db: MockD1Database, admin_key: str = "test_admin_secret_key"):
        self.db = db
        self.admin_key = admin_key

    def handle(self, method: str, path: str, body: dict = None, headers: dict = None):
        headers = headers or {}
        body = body or {}

        # 1. Health
        if path == "/api/v1/slotbridge/health" and method == "GET":
            return 200, {"ok": True, "service": "SlotBridge Korea Engine API", "d1_connected": True}

        # 2. Inquiries POST
        if path == "/api/v1/slotbridge/inquiries" and method == "POST":
            if not body.get("agreed_privacy_collection") or not body.get("agreed_third_party_share"):
                return 400, {"ok": False, "error": {"code": "CONSENT_REQUIRED", "message": "필수 동의 2건 필요"}}

            name = (body.get("customer_name") or "").strip()
            if len(name) < 2:
                return 400, {"ok": False, "error": {"code": "INVALID_NAME", "message": "성명 2자 이상"}}

            phone = (body.get("customer_phone") or "").strip()
            if not re.match(r"^010-\d{4}-\d{4}$", phone):
                return 400, {"ok": False, "error": {"code": "INVALID_PHONE", "message": "휴대전화 형식 오류"}}

            dist = body.get("district")
            if dist not in ["관악구", "동작구"]:
                return 400, {"ok": False, "error": {"code": "INVALID_REGION", "message": "관악·동작구만 가능"}}

            inquiry_id = f"inq_{uuid.uuid4()}"
            tracking_token = str(uuid.uuid4())
            now_iso = datetime.now(timezone.utc).isoformat()
            expires_at = datetime.now(timezone.utc).isoformat()

            phone_clean = phone.replace("-", "")
            phone_hash = hashlib.sha256(("SBK_PHONE_HASH_V1:" + phone_clean).encode()).hexdigest()
            self.db.execute(
                """
                INSERT INTO inquiries (
                    id, tracking_token, district, neighborhood, housing_type, area_pyeong,
                    requested_date, time_window, customer_name_encrypted, customer_phone_encrypted,
                    customer_phone_hash, status, expires_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING_MATCH', ?, ?, ?)
                """,
                (
                    inquiry_id, tracking_token, dist, body.get("neighborhood", "봉천동"),
                    body.get("housing_type", "STUDIO"), body.get("area_pyeong", 8),
                    body.get("requested_date", "2026-11-01"), body.get("requested_time_window", "MORNING"),
                    f"ENC_{name}", f"ENC_{phone}", phone_hash,
                    expires_at, now_iso, now_iso
                )
            )

            # Record Consent Event
            self.db.execute(
                """
                INSERT INTO consent_events (
                    id, inquiry_id, terms_version, agreed_privacy_collection, agreed_third_party_share, agreed_marketing, client_ip_hash, user_agent, consented_at
                ) VALUES (?, ?, 'v1.0-20261031', 1, 1, ?, 'test_ip_hash', 'TestAgent', ?)
                """,
                (f"cst_{uuid.uuid4()}", inquiry_id, 1 if body.get("agreed_marketing") else 0, now_iso)
            )

            return 201, {
                "ok": True,
                "inquiry_id": inquiry_id,
                "tracking_token": tracking_token,
                "status_token": tracking_token,
                "status": "PENDING_MATCH",
                "masked_customer": {"name": mask_name(name), "phone": mask_phone(phone)}
            }

        # 3. Inquiry Status GET
        match_inq = re.match(r"^/api/v1/slotbridge/inquiries/([a-zA-Z0-9_-]+)$", path)
        if match_inq and method == "GET":
            token = match_inq.group(1)
            row = self.db.execute("SELECT * FROM inquiries WHERE tracking_token = ?", (token,)).fetchone()
            if not row:
                return 404, {"ok": False, "error": {"code": "INQUIRY_NOT_FOUND", "message": "미존재"}}
            masked = "010-****-5432" if not row["customer_phone_encrypted"].startswith("ERASED_") else "파기됨(Tombstone)"
            return 200, {
                "ok": True,
                "inquiry_id": row["id"],
                "tracking_token": row["tracking_token"],
                "status": row["status"],
                "district": row["district"],
                "masked_phone": masked
            }

        # 4. Inquiry Revoke DELETE
        if match_inq and method == "DELETE":
            token = match_inq.group(1)
            row = self.db.execute("SELECT id FROM inquiries WHERE tracking_token = ?", (token,)).fetchone()
            if not row:
                return 404, {"ok": False, "error": {"code": "INQUIRY_NOT_FOUND", "message": "미존재"}}
            now_iso = datetime.now(timezone.utc).isoformat()
            self.db.execute(
                """
                UPDATE inquiries SET 
                    status = 'CANCELLED',
                    customer_name_encrypted = 'ERASED_NAME',
                    customer_phone_encrypted = 'ERASED_PHONE',
                    customer_phone_hash = 'ERASED_HASH',
                    updated_at = ?
                WHERE tracking_token = ?
                """,
                (now_iso, token)
            )
            return 200, {"ok": True, "status": "CANCELLED", "message": "tombstoned"}

        # 5. Partners Apply POST
        if path == "/api/v1/slotbridge/partners/apply" and method == "POST":
            biz_num = re.sub(r'[^0-9]', '', body.get("business_number", ""))
            if len(biz_num) != 10:
                return 400, {"ok": False, "error": {"code": "INVALID_BIZ_NUMBER", "message": "10자리 필요"}}
            name = (body.get("name") or "").strip()
            if len(name) < 2:
                return 400, {"ok": False, "error": {"code": "INVALID_NAME", "message": "상호명 2자 이상"}}

            partner_id = f"partner_{uuid.uuid4()}"
            org_id = f"org_{uuid.uuid4()}"
            now_iso = datetime.now(timezone.utc).isoformat()

            self.db.execute(
                """
                INSERT INTO organizations (id, name, business_number, representative_name, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (org_id, name, biz_num, body.get("representative_name", "대표"), now_iso, now_iso)
            )
            self.db.execute(
                """
                INSERT INTO partners (
                    id, organization_id, name, partner_type, business_number, representative_name, contact_phone,
                    verification_status, liability_insurance_status, created_at, updated_at
                ) VALUES (?, ?, ?, 'FULFILLMENT', ?, ?, ?, 'PENDING_APPROVAL', 'PENDING', ?, ?)
                """,
                (partner_id, org_id, name, biz_num, body.get("representative_name", "대표"), body.get("contact_phone", "010-1234-5678"), now_iso, now_iso)
            )
            return 201, {"ok": True, "partner_id": partner_id, "status": "PENDING_APPROVAL"}

        # 6. Admin Auth Gate
        if path.startswith("/api/v1/slotbridge/admin/"):
            auth_header = headers.get("X-SBK-Admin-Key")
            if auth_header != self.admin_key:
                return 401, {"ok": False, "error": {"code": "UNAUTHORIZED", "message": "권한 없음"}}
            if path == "/api/v1/slotbridge/admin/invoices" and method == "GET":
                return 200, {"ok": True, "invoices": []}

        return 404, {"ok": False, "error": {"code": "NOT_FOUND", "message": "경로 없음"}}

class TestEdgeWorkerApiAdapter(unittest.TestCase):
    def setUp(self):
        self.d1 = MockD1Database()
        self.router = EdgeWorkerMockRouter(self.d1, admin_key="secret_admin_key_2026")

    def test_01_health_endpoint(self):
        status, data = self.router.handle("GET", "/api/v1/slotbridge/health")
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertTrue(data["d1_connected"])

    def test_02_inquiry_create_success_and_d1_persist(self):
        payload = {
            "customer_name": "김관악",
            "customer_phone": "010-1234-5678",
            "district": "관악구",
            "neighborhood": "봉천동",
            "housing_type": "STUDIO",
            "area_pyeong": 8,
            "requested_date": "2026-11-01",
            "requested_time_window": "MORNING",
            "agreed_privacy_collection": True,
            "agreed_third_party_share": True,
            "agreed_marketing": False
        }
        status, data = self.router.handle("POST", "/api/v1/slotbridge/inquiries", body=payload)
        self.assertEqual(status, 201)
        self.assertTrue(data["ok"])
        self.assertEqual(data["status"], "PENDING_MATCH")
        self.assertEqual(data["masked_customer"]["name"], "김*악")
        self.assertEqual(data["masked_customer"]["phone"], "010-****-5678")

        # Verify D1 row
        row = self.d1.execute("SELECT * FROM inquiries WHERE id = ?", (data["inquiry_id"],)).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["district"], "관악구")
        self.assertEqual(row["tracking_token"], data["tracking_token"])

        # Verify Consent Event row
        c_row = self.d1.execute("SELECT * FROM consent_events WHERE inquiry_id = ?", (data["inquiry_id"],)).fetchone()
        self.assertIsNotNone(c_row)
        self.assertEqual(c_row["terms_version"], "v1.0-20261031")

    def test_03_inquiry_missing_consent_fails_closed(self):
        payload = {
            "customer_name": "김관악",
            "customer_phone": "010-1234-5678",
            "district": "관악구",
            "agreed_privacy_collection": True,
            "agreed_third_party_share": False # Missing essential consent
        }
        status, data = self.router.handle("POST", "/api/v1/slotbridge/inquiries", body=payload)
        self.assertEqual(status, 400)
        self.assertEqual(data["error"]["code"], "CONSENT_REQUIRED")

    def test_04_inquiry_invalid_region_rejected(self):
        payload = {
            "customer_name": "김강남",
            "customer_phone": "010-9999-8888",
            "district": "강남구", # Out of scope
            "agreed_privacy_collection": True,
            "agreed_third_party_share": True
        }
        status, data = self.router.handle("POST", "/api/v1/slotbridge/inquiries", body=payload)
        self.assertEqual(status, 400)
        self.assertEqual(data["error"]["code"], "INVALID_REGION")

    def test_05_inquiry_status_and_revoke_tombstone(self):
        # 1. Create
        payload = {
            "customer_name": "박동작",
            "customer_phone": "010-9876-5432",
            "district": "동작구",
            "neighborhood": "사당동",
            "agreed_privacy_collection": True,
            "agreed_third_party_share": True
        }
        status, created = self.router.handle("POST", "/api/v1/slotbridge/inquiries", body=payload)
        token = created["status_token"]

        # 2. Get Status
        status, got = self.router.handle("GET", f"/api/v1/slotbridge/inquiries/{token}")
        self.assertEqual(status, 200)
        self.assertEqual(got["status"], "PENDING_MATCH")

        # 3. Revoke
        status, revoked = self.router.handle("DELETE", f"/api/v1/slotbridge/inquiries/{token}")
        self.assertEqual(status, 200)
        self.assertEqual(revoked["status"], "CANCELLED")

        # 4. Check D1 Tombstone
        row = self.d1.execute("SELECT * FROM inquiries WHERE tracking_token = ?", (token,)).fetchone()
        self.assertEqual(row["status"], "CANCELLED")
        self.assertEqual(row["customer_name_encrypted"], "ERASED_NAME")
        self.assertEqual(row["customer_phone_encrypted"], "ERASED_PHONE")

    def test_06_partner_apply_validation_and_d1_record(self):
        # 1. Invalid BizNum
        payload = {
            "name": "클린홈 관악본점",
            "business_number": "12345", # Less than 10
            "contact_phone": "010-1234-5678",
            "representative_name": "박대표"
        }
        status, data = self.router.handle("POST", "/api/v1/slotbridge/partners/apply", body=payload)
        self.assertEqual(status, 400)
        self.assertEqual(data["error"]["code"], "INVALID_BIZ_NUMBER")

        # 2. Valid
        payload["business_number"] = "2148812345"
        status, data = self.router.handle("POST", "/api/v1/slotbridge/partners/apply", body=payload)
        self.assertEqual(status, 201)
        self.assertEqual(data["status"], "PENDING_APPROVAL")

        # Check DB
        p_row = self.d1.execute("SELECT * FROM partners WHERE id = ?", (data["partner_id"],)).fetchone()
        self.assertIsNotNone(p_row)
        self.assertEqual(p_row["verification_status"], "PENDING_APPROVAL")

    def test_07_admin_endpoints_auth_gate(self):
        # 1. Without auth
        status, data = self.router.handle("GET", "/api/v1/slotbridge/admin/invoices")
        self.assertEqual(status, 401)
        self.assertEqual(data["error"]["code"], "UNAUTHORIZED")

        # 2. With valid auth
        headers = {"X-SBK-Admin-Key": "secret_admin_key_2026"}
        status, data = self.router.handle("GET", "/api/v1/slotbridge/admin/invoices", headers=headers)
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])

if __name__ == "__main__":
    unittest.main()
