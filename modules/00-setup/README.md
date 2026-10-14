# Module 00: Setup

Bazaar, the marketplace you build in this course, will eventually run on replicated, sharded PostgreSQL clusters behind a Python service. It starts much smaller: one PostgreSQL 17 server in a Docker container on your laptop, and a terminal connected to it. This module gets that server running, shows you how to connect to it in three different ways, and explains how the rest of the course is organised so you can find your way around.

If you have used Docker and PostgreSQL before, skim sections 1 and 4, run the quick check in section 9, and move on to [Module 01](../01-psql/).

**What you'll learn**

- How a module is laid out, what the ★ levels mean, and how `reset.sh` lets you jump to any point in the project.
- How to install Docker and Docker Compose, and start the course database with one command.
- What every line of the course's `docker-compose.yml` does, and why.
- How to connect with the bundled `./project/bin/psql.sh`, with a native `psql` on your machine, and with a GUI.
- How connection parameters, URIs, environment variables and `~/.pgpass` fit together.
- How to fix the usual first-day problems (port 5432 already taken, wrong password, a database you want to wipe).
- How to check which PostgreSQL version you are talking to.

**Before you start**

You need a terminal, about 1 GB of free disk space, and permission to install software. No database knowledge is assumed.

---

## 1. How this course works

### 1.1 Modules

The course is a sequence of numbered modules in `modules/NN-slug/`. Each one has the same four parts:

```
modules/01-psql/
├── README.md        # the theory: read this with a psql session open
├── exercises.md     # practice problems
├── project.md       # this module's Bazaar milestone
└── solutions/
    ├── README.md    # worked solutions with explanations
    └── *.sql ...    # runnable solution files
```

Read the theory with a terminal next to it, and type the examples instead of copying them. Every SQL example in the course was run on PostgreSQL 17, and the output shown is the real output. If yours differs, something in your setup differs too, which is worth understanding.

### 1.2 Exercise levels

Exercises are marked with stars:

| Level | Meaning | Typical effort |
|---|---|---|
| ★ | Warm-up: applies one idea from the module directly. | 2–5 minutes |
| ★★ | Core: combines two ideas, or applies one to a realistic Bazaar question. | 10–20 minutes |
| ★★★ | Stretch: needs some digging, or touches a production edge case. | 20–45 minutes |

Each exercise says how to check your answer: an expected row count, a value, or a property that must hold. Hints are folded away in `<details>` blocks, so you can try without them first. Solutions are in `solutions/README.md`. Read a solution even when you solved the exercise, because it usually discusses an alternative approach and its trade-offs.

### 1.3 The project and `reset.sh`

Every module ends with a **project milestone** (`project.md`): the next piece of Bazaar for you to build. The milestones build on each other. Module 02 creates the core tables, module 03 fills them with data, module 07 refines the model, and so on.

The reference implementation lives in `project/db/` as ordered SQL files (`0201_schema_core.sql`, `0301_seed_small.sql`, ...). The first two digits are the module that introduces the file. The script `./project/bin/reset.sh NN` throws away the `bazaar` database and rebuilds it **as it should look at the start of module NN**, by applying every reference file from modules lower than NN:

```bash
./project/bin/reset.sh 01     # empty bazaar database with a bazaar schema
./project/bin/reset.sh 07     # everything modules 02-06 built
./project/bin/reset.sh all    # every reference file
```

This gives you two ways to work:

- **Build it yourself (recommended).** Do each milestone on your own database. When you start a module, compare with the reference and carry on.
- **Catch up.** If you fall behind, get stuck, or simply want to start module 11 today, run `reset.sh 11` and you have exactly the database that module expects.

> **Pitfall:** `reset.sh` **drops** the `bazaar` database. Anything you built inside it is gone. That's why the next section asks you to keep your own work in files.

### 1.4 Keep your own work in `workspace/`

Create a directory called `workspace/` at the root of the repository and keep your own milestone files there, one subdirectory per module:

```bash
mkdir -p workspace/01 workspace/02
```

The repository's `.gitignore` already ignores `workspace/`, so your files never get mixed up with the course files, and `git pull` never conflicts with them. A good habit is to name files like the reference ones (`workspace/02/0201_schema_core.sql`), write every change as a script rather than typing it into psql and forgetting it, and re-run your scripts on a fresh database every now and then. A database you can rebuild from files is a database you understand. Module 26 makes this habit formal with migration tooling.

