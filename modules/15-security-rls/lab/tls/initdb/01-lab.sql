-- Module 15 TLS lab: runs once, when the container initializes its data directory.
-- Passwords here are for this throw-away lab only.
CREATE DATABASE lab;

CREATE ROLE lab_users NOLOGIN;
CREATE ROLE app   LOGIN PASSWORD 'app_pw' IN ROLE lab_users;
CREATE ROLE alice LOGIN IN ROLE lab_users;       -- no password: she authenticates with a certificate
CREATE ROLE mallory LOGIN PASSWORD 'mallory_pw'; -- not in lab_users: no pg_hba line lets her in

-- A role from "an old installation": its password is stored as an md5 hash.
SET password_encryption = 'md5';
CREATE ROLE legacy LOGIN PASSWORD 'legacy_pw';
RESET password_encryption;
