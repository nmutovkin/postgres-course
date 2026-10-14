# Module 00: Project milestone — a working environment

**Goal.** Before anyone writes a line of Bazaar, the team needs an environment everybody can rebuild the same way: the same PostgreSQL version, the same configuration, the same way to connect. Your milestone is to have that environment running, and to have a place for your own project files.

## Requirements

1. **The server is running.** `docker compose ps` shows `course-pg` as `(healthy)`. If port 5432 is taken on your machine, you have set `PGPORT_HOST` in a `.env` file at the repository root.
2. **The course database exists.** You have run `./project/bin/reset.sh 01`. (In module 01 you'll create it by hand to see how it's done.)
3. **You can connect in two ways:** with `./project/bin/psql.sh`, and with a native psql 17 (or newer) using `-h localhost`.
4. **Your workspace exists.** There is a `workspace/` directory at the repository root with a subdirectory per module (at least `workspace/00` and `workspace/01`), and `git status` doesn't list it.
5. **Connection settings are scripted.** `workspace/00/env.sh` exports `PGHOST`, `PGPORT`, `PGUSER` and `PGDATABASE`. The password comes from `~/.pgpass` (mode 600), not from the script. (This is exercise 5. If you're doing the course through `psql.sh` only, you may skip this requirement.)

## Acceptance checks

Run each of these and compare:

```bash
docker compose ps --format '{{.Name}} {{.Status}}'
# course-pg Up ... (healthy)

./project/bin/psql.sh bazaar -c 'SHOW server_version_num'
# a number >= 170000

git status --porcelain workspace/
# (no output: workspace/ is ignored)

source workspace/00/env.sh && psql -w -Atc "SELECT current_database() || ' ' || current_setting('server_version_num')"
# bazaar 1700xx      (no password prompt)
```

And from inside psql, the database has its schema and search path (both set up by `reset.sh 01`):

```sql
SHOW search_path;
```

```text
  search_path   
----------------
 bazaar, public
```

## Reference solution

There is no SQL to write in this module. The reference environment is the repository's own [`docker-compose.yml`](../../docker-compose.yml), [`project/bin/psql.sh`](../../project/bin/psql.sh) and [`project/bin/reset.sh`](../../project/bin/reset.sh). The `env.sh` and `.pgpass` contents are shown in [solutions/README.md](solutions/README.md#exercise-5--password-free-native-psql-done-right).
