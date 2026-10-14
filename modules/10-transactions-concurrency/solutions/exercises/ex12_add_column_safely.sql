-- Exercise 12: add a column without ever blocking other sessions for more than 200 ms
SET search_path = bazaar, public;

CREATE OR REPLACE PROCEDURE add_column_safely(
    p_table        regclass,
    p_column       text,
    p_type         text,
    p_max_attempts int DEFAULT 10
)
LANGUAGE plpgsql
AS $$
DECLARE
    v_attempt int := 0;
BEGIN
    IF EXISTS (SELECT 1 FROM pg_attribute
               WHERE attrelid = p_table AND attname = p_column AND NOT attisdropped) THEN
        RAISE NOTICE 'column %.% already exists, nothing to do', p_table, p_column;
        RETURN;
    END IF;

    LOOP
        v_attempt := v_attempt + 1;
        BEGIN
            -- local to this transaction: the caller's setting is untouched afterwards
            PERFORM set_config('lock_timeout', '200ms', true);
            EXECUTE format('ALTER TABLE %s ADD COLUMN %I %s', p_table, p_column, p_type);
            RAISE NOTICE 'attempt %: column %.% added', v_attempt, p_table, p_column;
            RETURN;
        EXCEPTION WHEN lock_not_available THEN
            -- the failed attempt was rolled back to the block's implicit savepoint,
            -- so we hold no lock on p_table while we sleep
            IF v_attempt >= p_max_attempts THEN
                RAISE EXCEPTION 'could not lock % after % attempts', p_table, v_attempt
                    USING ERRCODE = 'lock_not_available';
            END IF;
            RAISE NOTICE 'attempt %: % is busy, retrying in % s', v_attempt, p_table, 0.5 * v_attempt;
            PERFORM pg_sleep(0.5 * v_attempt);
        END;
    END LOOP;
END;
$$;
