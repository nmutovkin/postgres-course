# Postgres 17 + Patroni (etcd v3 support) in one image.
# Every Patroni node runs this image; the lab's "client" container reuses it
# because it already has psql, Python, and psycopg.
ARG PG_IMAGE=postgres:17
FROM ${PG_IMAGE}

# python3-venv: an isolated environment for Patroni (Debian forbids pip into
#   the system Python, PEP 668).
# curl/jq: talk to the Patroni REST API from the shell.
# iproute2/iputils-ping: poke at the network during partition experiments.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        python3 python3-venv curl jq iproute2 iputils-ping ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# patroni[etcd3]  -> the etcd v3 DCS driver
# patroni[psycopg3] / psycopg[binary] -> the driver Patroni uses to talk to Postgres,
#   also used by our Python client scripts.
RUN python3 -m venv /opt/patroni \
 && /opt/patroni/bin/pip install --no-cache-dir \
        "patroni[etcd3]==4.1.5" "psycopg[binary]==3.3.6" \
 && ln -s /opt/patroni/bin/patroni    /usr/local/bin/patroni \
 && ln -s /opt/patroni/bin/patronictl /usr/local/bin/patronictl

ENV PATH=/opt/patroni/bin:$PATH \
    PATRONICTL_CONFIG_FILE=/etc/patroni

# Patroni (not docker-entrypoint.sh) owns initdb, configuration and start/stop.
USER postgres
WORKDIR /var/lib/postgresql
EXPOSE 5432 8008
# /etc/patroni holds 00-common.yml + 10-node.yml (see compose.yml); Patroni merges them.
CMD ["patroni", "/etc/patroni"]
