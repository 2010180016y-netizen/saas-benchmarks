"""
Unit and regression tests for SBK-FIX-09: Marketing, Copywriting and Compliance.
Ensures zero prohibited/exaggerated claims, 100% compliance with Fair Trade Commission (FTC)
endorsement guidelines, and full statutory electronic commerce broker disclosures.
"""

import os
import re
import unittest

class TestMarketingAndCompliance(unittest.TestCase):
    def setUp(self):
        self.base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        self.slotbridge_dir = os.path.join(self.base_dir, "slotbridge")

    def test_zero_prohibited_phrases_in_html(self):
        """Verify that zero prohibited/exaggerated claims exist across all SlotBridge HTML files."""
        prohibited_phrases = [
            "100% 만족 보장",
            "즉시 출동",
            "모든 일정 해결",
            "평점 4.97",
            "월 1억",
            "가짜 리뷰",
            "조작 별점"
        ]

        html_files = []
        for root, _, files in os.walk(self.slotbridge_dir):
            for f in files:
                if f.endswith(".html"):
                    html_files.append(os.path.join(root, f))

        self.assertGreater(len(html_files), 5, "Should inspect multiple HTML files")

        for file_path in html_files:
            rel_path = os.path.relpath(file_path, self.base_dir)
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
                for phrase in prohibited_phrases:
                    self.assertNotIn(
                        phrase,
                        content,
                        f"Prohibited phrase '{phrase}' found in {rel_path}"
                    )

    def test_statutory_disclaimers_in_inquiry_page(self):
        """Verify that inquiry.html contains full statutory broker, FTC, refund and A/S disclosures."""
        inquiry_path = os.path.join(self.slotbridge_dir, "inquiry.html")
        with open(inquiry_path, "r", encoding="utf-8") as f:
            content = f.read()

        # E-Commerce broker disclaimer
        self.assertIn("통신판매중개", content)
        self.assertIn("전자상거래 등에서의 소비자보호에 관한 법률", content)
        self.assertIn("거래의 당사자가 아니며", content)

        # FTC Economic interest disclosure
        self.assertIn("공정거래위원회", content)
        self.assertIn("추천·보증 등에 관한 표시·광고 심사지침", content)
        self.assertIn("8%", content)

        # Cancellation, refund & dispute/AS standard
        self.assertIn("취소 및 환불 기준", content)
        self.assertIn("1-Click 철회", content)
        self.assertIn("무상 재청소 A/S", content)
        self.assertIn("support@buykoreanow.com", content)
        self.assertIn("010-4005-3071", content)

    def test_statutory_disclaimers_in_seo_landing_pages(self):
        """Verify that SEO regional landing pages contain statutory disclaimers."""
        seo_files = ["gwanak.html", "dongjak.html"]
        for sf in seo_files:
            seo_path = os.path.join(self.slotbridge_dir, "seo", sf)
            with open(seo_path, "r", encoding="utf-8") as f:
                content = f.read()

            self.assertIn("통신판매중개", content)
            self.assertIn("공정위", content)
            self.assertIn("8%", content)
            self.assertIn("A/S", content)
            self.assertIn("support@buykoreanow.com", content)

    def test_widget_ftc_and_broker_disclaimer(self):
        """Verify that widget.js embeds compliance notices in its footer."""
        widget_path = os.path.join(self.slotbridge_dir, "widget.js")
        with open(widget_path, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn("통신판매중개", content)
        self.assertIn("공정위", content)
        self.assertIn("수수료", content)

    def test_community_scripts_zero_link_spam_and_compliance(self):
        """Verify that community scripts enforce zero spam and FTC compliance."""
        script_path = os.path.join(self.base_dir, "docs", "business", "NATIVE_COMMUNITY_SCRIPTS.md")
        with open(script_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Anti-spam and anti-ban principles
        self.assertIn("도배성 바이럴 원천 금지", content)
        self.assertIn("링크 강제 삽입 금지", content)
        self.assertIn("정보성 가치 우선", content)
        self.assertIn("공정거래위원회", content)

        # Ensure no raw http:// or https:// URLs in the post bodies
        sections = content.split("## ")
        for sec in sections[1:]:
            lines = sec.split("\n")
            in_body = False
            for line in lines:
                if "**본문:**" in line or "**댓글 원고:**" in line or "**답변 원고:**" in line:
                    in_body = True
                    continue
                if in_body:
                    if line.startswith("---") or line.startswith("##"):
                        in_body = False
                    else:
                        self.assertNotIn("http://", line, f"Raw URL found in community post body: {line}")
                        self.assertNotIn("https://", line, f"Raw URL found in community post body: {line}")

    def test_compliance_guidelines_documentation(self):
        """Verify that MARKETING_COMPLIANCE_GUIDELINES.md is complete and authoritative."""
        guideline_path = os.path.join(self.base_dir, "docs", "business", "MARKETING_COMPLIANCE_GUIDELINES.md")
        self.assertTrue(os.path.exists(guideline_path), "Guidelines file must exist")

        with open(guideline_path, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn("6대 절대 금지 과장·허위 문구 사전", content)
        self.assertIn("공정거래위원회 추천·보증 심사지침", content)
        self.assertIn("전자상거래법 필수 4대 법정 고지 사항", content)
        self.assertIn("커뮤니티 바이럴 배포 4대 클린 운영 수칙", content)

if __name__ == "__main__":
    unittest.main()
