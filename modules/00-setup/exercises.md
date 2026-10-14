# Module 00: Exercises

These are small, hands-on checks that your environment works and that you understand what you just installed. Run them against the course container after `./project/bin/reset.sh 01`. Solutions are in [solutions/README.md](solutions/README.md).

If you moved the server to another host port (`PGPORT_HOST`), use that port wherever these exercises say 5432.

---

## Exercise 1 ★: Three version numbers

Find and write down:

1. the server's full version string,
2. the server's version as a number a script could compare,
3. the version of the psql **inside** the container, and
4. the version of your **native** psql (if you installed one).

**Self-check:** (2) is at least `170000`. (3) starts with `psql (PostgreSQL) 17.`. If (4) is lower than 17, re-read section 6.1 of the theory before you continue.

<details><summary>Hint</summary>

Two of the answers come from SQL, two from `psql --version`. For the container's psql, remember that `docker exec course-pg <command>` runs any command inside the container.

</details>

---

## Exercise 2 ★: Same server, two doors

Connect once with `./project/bin/psql.sh` and once with a native psql (`-h localhost`). In **each** session run:

```sql
\conninfo
SELECT inet_server_addr(), inet_client_addr(), inet_server_port();
```

Explain the differences between the two outputs.

**Self-check:** in exactly one of the two sessions, all three columns are empty (NULL). In the other session, `inet_server_port()` is `5432` even if you published the server on a different host port.

<details><summary>Hint</summary>

`\conninfo` tells you whether you came in "via socket" or "on host". What is the "address" of a Unix socket connection?

</details>

---

## Exercise 3 ★: `down` versus `down -v`

1. In the `postgres` database (`./project/bin/psql.sh postgres`), create a table `survivor` with one text column and insert the row `'still here'`.
2. Run `docker compose down`, then `docker compose up -d --wait`. Is the row still there?
3. Run `docker compose down -v`, then `docker compose up -d --wait`. Is the row still there? Is the `bazaar` database?
4. Put things back the way the course expects them.

**Self-check:** after step 2, `TABLE survivor;` returns one row. After step 3, `\l` (in `./project/bin/psql.sh postgres`) lists exactly three databases: `postgres`, `template0`, `template1`.

<details><summary>Hint</summary>

Where does the data live: in the container or in the volume? Which of the two commands removes which? For step 4, there's a one-line command that recreates `bazaar`.

</details>

---

## Exercise 4 ★★: Prove the compose file is in effect

Using SQL only (no looking at `docker-compose.yml`), show that:

1. exactly four settings were set on the server's command line, and what they are;
2. the server's data directory is the path the compose file mounts the `pgdata` volume on;
3. the container's shared memory is bigger than Docker's 64 MB default. (This one is allowed a shell command.)

**Self-check:** (1) returns 4 rows, including `track_io_timing = on`. (2) is `/var/lib/postgresql/data`. (3) shows a size of `256M`.

<details><summary>Hint</summary>

`pg_settings` has a `source` column. `SHOW` works for any setting, including `data_directory`. For (3), `df -h /dev/shm` inside the container.

</details>

---

## Exercise 5 ★★: Password-free native psql, done right

Make your native psql connect to the course server **without** a password prompt and **without** `PGPASSWORD` set, and keep the connection settings in a file you can `source`:

1. Create `workspace/00/env.sh` that exports `PGHOST`, `PGPORT`, `PGUSER` and `PGDATABASE` (but not the password).
2. Set up `~/.pgpass` so the password is found automatically.
3. Prove it works: `source workspace/00/env.sh && psql -w -c 'SELECT current_user, current_database()'`.
4. Break it on purpose with `chmod 644 ~/.pgpass`, run step 3 again, read the message, and fix it.

**Self-check:** step 3 prints `postgres | bazaar` without asking for anything. Step 4 prints a `WARNING: password file ... has group or world access` followed by `fe_sendauth: no password supplied`.

<details><summary>Hint</summary>

`-w` means "never prompt", so a missing password becomes an error instead of a prompt. A `.pgpass` line has five colon-separated fields: `host:port:database:user:password`.

</details>

---

## Exercise 6 ★★: Find yourself in the server

Connect with native psql using a **URI** that also sets `application_name=bazaar-setup`. Then write a query against `pg_stat_activity` (a view with one row per connection) that returns only *your own* connection, with its `application_name` and `client_addr`.

**Self-check:** one row, `application_name = bazaar-setup`. `client_addr` is **not** `127.0.0.1`, even though you connected to `localhost`. Explain why.

<details><summary>Hint</summary>

`pg_backend_pid()` returns the process id of the server process serving *your* connection, and `pg_stat_activity` has a `pid` column. For the address: who really opens the TCP connection to PostgreSQL when you connect to a port that Docker publishes?

</details>
