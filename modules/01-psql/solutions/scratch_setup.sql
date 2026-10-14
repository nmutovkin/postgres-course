-- =============================================================================
-- scratch_setup.sql -- the scratch objects used by Module 01's theory and exercises.
-- -----------------------------------------------------------------------------
-- The Bazaar tables arrive in module 02, so module 01 practises on a tiny
-- "launch checklist". Run after ./project/bin/reset.sh 01 (or after your own
-- milestone setup). Safe to re-run: it drops and recreates the objects.
--   docker exec -i course-pg psql -X -U postgres -d bazaar < modules/01-psql/solutions/scratch_setup.sql
-- =============================================================================
SET search_path = bazaar, public;

DROP TABLE IF EXISTS launch_checklist CASCADE;   -- CASCADE also drops the view
DROP FUNCTION IF EXISTS tasks_left(text);

CREATE TABLE launch_checklist (
    id      int     GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    task    text    NOT NULL,
    owner   text,
    done    boolean NOT NULL DEFAULT false,
    due     date
);

INSERT INTO launch_checklist (task, owner, done, due) VALUES
    ('Pick a database',        'Ana', true,  '2026-01-05'),
    ('Install PostgreSQL 17',  'Ana', true,  '2026-01-06'),
    ('Create bazaar database', 'Ben', false, '2026-01-07'),
    ('Write .psqlrc',          'Ben', false, NULL),
    ('Design core schema',     NULL,  false, '2026-01-20');

CREATE VIEW open_tasks AS
SELECT id, task, owner, due FROM launch_checklist WHERE NOT done;

CREATE FUNCTION tasks_left(p_owner text) RETURNS bigint
LANGUAGE sql STABLE
AS $$
    SELECT count(*) FROM launch_checklist WHERE owner = p_owner AND NOT done
$$;

COMMENT ON TABLE launch_checklist IS 'Things to do before Bazaar goes live.';