### 1.5 Callouts

You will see four kinds of boxed notes:

> **Pitfall:** a mistake people really make, and how to avoid it.

> **Under the hood:** how PostgreSQL implements what you just used. Optional on a first read.

> **In production:** what changes when real users and real data are involved.

> **PG 18:** where PostgreSQL 18 behaves differently from 17, the version this course targets.

---

## 2. Installing Docker and Docker Compose

The course database runs in a **container**: an isolated process with its own filesystem, started from an **image** that already contains PostgreSQL 17. You don't install PostgreSQL itself on your machine. That has three advantages. Everybody runs exactly the same version and configuration, so the outputs in the course match yours. Throwing the server away and starting fresh takes seconds. And later modules start several PostgreSQL servers at once (replication, high availability, sharding), which is painful to do with native installs.

**Docker Compose** is a tool that reads a YAML file (`docker-compose.yml`) describing one or more containers and starts them with one command. Modern Docker ships Compose as a plugin, invoked as `docker compose` (with a space). The old standalone `docker-compose` (with a hyphen) is deprecated. Use the space form.

Install Docker for your platform:

| Platform | What to install | Notes |
|---|---|---|
| macOS | Docker Desktop, or OrbStack, or Colima | All three provide `docker` and `docker compose`. OrbStack and Colima are lighter alternatives to Docker Desktop. |
| Windows | Docker Desktop with the WSL 2 backend | Clone the course **inside** your WSL distribution (for example Ubuntu) and run every command from a WSL terminal. The shell scripts in `project/bin/` are bash scripts. |
| Linux | Docker Engine plus the Compose plugin from your distribution or from Docker's own repository | Add yourself to the `docker` group (`sudo usermod -aG docker $USER`, then log out and in) so you don't need `sudo` for every command. |

Check that both tools work:

```bash
docker --version
docker compose version
docker run --rm hello-world
```

The last command downloads a tiny image and prints a greeting. If it prints an error about the daemon (`Cannot connect to the Docker daemon`), Docker is installed but not running: start Docker Desktop, or `sudo systemctl start docker` on Linux.

> **Pitfall:** On Linux, `permission denied while trying to connect to the Docker daemon socket` means your user is not in the `docker` group yet, or you haven't logged out and back in since adding it.

---

## 3. Starting the course database

From the root of the repository:

```bash
docker compose up -d
```

The first time, Docker downloads the `postgres:17` image (around 170 MB compressed). Then Compose creates a network, a named volume for the data, and the container, and starts it. `-d` means "detached": the container runs in the background and you get your prompt back.

Check that it's up:

```bash
docker compose ps
```

```text
NAME        IMAGE         COMMAND                  SERVICE   CREATED          STATUS                    PORTS
course-pg   postgres:17   "docker-entrypoint.s…"   pg        14 minutes ago   Up 14 minutes (healthy)   0.0.0.0:5432->5432/tcp
```

Three things to read in that line:

- **`(healthy)`**: the compose file defines a health check that runs `pg_isready` every 2 seconds. Right after start you may see `(health: starting)`. Wait for `healthy` before connecting.
- **`0.0.0.0:5432->5432/tcp`**: port 5432 on your machine is forwarded to port 5432 inside the container, so programs on your laptop can reach the server at `localhost:5432`.
- **`course-pg`**: the container's name. The helper scripts use it.

On the very first start the server initialises an empty **data directory** (the files where PostgreSQL keeps everything), then restarts itself. You can watch this in the logs:

```bash
docker compose logs pg | tail -5
```

```text
pg-m00  | 2026-10-14 07:16:17.708 UTC [1] LOG:  starting PostgreSQL 17.11 (Debian 17.11-1.pgdg13+2) on x86_64-pc-linux-gnu, compiled by gcc (Debian 14.2.0-19) 14.2.0, 64-bit
pg-m00  | 2026-10-14 07:16:17.708 UTC [1] LOG:  listening on IPv4 address "0.0.0.0", port 5432
pg-m00  | 2026-10-14 07:16:17.709 UTC [1] LOG:  listening on Unix socket "/var/run/postgresql/.s.PGSQL.5432"
pg-m00  | 2026-10-14 07:16:17.712 UTC [62] LOG:  database system was shut down at 2026-10-14 07:16:17 UTC
pg-m00  | 2026-10-14 07:16:17.715 UTC [1] LOG:  database system is ready to accept connections
```

