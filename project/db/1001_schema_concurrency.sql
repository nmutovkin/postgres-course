-- =============================================================================
-- 1001_schema_concurrency.sql  --  Module 10: Transactions and Concurrency
-- -----------------------------------------------------------------------------
-- 1. place_order(): checkout as one atomic, concurrency-safe database call.
--    It splits a cart into one order per seller, reserves stock under row
--    locks taken in a fixed order (deadlock avoidance), and fails cleanly with
--    a dedicated SQLSTATE when stock is insufficient.
-- 2. jobs + dequeue_jobs() / complete_job() / fail_job(): a job queue on plain
--    Postgres using FOR UPDATE SKIP LOCKED, with leases and exponential backoff.
--
-- Error codes raised by place_order() (catch them by SQLSTATE in the app):
--     BZ001  insufficient stock          (DETAIL has requested/available)
--     BZ002  product missing / not active
--     BZ003  user missing / inactive
--     BZ004  shipping address does not belong to the user
--     22023  invalid_parameter_value     (malformed p_items)
-- Custom SQLSTATEs are any 5 characters outside the standard classes; a class
-- of our own ('BZ') keeps them from colliding with Postgres' codes.
-- =============================================================================

SET search_path = bazaar, public;

BEGIN;

-- -----------------------------------------------------------------------------
-- 1. place_order
-- -----------------------------------------------------------------------------
-- p_items: [{"product_id": 1, "quantity": 2}, {"product_id": 24, "quantity": 1}]
-- Returns the ids of the created orders (one per seller), ascending.
--
-- Concurrency design:
--   * Stock is checked and reserved while holding a row lock (FOR UPDATE) on
--     each inventory row, so two checkouts cannot both take the last unit:
--     the second waits for the first to commit, then re-reads the row.
--   * Locks are always acquired in product_id order. If transaction A locked
--     product 1 then 2, while B locked 2 then 1, each could wait for the other
--     forever (a deadlock; Postgres would abort one after deadlock_timeout).
--     A global lock order makes that cycle impossible.
--   * Everything happens inside the caller's transaction: if anything fails,
--     no order, item or reservation survives.
--   * orders.total is NOT computed here: the statement-level trigger from
--     0901 (order_items_maintain_total) adds the items' value to the order.
CREATE FUNCTION place_order(
    p_user_id             bigint,
    p_items               jsonb,
    p_shipping_address_id bigint DEFAULT NULL
)
RETURNS SETOF bigint
LANGUAGE plpgsql
AS $$
DECLARE
    v_requested  int;      -- number of distinct products requested
    v_locked     int := 0; -- number of inventory rows locked
    v_seller     bigint;
    v_order_id   bigint;
    v_pids       bigint[]; -- requested product ids, ascending
    v_qtys       int[];    -- matching quantities
    r            record;
