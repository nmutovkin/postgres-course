# Module 21 lab

Two small clusters for the sharding module. Everything is prefixed `m21-`, publishes no ports, and uses `trust` authentication inside its Docker network.

```bash
cd modules/21-sharding/lab

# Citus: coordinator + 2 workers (sections 12-17, the milestone)
docker compose up -d
./scripts/citus-build.sh small          # or: large (about 6 minutes, about 5 GB of disk)
docker exec -it m21-coord psql -U postgres -d bazaar

# a third worker for rebalancing (section 16)
docker compose --profile scale up -d worker3
./scripts/citus-init-nodes.sh m21-worker3

# plain PostgreSQL 17: postgres_fdw cluster and the app router (sections 9-11)
docker compose --profile fdw up -d
./scripts/fdw-setup.sh
docker exec m21-app python router.py init     # then: load, where, dashboard, top-sellers

# tear everything down, volumes included
docker compose --profile fdw --profile scale down -v
```

| Path | What it is |
|---|---|
| `compose.yml` | All containers. Citus nodes run `citusdata/citus:14.2.0-pg17` with `wal_level=logical` and `pg_stat_statements`. |
| `scripts/load-bazaar.sh` | `small`: `reset.sh 11` + the schema-only migrations 1102/1201/1301/1401/1501. `large`: `reset.sh 21`. Then applies `project/db/sharding/2101_schema_shard_prep.sql`. |
| `scripts/citus-init-nodes.sh` | Creates `bazaar` + `citus` on the workers and registers them with the coordinator. |
| `scripts/distribute.sql` | Reference and distributed tables, co-location, and the trigger and setting changes Citus needs. |
| `scripts/citus-adapt.sql` | Co-location-aware `active_products` and `seller_sales_daily`, and `place_order_sharded()`. |
| `scripts/citus-build.sh` | All four steps above in one command. |
| `scripts/queries.sql` | The seller-dashboard and global-report queries measured in section 14.6 (`-v seller=42`). |
| `scripts/fdw-setup.sh`, `fdw/shard.sql`, `fdw/coord.sql` | The `postgres_fdw` + hash partitioning cluster. |
| `scripts/2pc-drill.sh` | Leaves a half-committed two-phase commit behind (exercise 7). |
| `router/router.py` | The application-level router (section 10). |

If a step fails halfway, rerunning `citus-build.sh` starts from scratch: it recreates the `bazaar` database on every node.
