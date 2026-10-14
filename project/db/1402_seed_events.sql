-- =============================================================================
-- 1402_seed_events.sql  --  Module 14: Partitioning
-- -----------------------------------------------------------------------------
-- ~5 million (4,983,725) clickstream events for calendar year 2025 (partitions
-- events_y2025m01 .. events_y2025m12; a handful spill into events_y2026m01;
-- later partitions and DEFAULT stay empty),
-- generated from ~850k browsing sessions with a realistic funnel:
--
--   every session      1-3 page_view
--   ~45% of sessions   1-2 search          (payload {"q": "..."})
--   ~90% of sessions   1-6 product_view    (payload {"source": ...})
--   ~28% of sessions   add_to_cart          (of sessions with product views)
--   ~20% of carts      remove_from_cart
--   ~55% of carts      checkout_started
--   ~75% of checkouts  order_placed
--
-- => product_view -> add_to_cart -> checkout -> order is roughly
--    100% -> 28% -> 15% -> 11% of browsing sessions.
-- 65% of sessions belong to a logged-in user (user_id set), the rest are
-- anonymous (user_id NULL). Product popularity is skewed like the orders.
-- Traffic follows a weekly and daily rhythm and grows over the year.
--
-- Deterministic: all choices are hash functions of the session number (see
-- 1101_seed_large.sql for the rnd() technique).
-- Runtime: ~65 s on the reference machine; ~860 MB on disk including indexes.
-- =============================================================================

SET search_path = bazaar, public;
SET work_mem = '256MB';
SET maintenance_work_mem = '512MB';
SET synchronous_commit = off;

CREATE FUNCTION pg_temp.rnd(k bigint, salt bigint) RETURNS float8
LANGUAGE sql IMMUTABLE PARALLEL SAFE
AS $$ SELECT ((hashint8extended(k, salt) >> 11) & 9007199254740991)::float8 / 9007199254740992.0 $$;

CREATE FUNCTION pg_temp.pick(a anyarray, r float8) RETURNS anyelement
LANGUAGE sql IMMUTABLE PARALLEL SAFE
AS $$ SELECT a[1 + floor(r * cardinality(a))::int] $$;

BEGIN;

-- Day-of-year sampling weights (growth x weekday) as cumulative thresholds.
CREATE TEMP TABLE ev_sampling AS
SELECT
    (SELECT array_agg(c ORDER BY d) FROM (
        SELECT d, (coalesce(sum(w) OVER (ORDER BY d ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING), 0)
                   / sum(w) OVER ())::float8 AS c
        FROM (
            SELECT d::date AS d,
                   (1 + 0.5 * extract(doy FROM d) / 365.0)
                   * CASE extract(isodow FROM d) WHEN 6 THEN 1.2 WHEN 7 THEN 1.3 ELSE 1 END
                   * CASE WHEN d::date BETWEEN date '2025-11-24' AND date '2025-12-22' THEN 1.6 ELSE 1 END AS w
            FROM generate_series(timestamp '2025-01-01', timestamp '2025-12-31', interval '1 day') AS d
        ) days
    ) x) AS day_cum,
    (SELECT array_agg(c ORDER BY h) FROM (
        SELECT h, (coalesce(sum(w) OVER (ORDER BY h ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING), 0)
                   / sum(w) OVER ())::float8 AS c
        FROM unnest('{2,1,1,1,1,2,3,5,6,7,7,8,8,7,7,7,8,9,10,11,11,9,6,4}'::float8[]) WITH ORDINALITY AS t (w, h)
    ) x) AS hour_cum;

-- One row per browsing session.
CREATE TEMP TABLE sessions AS
SELECT
    s,
    md5('bazaar-session-' || s)::uuid AS session_id,
    CASE WHEN pg_temp.rnd(s, 1) < 0.65
         THEN 1 + floor(100000 * power(pg_temp.rnd(s, 2), 1.3))::bigint END AS user_id,
    (date '2025-01-01' + (width_bucket(pg_temp.rnd(s, 3), x.day_cum) - 1)
        + make_interval(hours => width_bucket(pg_temp.rnd(s, 4), x.hour_cum) - 1,
                        secs  => floor(pg_temp.rnd(s, 5) * 3600)))
        AT TIME ZONE 'UTC' AS started_at,
    1 + floor(pg_temp.rnd(s, 10) * 3)::int                                        AS n_page_views,
    CASE WHEN pg_temp.rnd(s, 11) < 0.45 THEN 1 + floor(pg_temp.rnd(s, 12) * 2)::int ELSE 0 END AS n_searches,
    CASE WHEN pg_temp.rnd(s, 13) < 0.90 THEN 1 + floor(6 * power(pg_temp.rnd(s, 14), 1.5))::int ELSE 0 END AS n_views,
    pg_temp.rnd(s, 15) AS r_cart,
    pg_temp.rnd(s, 16) AS r_remove,
    pg_temp.rnd(s, 17) AS r_checkout,
    pg_temp.rnd(s, 18) AS r_order
