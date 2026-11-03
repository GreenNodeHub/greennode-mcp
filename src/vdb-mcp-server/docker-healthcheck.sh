#!/bin/sh
# Probe the /health endpoint (unauthenticated) on the HTTP transport port.
PORT="${MCP_PORT:-8080}"

exec python -c "import sys, urllib.request; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:${PORT}/health', timeout=3).getcode() == 200 else 1)"
