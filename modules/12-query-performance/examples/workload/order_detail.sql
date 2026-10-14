-- Customer: order detail page, written the "ORM way": one query for the order,
-- one for its lines, then one query PER LINE for the product (the N+1 pattern;
-- an order has 2-3 lines on average, simulated here with three lookups).
\set oid random(1, 500000)
\set p1 random(1, 200000)
\set p2 random(1, 200000)
\set p3 random(1, 200000)
SELECT id, user_id, seller_id, status, total, created_at FROM bazaar.orders WHERE id = :oid;
SELECT product_id, quantity, unit_price FROM bazaar.order_items WHERE order_id = :oid;
SELECT id, title, price FROM bazaar.products WHERE id = :p1;
SELECT id, title, price FROM bazaar.products WHERE id = :p2;
SELECT id, title, price FROM bazaar.products WHERE id = :p3;
SELECT id, amount, provider, status FROM bazaar.payments WHERE order_id = :oid ORDER BY created_at;