(This capture comes from a test container named `pg-m00`; yours says `course-pg`.) `database system is ready to accept connections` is the line you want. The `listening on` lines show the two ways in: a TCP port and a **Unix socket**, a special file that local programs can connect through without going over the network.

Now create the course database:

```bash
./project/bin/reset.sh 01
```

```text
>> recreating database bazaar
 pg_terminate_backend 
----------------------
(0 rows)

NOTICE:  database "bazaar" does not exist, skipping
>> done (state at start of module 01)
```

The `NOTICE` is harmless: the script drops `bazaar` "if it exists", and on the first run it doesn't.

The everyday commands:

| Command | Effect |
|---|---|
| `docker compose up -d` | Start (or create and start) the server. |
| `docker compose stop` | Stop the server. Data is kept. |
| `docker compose start` | Start a stopped server again. |
| `docker compose down` | Stop and **remove** the container. Data is still kept, because it lives in a volume. |
| `docker compose down -v` | Remove the container **and the volume**. All data is gone. See section 8. |
| `docker compose logs -f pg` | Follow the server log (Ctrl-C to stop following). |

---

## 4. What the compose file configures, and why

Here is the course's `docker-compose.yml`:

```yaml
services:
  pg:
    image: postgres:17
    container_name: course-pg
    environment:
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: postgres
      POSTGRES_DB: postgres
    ports:
      - "${PGPORT_HOST:-5432}:5432"
    command:
      - postgres
      - -c
      - shared_preload_libraries=pg_stat_statements,auto_explain
      - -c
      - pg_stat_statements.track=all
      - -c
      - track_io_timing=on
      - -c
      - log_min_duration_statement=1000
    volumes:
      - pgdata:/var/lib/postgresql/data
      - ./project:/course/project:ro
      - ./modules:/course/modules:ro
      - ./workspace:/course/workspace:ro   # your own milestone files; use \i /course/workspace/... in psql
    shm_size: 256mb
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres"]
      interval: 2s
      timeout: 3s
      retries: 30

volumes:
  pgdata:
```

Line by line:

- **`image: postgres:17`**: the official PostgreSQL image, latest 17.x minor release. Minor releases (17.1, 17.2, ...) contain only bug and security fixes and are designed not to change behaviour, so following the latest minor is safe. Major versions (17 → 18) can change behaviour, which is why the tag pins 17.
- **`container_name: course-pg`**: a fixed name, so scripts can say `docker exec course-pg ...` without looking it up.
- **`environment`**: read by the image's start-up script **only when the data directory is empty**, i.e. on the very first start. It creates a superuser called `postgres` with password `postgres`, and a database called `postgres`.

  > **Pitfall:** Changing `POSTGRES_PASSWORD` later has no effect, because the data directory already exists. To change the password of a running server, use SQL (`ALTER ROLE postgres PASSWORD '...'`), or wipe the volume and start again.

  > **In production:** a password like `postgres` is fine for a laptop-only practice server and nowhere else. Module 15 covers roles, passwords and authentication properly.

- **`ports: "${PGPORT_HOST:-5432}:5432"`**: publish container port 5432 on host port `PGPORT_HOST`, defaulting to 5432. The variable exists so you can move the host side if 5432 is taken (section 8).
- **`command`**: the command line the server starts with. Each `-c name=value` pair sets a configuration parameter, exactly as if it were written in `postgresql.conf`. This course sets four:
  - `shared_preload_libraries=pg_stat_statements,auto_explain` loads two extensions into the server at start-up. One records statistics about every query, the other logs the execution plan of slow queries. Some libraries can only be loaded at server start, which is why this is configured here and not later. You don't need them yet: module 12 explains `pg_stat_statements` and module 17 explains `auto_explain`.
  - `pg_stat_statements.track=all` tells it to also track statements run inside functions.
  - `track_io_timing=on` makes the server measure how long it waits for disk reads and writes, so performance tools can show you I/O time. Module 12 uses it.
  - `log_min_duration_statement=1000` writes any statement that takes longer than 1000 ms to the server log. Useful later when you hunt slow queries.
