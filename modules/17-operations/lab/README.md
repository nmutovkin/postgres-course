# Module 17 lab: PgBouncer in front of PostgreSQL 17

A self-contained Docker Compose setup used in section 11 of the [module 17 theory](../README.md#11-connection-pooling-with-pgbouncer) and in exercises 11 and 12.

| Service | Image | Inside the network | On your machine |
|---|---|---|---|
| `pg` | `postgres:17` (`max_connections = 100`) | `pg:5432` | `localhost:55432` (`LAB_PG_PORT`) |
| `pgbouncer` | `edoburu/pgbouncer:v1.26.0-p0` | `pgbouncer:6432` | `localhost:56432` (`LAB_BOUNCER_PORT`) |

Credentials: `app` / `app_pw` (application role, owns database `bench`) and `postgres` / `postgres` (superuser, PgBouncer admin).

PgBouncer exposes three pooled databases, all backed by the real database `bench`:

| Pooled name | Pool mode | Server connections |
|---|---|---|
| `bench` | transaction | up to 20 |
| `bench_session` | session | up to 20 |
| `bench_tiny` | transaction | exactly 1 (makes state leaks deterministic) |

## Usage

```bash
cd modules/17-operations/lab
docker compose up -d --wait          # start; the first start creates role app and database bench
./bench.sh                           # pgbench: direct vs pooled (about 2 minutes; BENCH_SECONDS=15 per run)
./pitfalls.sh                        # what breaks in transaction pooling
docker compose down -v               # stop and delete everything, including the data volume
```

Interactive sessions, without installing anything locally:

```bash
# psql through PgBouncer (transaction mode)
docker compose exec -e PGPASSWORD=app_pw pg psql -h pgbouncer -p 6432 -U app bench
# psql directly to PostgreSQL
docker compose exec -e PGPASSWORD=app_pw pg psql -h pg -p 5432 -U app bench
# PgBouncer admin console
docker compose exec -e PGPASSWORD=postgres pg psql -h pgbouncer -p 6432 -U postgres pgbouncer
```

After editing `pgbouncer/pgbouncer.ini`, apply it with `RELOAD;` in the admin console (or `docker compose restart pgbouncer`). Some settings, such as `max_prepared_statements` and `default_pool_size`, can also be changed on the fly with `SET name = value;` in the admin console.

The plain-text `userlist.txt` is fine for a lab. In production, use `auth_query` or SCRAM secrets (see section 11.6 of the theory).
