# PostgreSQL: From First Query to Sharded Clusters

A complete, self-contained PostgreSQL course. It starts with your first `psql` session and finishes with replicated, highly-available, sharded clusters and production Python services.

- **Target version:** PostgreSQL 17. Notable PostgreSQL 18 changes are called out where relevant.
- **Assumed background:** you can program (ideally in Python) and use a terminal. No SQL knowledge is assumed.
- **Self-contained theory:** each module explains the *why* and the mechanics, so you shouldn't need external reading.
- **One project throughout:** you build **Bazaar**, a multi-seller marketplace database (and later a Python service), a little more in every module.

---

## How the course is organized

Every module lives in `modules/NN-topic/` and contains:

| File | Purpose |
|---|---|
| `README.md` | The theory. Read it with a `psql` session open and run the examples as you go. |
| `exercises.md` | Practice problems, marked ★ (warm-up), ★★ (core), and ★★★ (stretch). Hints are hidden in collapsible blocks. |
| `project.md` | This module's **Bazaar milestone**: the next piece of the project to build. |
| `solutions/` | Full worked solutions with explanations. Try the exercises first! |

The reference implementation of the project lives in [`project/`](project/). If you get stuck or fall behind, you can reset your database to the reference state at the start of any module:

```bash
./project/bin/reset.sh 07     # database as it should look at the START of module 07
```

See [`project/README.md`](project/README.md) for the project spec and [`modules/00-setup`](modules/00-setup/) to get your environment running.

---

## Curriculum

### Part I: SQL Foundations

| # | Module | Highlights |
|---|---|---|
| 00 | [Setup](modules/00-setup/) | Docker Compose, connecting, the course tooling, and how to use this course |
| 01 | [psql and the Postgres Landscape](modules/01-psql/) | Client/server architecture, clusters, databases, and schemas; psql meta-commands, `\x`, `\timing`, variables, `\gset`, `\watch`, `\copy`, `.psqlrc`; the system catalogs |
| 02 | [Data Types and Tables](modules/02-types-and-ddl/) | Numeric, text, temporal, boolean, uuid, and identity types; constraints (PK, FK, UNIQUE, CHECK, NOT NULL); defaults; generated columns; `ALTER TABLE`; **Bazaar core schema** |
| 03 | [Reading and Writing Data](modules/03-crud/) | `SELECT`/`WHERE`/`ORDER BY`/`LIMIT`, three-valued logic and `NULL`, operators and functions, `CASE`, `INSERT`/`UPDATE`/`DELETE`, `RETURNING`, `ON CONFLICT` upserts, `COPY` |
| 04 | [Joins and Subqueries](modules/04-joins-subqueries/) | Inner, outer, cross, and self joins; anti/semi joins; `EXISTS`/`IN`/`ANY`; correlated subqueries; `LATERAL`; set operations |
| 05 | [Aggregation and Grouping](modules/05-aggregation/) | `GROUP BY`, `HAVING`, `FILTER`, `DISTINCT ON`, `GROUPING SETS`/`ROLLUP`/`CUBE`, `array_agg`/`string_agg`, date bucketing and gap filling with `generate_series` |
| 06 | [Advanced SQL](modules/06-advanced-sql/) | CTEs, recursive CTEs (category trees), window functions (ranking, running totals, frames), `MERGE`, top-N per group, gaps and islands |
| 07 | [Data Modeling](modules/07-data-modeling/) | Keys, normalization (1NF–BCNF), relationship patterns, ERDs, deliberate denormalization, soft deletes, history tables, naming conventions; **Bazaar schema v2** |

### Part II: PostgreSQL Power Features

| # | Module | Highlights |
|---|---|---|
| 08 | [Beyond Scalars](modules/08-beyond-scalars/) | JSONB in depth (operators, jsonpath, indexing preview), arrays, enums, domains, composite types, range types, exclusion constraints |
| 09 | [Views, Functions and Triggers](modules/09-views-functions-triggers/) | Views and updatable views, materialized views, SQL and PL/pgSQL functions, procedures, error handling, triggers (row/statement, `BEFORE`/`AFTER`), audit logging |
| 10 | [Transactions and Concurrency](modules/10-transactions-concurrency/) | ACID, MVCC intuition, isolation levels and anomalies, row and table locks, `FOR UPDATE`/`SKIP LOCKED`, deadlocks, advisory locks, serialization retries, job queues |
| 11 | [Indexes](modules/11-indexes/) | B-tree internals, multicolumn ordering, partial, expression, and covering indexes; GIN, GiST, BRIN, and hash; `CREATE INDEX CONCURRENTLY`; index bloat; when indexes hurt |
| 12 | [Query Planning and Performance](modules/12-query-performance/) | `EXPLAIN (ANALYZE, BUFFERS)`, scan and join nodes, cost model, statistics and extended statistics, `pg_stat_statements`, common anti-patterns, pagination |
| 13 | [Full-Text and Fuzzy Search](modules/13-full-text-search/) | `tsvector`/`tsquery`, parsers and dictionaries, ranking, highlighting, `websearch_to_tsquery`, `pg_trgm`, `unaccent`, autocomplete |
| 14 | [Partitioning](modules/14-partitioning/) | Range/list/hash partitioning, pruning, partition-wise operations, maintenance (attach/detach), retention, partitioned indexes, pitfalls |

