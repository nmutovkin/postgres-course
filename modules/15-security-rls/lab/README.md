# Module 15 lab

Two things live here:

| Path | What it is |
|---|---|
| [`small15.sh`](small15.sh) | Builds the schema of state 15 on the **small** dataset (fast, about 2 MB), optionally with the reference `1501` applied. Most of the module's examples use it. |
| [`tls/`](tls/) | A Docker Compose lab: PostgreSQL 17 with TLS, a private certificate authority, client-certificate authentication, and a strict `pg_hba.conf`. Used in sections 14 and 15 of the theory and in exercises 11 and 12. |

## small15.sh

```bash
./modules/15-security-rls/lab/small15.sh               # start of module 15, small data
./modules/15-security-rls/lab/small15.sh --with-1501   # ... plus the reference 1501_schema_security.sql
```

It runs `./project/bin/reset.sh 11` (which loads the small dataset) and then applies only the schema files of modules 11 to 14 (`1102`, `1201`, `1301`, `1401`). It skips the large seeds `1101` and `1402`. The result has the same tables, indexes, functions and partitions as `reset.sh 15`, with the hand-readable data documented in `project/db/NOTES.md`. It uses the container named in `COURSE_PG_CONTAINER` (default `course-pg`), as `reset.sh` does.

Roles are cluster-wide, so neither script removes them. To get rid of the module's roles completely (for example, before you create your own versions in the milestone), run this on the course container:

```bash
docker exec -i course-pg psql -U postgres -d postgres <<'SQL'
DROP DATABASE IF EXISTS bazaar WITH (FORCE);   -- removes every object and privilege in it
DROP ROLE IF EXISTS bazaar_seller, bazaar_analyst, bazaar_app, bazaar_readonly, bazaar_owner;
SQL
```

Then rebuild the database with `reset.sh` or `small15.sh`.

## tls/: PostgreSQL with TLS

```text
tls/
├── make-certs.sh       # creates certs/: a lab CA, a server certificate, a client certificate for alice
├── compose.yaml        # services db (the server) and client (psql + openssl toolbox)
├── conf/pg_hba.conf    # the lab's authentication rules (mounted read-only, edit on your machine)
├── initdb/01-lab.sql   # database lab; roles app, alice, mallory, legacy and group lab_users
└── tls-demo.sh         # tries every sslmode and authentication method and prints the outcome
```

| Service | Inside the Compose network | On your machine |
|---|---|---|
| `db` | `db:5432`, also `db.bazaar.internal:5432` (the name in the certificate) | `localhost:55433` (`LAB_TLS_PORT`) |
| `client` | runs `sleep`; the certificates are copied to `/lab/certs` | |

Credentials (this lab only): `app` / `app_pw`, `legacy` / `legacy_pw` (stored as an md5 hash), `mallory` / `mallory_pw` (no `pg_hba.conf` line admits her), `alice` (certificate only, no password), `postgres` / `postgres`.

### Usage

```bash
cd modules/15-security-rls/lab/tls
./make-certs.sh                 # once; uses openssl inside the postgres:17 image
docker compose up -d --wait
./tls-demo.sh                   # the sslmode matrix and the authentication checks
docker compose down -v          # remove containers and the data volume
rm -rf certs                    # if you also want the keys gone
```

Interactive sessions:

```bash
# as the administrator, through the Unix socket (the "local ... peer" line)
docker compose exec -u postgres db psql -d lab

# as app, over TLS with full verification
docker compose exec client psql "host=db.bazaar.internal dbname=lab user=app password=app_pw sslmode=verify-full sslrootcert=/lab/certs/ca.crt"

# as alice, with her certificate
docker compose exec client psql "host=db.bazaar.internal dbname=lab user=alice sslmode=verify-full sslrootcert=/lab/certs/ca.crt sslcert=/lab/certs/alice.crt sslkey=/lab/certs/alice.key"
```

After editing `conf/pg_hba.conf`, reload and check for mistakes:

```bash
docker compose exec -u postgres db psql -d lab -c "SELECT pg_reload_conf()" \
  -c "SELECT line_number, type, database, user_name, auth_method, error FROM pg_hba_file_rules"
```

The server logs every connection attempt and the `pg_hba.conf` line it matched (`log_connections = on`): `docker compose logs db | tail -20`.

### What the expected output looks like

`./tls-demo.sh` on a fresh lab prints (IP addresses vary):

```text
== 1. sslmode, connecting by the name in the certificate (db.bazaar.internal)
sslmode=disable                              FAIL pg_hba.conf rejects connection for host "172.19.0.3", user "app", database "lab", no encryption
sslmode=allow                                OK  user=app ssl=true TLSv1.3 TLS_AES_256_GCM_SHA384
sslmode=prefer                               OK  user=app ssl=true TLSv1.3 TLS_AES_256_GCM_SHA384
sslmode=require                              OK  user=app ssl=true TLSv1.3 TLS_AES_256_GCM_SHA384
sslmode=verify-ca                            OK  user=app ssl=true TLSv1.3 TLS_AES_256_GCM_SHA384
sslmode=verify-full                          OK  user=app ssl=true TLSv1.3 TLS_AES_256_GCM_SHA384

== 2. sslmode, connecting by another name for the same server (db)
sslmode=require                              OK  user=app ssl=true TLSv1.3 TLS_AES_256_GCM_SHA384
sslmode=verify-ca                            OK  user=app ssl=true TLSv1.3 TLS_AES_256_GCM_SHA384
sslmode=verify-full                          FAIL connection to server at "db" (172.19.0.4), port 5432 failed: server certificate for "db.bazaar.internal" (and 2 other names) does not match host name "db"

== 3. verify-full without the CA certificate
verify-full, no sslrootcert                  FAIL connection to server at "db.bazaar.internal" (172.19.0.4), port 5432 failed: root certificate file "/nonexistent/ca.crt" does not exist

== 4. authentication methods (all over verify-full)
app, right password (scram)                  OK  user=app ssl=true TLSv1.3 TLS_AES_256_GCM_SHA384
app, wrong password                          FAIL password authentication failed for user "app"
alice, client certificate (cert)             OK  user=alice ssl=true TLSv1.3 TLS_AES_256_GCM_SHA384
alice, no certificate                        FAIL connection requires a valid client certificate
app, presenting alice's cert                 OK  user=app ssl=true TLSv1.3 TLS_AES_256_GCM_SHA384
mallory (no matching hba line)               FAIL no pg_hba.conf entry for host "172.19.0.3", user "mallory", database "lab", SSL encryption
legacy, md5 line, md5 hash                   OK  user=legacy ssl=true TLSv1.3 TLS_AES_256_GCM_SHA384
app to database postgres                     FAIL no pg_hba.conf entry for host "172.19.0.3", user "app", database "postgres", SSL encryption
```

The theory (section 15) explains every line.

> **Pitfall:** the keys in `certs/` are real private keys, made for this lab. `certs/` is meant to stay on your machine. Don't commit it, and never reuse these files anywhere else.
