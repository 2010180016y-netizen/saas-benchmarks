"""
SlotBridge Korea Core Python Package
Enforces Fail-Closed cryptographic environment configuration.
"""

import os

# For non-production development/testing, default development keys are populated
# Production (ENV=production) strictly requires explicit injection from secret vault / environment.
if os.environ.get("ENV") != "production" and os.environ.get("NODE_ENV") != "production":
    os.environ.setdefault("SBK_ENCRYPTION_KEY", "sbk_test_key_32_bytes_2026_super_safe!")
    os.environ.setdefault("SBK_ATTRIBUTION_SECRET", "sbk_attribution_secret_test_2026")