FROM generate_series(1, 850000) AS s
CROSS JOIN ev_sampling x;

-- Events of each session, step by step: "step" orders events within a session.
CREATE TEMP TABLE ev AS
WITH steps AS (
    -- page views first
    SELECT s.s, g AS step, 'page_view' AS event_type, NULL::bigint AS product_id,
           jsonb_build_object('path', pg_temp.pick('{/,/,/,/deals,/c/electronics,/c/books,/c/clothing,/c/home-garden,/account}'::text[], pg_temp.rnd(s.s * 4 + g, 20))) AS payload
    FROM sessions s, generate_series(1, s.n_page_views) AS g
    UNION ALL
    SELECT s.s, 10 + g, 'search', NULL,
           jsonb_build_object('q', pg_temp.pick('{"wireless headphones",laptop,"cast iron skillet","sci-fi novel",hoodie,"yoga mat","board game",tent,"face cream",monitor,"running shoes",kettle,"standing desk","mystery books","usb-c charger",headphnes,lapptop,"crème"}'::text[], pg_temp.rnd(s.s * 4 + g, 21)))
    FROM sessions s, generate_series(1, s.n_searches) AS g
    UNION ALL
    SELECT s.s, 20 + g, 'product_view',
           1 + floor(200000 * power(pg_temp.rnd(s.s * 8 + g, 22), 2))::bigint,
           jsonb_build_object('source', pg_temp.pick('{search,category,home,recommendation,external}'::text[], pg_temp.rnd(s.s * 8 + g, 23)))
    FROM sessions s, generate_series(1, s.n_views) AS g
),
-- the product of the last view is the one that goes into the cart
last_view AS (
    SELECT s, max(step) AS step FROM steps WHERE event_type = 'product_view' GROUP BY s
),
funnel AS (
    SELECT s.s, f.step, f.event_type, st.product_id, f.payload
    FROM sessions s
    JOIN last_view lv ON lv.s = s.s
    JOIN steps st ON st.s = lv.s AND st.step = lv.step
    CROSS JOIN LATERAL (VALUES
        (40, 'add_to_cart',      jsonb_build_object('quantity', 1 + floor(pg_temp.rnd(s.s, 30) * 1.3)::int), s.r_cart < 0.28),
        (41, 'remove_from_cart', '{}'::jsonb, s.r_cart < 0.28 AND s.r_remove < 0.20),
        (50, 'checkout_started', '{}'::jsonb, s.r_cart < 0.28 AND s.r_remove >= 0.20 AND s.r_checkout < 0.69),
        (60, 'order_placed',     '{}'::jsonb, s.r_cart < 0.28 AND s.r_remove >= 0.20 AND s.r_checkout < 0.69 AND s.r_order < 0.75)
    ) AS f (step, event_type, payload, happens)
    WHERE f.happens
)
SELECT
    s.started_at
        -- event k of a session happens (k-1)*75 s + 0..44 s after the start,
        -- so events of one session are always in step order
        + make_interval(secs => (st.step_rank - 1) * 75 + floor(pg_temp.rnd(st.s * 64 + st.step, 40) * 45)) AS occurred_at,
    s.user_id,
    s.session_id,
    st.event_type,
    st.product_id,
    st.payload
FROM (
    SELECT u.*, row_number() OVER (PARTITION BY u.s ORDER BY u.step) AS step_rank
    FROM (SELECT * FROM steps UNION ALL SELECT * FROM funnel) u
) st
JOIN sessions s ON s.s = st.s;

-- Sessions that start on Dec 31 late in the evening may spill past midnight:
-- they simply land in the 2026-01 partition, like they would in real life.
INSERT INTO events (occurred_at, user_id, session_id, event_type, product_id, payload)
SELECT occurred_at, user_id, session_id, event_type, product_id, payload
FROM ev
ORDER BY occurred_at;

COMMIT;

VACUUM (ANALYZE) events;

RESET work_mem;
RESET maintenance_work_mem;
RESET synchronous_commit;
