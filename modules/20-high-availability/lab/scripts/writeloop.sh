#!/usr/bin/env bash
# Run the write-loop client inside m20-client. All arguments go to writeloop.py.
#   ./scripts/writeloop.sh --duration 60
#   ./scripts/writeloop.sh --dsn "host=m20-pg1,m20-pg2,m20-pg3 dbname=postgres user=postgres target_session_attrs=read-write connect_timeout=2"
exec docker exec -i m20-client python3 -u /lab/client/writeloop.py "$@"
