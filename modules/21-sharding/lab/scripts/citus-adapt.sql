-- =============================================================================
-- citus-adapt.sql  --  Module 21 lab: make Bazaar's views and checkout work on
-- the distributed schema. Run after distribute.sql:
--
--   docker exec -i m21-coord psql -U postgres -d bazaar -v ON_ERROR_STOP=1 < scripts/citus-adapt.sql
--
-- Everything here is lab-only: project/db keeps the single-node versions.
-- =============================================================================

SET search_path = bazaar, public;

BEGIN;
SET LOCAL ROLE bazaar_owner;
-- Citus copies new functions to every node; mixing that with the parallel
-- REFRESH below in one transaction needs one connection per node.
SET LOCAL citus.multi_shard_modify_mode = 'sequential';

-- -----------------------------------------------------------------------------
-- 1. active_products joined inventory ON i.product_id = p.id. Both tables are
--    distributed by seller_id, but that join condition does not mention it, so
--    Citus cannot prove that matching rows live on the same shard:
--      ERROR: complex joins are only supported when all distributed tables are
--             co-located and joined on their distribution columns
--    Adding i.seller_id = p.seller_id makes it a co-located join. (sellers and
--    categories are reference tables: joins to them are always local.)
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW active_products AS
SELECT
    p.id,
    p.seller_id,
    s.shop_name,
    p.category_id,
    c.name                                         AS category_name,
    p.sku,
    p.title,
    p.description,
    p.price,
    p.attributes,
    p.tags,
    coalesce(i.quantity - i.reserved, 0)           AS available,
    p.created_at,
    p.updated_at
FROM products p
JOIN sellers s         ON s.id = p.seller_id
JOIN categories c      ON c.id = p.category_id
LEFT JOIN inventory i  ON i.seller_id = p.seller_id AND i.product_id = p.id
WHERE p.status = 'active';

-- -----------------------------------------------------------------------------
-- 2. seller_sales_daily: same problem (the subquery grouped order_items by
--    order_id only and joined on order_id). Rebuilt with seller_id in the
--    GROUP BY and the join. It stays a LOCAL materialized view on the
--    coordinator: REFRESH runs one distributed query and stores the result here.
-- -----------------------------------------------------------------------------
DROP MATERIALIZED VIEW seller_sales_daily;
CREATE MATERIALIZED VIEW seller_sales_daily AS
SELECT
    o.seller_id,
    (o.created_at AT TIME ZONE 'UTC')::date  AS day,
    count(*)                                 AS orders_count,
    sum(i.items)                             AS items_sold,
    sum(o.total)                             AS revenue
FROM orders o
JOIN (
    SELECT seller_id, order_id, sum(quantity) AS items
    FROM order_items
    GROUP BY seller_id, order_id
) i ON i.seller_id = o.seller_id AND i.order_id = o.id
WHERE o.status IN ('paid', 'shipped', 'delivered')
GROUP BY o.seller_id, (o.created_at AT TIME ZONE 'UTC')::date;

CREATE UNIQUE INDEX seller_sales_daily_seller_id_day_key
    ON seller_sales_daily (seller_id, day);
GRANT SELECT ON seller_sales_daily TO bazaar_readonly, bazaar_app;

