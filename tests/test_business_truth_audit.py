"""
SlotBridge Korea — Business Truth & Marketing Compliance Tests (SBK-MINI-14)
Validates that marketing, outreach, SEO, and widget materials contain zero unverified
claims of secured partners, accredited federations ('공인 연합'), guaranteed 100% match,
or measured 0-CAC without explicit hypothesis/pending disclaimers.
"""

import os
import unittest
import csv

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestBusinessTruthAudit(unittest.TestCase):

    def test_marketing_strategy_truth(self):
        path = os.path.join(BASE_DIR, "docs", "business", "MARKETING_STRATEGY.md")
        self.assertTrue(os.path.exists(path))
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()

        # Forbidden unverified assertions
        self.assertNotIn("3대 확정 수요 파이프라인", content)
        self.assertNotIn("건당 100% 매칭 전환", content)

        # Must explicitly state hypothesis / target
        self.assertIn("HYPOTHESIS", content)
        self.assertIn("가설 기반", content)

    def test_outreach_playbook_truth(self):
        path = os.path.join(BASE_DIR, "docs", "business", "OUTREACH_PLAYBOOK.md")
        self.assertTrue(os.path.exists(path))
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()

        # Forbidden claims of secured partners before contracts
        self.assertNotIn("7곳 확보", content)
        self.assertNotIn("8곳 확보", content)
        self.assertNotIn("보증 규약으로 100% 처리", content)

        # Must indicate targets are uncontracted / pending
        self.assertIn("PENDING", content)
        self.assertIn("아웃리치 목표", content)

    def test_outreach_targets_csv_status(self):
        path = os.path.join(BASE_DIR, "docs", "business", "OUTREACH_TARGETS.csv")
        self.assertTrue(os.path.exists(path))
        with open(path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        self.assertEqual(len(rows), 15)
        for row in rows:
            # All prospective partners must be pending outreach
            self.assertEqual(row["status"], "PENDING_OUTREACH")
            self.assertNotEqual(row["status"], "CONTRACTED")
            self.assertNotEqual(row["status"], "ACTIVE")

    def test_seo_pages_no_fake_accreditation(self):
        for district in ["gwanak", "dongjak"]:
            path = os.path.join(BASE_DIR, "slotbridge", "seo", f"{district}.html")
            self.assertTrue(os.path.exists(path))
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()

            # Must not claim unaccredited federation ('공인 연합')
            self.assertNotIn("공인 연합", content)

            # Must preserve legal broker disclaimers and preparation notices
            self.assertIn("통신판매중개 지위", content)
            self.assertIn("공정위 경제적 대가 고지", content)
            self.assertIn("서비스 준비 안내", content)

    def test_widget_no_fake_accreditation(self):
        path = os.path.join(BASE_DIR, "slotbridge", "widget.js")
        self.assertTrue(os.path.exists(path))
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()

        # Must not claim unaccredited federation ('공인 연합')
        self.assertNotIn("공인 연합", content)
        self.assertIn("파트너 청소팀", content)

    def test_transition_report_truth(self):
        path = os.path.join(BASE_DIR, "docs", "product", "TRANSITION_COMPLETION_REPORT.md")
        self.assertTrue(os.path.exists(path))
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertNotIn("CAC 0원 실측", content)
        self.assertIn("CAC 0원 가설 시뮬레이션", content)


if __name__ == "__main__":
    unittest.main()
