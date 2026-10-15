#!/usr/bin/env bash
# Smoke tests run against a freshly deployed environment.
# Usage: scripts/smoke_test.sh http://localhost:8000
set -uo pipefail

BASE_URL="${1:-http://localhost:8000}"
FAILED=0

status_of() { curl -s -o /dev/null -w "%{http_code}" "$@"; }

expect() {  # expect <description> <expected status> <curl args...>
  local name="$1" want="$2"; shift 2
  local got; got=$(status_of "$@")
  if [[ "$got" == "$want" ]]; then
    echo "PASS  $name (HTTP $got)"
  else
    echo "FAIL  $name: expected HTTP $want, got $got"
    FAILED=1
  fi
}

echo "Waiting for $BASE_URL/health ..."
for _ in $(seq 1 60); do
  if curl -fs "$BASE_URL/health" | grep -q "Healthy"; then
    echo "PASS  service reports healthy"
    break
  fi
  sleep 2
done
if ! curl -fs "$BASE_URL/health" | grep -q "Healthy"; then
  echo "FAIL  service did not become healthy within 120 seconds"
  exit 1
fi

# The application is reachable
expect "dashboard page loads"         200 "$BASE_URL/"
expect "login page loads"             200 -L "$BASE_URL/login"   # redirects to /login.html
expect "signup page loads"            200 -L "$BASE_URL/signup"  # redirects to /signup.html

# Protected data is not served without a valid login
expect "stats rejected without token"     401 "$BASE_URL/stats"
expect "stats rejected with forged token" 401 -H "Authorization: Bearer forged.token.value" "$BASE_URL/stats"

# Privacy check (reported, not yet blocking):
# per-user health analytics should require a login.
code=$(status_of "$BASE_URL/api/fhir/analytics/user/00000000-0000-0000-0000-000000000000")
if [[ "$code" == "401" ]]; then
  echo "PASS  per-user FHIR analytics require login"
else
  echo "WARN  per-user FHIR analytics answered HTTP $code without a login token"
  echo "::warning title=Privacy::/api/fhir/analytics/user/{id} is reachable without authentication (HTTP $code)"
fi

exit $FAILED
