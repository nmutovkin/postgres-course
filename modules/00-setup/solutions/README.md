# Module 00: Solutions

<!-- nav:start -->
> [Theory](../README.md) · [Exercises](../exercises.md) · [Project](../project.md) · **Solutions**  
> [Course home](../../../README.md) · [01 psql and the Postgres Landscape →](../../01-psql/README.md)
<!-- nav:end -->

All outputs below come from a PostgreSQL 17 container started from the course's `docker-compose.yml`. Some captures were taken from a test container published on host port **5433** (because 5432 was in use on the test machine), which is why a few lines say 5433. On your machine they say 5432 unless you set `PGPORT_HOST`.

---

<!-- toc:start -->
**Contents**

- [Exercise 1 ★: Four version numbers](#exercise-1--four-version-numbers)
- [Exercise 2 ★: Same server, two doors](#exercise-2--same-server-two-doors)
- [Exercise 3 ★: `down` versus `down -v`](#exercise-3--down-versus-down--v)
- [Exercise 4 ★★: Prove the compose file is in effect](#exercise-4--prove-the-compose-file-is-in-effect)
- [Exercise 5 ★★: Password-free native psql, done right](#exercise-5--password-free-native-psql-done-right)
- [Exercise 6 ★★: Find yourself in the server](#exercise-6--find-yourself-in-the-server)

<!-- toc:end -->

## Exercise 1 ★: Four version numbers

```sql
SELECT version();                 -- (1) full string
SHOW server_version_num;          -- (2) comparable number
```

```text
 PostgreSQL 17.11 (Debian 17.11-1.pgdg13+2) on x86_64-pc-linux-gnu, compiled by gcc (Debian 14.2.0-19) 14.2.0, 64-bit

 server_version_num 
--------------------
 170011
```

```bash
docker exec course-pg psql --version   # (3) the container's client
psql --version                         # (4) your native client
```

```text
psql (PostgreSQL) 17.11 (Debian 17.11-1.pgdg13+2)
```

**Reasoning.** The server and psql are two separate programs. The container's psql is always the same version as its server, because they come from the same image. Your native psql is whatever your package manager installed. `server_version_num` packs `major * 10000 + minor` into an integer, so `170011` is 17.11. Compare it numerically (`>= 170000`). Comparing version *strings* breaks, because as text `'9.6'` sorts after `'17.1'`.

**Alternative.** `SELECT current_setting('server_version_num')::int;` returns the same number as a value you can use inside a larger query. Module 01's project uses it. Inside psql, the variable `:SERVER_VERSION_NUM` holds it too (module 01).

---

## Exercise 2 ★: Same server, two doors

Through `./project/bin/psql.sh` (inside the container, Unix socket):

```text
You are connected to database "bazaar" as user "postgres" via socket in "/var/run/postgresql" at port "5432".

 inet_server_addr | inet_client_addr | inet_server_port 
------------------+------------------+------------------
                  |                  |                 
```

Through native psql (TCP via the published port):

```text
You are connected to database "bazaar" as user "postgres" on host "localhost" (address "127.0.0.1") at port "5433".

 inet_server_addr | inet_client_addr | inet_server_port 
------------------+------------------+------------------
 172.19.0.2       | 172.19.0.1       |             5432
```

**Reasoning.**

- A Unix-socket connection has no IP addresses or port, so all three functions return NULL. (The `port "5432"` in `\conninfo` is only part of the socket file's name, `.s.PGSQL.5432`.)
- Over TCP, the server sees the world from **inside** the container. Its own address is the container's address on Docker's network (`172.19.0.2` here; yours will differ). It listens on 5432 whatever host port you published. The client address is Docker's gateway (`172.19.0.1`), because Docker's port forwarding opens the inner connection on your behalf.
- `\conninfo`, on the other hand, reports what the **client** dialled: `localhost`, port 5433.

Different viewpoints, same connection. When a log line or a `pg_hba.conf` rule (module 15) mentions an address, always ask whose viewpoint it is.

---

## Exercise 3 ★: `down` versus `down -v`

```bash
./project/bin/psql.sh postgres -c "CREATE TABLE survivor (note text)" -c "INSERT INTO survivor VALUES ('still here')"
docker compose down
docker compose up -d --wait
./project/bin/psql.sh postgres -c 'TABLE survivor'
```

```text
    note    
------------
 still here
(1 row)
```

```bash
docker compose down -v
docker compose up -d --wait
./project/bin/psql.sh postgres -c 'TABLE survivor'
```

```text
ERROR:  relation "survivor" does not exist
LINE 1: TABLE survivor
              ^
```

`\l` now lists only `postgres`, `template0` and `template1`. The `bazaar` database is gone as well. Step 4:

```bash
./project/bin/reset.sh 01
```

**Reasoning.** The container is disposable. Everything PostgreSQL stores lives in the data directory `/var/lib/postgresql/data`, which the compose file maps to the named volume `pgdata`. `down` removes the container and network but not named volumes. `down -v` removes the volumes too, so the next `up` finds an empty data directory and initialises a brand-new cluster. That's also the only time the `POSTGRES_*` environment variables are read.

(`TABLE survivor` is shorthand for `SELECT * FROM survivor`. Module 03 covers it.)

---

## Exercise 4 ★★: Prove the compose file is in effect

```sql
SELECT name, setting
FROM pg_settings
WHERE source = 'command line'
ORDER BY name;
```

```text
            name            |             setting             
----------------------------+---------------------------------
 log_min_duration_statement | 1000
 pg_stat_statements.track   | all
 shared_preload_libraries   | pg_stat_statements,auto_explain
 track_io_timing            | on
(4 rows)
```

```sql
SHOW data_directory;
```

```text
      data_directory      
--------------------------
 /var/lib/postgresql/data
```

```bash
docker exec course-pg df -h /dev/shm
```

```text
Filesystem      Size  Used Avail Use% Mounted on
shm             256M  1.1M  255M   1% /dev/shm
```

**Reasoning.** Every setting in `pg_settings` records where its current value came from. `default` is the built-in value, `configuration file` is `postgresql.conf`, and `command line` is the `-c` options in the compose file's `command:`. The other places a value can come from (`database`, `user`, `client`, `session`) show up in module 01, when you set `search_path` per database. `data_directory` matching the volume's mount point is what makes the data survive `docker compose down`.

**Alternative.** `SELECT source, count(*) FROM pg_settings GROUP BY source;` gives the whole picture at once. On the test machine it showed 372 settings at `default`, 14 from the configuration file and 4 from the command line, plus a few `override` and `client` ones.

---

## Exercise 5 ★★: Password-free native psql, done right

`workspace/00/env.sh`:

```bash
# Source me: `source workspace/00/env.sh`
export PGHOST=localhost
export PGPORT=5432          # or your PGPORT_HOST
export PGUSER=postgres
export PGDATABASE=bazaar
# No PGPASSWORD here on purpose: ~/.pgpass supplies it.
```

`~/.pgpass`:

```text
localhost:5432:*:postgres:postgres
```

```bash
chmod 600 ~/.pgpass
source workspace/00/env.sh
psql -w -c 'SELECT current_user, current_database()'
```

```text
 current_user | current_database 
--------------+------------------
 postgres     | bazaar
(1 row)
```

With `chmod 644 ~/.pgpass`:

```text
WARNING: password file "/home/you/.pgpass" has group or world access; permissions should be u=rw (0600) or less
psql: error: connection to server at "localhost" (127.0.0.1), port 5433 failed: fe_sendauth: no password supplied
```

**Reasoning.** libpq looks up the password in `.pgpass` by matching host, port, database and user. A file that others can read is treated as compromised and ignored. libpq warns rather than silently using it. `-w` turns "I'd have to prompt" into an immediate error, which is exactly what you want in scripts and cron jobs: a missing password should fail loudly, not hang waiting for input.

**Alternatives.** Putting `PGPASSWORD` in `env.sh` works, but it leaks into every child process's environment and into the file itself. A connection *service file* (`~/.pg_service.conf`, used with `psql service=bazaar`) can bundle all parameters under one name. It's handy when you juggle several servers, and it still pairs with `.pgpass` for the password.

---

## Exercise 6 ★★: Find yourself in the server

```bash
psql "postgresql://postgres@localhost:5432/bazaar?application_name=bazaar-setup"
```

```sql
SELECT pid, application_name, client_addr, datname
FROM pg_stat_activity
WHERE pid = pg_backend_pid();
```

```text
 pid | application_name | client_addr | datname 
-----+------------------+-------------+---------
  99 | bazaar-setup     | 172.19.0.1  | bazaar
(1 row)
```

**Reasoning.** Every connection is served by its own server process (module 01 explains this). `pg_backend_pid()` is that process's id, and `pg_stat_activity` has one row per process, so filtering on the pid finds exactly your session. `client_addr` is not `127.0.0.1` because your laptop doesn't talk to PostgreSQL directly. Docker accepts the connection on the published port and opens a new one to the container from its own network gateway. On Docker Desktop (macOS/Windows) the address will be different again, from the internal VM network. It still won't be `127.0.0.1`.

`application_name` is free text the client sends at connect time. Setting it in every application ("bazaar-api", "bazaar-worker") makes `pg_stat_activity` and the server logs far easier to read during an incident. Module 17 makes use of it.
