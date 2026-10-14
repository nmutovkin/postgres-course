-- Exercise 13: idempotent checkout with idempotency keys
SET search_path = bazaar, public;

BEGIN;

CREATE TABLE checkout_requests (
    idempotency_key text        PRIMARY KEY,
    user_id         bigint      NOT NULL REFERENCES users (id),
    request_hash    text        NOT NULL,       -- fingerprint of user + cart + address
    order_ids       bigint[],                   -- NULL only while the first call is running
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE FUNCTION place_order_idempotent(
    p_key                 text,
    p_user_id             bigint,
    p_items               jsonb,
    p_shipping_address_id bigint DEFAULT NULL
)
RETURNS SETOF bigint
LANGUAGE plpgsql
AS $$
DECLARE
    v_hash  text := md5(jsonb_build_array(p_user_id, p_items, p_shipping_address_id)::text);
    v_req   checkout_requests;
    v_ids   bigint[];
BEGIN
    -- Claim the key. If another transaction inserted it and has not committed,
    -- this waits for it (unique index), then either inserts (the other rolled
    -- back) or does nothing (the other committed).
    INSERT INTO checkout_requests (idempotency_key, user_id, request_hash)
    VALUES (p_key, p_user_id, v_hash)
    ON CONFLICT (idempotency_key) DO NOTHING
    RETURNING * INTO v_req;

    IF v_req.idempotency_key IS NOT NULL THEN
        -- We own the key: do the real work in the same transaction.
        SELECT array_agg(o ORDER BY o) INTO v_ids
        FROM place_order(p_user_id, p_items, p_shipping_address_id) AS o;

        UPDATE checkout_requests SET order_ids = v_ids WHERE idempotency_key = p_key;
        RETURN QUERY SELECT unnest(v_ids);
        RETURN;
    END IF;

    -- Someone already handled this key (and committed). Replay their answer.
    SELECT * INTO v_req FROM checkout_requests WHERE idempotency_key = p_key;
    IF v_req.request_hash <> v_hash THEN
        RAISE EXCEPTION 'idempotency key % was already used for a different request', p_key
            USING ERRCODE = 'BZ007';
    END IF;
    RETURN QUERY SELECT unnest(v_req.order_ids);
END;
$$;

COMMIT;
