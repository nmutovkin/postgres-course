-- Exercise 6: sellers cannot modify archived products (but may still archive an active one).
-- RESTRICTIVE: ANDed with the permissive products_seller policy, never widening it.
CREATE POLICY products_seller_not_archived ON bazaar.products
    AS RESTRICTIVE
    FOR UPDATE
    TO bazaar_seller
    USING (status <> 'archived')     -- which existing rows may be updated
    WITH CHECK (true);               -- the new row may be anything (archiving is allowed);
                                     -- without this line USING would be reused as WITH CHECK
