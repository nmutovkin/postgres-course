-- Exercise 12: prefix_tsquery(input) -- a search-as-you-type tsquery that
-- survives stop words, stemming, and hostile input.
SET search_path = bazaar, public;

CREATE OR REPLACE FUNCTION prefix_tsquery(input text)
RETURNS tsquery
LANGUAGE plpgsql
IMMUTABLE PARALLEL SAFE
AS $$
DECLARE
    v_words  text[];
    v_word   text;
    v_part   tsquery;
    v_simple tsquery;
    v_result tsquery := ''::tsquery;          -- the empty query
    i        int;
BEGIN
    -- Keep letters, digits and hyphens only, so that no tsquery syntax
    -- (quotes, &, |, !, :, parentheses, backslashes) can get through.
    v_words := array_remove(
        regexp_split_to_array(
            regexp_replace(lower(coalesce(input, '')), '[^[:alnum:]\s-]', '', 'g'),
            '\s+'),
        '');

    FOR i IN 1 .. coalesce(array_length(v_words, 1), 0) LOOP
        v_word := v_words[i];
        CONTINUE WHEN v_word ~ '^-*$';

        IF i < array_length(v_words, 1) THEN
            -- A finished word: normal stemming. A stop word gives an empty
            -- query, which && simply ignores.
            v_part := plainto_tsquery('english', v_word);
        ELSE
            -- The word being typed:
            --   english prefix: 'headphones' -> 'headphon':*  (matches the stemmed index)
            --   simple prefix:  'he'         -> 'he':*        (survives the stop-word list)
            v_part   := to_tsquery('english', '''' || v_word || ''':*');
            v_simple := to_tsquery('simple',  '''' || v_word || ''':*');
            IF v_part::text <> v_simple::text THEN   -- skip 'wi':* | 'wi':*
                v_part := v_part || v_simple;
            END IF;
        END IF;

        v_result := v_result && v_part;
    END LOOP;

    RETURN v_result;
END;
$$;
