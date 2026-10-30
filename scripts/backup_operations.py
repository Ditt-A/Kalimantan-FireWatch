"""Create a consistent SQLite or PostgreSQL backup and a checksum manifest."""
from __future__ import annotations
from datetime import datetime, timezone
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.firewatch.releases import atomic_json, current_snapshot, sha256

def main():
    directory = Path(os.environ.get("FIREWATCH_BACKUP_DIR", ROOT / "data/operational/backups"))
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    url = os.environ.get("FIREWATCH_DATABASE_URL")
    if url:
        parsed = urlsplit(url)
        environment = dict(os.environ, PGHOST=parsed.hostname or "", PGPORT=str(parsed.port or 5432), PGDATABASE=unquote(parsed.path.lstrip("/")), PGUSER=unquote(parsed.username or ""), PGPASSWORD=unquote(parsed.password or ""))
        target = directory / (stamp + ".dump")
        temporary = target.with_suffix(".pending")
        try:
            subprocess.run(["pg_dump", "--format=custom", "--file", str(temporary)], env=environment, capture_output=True, check=True)
            temporary.replace(target)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise RuntimeError("Database backup failed; no successful manifest was written") from None
        kind = "postgresql_custom_dump"
    else:
        source = Path(os.environ.get("FIREWATCH_OPERATIONS_DB", ROOT / "data/operational/operations.sqlite3"))
        if not source.exists():
            raise ValueError("Operational database does not exist")
        target = directory / (stamp + ".sqlite3")
        with closing(sqlite3.connect(source)) as live, closing(sqlite3.connect(target)) as destination:
            live.backup(destination)
        with closing(sqlite3.connect(target)) as check:
            if check.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("SQLite backup integrity check failed")
        kind = "sqlite_online_backup"
    snapshot = current_snapshot()
    manifest = {"status": "passed", "backup": target.name, "format": kind, "sha256": sha256(target), "release_id": snapshot.token, "created_at_utc": datetime.now(timezone.utc).isoformat(), "note": "Copy this backup and the referenced immutable release to independent storage; a local backup alone is not disaster recovery."}
    atomic_json(directory / (stamp + ".json"), manifest)
    print(json.dumps(manifest))

if __name__ == "__main__":
    main()
