"""
SlotBridge Korea — FIX-A Test Suite: Partner Auth & HMAC Signature Security (P0-01, P0-02)
Validates:
1. Strict HMAC signature verification (rejection of empty, truncated, or invalid format signatures).
2. Permanent elimination of business_number and SBK_ATTRIBUTION_SECRET as partner authentication passwords.
3. Header-only authentication enforcement (elimination of URL parameter auth leakage).
4. Secure cryptographically-derived partner token (sbk_ptk_*) generation and partner-bound verification.
5. Flexible individual pilot onboarding (starting without formal business registration).
"""

import unittest
import os
import hmac
import hashlib
import time

from src.attribution import generate_attribution_token, verify_attribution_token, SECRET_KEY

class TestFixAPartnerAuthAndHmac(unittest.TestCase):
    def setUp(self):
        self.repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.worker_js_path = os.path.join(self.repo_root, "src", "worker.js")
        self.widget_js_path = os.path.join(self.repo_root, "slotbridge", "widget.js")
        self.partner_slots_html_path = os.path.join(self.repo_root, "slotbridge", "partner-slots.html")

    def test_p0_02_empty_signature_rejected_in_python_attribution(self):
        """P0-02: Empty signature 'payload:' strictly fails verification."""
        token_empty = "partner_test:camp_test:9999999999:"
        is_valid, data = verify_attribution_token(token_empty)
        self.assertFalse(is_valid)
        self.assertIsNone(data)

    def test_p0_02_truncated_signature_rejected_in_python_attribution(self):
        """P0-02: Truncated signature strictly fails verification."""
        token_trunc = "partner_test:camp_test:9999999999:a"
        is_valid, data = verify_attribution_token(token_trunc)
        self.assertFalse(is_valid)
        self.assertIsNone(data)

    def test_p0_02_non_hex_signature_rejected_in_python_attribution(self):
        """P0-02: Non-hex characters in signature strictly fail verification."""
        token_invalid = "partner_test:camp_test:9999999999:zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz"
        is_valid, data = verify_attribution_token(token_invalid)
        self.assertFalse(is_valid)
        self.assertIsNone(data)

    def test_p0_02_valid_hmac_signature_passes(self):
        """P0-02: Valid 32-char hex HMAC token passes verification."""
        token_valid = generate_attribution_token("partner_test", "camp_test", ttl_seconds=3600)
        is_valid, data = verify_attribution_token(token_valid)
        self.assertTrue(is_valid)
        self.assertEqual(data["partner_id"], "partner_test")

    def test_p0_02_worker_js_has_strict_sig_validation(self):
        """P0-02: Worker JS enforces strict signature length and eliminates empty slice forgery."""
        with open(self.worker_js_path, "r", encoding="utf-8") as f:
            code = f.read()

        # Ensures signature length check exists and empty signature is rejected
        self.assertIn("sig.length !== 32 && sig.length !== 64", code)
        self.assertNotIn(".slice(0, sig.length)", code)

    def test_p0_01_worker_js_partner_auth_helpers(self):
        """P0-01: Worker JS implements generatePartnerAuthToken and verifyPartnerAuthToken."""
        with open(self.worker_js_path, "r", encoding="utf-8") as f:
            code = f.read()

        self.assertIn("async function generatePartnerAuthToken(partnerId, env)", code)
        self.assertIn("async function verifyPartnerAuthToken(partnerKey, partnerId, env)", code)
        self.assertIn("sbk_ptk_", code)

    def test_p0_01_worker_js_eliminates_business_number_as_auth_key(self):
        """P0-01: business_number and SBK_ATTRIBUTION_SECRET are never used as partner passwords."""
        with open(self.worker_js_path, "r", encoding="utf-8") as f:
            code = f.read()

        # Must not compare partnerKey to business_number
        self.assertNotIn("partnerKey === partner.business_number", code)
        self.assertNotIn("partnerKey === partnerRow.business_number", code)
        self.assertNotIn("partnerKey === env.SBK_ATTRIBUTION_SECRET", code)

    def test_p0_01_worker_js_eliminates_url_param_partner_key_in_endpoints(self):
        """P0-01: Endpoints do not accept partner_key from URL query parameters."""
        with open(self.worker_js_path, "r", encoding="utf-8") as f:
            code = f.read()

        # URL param partner_key must not be accepted in slots and offers
        self.assertNotIn('url.searchParams.get("partner_key")', code)
        self.assertNotIn('body.partner_key', code)

    def test_p0_01_frontend_uses_header_not_query_param(self):
        """P0-01: partner-slots.html and widget.js transmit partner key via X-SBK-Partner-Key header."""
        with open(self.partner_slots_html_path, "r", encoding="utf-8") as f:
            html = f.read()
        with open(self.widget_js_path, "r", encoding="utf-8") as f:
            widget = f.read()

        # Frontend does not hardcode business number 1234567890
        self.assertNotIn("1234567890", html)
        self.assertIn("X-SBK-Partner-Key", html)
        self.assertIn("X-SBK-Partner-Key", widget)
        self.assertNotIn("&partner_key=", widget)

    def test_individual_pilot_partner_apply_support(self):
        """Supports starting without business registration (individual pilot mode)."""
        with open(self.worker_js_path, "r", encoding="utf-8") as f:
            code = f.read()

        self.assertIn("INDIV_", code)
        self.assertIn("partner_key", code)

if __name__ == "__main__":
    unittest.main()