BEGIN
    -- --- validate input ---------------------------------------------------------
    IF p_items IS NULL OR jsonb_typeof(p_items) <> 'array' OR jsonb_array_length(p_items) = 0 THEN
        RAISE EXCEPTION 'p_items must be a non-empty JSON array'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM jsonb_array_elements(p_items) AS e
        WHERE jsonb_typeof(e -> 'product_id') IS DISTINCT FROM 'number'
           OR jsonb_typeof(e -> 'quantity')   IS DISTINCT FROM 'number'
           OR (e ->> 'quantity')::numeric <= 0
           OR (e ->> 'quantity')::numeric <> trunc((e ->> 'quantity')::numeric)
    ) THEN
        RAISE EXCEPTION 'each item needs a numeric product_id and a positive integer quantity'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    PERFORM 1 FROM users WHERE id = p_user_id AND is_active;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'user % does not exist or is inactive', p_user_id
            USING ERRCODE = 'BZ003';
    END IF;

    IF p_shipping_address_id IS NOT NULL THEN
        PERFORM 1 FROM addresses WHERE id = p_shipping_address_id AND user_id = p_user_id;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'address % does not belong to user %', p_shipping_address_id, p_user_id
                USING ERRCODE = 'BZ004';
        END IF;
    END IF;

    -- Normalise the request into two parallel arrays, one entry per product
    -- (duplicate lines for the same product are summed). Below,
    -- unnest(v_pids, v_qtys) turns them back into rows (product_id, quantity).
    -- (Arrays instead of a temp table: creating a temp table per checkout
    -- churns the system catalogs and breaks behind transaction-mode poolers.)
    SELECT array_agg(product_id ORDER BY product_id),
           array_agg(quantity   ORDER BY product_id)
    INTO v_pids, v_qtys
    FROM (
        SELECT (e ->> 'product_id')::bigint AS product_id,
               sum((e ->> 'quantity')::int)  AS quantity
        FROM jsonb_array_elements(p_items) AS e
        GROUP BY 1
    ) s;
    v_requested := cardinality(v_pids);

    -- --- lock inventory rows in a deterministic order and check stock ---------
    -- In the plan, LockRows sits ABOVE the Sort, so rows are locked in
    -- product_id order. FOR UPDATE OF i locks only inventory rows (not the
    -- joined products rows).
    FOR r IN
        SELECT i.product_id, i.quantity, i.reserved, q.quantity AS wanted, p.status
        FROM inventory i
        JOIN unnest(v_pids, v_qtys) AS q (product_id, quantity) ON q.product_id = i.product_id
        JOIN products p ON p.id = i.product_id
        ORDER BY i.product_id
        FOR UPDATE OF i
    LOOP
        v_locked := v_locked + 1;
        IF r.status <> 'active' THEN
            RAISE EXCEPTION 'product % is not available for sale', r.product_id
                USING ERRCODE = 'BZ002';
        END IF;
        IF r.quantity - r.reserved < r.wanted THEN
            RAISE EXCEPTION 'insufficient stock for product %', r.product_id
                USING ERRCODE = 'BZ001',
                      DETAIL  = format('requested %s, available %s', r.wanted, r.quantity - r.reserved);
        END IF;
    END LOOP;

    IF v_locked < v_requested THEN
        RAISE EXCEPTION 'product(s) % do not exist',
            (SELECT array_agg(q.product_id ORDER BY q.product_id)
             FROM unnest(v_pids) AS q (product_id)
             WHERE NOT EXISTS (SELECT 1 FROM inventory i WHERE i.product_id = q.product_id))
            USING ERRCODE = 'BZ002';
    END IF;

    -- --- reserve the stock (rows are already locked by us) ----------------------
    UPDATE inventory i
    SET reserved = i.reserved + q.quantity
    FROM unnest(v_pids, v_qtys) AS q (product_id, quantity)
    WHERE i.product_id = q.product_id;

    -- --- one order per seller, items at the current price ----------------------
    FOR v_seller IN
        SELECT DISTINCT p.seller_id
        FROM products p
        WHERE p.id = ANY (v_pids)
        ORDER BY p.seller_id
    LOOP
        INSERT INTO orders (user_id, seller_id, shipping_address_id)
        VALUES (p_user_id, v_seller, p_shipping_address_id)
        RETURNING id INTO v_order_id;

        -- One multi-row INSERT => the total trigger fires once for this order.
        INSERT INTO order_items (order_id, product_id, quantity, unit_price)
        SELECT v_order_id, p.id, q.quantity, p.price
        FROM unnest(v_pids, v_qtys) AS q (product_id, quantity)
        JOIN products p ON p.id = q.product_id
        WHERE p.seller_id = v_seller;

        RETURN NEXT v_order_id;
    END LOOP;

    RETURN;
END;
$$;

COMMENT ON FUNCTION place_order(bigint, jsonb, bigint) IS
    'Checkout: reserves stock (locks in product_id order) and creates one order per seller. Errors: BZ001 insufficient stock, BZ002 product unavailable, BZ003 user, BZ004 address.';

