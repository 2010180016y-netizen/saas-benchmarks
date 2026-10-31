"""
SlotBridge Korea Test Suite Package Initialization
Ensures standard cryptographic keys are present during test runs.
"""

import os

# Default test keys for test runner
os.environ.setdefault("SBK_ENCRYPTION_KEY", "sbk_test_key_32_bytes_2026_super_safe!")
os.environ.setdefault("SBK_ATTRIBUTION_SECRET", "sbk_attribution_secret_test_2026")
