-- =============================================================================
-- 1201_schema_stats.sql  --  Module 12: Query Planning and Performance
-- -----------------------------------------------------------------------------
-- 1. pg_stat_statements: per-query-shape execution statistics.
--    The library must be loaded at server start (shared_preload_libraries,
--    configured in docker-compose.yml); CREATE EXTENSION then creates the
--    pg_stat_statements view and functions in this database.
--
-- 2. Extended statistics on a correlated column pair.
--    The planner assumes columns are independent, so for
--        WHERE country_code = 'DE' AND city = 'Berlin'
--    it multiplies the two selectivities: P(DE) * P(Berlin) ~ 0.10 * 0.015,
--    estimating ~180 rows. But city (almost) determines country: every Berlin
--    is in DE, so the real answer is P(Berlin) ~ 1,800 rows, a 10x
--    underestimate that can flip join strategies in bigger queries.
--    (Measured on the large dataset: estimate 179, actual 1,827.)
--
--    CREATE STATISTICS ... (dependencies, ndistinct, mcv) teaches ANALYZE to
--    measure the combination:
--      dependencies  "city => country_code" holds for ~98% of rows (not 100%:
--                    the data has a London in GB *and* in CA, Ontario)
--      ndistinct     number of distinct (country_code, city) pairs, for GROUP BY
--      mcv           most common value COMBINATIONS with their frequencies -
--                    exact for frequent pairs, so ('CA', 'London') is estimated
--                    correctly too
--    After ANALYZE the Berlin estimate becomes ~1,850.
--
--    Inspect with:  SELECT * FROM pg_stats_ext WHERE statistics_name = 'addresses_country_city_stats';
-- =============================================================================

SET search_path = bazaar, public;

CREATE EXTENSION IF NOT EXISTS pg_stat_statements WITH SCHEMA public;

CREATE STATISTICS addresses_country_city_stats (dependencies, ndistinct, mcv)
    ON country_code, city
    FROM addresses;

-- Extended statistics are only filled in by ANALYZE.
ANALYZE addresses;

-- Start module 12 with a clean slate (the reset itself ran many statements).
SELECT pg_stat_statements_reset() \g /dev/null
