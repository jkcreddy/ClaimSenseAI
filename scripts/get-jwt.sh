#!/usr/bin/env bash
# Helper script to generate a signed JWT badge for local dev testing
# Usage: ./scripts/get-jwt.sh [username] [base_url]
# Example: ./scripts/get-jwt.sh alice

set -euo pipefail

USER="${1:-alice}"
PASS="${USER}123"
URL="${2:-http://localhost:8080}"

TOKEN=$(curl -s -X POST "$URL/auth/login" \
  -H "Content-Type: application/json" \
  -d "{\"username\": \"$USER\", \"password\": \"$PASS\"}" | grep -o '"access_token":"[^"]*' | cut -d'"' -f4)

if [ -n "$TOKEN" ]; then
  echo "$TOKEN"
else
  echo "Failed to get JWT for user $USER" >&2
  exit 1
fi

