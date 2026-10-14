#!/usr/bin/env bash
# Module 15 TLS lab: what each client sslmode does, and pg_hba.conf in action.
# Run from this directory after `docker compose up -d --wait`.
set -uo pipefail
cd "$(dirname "$0")"

client() { docker compose exec -T client "$@"; }

try() {   # try <description> <conninfo>
  local out
  if out=$(client psql "$2 connect_timeout=5" -XAtqc \
      "SELECT 'OK  user=' || current_user || ' ssl=' || ssl || coalesce(' ' || version || ' ' || cipher, '')
       FROM pg_stat_ssl WHERE pid = pg_backend_pid()" 2>&1); then
    printf '%-44s %s\n' "$1" "$out"
  else
    printf '%-44s FAIL %s\n' "$1" "$(echo "$out" | grep -m1 -E 'FATAL|error|failed' | sed -E 's/.*(FATAL|error): *//')"
  fi
}

CA="sslrootcert=/lab/certs/ca.crt"
APP="dbname=lab user=app password=app_pw"

echo "== 1. sslmode, connecting by the name in the certificate (db.bazaar.internal)"
for m in disable allow prefer require; do
  try "sslmode=$m" "host=db.bazaar.internal $APP sslmode=$m"
done
for m in verify-ca verify-full; do
  try "sslmode=$m" "host=db.bazaar.internal $APP sslmode=$m $CA"
done

echo
echo "== 2. sslmode, connecting by another name for the same server (db)"
try "sslmode=require" "host=db $APP sslmode=require"
for m in verify-ca verify-full; do
  try "sslmode=$m" "host=db $APP sslmode=$m $CA"
done

echo
echo "== 3. verify-full without the CA certificate"
try "verify-full, no sslrootcert" "host=db.bazaar.internal $APP sslmode=verify-full sslrootcert=/nonexistent/ca.crt"

echo
echo "== 4. authentication methods (all over verify-full)"
TLS="host=db.bazaar.internal dbname=lab sslmode=verify-full $CA"
try "app, right password (scram)"       "$TLS user=app password=app_pw"
try "app, wrong password"               "$TLS user=app password=nope"
try "alice, client certificate (cert)"  "$TLS user=alice sslcert=/lab/certs/alice.crt sslkey=/lab/certs/alice.key"
try "alice, no certificate"             "$TLS user=alice"
try "app, presenting alice's cert"      "$TLS user=app password=app_pw sslcert=/lab/certs/alice.crt sslkey=/lab/certs/alice.key"
try "mallory (no matching hba line)"    "$TLS user=mallory password=mallory_pw"
try "legacy, md5 line, md5 hash"        "$TLS user=legacy password=legacy_pw"
try "app to database postgres"          "host=db.bazaar.internal dbname=postgres sslmode=verify-full $CA user=app password=app_pw"