- **`volumes`**:
  - `pgdata:/var/lib/postgresql/data` stores the data directory in a **named volume** called `pgdata`. A volume is storage managed by Docker that outlives the container. That's why `docker compose down` doesn't lose your data, but `down -v` does. Docker prefixes the name with the project (directory) name, so `docker volume ls` shows it as `postgres-course_pgdata`.
  - `./project` and `./modules` are mounted **read-only** at `/course/project` and `/course/modules` inside the container. Server-side commands that read files, which you meet in module 03, can therefore read the course's files.
  - `./workspace` (your own work, see 1.4) is mounted read-only at `/course/workspace`, so psql inside the container can run your scripts with `\i`. Create the directory before `docker compose up`; otherwise Docker creates an empty, root-owned one.
- **`shm_size: 256mb`**: PostgreSQL's parallel query workers exchange data through shared memory in `/dev/shm`. Docker's default of 64 MB is small enough that big parallel queries in later modules could fail with `could not resize shared memory segment`.
- **`healthcheck`**: runs `pg_isready` (a tiny tool that asks "are you accepting connections?") every 2 seconds. This is what turns the status into `(healthy)`, and what `docker compose up --wait` waits for.

You can ask the running server which settings came from that command line:

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

(You will run that query yourself in a moment, once you know how to connect.)

---

## 5. Connecting with `./project/bin/psql.sh`

**psql** is PostgreSQL's command-line client: you type SQL, it sends it to the server and prints the result. Module 01 is entirely about psql. For now, you only need to get in and out.

The image already contains psql 17, so the easiest way to connect is to run psql *inside* the container. The course wraps that in a script:

```bash
./project/bin/psql.sh            # connect to the bazaar database
./project/bin/psql.sh postgres   # connect to another database
```

```text
psql (17.11 (Debian 17.11-1.pgdg13+2))
Type "help" for help.

bazaar=# 
```

`bazaar=#` is the prompt: the current database, then `=#`. The `#` means you are a superuser, which is what you want on a practice server. Type a query, end it with a semicolon, and press Enter:

```sql
SELECT 1 + 1 AS two;
```

Leave with `\q` (or Ctrl-D).

The script is three lines of bash. The interesting one is:

```bash
exec docker exec -it -e PGOPTIONS="${PGOPTIONS:-}" "$CONTAINER" psql -U postgres -d "$DB" "$@"
```

It runs `psql -U postgres -d bazaar` inside the container. Anything after the database name is passed to psql: `./project/bin/psql.sh bazaar -c 'SELECT 1'` works. The container name defaults to `course-pg` and can be overridden with `COURSE_PG_CONTAINER`.

Why doesn't it ask for a password? Inside the container, psql connects through the Unix socket, and the image's authentication rules (in a file called `pg_hba.conf`, covered in module 15) trust local socket connections. Connections from outside the container, including from your laptop, must use a password:

```text
local   all             all                                     trust
host    all             all             127.0.0.1/32            trust
...
host all all all scram-sha-256
```

The last line applies to everything arriving over the network, and requires a password checked with the SCRAM-SHA-256 method.

> **Tip: running script files.** `psql.sh` attaches a terminal only when you're typing interactively, so you can pipe a file into it:
>
> ```bash
> ./project/bin/psql.sh bazaar -v ON_ERROR_STOP=1 < workspace/01/setup.sql
> ```
>
> Your `workspace/` folder is also mounted read-only inside the container at `/course/workspace`, so from an interactive session you can run `\i /course/workspace/01/setup.sql`.
>
> Module 01 explains `-v ON_ERROR_STOP=1`. Or use a native psql (next section), which reads files directly.

---

## 6. A native psql on your machine

You can do the whole course through `psql.sh`. A psql installed on your own machine is still worth having: it reads files from your disk directly (your `workspace/`), it uses your own `~/.psqlrc` (module 01), and it behaves the same as connecting to any real server, which is what you do at work.

### 6.1 Installing psql 17

