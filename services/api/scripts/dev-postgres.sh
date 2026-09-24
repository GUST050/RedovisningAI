#!/usr/bin/env bash
# Starta en lokal PostgreSQL 16 för utveckling och tester (utan Docker).
# Användning: scripts/dev-postgres.sh [start|stop]
set -euo pipefail
PGBIN=${PGBIN:-/usr/lib/postgresql/16/bin}
DIR=${RAI_PG_DIR:-/tmp/rai-pg}
PORT=${RAI_PG_PORT:-54329}
RUN=()
if [ "$(id -u)" = "0" ]; then RUN=(runuser -u postgres --); fi
case "${1:-start}" in
  start)
    if [ ! -d "$DIR/data" ]; then
      mkdir -p "$DIR"; [ "$(id -u)" = "0" ] && chown postgres:postgres "$DIR"
      "${RUN[@]}" "$PGBIN/initdb" -D "$DIR/data" -U postgres --auth=trust -E UTF8 --locale=C.UTF-8 >/dev/null
    fi
    if ! "${RUN[@]}" "$PGBIN/pg_ctl" -D "$DIR/data" status >/dev/null 2>&1; then
      "${RUN[@]}" "$PGBIN/pg_ctl" -D "$DIR/data" -o "-p $PORT -k $DIR -c listen_addresses=127.0.0.1" -l "$DIR/log" -w start >/dev/null
    fi
    echo "PostgreSQL kör på 127.0.0.1:$PORT"
    ;;
  stop)
    "${RUN[@]}" "$PGBIN/pg_ctl" -D "$DIR/data" stop >/dev/null && echo "Stoppad" ;;
esac
