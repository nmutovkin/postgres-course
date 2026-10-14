-- =============================================================================
-- acceptance_functional.sql  --  Module 10 milestone: single-session checks
-- -----------------------------------------------------------------------------
-- Checks the contract of place_order() and the job queue functions on the
-- small dataset (state 10 + your milestone, or ./project/bin/reset.sh 11).
-- Everything runs in one transaction that is rolled back at the end, so the
-- script can be re-run any number of times.
--
--   ./project/bin/psql.sh < modules/10-transactions-concurrency/solutions/acceptance_functional.sql
--
-- Every line of output must start with PASS. Any FAIL (or an unexpected error)
-- points at the requirement that is not met.
-- =============================================================================
SET search_path = bazaar, public;
\set ON_ERROR_STOP on
SET client_min_messages = notice;

BEGIN;

-- Helper: run a statement, return its SQLSTATE ('00000' if it succeeded).
CREATE FUNCTION pg_temp.sqlstate_of(p_sql text)
RETURNS text
LANGUAGE plpgsql
AS $$
BEGIN
    EXECUTE p_sql;
    RETURN '00000';
EXCEPTION WHEN OTHERS THEN
    RETURN SQLSTATE;
END;
$$;

CREATE FUNCTION pg_temp.check(p_name text, p_ok boolean)
RETURNS text
LANGUAGE sql
AS $$ SELECT CASE WHEN p_ok THEN 'PASS  ' ELSE 'FAIL  ' END || p_name $$;

\pset tuples_only on
\pset format unaligned

-- --- error contract -----------------------------------------------------------
SELECT pg_temp.check('BZ001 when stock is insufficient (product 13 is fully reserved)',
    pg_temp.sqlstate_of($q$SELECT place_order(8, '[{"product_id": 13, "quantity": 1}]')$q$) = 'BZ001');
SELECT pg_temp.check('BZ002 for a draft product (14)',
    pg_temp.sqlstate_of($q$SELECT place_order(8, '[{"product_id": 14, "quantity": 1}]')$q$) = 'BZ002');
SELECT pg_temp.check('BZ002 for an archived product (5)',
    pg_temp.sqlstate_of($q$SELECT place_order(8, '[{"product_id": 5, "quantity": 1}]')$q$) = 'BZ002');
SELECT pg_temp.check('BZ002 for a product that does not exist (999)',
    pg_temp.sqlstate_of($q$SELECT place_order(8, '[{"product_id": 999, "quantity": 1}]')$q$) = 'BZ002');
SELECT pg_temp.check('BZ003 for an inactive user (9)',
    pg_temp.sqlstate_of($q$SELECT place_order(9, '[{"product_id": 1, "quantity": 1}]')$q$) = 'BZ003');
SELECT pg_temp.check('BZ003 for a user that does not exist (999)',
    pg_temp.sqlstate_of($q$SELECT place_order(999, '[{"product_id": 1, "quantity": 1}]')$q$) = 'BZ003');
SELECT pg_temp.check('BZ004 for an address of another user (13 belongs to user 11)',
    pg_temp.sqlstate_of($q$SELECT place_order(8, '[{"product_id": 1, "quantity": 1}]', 13)$q$) = 'BZ004');
SELECT pg_temp.check('22023 for an empty cart',
    pg_temp.sqlstate_of($q$SELECT place_order(8, '[]')$q$) = '22023');
SELECT pg_temp.check('22023 for a zero quantity',
    pg_temp.sqlstate_of($q$SELECT place_order(8, '[{"product_id": 1, "quantity": 0}]')$q$) = '22023');
SELECT pg_temp.check('22023 for a fractional quantity',
    pg_temp.sqlstate_of($q$SELECT place_order(8, '[{"product_id": 1, "quantity": 1.5}]')$q$) = '22023');
SELECT pg_temp.check('22023 when product_id is missing',
    pg_temp.sqlstate_of($q$SELECT place_order(8, '[{"quantity": 1}]')$q$) = '22023');

-- --- a failed checkout leaves no trace ------------------------------------------
-- product 1 has stock, product 13 has none: nothing at all may be created/reserved
CREATE TEMP TABLE before_fail AS
SELECT (SELECT count(*) FROM orders) AS orders_count,
       (SELECT reserved FROM inventory WHERE product_id = 1) AS reserved_1;
SELECT pg_temp.check('a cart with one unavailable line fails as a whole',
    pg_temp.sqlstate_of($q$SELECT place_order(8, '[{"product_id": 1, "quantity": 1}, {"product_id": 13, "quantity": 1}]')$q$) = 'BZ001'
    AND (SELECT count(*) FROM orders) = (SELECT orders_count FROM before_fail)
    AND (SELECT reserved FROM inventory WHERE product_id = 1) = (SELECT reserved_1 FROM before_fail));

