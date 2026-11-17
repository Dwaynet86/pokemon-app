#!/bin/sh
# Wait for Postgres, apply the schema (safe to repeat: it only creates what's missing), then start the web server.
set -e
python - <<'PY'
import sys, time
import psycopg
from app.config import Config

for attempt in range(60):
    try:
        psycopg.connect(Config.DATABASE_URL, connect_timeout=3).close()
        break
    except Exception as exc:                                   # noqa: BLE001
        print(f"Waiting for the database ({attempt + 1}/60): {exc}", flush=True)
        time.sleep(2)
else:
    sys.exit("The database never became reachable. Check DATABASE_URL.")
PY
python scripts/init_db.py
exec gunicorn run:app --bind "0.0.0.0:${PORT:-8000}" --workers "${WEB_WORKERS:-2}" --threads "${WEB_THREADS:-4}" \
    --timeout 120 --access-logfile - --forwarded-allow-ips "*"