You need only the **client**, not a server. Install version 17 or newer: psql works with older servers, but an older psql talking to a newer server can break, as you'll see in a moment.

| Platform | Command |
|---|---|
| macOS (Homebrew) | `brew install libpq`, then add it to your `PATH`: `echo 'export PATH="$(brew --prefix libpq)/bin:$PATH"' >> ~/.zshrc`. (`libpq` is "keg-only": Homebrew installs it but doesn't link it into the `PATH`.) Alternatively `brew install postgresql@17` gives you the full server package too. |
| Debian/Ubuntu/WSL | Add the PostgreSQL project's own apt repository (PGDG; the distribution's packages are often an older major version), then `sudo apt install postgresql-client-17`. The PGDG page for your distribution has a one-line setup script: `sudo apt install postgresql-common && sudo /usr/share/postgresql-common/pgdg/apt.postgresql.org.sh`. |
| Fedora/RHEL | `sudo dnf install postgresql` if your release ships 17 or newer, otherwise the PGDG yum repository. |
| Windows (outside WSL) | The EDB installer from postgresql.org. Untick everything except "Command Line Tools". |

Check:

```bash
psql --version
```

Why version 17 matters: psql's backslash commands (like `\l`, which lists databases) are built from queries against the server's internal tables, and those tables change between major versions. Here is psql 16 connecting to our 17 server:

```text
psql (16.15 (Ubuntu 16.15-0ubuntu0.24.04.1), server 17.11 (Debian 17.11-1.pgdg13+2))
WARNING: psql major version 16, server major version 17.
         Some psql features might not work.
Type "help" for help.
```

And the warning is real. Listing databases fails because PostgreSQL 17 renamed a column that psql 16 still asks for:

```text
psql: ERROR:  column d.daticulocale does not exist
LINE 8:   d.daticulocale as "ICU Locale",
          ^
HINT:  Perhaps you meant to reference the column "d.datlocale".
```

Newer psql versions know how to talk to older servers, but not the other way round. So keep your client at least as new as the newest server you connect to.

### 6.2 Connection parameters

Every PostgreSQL client, whether psql, Python's psycopg or a GUI, needs the same five facts:

| Parameter | psql option | Environment variable | Course value |
|---|---|---|---|
| host | `-h` | `PGHOST` | `localhost` |
| port | `-p` | `PGPORT` | `5432` (or your `PGPORT_HOST`) |
| user | `-U` | `PGUSER` | `postgres` |
| database | `-d` | `PGDATABASE` | `bazaar` |
| password | (prompted) | `PGPASSWORD` | `postgres` |

There are three equivalent ways to pass them.

**Options:**

```bash
psql -h localhost -p 5432 -U postgres -d bazaar
```

psql prompts for the password. If you don't give `-h`, psql uses a Unix socket on your machine, which doesn't exist unless you run a native server. That produces a confusing `No such file or directory` error about `/var/run/postgresql/.s.PGSQL.5432`. With Docker, always give `-h localhost`.

**A connection URI** (also called a connection URL), the format applications and Python libraries use:

```bash
psql "postgresql://postgres:postgres@localhost:5432/bazaar"
```

The shape is `postgresql://user:password@host:port/database?param=value`. Every part is optional. Extra parameters go after `?`, for example `?application_name=bazaar-setup&sslmode=disable`. Special characters in the password (`@`, `:`, `/`, `%`) must be percent-encoded, for example `@` becomes `%40`. The older `key=value` form works too: `psql "host=localhost port=5432 user=postgres dbname=bazaar"`.

**Environment variables**, which every libpq-based client reads (libpq is the C library that psql, psycopg and most other clients use underneath):

```bash
export PGHOST=localhost PGPORT=5432 PGUSER=postgres PGDATABASE=bazaar PGPASSWORD=postgres
psql
```

Command-line options override environment variables, which override built-in defaults. Put the exports in a small file and `source` it when you work on the course. Don't put `PGPASSWORD` in your global shell profile: on a shared machine, other users can sometimes read a process's environment.

Once connected, `\conninfo` tells you how you got there:

```text
You are connected to database "postgres" as user "postgres" on host "localhost" (address "127.0.0.1") at port "5433".
```