-- -----------------------------------------------------------------------------
-- 3. place_order_sharded(): checkout on the distributed schema.
--
-- The original place_order() fails on Citus with
--     ERROR: could not run distributed query with FOR UPDATE/SHARE commands
--     HINT:  Consider using an equality filter on the distributed table's partition column.
-- because its locking query names inventory rows by product_id only: Citus
-- would have to lock rows on every shard, which it refuses to do. It also
-- inserted order_items without seller_id (the 2101 fill trigger is gone) and
-- relied on the orders.total trigger (gone too).
--
-- Changes:
--   * items carry seller_id: [{"seller_id": 1, "product_id": 1, "quantity": 2}]
--     The cart UI knows each product's seller, and with the shard key every
--     statement below is a single-shard ("router") statement. Lines without
--     seller_id are resolved with ONE lookup on products by id, which has to
--     ask all 32 shards: it works, but it is the expensive path.
--   * stock is locked per seller with  WHERE i.seller_id = v_seller AND
--     i.product_id = ANY (...)  FOR UPDATE: a router query, which Citus allows.
--     Lock order is (seller_id, product_id) everywhere, so two checkouts can
--     still not deadlock each other.
--   * orders.total is computed here, from the same rows that are inserted.
--   * A cart with products of two sellers touches two shards, possibly on two
--     nodes: Citus then commits with two-phase commit (README section 15).
-- Error codes are the same as place_order(): BZ001..BZ004, 22023.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION place_order_sharded(
    p_user_id             bigint,
    p_items               jsonb,
    p_shipping_address_id bigint DEFAULT NULL
)
RETURNS SETOF bigint
LANGUAGE plpgsql
AS $$
DECLARE
    v_seller    bigint;
    v_order_id  bigint;
    v_total     numeric(12,2);
    v_pids      bigint[];
    v_qtys      int[];
    v_found     int;
    v_missing   bigint[];   -- product ids that came without a seller_id
    v_mpids     bigint[];   -- ... and what the lookup found: product ids
    v_msellers  bigint[];   --     with their seller ids
    v_sellers   bigint[];   -- normalised lines: seller_id,
    v_lpids     bigint[];   --                   product_id,
    v_lqtys     int[];      --                   quantity
    r           record;