-- -----------------------------------------------------------------------------
-- 2. A job queue
-- -----------------------------------------------------------------------------
-- Lifecycle of a row:
--   ready     done_at IS NULL, run_at <= now(), not leased, attempts < max_attempts
--   leased    locked_at/locked_by set by dequeue_jobs(); the lease expires after
--             p_lease, so a job whose worker crashed is picked up again
--   done      done_at IS NOT NULL                        (complete_job)
--   retrying  run_at in the future, lease cleared        (fail_job)
--   dead      attempts >= max_attempts and done_at NULL  (needs a human)
--
-- Workers dequeue in a short transaction and COMMIT, then do the work, then call
-- complete_job()/fail_job(). The alternative (keep the transaction and the row
-- lock open while working) needs no lease, but holds a connection and a
-- transaction open for the whole duration of the job.
CREATE TABLE jobs (
    id            bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    queue         text        NOT NULL DEFAULT 'default',
    kind          text        NOT NULL,                    -- e.g. 'send_email'
    payload       jsonb       NOT NULL DEFAULT '{}',
    run_at        timestamptz NOT NULL DEFAULT now(),      -- not before this time
    attempts      int         NOT NULL DEFAULT 0,
    max_attempts  int         NOT NULL DEFAULT 5,
    locked_at     timestamptz,                             -- lease start
    locked_by     text,                                    -- worker id
    last_error    text,
    done_at       timestamptz,
    created_at    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT jobs_attempts_nonnegative CHECK (attempts >= 0),
    CONSTRAINT jobs_max_attempts_positive CHECK (max_attempts > 0)
);

-- The queue's hot path: "oldest ready job in queue X". A partial index over
-- unfinished jobs stays small however many finished jobs pile up.
CREATE INDEX jobs_ready_idx
    ON jobs (queue, run_at)
    WHERE done_at IS NULL;

COMMENT ON TABLE jobs IS 'Background job queue; consume with dequeue_jobs() (FOR UPDATE SKIP LOCKED).';

-- dequeue_jobs: claim up to p_limit ready jobs for p_worker.
-- FOR UPDATE SKIP LOCKED makes concurrent workers skip rows another worker is
-- claiming right now instead of queueing behind its lock, so N workers get N
-- different jobs without blocking each other.
CREATE FUNCTION dequeue_jobs(
    p_queue  text,
    p_worker text,
    p_limit  int      DEFAULT 1,
    p_lease  interval DEFAULT interval '5 minutes'
)
RETURNS SETOF jobs
LANGUAGE sql
AS $$
    UPDATE jobs j
    SET locked_at = now(),
        locked_by = p_worker,
        attempts  = j.attempts + 1
    WHERE j.id IN (
        SELECT c.id
        FROM jobs c
        WHERE c.queue = p_queue
          AND c.done_at IS NULL
          AND c.run_at <= now()
          AND c.attempts < c.max_attempts
          AND (c.locked_at IS NULL OR c.locked_at < now() - p_lease)  -- free or lease expired
        ORDER BY c.run_at, c.id
        LIMIT p_limit
        FOR UPDATE SKIP LOCKED
    )
    RETURNING j.*;
$$;

COMMENT ON FUNCTION dequeue_jobs(text, text, int, interval) IS
    'Claims up to p_limit ready jobs (SKIP LOCKED) and leases them to p_worker.';

-- complete_job: mark a job done. Returns false if it was not leased (e.g. the
-- lease expired and another worker already finished it).
CREATE FUNCTION complete_job(p_id bigint)
RETURNS boolean
LANGUAGE sql
AS $$
    UPDATE jobs
    SET done_at   = now(),
        locked_at = NULL,
        last_error = NULL
    WHERE id = p_id
      AND done_at IS NULL
    RETURNING true;
$$;

-- fail_job: record the error and schedule a retry with exponential backoff:
-- 10s, 20s, 40s, 80s, ... capped at 1 hour. attempts was already incremented
-- by dequeue_jobs(); once attempts reaches max_attempts the job is dead.
CREATE FUNCTION fail_job(p_id bigint, p_error text)
RETURNS boolean
LANGUAGE sql
AS $$
    UPDATE jobs
    SET last_error = p_error,
        locked_at  = NULL,
        locked_by  = NULL,
        run_at     = now() + least(interval '10 seconds' * power(2, greatest(attempts - 1, 0)),
                               interval '1 hour')
    WHERE id = p_id
      AND done_at IS NULL
    RETURNING true;
$$;

COMMIT;
