"""Restore one generated backup into a new, explicitly separate test database."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
from urllib.parse import urlsplit, unquote
import psycopg
from psycopg import sql

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("backup")
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9]{8}T[0-9]{6}Z.dump", args.backup):
        raise ValueError("Use a generated backup basename")
    directory = Path(os.environ["FIREWATCH_BACKUP_DIR"]).resolve()
    source = directory / args.backup
    if not source.is_file():
        raise ValueError("Backup file not found")
    url = os.environ["FIREWATCH_DATABASE_URL"]
    parsed = urlsplit(url)
    target = "firewatch_restore_check"
    if unquote(parsed.path.lstrip("/")) == target:
        raise ValueError("Restore check database must differ from the live database")
    with psycopg.connect(url, autocommit=True) as database:
        # CREATE fails if the target already exists. It is never overwritten.
        database.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(target)))
    environment = dict(os.environ, PGHOST=parsed.hostname or "", PGPORT=str(parsed.port or 5432), PGDATABASE=target, PGUSER=unquote(parsed.username or ""), PGPASSWORD=unquote(parsed.password or ""))
    subprocess.run(["pg_restore", "--exit-on-error", "--dbname", target, str(source)], env=environment, capture_output=True, check=True)
    with psycopg.connect(host=environment["PGHOST"], port=int(environment["PGPORT"]), user=environment["PGUSER"], password=environment["PGPASSWORD"], dbname=target) as restored:
        count = restored.execute("SELECT COUNT(*) FROM operational_events").fetchone()[0]
        observations = restored.execute("SELECT COUNT(*) FROM incident_observations").fetchone()[0]
    print(json.dumps({"status": "passed", "restore_database": target, "operational_events": count, "incident_observations": observations, "live_database_unchanged": True}))

if __name__ == "__main__":
    main()