-- --- a successful multi-seller checkout ---------------------------------------
-- products 24 (seller 2), 1 (seller 1, two lines of 1 and 1), 47 (seller 4)
CREATE TEMP TABLE placed AS
SELECT o AS order_id, row_number() OVER () AS n
FROM place_order(8, '[{"product_id": 24, "quantity": 1},
                      {"product_id": 1,  "quantity": 1},
                      {"product_id": 47, "quantity": 3},
                      {"product_id": 1,  "quantity": 1}]', 9) AS o;

SELECT pg_temp.check('one order per seller (3 orders)',
    (SELECT count(*) FROM placed) = 3);
SELECT pg_temp.check('order ids returned in ascending order',
    (SELECT array_agg(order_id ORDER BY n) = array_agg(order_id ORDER BY order_id) FROM placed));
SELECT pg_temp.check('orders belong to sellers 1, 2, 4 (in id order), are pending, ship to address 9',
    (SELECT array_agg(o.seller_id ORDER BY o.id) FROM orders o JOIN placed p ON p.order_id = o.id) = '{1,2,4}'
    AND (SELECT bool_and(o.status = 'pending' AND o.user_id = 8 AND o.shipping_address_id = 9)
         FROM orders o JOIN placed p ON p.order_id = o.id));
SELECT pg_temp.check('duplicate lines are summed (product 1: one item with quantity 2)',
    (SELECT array_agg(quantity) FROM order_items oi JOIN placed p ON p.order_id = oi.order_id
     WHERE oi.product_id = 1) = '{2}');
SELECT pg_temp.check('items use the current price and totals are right (1998.00, 11.99, 57.00)',
    (SELECT array_agg(o.total ORDER BY o.id) FROM orders o JOIN placed p ON p.order_id = o.id)
    = '{1998.00,11.99,57.00}');
SELECT pg_temp.check('stock reserved: product 1 +2, product 24 +1, product 47 +3',
    (SELECT array_agg(reserved ORDER BY product_id) FROM inventory WHERE product_id IN (1, 24, 47))
    = '{2,1,4}');

-- --- job queue --------------------------------------------------------------------
INSERT INTO jobs (queue, kind, payload, run_at)
VALUES ('acceptance', 'noop', '{"n": 1}', now() - interval '3 minutes'),
       ('acceptance', 'noop', '{"n": 2}', now() - interval '2 minutes'),
       ('acceptance', 'noop', '{"n": 3}', now() + interval '1 hour');   -- not ready yet

CREATE TEMP TABLE got AS
SELECT * FROM dequeue_jobs('acceptance', 'tester', 5);

SELECT pg_temp.check('dequeue_jobs returns only ready jobs (2 of 3)',
    (SELECT count(*) FROM got) = 2);
SELECT pg_temp.check('dequeue_jobs leases them and counts the attempt',
    (SELECT bool_and(locked_by = 'tester' AND locked_at IS NOT NULL AND attempts = 1) FROM got));
SELECT pg_temp.check('leased jobs are not handed out again',
    NOT EXISTS (SELECT 1 FROM dequeue_jobs('acceptance', 'other', 5)));
-- (call first, check in a separate statement: a statement does not see
--  the changes made by functions it calls in its own snapshot)
CREATE TEMP TABLE results AS
SELECT complete_job((SELECT min(id) FROM got))         AS completed,
       fail_job((SELECT max(id) FROM got), 'boom')     AS failed;
SELECT pg_temp.check('complete_job marks the job done and returns true',
    (SELECT completed FROM results)
    AND (SELECT done_at IS NOT NULL FROM jobs WHERE id = (SELECT min(id) FROM got)));
SELECT pg_temp.check('fail_job records the error and schedules a retry in the future',
    (SELECT failed FROM results)
    AND (SELECT last_error = 'boom' AND locked_at IS NULL AND run_at > now()
         FROM jobs WHERE id = (SELECT max(id) FROM got)));
-- simulate a worker that crashed while holding the remaining job's lease
UPDATE jobs SET run_at = now() - interval '1 second', locked_at = now() - interval '10 minutes', locked_by = 'crashed'
WHERE id = (SELECT max(id) FROM got);
SELECT pg_temp.check('a job whose lease expired is picked up by another worker',
    (SELECT locked_by FROM dequeue_jobs('acceptance', 'rescuer', 1)) = 'rescuer');

\pset tuples_only off
\pset format aligned
ROLLBACK;
