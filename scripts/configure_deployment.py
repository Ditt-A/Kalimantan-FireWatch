"""Create a private deployment env with generated access secrets and optional source keys."""
from __future__ import annotations
import argparse
import os
from pathlib import Path
import secrets
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.scrape_firms import load_map_key
from scripts.scrape_era5 import credentials_from_file

def load_environment(path: Path):
    for line in path.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ[key] = value

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--secrets-file", type=Path)
    args = parser.parse_args()
    output = ROOT / "deploy/.env"
    if output.exists():
        raise ValueError("deploy/.env already exists; existing credentials will not be overwritten")
    values = {}
    for line in (ROOT / "deploy/.env.example").read_text().splitlines():
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    for key in ("FIREWATCH_POSTGRES_PASSWORD", "FIREWATCH_SESSION_SECRET", "FIREWATCH_OPERATOR_TOKEN", "FIREWATCH_ADMIN_TOKEN"):
        values[key] = secrets.token_hex(32)
    available = []
    if args.secrets_file:
        try:
            values["FIRMS_MAP_KEY"] = load_map_key(args.secrets_file)
            available.append("FIRMS_MAP_KEY")
        except (ValueError, FileNotFoundError):
            pass
        try:
            values["CDSAPI_URL"], values["CDSAPI_KEY"] = credentials_from_file(args.secrets_file)
            available.append("CDS credentials")
        except (ValueError, FileNotFoundError):
            pass
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        stream.write("# Private deployment settings; excluded from Git and Docker images.\n")
        stream.write("\n".join(f"{key}={value}" for key, value in values.items()) + "\n")
    print("Deployment configuration created; source credentials detected: " + ", ".join(available))
    print("Operator/admin access codes are stored privately in deploy/.env; values are not logged.")

if __name__ == "__main__":
    main()
