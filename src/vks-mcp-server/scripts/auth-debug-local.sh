#!/usr/bin/env bash
#
# auth-debug-local.sh — exercise the --auth-debug diagnostic locally.
#
# Starts the MCP server (streamable-http) with --auth-debug, mints a throwaway
# JWT (signature is irrelevant — the diagnostic NEVER verifies it), then probes
# GET /whoami with that token plus simulated Gateway forwarding headers. Prints
# the redacted summary and asserts the full token never leaks.
#
# This validates the diagnostic MECHANISM only. The real Gateway 3LO contract
# (what token/claims/headers the Gateway actually sends) can only be measured by
# deploying behind the Gateway and reading the AUTH-DEBUG logs / /whoami there.
#
# Usage:
#   ./scripts/auth-debug-local.sh            # start server + probe + stop
#   PORT=9000 ./scripts/auth-debug-local.sh  # custom port (default 8765)
#
# Run from the src/vks-mcp-server directory. Requires `uv` and `curl`.

set -euo pipefail

PORT="${PORT:-8765}"
HOST="127.0.0.1"
BASE="http://${HOST}:${PORT}"
SERVER_PID=""

cleanup() {
  if [[ -n "${SERVER_PID}" ]] && kill -0 "${SERVER_PID}" 2>/dev/null; then
    kill "${SERVER_PID}" 2>/dev/null || true
    wait "${SERVER_PID}" 2>/dev/null || true
  fi
}
trap cleanup EXIT

echo "==> Minting a throwaway JWT (NOT verified by the diagnostic)"
# A claim 'ssn' is included on purpose to prove the allow-list drops it.
TOKEN="$(uv run python -c "
import jwt, time
print(jwt.encode(
    {
        'iss': 'https://iam.vng.local',
        'aud': 'vks-mcp',
        'sub': 'alice@vng',
        'scope': 'mcp:use mcp:tools',
        'exp': int(time.time()) + 3600,
        'ssn': 'SECRET-SHOULD-NOT-APPEAR',
    },
    'dummy-secret',
    algorithm='HS256',
    headers={'kid': 'key-1'},
))
" 2>/dev/null)"
echo "    token length: ${#TOKEN}, prefix: ${TOKEN:0:6}"

echo "==> Starting server on ${BASE} (--auth-mode none --auth-debug)"
LOG="$(mktemp -t auth-debug-server.XXXXXX.log)"
uv run python -m greennode.vks_mcp_server.server \
  --transport streamable-http --host "${HOST}" --port "${PORT}" --auth-debug \
  >"${LOG}" 2>&1 &
SERVER_PID=$!

# Wait for readiness (up to ~15s) by polling the open /health endpoint.
for _ in $(seq 1 30); do
  if curl -fsS -o /dev/null "${BASE}/health" 2>/dev/null; then
    break
  fi
  sleep 0.5
done
if ! curl -fsS -o /dev/null "${BASE}/health" 2>/dev/null; then
  echo "ERROR: server did not become ready. Startup log:" >&2
  cat "${LOG}" >&2
  exit 1
fi

echo
echo "==> GET /whoami with Bearer JWT + simulated Gateway forwarding headers"
curl -fsS "${BASE}/whoami" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "X-GreenNode-User: alice" \
  -H "X-Forwarded-For: 10.1.2.3" \
  -H "Accept: application/json" | python3 -m json.tool

echo
echo "==> Safety assertions"
RESP="$(curl -fsS "${BASE}/whoami" -H "Authorization: Bearer ${TOKEN}")"
fail=0
if grep -qF "${TOKEN}" <<<"${RESP}"; then echo "  FAIL: full token leaked in /whoami"; fail=1; else echo "  OK: full token absent from /whoami"; fi
if grep -qF "SECRET-SHOULD-NOT-APPEAR" <<<"${RESP}"; then echo "  FAIL: sensitive claim leaked"; fail=1; else echo "  OK: sensitive 'ssn' claim dropped (allow-list)"; fi
if grep -qF "${TOKEN}" "${LOG}"; then echo "  FAIL: full token leaked in server log"; fail=1; else echo "  OK: full token absent from server log"; fi

echo
echo "==> AUTH-DEBUG middleware log lines (each inbound request)"
grep "AUTH-DEBUG" "${LOG}" | tail -3 || echo "  (none captured)"

echo
if [[ "${fail}" -eq 0 ]]; then
  echo "All safety assertions passed."
else
  echo "One or more safety assertions FAILED." >&2
  exit 1
fi
