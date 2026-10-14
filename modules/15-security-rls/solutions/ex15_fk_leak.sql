-- Exercise 15: the FK existence leak, and sealing it inside the policy.
-- Assumes small15.sh --with-1501.

-- === the leaky version ===
CREATE TABLE bazaar.order_notes (
    id        bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    seller_id bigint NOT NULL REFERENCES bazaar.sellers (id),
    order_id  bigint NOT NULL REFERENCES bazaar.orders (id),
    note      text   NOT NULL
);
ALTER TABLE bazaar.order_notes OWNER TO bazaar_owner;
ALTER TABLE bazaar.order_notes ENABLE ROW LEVEL SECURITY;
GRANT SELECT, INSERT ON bazaar.order_notes TO bazaar_seller;
GRANT USAGE ON SEQUENCE bazaar.order_notes_id_seq TO bazaar_seller;

CREATE POLICY order_notes_seller ON bazaar.order_notes TO bazaar_seller
    USING (seller_id = current_seller_id())
    WITH CHECK (seller_id = current_seller_id());   -- no existence check: leaks

-- Demonstrate the oracle (as seller 2; order 40 belongs to seller 1, order 4000 doesn't exist):
SET ROLE bazaar_seller;
SET app.seller_id = '2';
INSERT INTO bazaar.order_notes (seller_id, order_id, note) VALUES (2, 40, 'probe');    -- SUCCEEDS -> order 40 exists
INSERT INTO bazaar.order_notes (seller_id, order_id, note) VALUES (2, 4000, 'probe');  -- FK violation -> doesn't exist
RESET ROLE; RESET app.seller_id;

-- === the sealed version: move an existence check into WITH CHECK, where RLS applies ===
ALTER POLICY order_notes_seller ON bazaar.order_notes
    WITH CHECK (seller_id = current_seller_id()
                AND EXISTS (SELECT 1 FROM bazaar.orders AS o WHERE o.id = order_id));

SET ROLE bazaar_seller;
SET app.seller_id = '2';
-- Both the invisible order and the non-existent order now fail identically:
INSERT INTO bazaar.order_notes (seller_id, order_id, note) VALUES (2, 40, 'probe');    -- RLS violation
INSERT INTO bazaar.order_notes (seller_id, order_id, note) VALUES (2, 4000, 'probe');  -- RLS violation
-- One of seller 2's own orders still works:
INSERT INTO bazaar.order_notes (seller_id, order_id, note)
    SELECT 2, min(id), 'ok' FROM bazaar.orders;                                         -- succeeds
RESET ROLE; RESET app.seller_id;

-- === cleanup ===
DROP TABLE bazaar.order_notes;
