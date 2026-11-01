#!/usr/bin/env bash
# scripts/run_wrangler_e2e.sh
# ===========================
# Runs the independent E2E test suite for src/worker.js against local Wrangler runtime.
# Adheres strictly to [SBK-MINI-13 | INDEPENDENT E2E].

set -e

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

echo "=== [SBK-MINI-13] Environment Pre-Flight Check ==="

if ! command -v node >/dev/null 2>&1; then
  echo "[-] ERROR: Node.js runtime not found in PATH (/bin/sh: node: not found)."
  echo "    Required to execute Cloudflare Worker local emulator (Wrangler/Miniflare)."
  echo "    Installation command: curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && apt-get install -y nodejs"
  exit 127
fi

if ! command -v wrangler >/dev/null 2>&1 && ! command -v npx >/dev/null 2>&1; then
  echo "[-] ERROR: Wrangler CLI not found."
  echo "    Installation command: npm install -g wrangler"
  exit 127
fi

echo "[+] Node.js: $(node --version)"
echo "[+] Wrangler: $(npx wrangler --version 2>/dev/null || wrangler --version)"

echo "=== Starting local Wrangler dev with local D1 SQLite binding on :8787 ==="
# Starts wrangler dev in background
npx wrangler dev --port 8787 --local &
WRANGLER_PID=$!

trap "kill $WRANGLER_PID 2>/dev/null || true" EXIT

# Wait for local port
for i in {1..30}; do
  if curl -s http://127.0.0.1:8787/api/v1/slotbridge/health >/dev/null 2>&1; then
    echo "[+] Local Wrangler Worker is LIVE on :8787"
    break
  fi
  sleep 1
done

echo "=== Executing 10-Flow Independent E2E Test Suite ==="
python3 -m unittest tests/test_wrangler_integration_e2e.py
