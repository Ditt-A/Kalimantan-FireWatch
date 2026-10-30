"""Exercise a disposable Docker stack, including operator auth and PostgreSQL restore."""
from __future__ import annotations
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time

import requests
import urllib3

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.firewatch.releases import atomic_json

PROJECT = "firewatch-check"

def main():
    env_path = ROOT / "deploy/.env.check"
    if env_path.exists():
        raise ValueError("deploy/.env.check already exists; it will not be overwritten")
    existing = subprocess.check_output(["docker", "ps", "-aq", "--filter", f"label=com.docker.compose.project={PROJECT}"], text=True)
    if existing.strip():
        raise ValueError("A check stack already exists; existing containers will not be modified")
    operator = secrets.token_hex(32)
    values = {"FIREWATCH_POSTGRES_PASSWORD":secrets.token_hex(24), "FIREWATCH_SESSION_SECRET":secrets.token_hex(32), "FIREWATCH_OPERATOR_TOKEN":operator, "FIREWATCH_DOMAIN":"localhost", "FIREWATCH_BIND_ADDRESS":"127.0.0.1"}
    descriptor = os.open(env_path, os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)
    with os.fdopen(descriptor,"w") as stream:
        stream.write("\n".join(f"{key}={value}" for key,value in values.items())+"\n")
    compose = ["docker","compose","--env-file",str(env_path),"-p",PROJECT,"-f",str(ROOT/"deploy/compose.yaml"),"-f",str(ROOT/"deploy/compose.check.yaml")]
    checks = {}
    created = False
    def command(args, **kwargs):
        return subprocess.run(compose+args,cwd=ROOT,capture_output=True,text=True,check=True,**kwargs)
    def assert_status(response, expected, name):
        if response.status_code != expected:
            raise RuntimeError(f"{name}: expected HTTP {expected}, got {response.status_code}")
        checks[name] = response.status_code
    try:
        created = True
        command(["up","-d","--no-build","--wait","--wait-timeout","240"],timeout=300)
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        base = "https://localhost:18443"
        session = requests.Session()
        session.verify = False  # Isolated localhost certificate; never an external endpoint.
        for attempt in range(15):
            try:
                ready = session.get(base+"/ready",timeout=20)
                if ready.status_code == 200:
                    break
            except requests.RequestException:
                pass
            time.sleep(2)
        assert_status(session.get(base+"/health",timeout=20),200,"health")
        assert_status(session.get(base+"/ready",timeout=30),200,"readiness")
        assert_status(session.get(base+"/prioritization",timeout=30),200,"next_dashboard")
        grid = session.get(base+"/api/v1/priority/grid?limit=1",timeout=30)
        assert_status(grid,200,"forecast_grid")
        assert_status(session.post(base+"/api/v1/response-plans",json={},timeout=20),401,"anonymous_write_rejected")
        assert_status(session.post(base+"/api/v1/auth/login",json={"access_code":operator},timeout=20),200,"operator_login")
        plan = session.post(base+"/api/v1/response-plans",json={"latitude":-3.04,"longitude":113.9,"area_ha":2,"area_basis":"field_report","area_confirmed":True},timeout=90)
        assert_status(plan,200,"personnel_model")
        if plan.json()["planning_output"]["planned_personnel"] <= 0 or plan.json()["area_context"]["requires_confirmation"]:
            raise ValueError("Invalid model or area provenance output")
        observation = session.post(base+"/api/v1/field-observations",json={"incident_id":"docker-smoke-check","latitude":-3.04,"longitude":113.9,"fire_type_class":"surface"},timeout=60)
        assert_status(observation,200,"postgres_observation_write")
        assert_status(session.get(base+"/api/v1/field-observations?grid_id=G00301&incident_id=docker-smoke-check",timeout=20),200,"postgres_observation_read")
        assert_status(session.get(base+"/api/v1/priority/grid?release_id=obsolete",timeout=20),409,"stale_selection_rejected")
        proxy = command(["exec","-T","web","node","-e","fetch('http://127.0.0.1:3000/api/v1/priority/grid?limit=1').then(r=>process.exit(r.status===200?0:1)).catch(()=>process.exit(1))"],timeout=45)
        checks["next_api_proxy"] = "passed"
        backup = command(["run","--rm","--no-deps","worker","python","scripts/backup_operations.py"],timeout=90)
        backup_result = json.loads(backup.stdout.strip().splitlines()[-1])
        restore = command(["run","--rm","--no-deps","worker","python","scripts/check_postgres_restore.py",backup_result["backup"]],timeout=90)
        restore_result = json.loads(restore.stdout.strip().splitlines()[-1])
        if restore_result["incident_observations"] < 1:
            raise ValueError("Restore lost the test observation")
        checks["postgres_backup_restore"] = restore_result
        report = {"status":"passed","project":PROJECT,"checks":checks,"provider_acquisition_started":False,"temporary_stack_removed_after_test":True}
        atomic_json(ROOT/"reports/production_checks/docker_stack.json",report)
        print(json.dumps(report,indent=2))
    finally:
        if created:
            # Only this newly-created disposable project and its fixture
            # volumes are removed. No production data volume is selected.
            subprocess.run(compose+["down","--volumes","--remove-orphans"],cwd=ROOT,capture_output=True,text=True,timeout=90)
        env_path.unlink(missing_ok=True)

if __name__=="__main__":
    main()