(This capture is from a server on port 5433; yours says 5432.) Compare it with the same command through `psql.sh`, which says `via socket in "/var/run/postgresql"`.

### 6.3 `~/.pgpass`: passwords without typing them

Typing `postgres` at every prompt gets old, and putting passwords in URIs or environment variables leaks them into shell history. libpq's answer is a **password file**: `~/.pgpass` on macOS/Linux, `%APPDATA%\postgresql\pgpass.conf` on Windows. One line per server:

```text
# hostname:port:database:username:password
localhost:5432:*:postgres:postgres
```

`*` matches anything. The first matching line wins, so put specific lines before general ones. The file **must** be readable only by you, or libpq ignores it:

```bash
chmod 600 ~/.pgpass
```

Here's what happens with the wrong permissions (the `-w` option means "never prompt for a password", which makes failures obvious):

```text
WARNING: password file "/home/you/.pgpass" has group or world access; permissions should be u=rw (0600) or less
psql: error: connection to server at "localhost" (127.0.0.1), port 5433 failed: fe_sendauth: no password supplied
```

After `chmod 600`, the same command connects without a prompt.

> **In production:** `.pgpass` is fine for developer machines and cron jobs on a locked-down host. Services usually get credentials from a secrets manager instead. Module 27 discusses this.

---

## 7. A GUI, briefly

The course uses psql throughout, because it works everywhere (including on servers over SSH), it's scriptable, and it shows you exactly what you send and what comes back. A graphical client is still handy for browsing a schema or looking at a wide result. Two free options:

- **DBeaver Community**: a desktop app for many databases. New connection → PostgreSQL → host `localhost`, port `5432`, database `bazaar`, user `postgres`, password `postgres`. The first time, it downloads the JDBC driver.
- **pgAdmin 4**: the PostgreSQL project's own web-based admin tool. Register a server with the same five parameters. It can also run as a container if you prefer not to install it.

Both just need the parameters from section 6.2. If you can connect with native psql, you can connect with a GUI.

> **Pitfall:** GUIs often wrap your statements in transactions or auto-commit them in ways that differ from psql. When something behaves differently in a GUI, reproduce it in psql before concluding anything. Module 10 explains transactions.

---

## 8. Troubleshooting

### Port 5432 is already taken

If you already run PostgreSQL natively, or another project's container uses 5432, `docker compose up -d` fails:

```text
Error response from daemon: failed to set up container networking: driver failed programming external connectivity on endpoint pg-m00 (f4f3087306962ad97ac5576284e7542178a2c09867ac3e6b58473d964637204e): Bind for 0.0.0.0:5432 failed: port is already allocated
```

(The endpoint name in your message will be `course-pg`.) A native server on the same port can also show up as `address already in use`. The fix is to publish the container on another host port with `PGPORT_HOST`:

```bash
PGPORT_HOST=5433 docker compose up -d
docker compose ps
```

```text
NAME      IMAGE         COMMAND                  SERVICE   CREATED         STATUS                   PORTS
pg-m00    postgres:17   "docker-entrypoint.s…"   pg        4 seconds ago   Up 4 seconds (healthy)   0.0.0.0:5433->5432/tcp
```

To make it permanent, create a file called `.env` in the repository root containing `PGPORT_HOST=5433`. Compose reads it automatically, and `.env` is already in `.gitignore`. Then use `-p 5433` (or `PGPORT=5433`) for native psql. `psql.sh` and `reset.sh` are unaffected because they go through `docker exec`, not the port.

Inside the container the server still listens on 5432. Only the host side moved:

```sql
SELECT inet_server_port();
```

```text
 inet_server_port 
------------------
             5432
```

That's the container's view, even when you connected through host port 5433.

### The container isn't running, or isn't ready

```text
psql: error: connection to server at "localhost" (127.0.0.1), port 5499 failed: Connection refused
	Is the server running on that host and accepting TCP/IP connections?
```

"Connection refused" means nothing is listening on that host and port. Check `docker compose ps`. If the container is restarting over and over, `docker compose logs pg` shows why. You can also ask directly:

```bash
pg_isready -h localhost -p 5432
```

```text
localhost:5432 - accepting connections
```

### Authentication and name errors

