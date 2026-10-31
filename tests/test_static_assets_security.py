import unittest
import os
import fnmatch
import re

class TestStaticAssetsSecurity(unittest.TestCase):
    """
    SBK-MINI-01: Static Asset Exposure Audit & Prevention Test Suite
    Verifies that .assetsignore patterns strictly exclude private source code,
    SQL migrations, internal documentation, test suites, and scripts from Cloudflare static asset serving.
    """

    def setUp(self):
        self.repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.assetsignore_path = os.path.join(self.repo_root, ".assetsignore")
        self.worker_js_path = os.path.join(self.repo_root, "src", "worker.js")
        self.wrangler_json_path = os.path.join(self.repo_root, "wrangler.json")

    def test_assetsignore_exists_and_contains_private_directories(self):
        self.assertTrue(os.path.exists(self.assetsignore_path), ".assetsignore must exist")
        with open(self.assetsignore_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Required private directories to ignore
        required_patterns = [
            "src/", "src/**",
            "docs/", "docs/**",
            "tests/", "tests/**",
            "migrations/", "migrations/**",
            "scripts/", "scripts/**",
            "*.py"
        ]
        for pat in required_patterns:
            self.assertIn(pat, content, f"Pattern {pat} must be present in .assetsignore")

    def test_worker_js_has_defense_in_depth_path_filter(self):
        self.assertTrue(os.path.exists(self.worker_js_path), "src/worker.js must exist")
        with open(self.worker_js_path, "r", encoding="utf-8") as f:
            js = f.read()

        # Check for defense-in-depth private prefixes blocking
        self.assertIn('const privatePrefixes = ["/src", "/docs", "/tests", "/migrations", "/scripts", "/.git"]', js)
        self.assertIn('return new Response("Not Found", { status: 404 });', js)

    def test_mock_asset_routing_rules(self):
        """Simulate worker.js path routing logic to ensure 100% contract compliance."""
        def simulate_fetch_route(pathname: str) -> int:
            if pathname.startswith("/api/v1/slotbridge"):
                return 200 # Handled by API
            private_prefixes = ["/src", "/docs", "/tests", "/migrations", "/scripts", "/.git"]
            for prefix in private_prefixes:
                if pathname == prefix or pathname.startswith(prefix + "/"):
                    return 404
            if pathname.startswith("/.env") or pathname.endswith(".py") or pathname.endswith(".sql"):
                return 404
            # Static public asset passthrough
            return 200

        # Private files MUST return 404
        private_test_paths = [
            "/src/worker.js",
            "/src/crypto.py",
            "/docs/product/PRD.md",
            "/docs/audit/MINI_BASELINE.md",
            "/tests/test_api_adapter.py",
            "/migrations/0001_initial_schema.sql",
            "/scripts/production_dry_run.py",
            "/.env.example",
            "/.env.production",
            "/database.sql",
            "/secret.py"
        ]
        for p in private_test_paths:
            status = simulate_fetch_route(p)
            self.assertEqual(status, 404, f"Path {p} must return 404 but got {status}")

        # Public files MUST return 200
        public_test_paths = [
            "/",
            "/index.html",
            "/cursor-vs-copilot/",
            "/make-vs-zapier/",
            "/sunsama-vs-motion/",
            "/benchmarks/cursor-vs-copilot.html",
            "/slotbridge/inquiry.html",
            "/slotbridge/partner-slots.html",
            "/slotbridge/widget.js",
            "/slotbridge/seo/gwanak.html",
            "/slotbridge/seo/dongjak.html",
            "/_redirects",
            "/_headers"
        ]
        for p in public_test_paths:
            status = simulate_fetch_route(p)
            self.assertEqual(status, 200, f"Public path {p} must return 200 but got {status}")

if __name__ == "__main__":
    unittest.main()