BEGIN
    IF p_items IS NULL OR jsonb_typeof(p_items) <> 'array' OR jsonb_array_length(p_items) = 0 THEN
        RAISE EXCEPTION 'p_items must be a non-empty JSON array'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM jsonb_array_elements(p_items) AS e
        WHERE jsonb_typeof(e -> 'product_id') IS DISTINCT FROM 'number'
           OR jsonb_typeof(e -> 'quantity')   IS DISTINCT FROM 'number'
           OR (e -> 'seller_id' IS NOT NULL AND jsonb_typeof(e -> 'seller_id') <> 'number')
           OR (e ->> 'quantity')::numeric <= 0
           OR (e ->> 'quantity')::numeric <> trunc((e ->> 'quantity')::numeric)
    ) THEN
        RAISE EXCEPTION 'each item needs a numeric product_id and a positive integer quantity'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- users and addresses are reference tables: these checks read the local copy.
    PERFORM 1 FROM users WHERE id = p_user_id AND is_active;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'user % does not exist or is inactive', p_user_id USING ERRCODE = 'BZ003';
    END IF;
    IF p_shipping_address_id IS NOT NULL THEN
        PERFORM 1 FROM addresses WHERE id = p_shipping_address_id AND user_id = p_user_id;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'address % does not belong to user %', p_shipping_address_id, p_user_id
                USING ERRCODE = 'BZ004';
        END IF;
    END IF;

    -- Lines without seller_id: ONE lookup by product id. products is
    -- distributed by seller_id, so "WHERE id = ANY (...)" asks every shard.
    SELECT array_agg((e ->> 'product_id')::bigint)
    INTO v_missing
    FROM jsonb_array_elements(p_items) AS e
    WHERE e -> 'seller_id' IS NULL;
    IF v_missing IS NOT NULL THEN
        SELECT array_agg(p.id), array_agg(p.seller_id)
        INTO v_mpids, v_msellers
        FROM products p
        WHERE p.id = ANY (v_missing);
    END IF;

    -- Normalise into three parallel arrays, one entry per (seller, product),
    -- sorted by (seller_id, product_id); duplicate lines are summed.
    -- (Arrays rather than a temp table, as in place_order(): no catalog churn.)
    SELECT array_agg(seller_id ORDER BY seller_id, product_id),
           array_agg(product_id ORDER BY seller_id, product_id),
           array_agg(quantity ORDER BY seller_id, product_id)
    INTO v_sellers, v_lpids, v_lqtys
    FROM (
        SELECT coalesce((e ->> 'seller_id')::bigint,
                        v_msellers[array_position(v_mpids, (e ->> 'product_id')::bigint)]) AS seller_id,
               (e ->> 'product_id')::bigint AS product_id,
               sum((e ->> 'quantity')::int)::int AS quantity
        FROM jsonb_array_elements(p_items) AS e
        GROUP BY 1, 2
    ) l;

    IF array_position(v_sellers, NULL) IS NOT NULL THEN
        RAISE EXCEPTION 'product(s) % do not exist',
            (SELECT array_agg(pid ORDER BY pid) FROM unnest(v_sellers, v_lpids) AS x (sid, pid) WHERE sid IS NULL)
            USING ERRCODE = 'BZ002';
    END IF;

    -- One seller at a time, in seller_id order: lock, check, reserve, insert.
    FOR v_seller IN SELECT DISTINCT s FROM unnest(v_sellers) AS s ORDER BY s LOOP
        SELECT array_agg(pid ORDER BY pid), array_agg(qty ORDER BY pid)
        INTO v_pids, v_qtys
        FROM unnest(v_sellers, v_lpids, v_lqtys) AS x (sid, pid, qty)
        WHERE sid = v_seller;

        v_found := 0;
        -- Router query: every table is filtered on seller_id = v_seller.
        FOR r IN
            SELECT i.product_id, i.quantity, i.reserved, q.quantity AS wanted, p.status
            FROM inventory i
            JOIN products p ON p.seller_id = i.seller_id AND p.id = i.product_id
            JOIN unnest(v_pids, v_qtys) AS q (product_id, quantity) ON q.product_id = i.product_id
            WHERE i.seller_id = v_seller
            ORDER BY i.product_id
            FOR UPDATE OF i
        LOOP
            v_found := v_found + 1;
            IF r.status <> 'active' THEN
                RAISE EXCEPTION 'product % is not available for sale', r.product_id USING ERRCODE = 'BZ002';
            END IF;
            IF r.quantity - r.reserved < r.wanted THEN
                RAISE EXCEPTION 'insufficient stock for product %', r.product_id
                    USING ERRCODE = 'BZ001',
                          DETAIL  = format('requested %s, available %s', r.wanted, r.quantity - r.reserved);
            END IF;
        END LOOP;
        IF v_found < cardinality(v_pids) THEN
            RAISE EXCEPTION 'product(s) of seller % do not exist: %', v_seller, v_pids USING ERRCODE = 'BZ002';
        END IF;

        UPDATE inventory i
        SET reserved = i.reserved + q.quantity
        FROM unnest(v_pids, v_qtys) AS q (product_id, quantity)
        WHERE i.seller_id = v_seller AND i.product_id = q.product_id;

        -- Total first, then a plain INSERT ... VALUES: Citus routes it straight
        -- to the shard. (INSERT ... SELECT from unnest() would be computed on
        -- the coordinator and shipped as an "intermediate result" first.)
        SELECT sum(q.quantity * p.price)
        INTO v_total
        FROM unnest(v_pids, v_qtys) AS q (product_id, quantity)
        JOIN products p ON p.seller_id = v_seller AND p.id = q.product_id;

        INSERT INTO orders (user_id, seller_id, shipping_address_id, total)
        VALUES (p_user_id, v_seller, p_shipping_address_id, v_total)
        RETURNING id INTO v_order_id;

        -- One INSERT ... VALUES per line: each is a router insert. (A single
        -- INSERT ... SELECT FROM unnest(...) is evaluated on the coordinator and
        -- shipped to the shard with COPY, and Citus refuses COPY into a table
        -- with row-level security when the caller is subject to RLS, e.g.
        -- bazaar_app: "COPY FROM not supported with row-level security".)
        FOR r IN
            SELECT p.id, q.quantity, p.price
            FROM unnest(v_pids, v_qtys) AS q (product_id, quantity)
            JOIN products p ON p.seller_id = v_seller AND p.id = q.product_id
            ORDER BY p.id
        LOOP
            INSERT INTO order_items (seller_id, order_id, product_id, quantity, unit_price)
            VALUES (v_seller, v_order_id, r.id, r.quantity, r.price);
        END LOOP;

        RETURN NEXT v_order_id;
    END LOOP;
    RETURN;
END;
$$;

GRANT EXECUTE ON FUNCTION place_order_sharded(bigint, jsonb, bigint) TO bazaar_app;

COMMIT;
