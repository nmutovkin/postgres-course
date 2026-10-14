-- Exercise 9: sellers see only the reviews of their own products.
BEGIN;

ALTER TABLE bazaar.reviews ENABLE ROW LEVEL SECURITY;

-- Enabling RLS denies every non-owner role without a policy: keep the app and
-- the read-only bundle working first.
CREATE POLICY reviews_all_access ON bazaar.reviews
    TO bazaar_app, bazaar_readonly
    USING (true)
    WITH CHECK (true);

-- The subquery on products runs with the seller's rights, so products' own
-- policy already limits it to the seller's products.
CREATE POLICY reviews_seller_select ON bazaar.reviews
    FOR SELECT
    TO bazaar_seller
    USING (EXISTS (SELECT 1 FROM bazaar.products AS p WHERE p.id = reviews.product_id));

COMMIT;