### Part III: Administration, Internals and Scaling

| # | Module | Highlights |
|---|---|---|
| 15 | [Security and Row-Level Security](modules/15-security-rls/) | Roles, membership, privileges, default privileges, `SECURITY DEFINER`, RLS policies for multi-tenant access, `pg_hba.conf`, SCRAM, SSL |
| 16 | [Internals Deep Dive](modules/16-internals/) | Process model, shared memory, pages and tuples on disk, MVCC visibility, hint bits, HOT updates, TOAST, WAL, checkpoints, VACUUM, freezing and wraparound, the planner's statistics |
| 17 | [Configuration, Maintenance and Monitoring](modules/17-operations/) | `postgresql.conf` essentials, memory tuning, autovacuum tuning, bloat, `pg_stat_*` views, logging, `auto_explain`, connection pooling with PgBouncer |
| 18 | [Backup and Recovery](modules/18-backup-recovery/) | `pg_dump`/`pg_restore`, physical backups with `pg_basebackup`, WAL archiving, point-in-time recovery, pgBackRest, testing restores |
| 19 | [Replication](modules/19-replication/) | Streaming replication, sync vs async, replication slots, hot standby, read scaling, logical replication, publications/subscriptions, CDC |
| 20 | [High Availability](modules/20-high-availability/) | Failure modes, Patroni + etcd, failover and switchover, fencing and split-brain, HAProxy/PgBouncer routing, client behavior during failover |
| 21 | [Sharding](modules/21-sharding/) | Choosing shard keys, application-level routing, `postgres_fdw` + partitioning, Citus (distributed, reference, and co-located tables), rebalancing, cross-shard queries |

### Part IV: PostgreSQL from Python

| # | Module | Highlights |
|---|---|---|
| 22 | [psycopg 3](modules/22-psycopg/) | Connections, parameter binding and SQL injection, transactions, row factories, type adaptation, `COPY`, server-side cursors, pipelines, connection pools |
| 23 | [Async Python: psycopg async and asyncpg](modules/23-async-python/) | asyncio primer, `AsyncConnection`, asyncpg API and prepared statements, pools, concurrency patterns, LISTEN/NOTIFY, benchmarking the two |
| 24 | [SQLAlchemy Core](modules/24-sqlalchemy-core/) | Engines, connections, `MetaData`/`Table`, the SQL expression language, Postgres-specific constructs (JSONB, arrays, `ON CONFLICT`), reflection |
| 25 | [SQLAlchemy ORM](modules/25-sqlalchemy-orm/) | Declarative mapping, sessions and unit of work, relationships, loading strategies and N+1, async ORM, when to drop down to SQL |
| 26 | [Migrations with Alembic](modules/26-migrations/) | Revisions, autogenerate and its limits, data migrations, branching, zero-downtime schema change patterns (expand/contract, `NOT VALID`, concurrent indexes, lock timeouts) |
| 27 | [Production Python and Postgres](modules/27-python-production/) | Testing against real Postgres (pytest, transactional fixtures), retries, the transactional outbox, PgBouncer gotchas, timeouts, observability |
| 28 | [Capstone: Bazaar Service](modules/28-capstone/) | An async FastAPI service on the Bazaar database: checkout, search, seller portal with RLS, background jobs, migrations, tests |

---

## Quick start

```bash
git clone <this repo> && cd postgres-course
docker compose up -d                 # PostgreSQL 17 on localhost:5432
./project/bin/reset.sh 01            # create the (empty) bazaar database
./project/bin/psql.sh                # open psql connected to bazaar
```

Then open [modules/00-setup/README.md](modules/00-setup/README.md).