| Message | Meaning |
|---|---|
| `fe_sendauth: no password supplied` | The server wants a password and the client had none: no prompt (`-w`), no `PGPASSWORD`, no matching `.pgpass` line. |
| `FATAL:  password authentication failed for user "postgres"` | Wrong password. The server deliberately gives the same message for a user that doesn't exist (`potsgres`), so attackers can't probe for user names. |
| `FATAL:  database "bazar" does not exist` | Typo, or you haven't run `./project/bin/reset.sh 01` yet. |

### Starting over: resetting the volume

Two levels of "start over":

1. **Just the Bazaar database.** `./project/bin/reset.sh NN` drops and recreates `bazaar`. The server and other databases are untouched. This is what you'll use most.
2. **The whole server.** Remove the container *and* its volume, and let the image initialise a fresh data directory:

   ```bash
   docker compose down -v
   docker compose up -d --wait
   ./project/bin/reset.sh 01
   ```

   `--wait` makes `up` return only once the health check passes. Use this level when you have changed server-wide state you can't easily undo (roles, settings changed with `ALTER SYSTEM`, other databases), or when you changed `POSTGRES_PASSWORD` in the compose file.

You can see the difference between `down` and `down -v` for yourself. Create a table, run `docker compose down` and `up -d` again, and the table is still there. Run `down -v` and it's gone. Exercise 3 walks you through it.

> **In production:** a container's data must always live in a volume or on a mounted disk, never only in the container's own filesystem. Module 18 covers backups, because a volume is not a backup.

---

## 9. Which version am I running?

Two ways to ask, from any client:

```sql
SELECT version();
```

```text
                                                       version                                                        
----------------------------------------------------------------------------------------------------------------------
 PostgreSQL 17.11 (Debian 17.11-1.pgdg13+2) on x86_64-pc-linux-gnu, compiled by gcc (Debian 14.2.0-19) 14.2.0, 64-bit
(1 row)
```

`version()` is a function returning a human-readable string: version, packager, platform and compiler. Good for bug reports, awkward to parse.

```sql
SHOW server_version;
SHOW server_version_num;
```

```text
         server_version          
---------------------------------
 17.11 (Debian 17.11-1.pgdg13+2)
(1 row)

 server_version_num 
--------------------
 170011
(1 row)
```

`SHOW` displays a configuration parameter. `server_version_num` is the one to use in scripts: an integer, `major * 10000 + minor`, so "is this at least 17?" is simply `current_setting('server_version_num')::int >= 170000`. Module 01's project uses exactly that check.

Your minor version (`17.11` here) may be higher than in the course's outputs. That's expected, since the image tag `postgres:17` always pulls the newest 17.x. Nothing in this course depends on the minor version.

Also note the difference between the **server** version (above) and the **client** version (`psql --version`, or the first line of psql's banner). They are separate programs, possibly on separate machines, and as section 6.1 showed, the combination matters.

---

## Summary

- The course is a sequence of modules, each with theory, ★/★★/★★★ exercises, a Bazaar milestone and worked solutions. `./project/bin/reset.sh NN` rebuilds the database as of the start of module NN.
- Keep your own milestone scripts in the git-ignored `workspace/` directory, because `reset.sh` drops the `bazaar` database.
- `docker compose up -d` starts PostgreSQL 17 as `course-pg` on `localhost:5432` with its data in a named volume. `down` keeps the data, `down -v` deletes it.
- The compose file pre-loads `pg_stat_statements` and `auto_explain`, turns on I/O timing and slow-statement logging, and enlarges shared memory. Later modules rely on all of these.
- `./project/bin/psql.sh` connects through the container (no password, Unix socket). A native psql 17 connects through the published port and needs a password, from a prompt, `PGPASSWORD`, or `~/.pgpass` with mode 600.
- Connection parameters (host, port, user, database, password) can be given as options, a URI, or `PG*` environment variables. Every libpq-based client understands all three.
- Keep psql at least as new as the server. An older psql can fail on a newer server's catalogs.
- `SHOW server_version_num` gives a version number scripts can compare.

## What's next

Your server is running and you can connect to it. [Module 01](../01-psql/) explains what you're connected *to* (clusters, databases, schemas, roles) and turns psql from a prompt into a power tool.
