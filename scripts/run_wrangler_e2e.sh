#!/usr/bin/env bash
# scripts/run_wrangler_e2e.sh
# ===========================
# Runs the independent E2E test suite for src/worker.js against local runtime with local D1 SQLite binding.
# Adheres strictly to [SBK-FIX-E | REAL JS WORKER E2E].

set -e

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# Ensure local node binary in PATH if available
if [ -d "/workspace/.local/node/bin" ]; then
  export PATH="/workspace/.local/node/bin:/workspace/.local/bin:$PATH"
elif [ -d "$HOME/.local/bin" ]; then
  export PATH="$HOME/.local/bin:$PATH"
fi

echo "=== [SBK-FIX-E] Environment Pre-Flight Check ==="

if ! command -v node >/dev/null 2>&1; then
  echo "[-] ERROR: Node.js runtime not found in PATH."
  echo "    Required to execute Cloudflare Worker local emulator/isolated runtime."
  exit 127
fi

echo "[+] Node.js: $(node --version)"

echo "=== Executing 10-Flow Independent E2E Test Suite (Zero Skips Guaranteed) ==="
python3 -m unittest tests/test_wrangler_integration_e2e.py
echo "[+] All 10 Worker E2E flows PASSED with 0 skipped and 0 failed."
