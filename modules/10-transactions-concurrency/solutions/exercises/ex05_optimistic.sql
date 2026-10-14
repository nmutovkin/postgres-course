-- Exercise 5: optimistic locking for product edits
SET search_path = bazaar, public;

BEGIN;

ALTER TABLE products
    ADD COLUMN version int NOT NULL DEFAULT 1;

-- Every update bumps the version, whoever makes it (admin UI, scripts, psql).
CREATE FUNCTION bump_version()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.version := OLD.version + 1;
    RETURN NEW;
END;
$$;

CREATE TRIGGER products_bump_version
    BEFORE UPDATE ON products
    FOR EACH ROW EXECUTE FUNCTION bump_version();

-- Save an edit made on top of version p_expected_version.
-- Returns the new version, or raises BZ006 if somebody changed the product
-- in the meantime (and P0002 if it does not exist).
CREATE FUNCTION update_product(
    p_id               bigint,
    p_expected_version int,
    p_title            text,
    p_price            numeric(12,2)
)
RETURNS int
LANGUAGE plpgsql
AS $$
DECLARE
    v_new_version int;
    v_current     int;
BEGIN
    UPDATE products
    SET title = p_title,
        price = p_price
    WHERE id = p_id
      AND version = p_expected_version
    RETURNING version INTO v_new_version;

    IF FOUND THEN
        RETURN v_new_version;
    END IF;

    SELECT version INTO v_current FROM products WHERE id = p_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'product % does not exist', p_id USING ERRCODE = 'no_data_found';
    END IF;
    RAISE EXCEPTION 'product % was changed by someone else', p_id
        USING ERRCODE = 'BZ006',
              DETAIL  = format('your version %s, current version %s', p_expected_version, v_current),
              HINT    = 'Reload the product and apply your change again.';
END;
$$;

COMMIT;
