#!/usr/bin/env bash
# Exercise 7: leave a two-phase-commit transaction half finished.
# A "bundle refund" touches one order on each shard node (fdw profile):
#   m21-shard1: seller 1, order 2      m21-shard2: seller 3, order 4
# The coordinator (m21-fdw-coord) records its decision in public.twopc_log,
# commits the first participant and then "crashes" before the second.
set -euo pipefail
GID="refund-bundle-$(date +%s)"
psql_on() { docker exec -i "$1" psql -U postgres -d bazaar -q -v ON_ERROR_STOP=1; }

psql_on m21-fdw-coord <<SQL
CREATE TABLE IF NOT EXISTS public.twopc_log (
    gid        text PRIMARY KEY,
    decision   text NOT NULL CHECK (decision IN ('commit', 'abort')),
    decided_at timestamptz NOT NULL DEFAULT now()
);
SQL

# Phase 1: every participant does its work and PREPAREs (votes yes).
psql_on m21-shard1 <<SQL
BEGIN;
UPDATE shard.orders_p0 SET status = 'refunded' WHERE seller_id = 1 AND id = 2;
PREPARE TRANSACTION '$GID';
SQL
psql_on m21-shard2 <<SQL
BEGIN;
UPDATE shard.orders_p1 SET status = 'refunded' WHERE seller_id = 3 AND id = 4;
PREPARE TRANSACTION '$GID';
SQL

# The decision is durable BEFORE phase 2 starts: this row is the commit point.
psql_on m21-fdw-coord <<SQL
INSERT INTO public.twopc_log (gid, decision) VALUES ('$GID', 'commit');
SQL

# Phase 2 ... interrupted.
psql_on m21-shard1 <<SQL
COMMIT PREPARED '$GID';
SQL
echo ">> coordinator crashed before COMMIT PREPARED on m21-shard2 (gid $GID)"
docker restart m21-shard2 >/dev/null
echo ">> m21-shard2 restarted. Over to you."
