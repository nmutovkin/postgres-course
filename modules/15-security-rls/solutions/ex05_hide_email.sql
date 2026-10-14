-- Exercise 5: analysts may not read users.email; they get the e-mail domain instead.
BEGIN;

-- A column REVOKE does nothing while a table-level grant exists: remove the
-- table-level SELECT first, then grant the harmless columns one by one.
REVOKE SELECT ON bazaar.users FROM bazaar_readonly;
GRANT SELECT (id, full_name, is_active, created_at) ON bazaar.users TO bazaar_readonly;

-- The masked view runs with its owner's privileges (bazaar_owner may read email).
SET LOCAL ROLE bazaar_owner;
CREATE VIEW bazaar.users_masked AS
SELECT id,
       full_name,
       split_part(lower(email), '@', 2) AS email_domain,
       is_active,
       created_at
FROM bazaar.users;
GRANT SELECT ON bazaar.users_masked TO bazaar_readonly;

COMMIT;
