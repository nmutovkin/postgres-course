-- =============================================================================
-- 1401_schema_events.sql  --  Module 14: Partitioning
-- -----------------------------------------------------------------------------
-- events: the high-volume clickstream (page views, product views, cart
-- changes, checkouts, searches), RANGE-partitioned by month on occurred_at.
--
-- Why partition this table?
--   * Retention: dropping a month is DROP TABLE / DETACH PARTITION (instant,
--     no bloat) instead of a huge DELETE followed by VACUUM.
--   * Pruning: queries over a time window only touch the matching partitions.
--   * Maintenance: VACUUM / index builds work on manageable pieces; old
--     partitions stop changing and stay frozen.
--
-- Rules partitioning imposes (the "pitfalls" of module 14):
--   * Every PRIMARY KEY / UNIQUE constraint must include the partition key,
--     because uniqueness is enforced per partition. Hence PRIMARY KEY (id, occurred_at):
--     the identity column alone keeps ids unique in practice, but Postgres can
--     only *enforce* uniqueness of (id, occurred_at).
--   * A row must fit some partition or the INSERT fails; the DEFAULT
--     partition catches anything outside the created months. Keep it empty in
--     practice: creating a new partition whose range already has rows in the
--     DEFAULT partition fails until those rows are moved.
--
-- No foreign keys to users/products, deliberately:
--   * events are an append-only log; they must still be ingestible if the
--     user or product is later deleted (and must not block that deletion);
--   * an FK check per inserted event would cost an index lookup on the hot
--     ingestion path, and anonymous visitors have user_id NULL anyway;
--   * analytics tolerates the occasional dangling id; correctness of orders does not.
-- =============================================================================

SET search_path = bazaar, public;

BEGIN;

CREATE TABLE events (
    id           bigint      GENERATED ALWAYS AS IDENTITY,   -- identity on a partitioned table: PG 17+
    occurred_at  timestamptz NOT NULL,
    user_id      bigint,                                      -- NULL = anonymous visitor
    session_id   uuid        NOT NULL,
    event_type   text        NOT NULL,
    product_id   bigint,                                      -- for product-related events
    payload      jsonb       NOT NULL DEFAULT '{}',
    CONSTRAINT events_event_type_check CHECK (event_type IN (
        'page_view', 'product_view', 'add_to_cart', 'remove_from_cart',
        'checkout_started', 'order_placed', 'search')),
    PRIMARY KEY (id, occurred_at)
) PARTITION BY RANGE (occurred_at);

COMMENT ON TABLE events IS 'Clickstream events, partitioned by month (events_yYYYYmMM).';

-- -----------------------------------------------------------------------------
-- create_events_partition('2025-03-01') creates events_y2025m03 covering
-- [2025-03-01 00:00 UTC, 2025-04-01 00:00 UTC). Any day of the month works.
-- Idempotent: returns the name whether or not it had to create it, so a cron
-- job can simply call it for "next month" every day.
-- Bounds are given in UTC explicitly, so the result does not depend on the
-- session's TimeZone.
-- -----------------------------------------------------------------------------
CREATE FUNCTION create_events_partition(p_month date)
RETURNS text
LANGUAGE plpgsql
AS $$
DECLARE
    v_from  date := date_trunc('month', p_month)::date;
    v_to    date := (date_trunc('month', p_month) + interval '1 month')::date;
    v_name  text := format('events_y%sm%s', to_char(v_from, 'YYYY'), to_char(v_from, 'MM'));
BEGIN
    IF to_regclass(format('%I.%I', 'bazaar', v_name)) IS NULL THEN
        -- format(): %I quotes identifiers, %L quotes literals (no SQL injection).
        EXECUTE format(
            'CREATE TABLE bazaar.%I PARTITION OF bazaar.events FOR VALUES FROM (%L) TO (%L)',
            v_name,
            v_from::text || ' 00:00:00+00',
            v_to::text   || ' 00:00:00+00');
    END IF;
    RETURN v_name;
END;
$$;

COMMENT ON FUNCTION create_events_partition(date) IS
    'Creates (if missing) the monthly partition of events containing p_month; returns its name.';

-- Monthly partitions for 2025-01 .. 2026-12.
SELECT create_events_partition(m::date)
FROM generate_series(date '2025-01-01', date '2026-12-01', interval '1 month') AS m
\g /dev/null

-- Anything outside the known months lands here instead of failing.
CREATE TABLE events_default PARTITION OF events DEFAULT;

-- Partitioned indexes: defined once on the parent, created on every existing
-- partition and automatically on partitions created later.
-- (occurred_at): time-window scans within a partition, ORDER BY occurred_at
-- (product_id):  "views of product X" analytics
CREATE INDEX events_occurred_at_idx ON events (occurred_at);
CREATE INDEX events_product_id_idx ON events (product_id);

COMMIT;
