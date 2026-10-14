#!/usr/bin/env bash
# Module 12: run a mixed Bazaar workload for 60 seconds with pgbench, so that
# pg_stat_statements has something realistic to show.
#   ./modules/12-query-performance/examples/workload/run_workload.sh [seconds]
# The scripts are read inside the container from /course/modules/... (mounted by
# docker-compose.yml). Set COURSE_PG_CONTAINER to use another container.
set -euo pipefail
SECONDS_TO_RUN="${1:-60}"
CONTAINER="${COURSE_PG_CONTAINER:-course-pg}"
DIR=/course/modules/12-query-performance/examples/workload
docker exec "$CONTAINER" pgbench -U postgres -n -c 4 -j 2 -T "$SECONDS_TO_RUN" \
    -f "$DIR/my_orders.sql@40" \
    -f "$DIR/order_detail.sql@30" \
    -f "$DIR/category_page.sql@20" \
    -f "$DIR/seller_dashboard.sql@9" \
    -f "$DIR/admin_report.sql@1" \
    bazaar
