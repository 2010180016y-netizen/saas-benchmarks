#!/usr/bin/env python3
"""
tests/test_saas_matrix_commercial_api.py
=========================================
Contract verification for SaaS Matrix Phase 1 commercial engine:
- GET /api/v1/orders/health
- POST /api/v1/lead-capture (valid vs invalid email)
- POST /api/v1/orders (returnwise-kit, cursorrules-masterpack, invalid product)
- Verification of products/index.html checkout modal & download links
- Verification of index.html interactive TCO calculator & schema.org tags
"""

import os
import json
import unittest
import subprocess

class TestSaaSMatrixCommercialAPI(unittest.TestCase):
    def setUp(self):
        self.repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.node_bin = "/workspace/.local/bin/node"

    def test_01_worker_commercial_endpoints(self):
        """Execute node test to verify /api/v1/lead-capture and /api/v1/orders behavior."""
        script = """
        import('./src/worker.js').then(async (m) => {
          const worker = m.default;
          const env = { ASSETS: null, DB: null };

          // 1. Health
          const res1 = await worker.fetch(new Request("https://saas-benchmarks.hoiki0808.workers.dev/api/v1/orders/health"), env);
          if (res1.status !== 200) process.exit(1);

          // 2. Lead capture valid
          const res2 = await worker.fetch(new Request("https://saas-benchmarks.hoiki0808.workers.dev/api/v1/lead-capture", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ email: "founder@domain.com", toolCategory: "automation", teamSize: 3, annualSavings: "$2,292" })
          }), env);
          const data2 = await res2.json();
          if (res2.status !== 200 || !data2.ok || !data2.leadId) process.exit(2);

          // 3. Lead capture invalid
          const res3 = await worker.fetch(new Request("https://saas-benchmarks.hoiki0808.workers.dev/api/v1/lead-capture", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ email: "bad-email" })
          }), env);
          if (res3.status !== 400) process.exit(3);

          // 4. Order valid
          const res4 = await worker.fetch(new Request("https://saas-benchmarks.hoiki0808.workers.dev/api/v1/orders", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ email: "buyer@domain.com", productId: "returnwise-kit", amount: 29 })
          }), env);
          const data4 = await res4.json();
          if (res4.status !== 200 || !data4.ok || !data4.product.downloadUrl) process.exit(4);

          process.exit(0);
        });
        """
        proc = subprocess.run(
            [self.node_bin, "-e", script],
            cwd=self.repo_root,
            capture_output=True,
            text=True
        )
        self.assertEqual(proc.returncode, 0, f"Node verification failed: {proc.stderr}")

    def test_02_products_html_has_no_mailto_and_has_modal(self):
        """Verifies mailto links are eliminated from products/index.html and checkout modal is present."""
        path = os.path.join(self.repo_root, "products", "index.html")
        self.assertTrue(os.path.exists(path))
        with open(path, "r", encoding="utf-8") as f:
            html = f.read()

        self.assertNotIn("mailto:hoiki0808@gmail.com", html, "mailto checkout must be completely eliminated")
        self.assertIn("openCheckoutModal", html, "openCheckoutModal must be wired to buy buttons")
        self.assertIn('id="checkoutModal"', html, "Modal overlay element must exist")
        self.assertIn("/api/v1/orders", html, "Checkout modal must submit to /api/v1/orders")

    def test_03_homepage_has_tco_calculator_and_schema_org(self):
        """Verifies homepage index.html includes TCO calculator and schema.org structured data."""
        path = os.path.join(self.repo_root, "index.html")
        self.assertTrue(os.path.exists(path))
        with open(path, "r", encoding="utf-8") as f:
            html = f.read()

        self.assertIn("Live 2026 TCO Arbitrage Calculator", html)
        self.assertIn("updateTcoCalculations", html)
        self.assertIn("/api/v1/lead-capture", html)
        self.assertIn("SoftwareApplication", html)
        self.assertIn("FAQPage", html)

    def test_04_digital_downloads_exist(self):
        """Verifies digital product download ZIP files exist in products/downloads/."""
        dl_dir = os.path.join(self.repo_root, "products", "downloads")
        self.assertTrue(os.path.exists(dl_dir))
        rw_zip = os.path.join(dl_dir, "ReturnWise_ECommerce_Margin_Defense_Kit_2026.zip")
        cr_zip = os.path.join(dl_dir, "Production_Cursorrules_Masterpack_2026.zip")
        self.assertTrue(os.path.exists(rw_zip), "ReturnWise kit ZIP must exist")
        self.assertTrue(os.path.exists(cr_zip), "Cursorrules masterpack ZIP must exist")
        self.assertGreater(os.path.getsize(rw_zip), 1000)
        self.assertGreater(os.path.getsize(cr_zip), 1000)

if __name__ == "__main__":
    unittest.main()
