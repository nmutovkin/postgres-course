# PostgreSQL 17 + pgBackRest, for the Module 18 lab.
# The official postgres:17 image is Debian-based and already has the PGDG apt
# repository configured, so pgBackRest is one apt-get away.
FROM postgres:17

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends pgbackrest; \
    rm -rf /var/lib/apt/lists/*

# Directories the lab uses. Named volumes mounted here inherit this ownership
# the first time they are created, so the postgres user can write to them.
RUN set -eux; \
    mkdir -p /archive/wal /backups/base /var/lib/pgbackrest /var/log/pgbackrest /var/spool/pgbackrest /etc/pgbackrest; \
    chown -R postgres:postgres /archive /backups /var/lib/pgbackrest /var/log/pgbackrest /var/spool/pgbackrest; \
    chmod 750 /archive /backups /var/lib/pgbackrest
