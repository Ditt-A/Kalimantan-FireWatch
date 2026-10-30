"""Regression tests for release consistency, causal scoring, area provenance and access."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import joblib
import numpy as np
import pandas as pd
import xarray as xr
from fastapi.testclient import TestClient
from sklearn.dummy import DummyClassifier, DummyRegressor

from api.main import app, ResponsePlanRequest
from src.firewatch import auth, releases
from src.firewatch.area import polygon_area_ha
from src.firewatch.etl import canonical_detections, run_job, ingest_firms
from src.firewatch.forecasting import apply_weather_latency, score_features, WEATHER_COLUMNS
from src.firewatch.fire_weather import fire_weather_context
from src.firewatch.field_observations import save_fire_type_observation, latest_fire_type_observation
from src.firewatch.operations import audit_event
from scripts.rebuild_operational_weather import summarise

class ProductionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="firewatch-test-")
        self.root = Path(self.directory.name)
        self.environment = patch.dict(os.environ, {
            "FIREWATCH_RELEASE_ROOT": str(self.root / "releases"),
            "FIREWATCH_OPERATIONS_DB": str(self.root / "operations.sqlite3"),
            "FIREWATCH_WEATHER_CACHE": str(self.root / "weather"),
            "FIREWATCH_AUTH_REQUIRED": "0",
            "FIREWATCH_DATABASE_URL": "",
        })
        self.environment.start()
        self.client = TestClient(app)
    def tearDown(self):
        self.environment.stop()
        releases._published_snapshot.cache_clear()
        self.directory.cleanup()

    def test_polygon_and_footprint_are_distinct(self):
        polygon = {"type": "Polygon", "coordinates": [[[113.9,-3.04],[113.901,-3.04],[113.901,-3.039],[113.9,-3.039],[113.9,-3.04]]]}
        self.assertTrue(1 < polygon_area_ha(polygon) < 2)
        request = ResponsePlanRequest(latitude=-3.04, longitude=113.9, incident_polygon=polygon, area_basis="operator_polygon", area_confirmed=True)
        self.assertAlmostEqual(request.area_ha, polygon_area_ha(polygon))
        unverified = ResponsePlanRequest(latitude=-3.04, longitude=113.9, area_ha=250, area_basis="firms_footprint", area_confirmed=True)
        self.assertFalse(unverified.area_confirmed)
        with self.assertRaises(ValueError):
            polygon_area_ha({"type":"Point","coordinates":[113.9,-3.04]})

    def test_release_pin_cache_refresh_and_conflict(self):
        source = self.root / "source"
        relative = "data/processed/value.json"
        path = source / relative
        path.parent.mkdir(parents=True)
        path.write_text('{"value": 1}')
        with patch.object(releases, "RUNTIME_FILES", (relative,)):
            releases.publish_release(source, "r1", {})
            first = releases.current_snapshot()
            @releases.release_cached
            def read():
                return json.loads(releases.release_path(relative).read_text())["value"]
            self.assertEqual(read(), 1)
            path.write_text('{"value": 2}')
            releases.publish_release(source, "r2", {}, expected_release_id="r1")
            self.assertEqual(read(), 2)
            with releases.bind_snapshot(first):
                self.assertEqual(read(), 1)
                self.assertEqual(releases.current_snapshot().token, "r1")
            self.assertEqual(read(), 2)
            with self.assertRaises(ValueError):
                releases.publish_release(source, "r3", {}, expected_release_id="r1")
            self.assertEqual(releases.current_snapshot().token, "r2")

    def test_release_checksums_reject_tampering(self):
        source = self.root / "source"
        source.mkdir()
        (source / "artifact").write_text("trusted")
        with patch.object(releases, "RUNTIME_FILES", ("artifact",)):
            destination = releases.publish_release(source, "safe", {}, activate=False)
            (destination / "artifact").write_text("changed")
            with self.assertRaises(ValueError):
                releases.activate_release("safe")

    def test_weather_latency_uses_an_earlier_week(self):
        data = pd.DataFrame({"grid_id":["A"]*3, "week_start":pd.date_range("2026-01-05", periods=3, freq="7D"), "peat_pct":[50]*3})
        for name in WEATHER_COLUMNS:
            data[name] = [1.0, 2.0, 99.0]
        delayed = apply_weather_latency(data)
        self.assertEqual(delayed.iloc[2]["rainfall_7d"], 2)
        self.assertAlmostEqual(delayed.iloc[2]["peat_dryness_index"], -0.5)
        with self.assertRaises(ValueError):
            apply_weather_latency(data, 0)

    def test_scoring_has_no_target_dependency_and_rejects_expiry(self):
        model_dir = self.root / "models"
        model_dir.mkdir()
        x = pd.DataFrame({"area": [1., 2.]})
        contract = {"weather_lag_days":7, "precipitation_method":"era5_land_midnight_previous_day_v1"}
        thresholds = {"moderate":0.1,"high":0.2,"very_high":0.3}
        classifier = DummyClassifier(strategy="prior").fit(x, [0,1])
        counter = DummyRegressor(strategy="constant", constant=2).fit(x, [0,2])
        for filename, model in [("fire_occurrence_classifier.joblib", classifier), ("hotspot_count_model.joblib", counter)]:
            joblib.dump({"model":model,"features":["area"],"validation_thresholds":thresholds,"operational_contract":contract}, model_dir / filename)
        frame = pd.DataFrame({"grid_id":["A"],"week_start":pd.to_datetime(["2026-08-03"]),"province":["Central Kalimantan"],"district":["Pulang Pisau"],"centroid_lat":[-3.04],"centroid_lon":[113.9],"peat_pct":[100.],"rainfall_30d":[100.],"hotspot_prev_30d":[3.],"area":[1.]})
        result, metadata = score_features(frame, model_dir, issued_at="2026-08-03T06:00:00Z")
        self.assertEqual(result.iloc[0]["predicted_hotspot_count_next_7d"], 2)
        self.assertFalse(metadata["historical"])
        self.assertNotIn("fire_next_7d", result)
        with self.assertRaises(ValueError):
            score_features(frame, model_dir, issued_at="2026-10-30T00:00:00Z")

    def test_firms_revision_is_one_observation(self):
        data = pd.DataFrame({"latitude":[-3.,-3.],"longitude":[113.9,113.9],"acq_date":["2026-10-29"]*2,"acq_time":[1230]*2,"satellite":["20","N20"],"instrument":["VIIRS"]*2,"frp":[10.,20.],"scan":[0.4]*2,"track":[0.4]*2,"source_product":["VIIRS_NOAA20_NRT","VIIRS_NOAA20_SP"],"confidence":["n","h"]})
        canonical = canonical_detections(data)
        self.assertEqual(len(canonical), 1)
        self.assertEqual(canonical.iloc[0]["frp"],20.)
        repeated = canonical_detections(pd.concat([canonical, data], ignore_index=True))
        self.assertEqual(repeated.iloc[0]["detection_id"], canonical.iloc[0]["detection_id"])

    def test_fwi_does_not_select_future_noon(self):
        times = pd.date_range("2026-01-01", periods=41*24, freq="h")
        hourly = {"time":times.strftime("%Y-%m-%dT%H:%M").tolist(),"temperature_2m":[30.]*len(times),"relative_humidity_2m":[70.]*len(times),"wind_speed_10m":[10.]*len(times),"precipitation":[0.]*len(times)}
        current = datetime(2026,2,10,8)
        payload = {"hourly":hourly,"current":{"time":current.isoformat()},"timezone":"Asia/Jakarta","_fetched_at_utc":"2026-02-10T01:00:00Z"}
        with patch("src.firewatch.fire_weather._open_meteo_payload", return_value=payload):
            context = fire_weather_context(-3.04,113.9)
        used = datetime.fromisoformat(context["weather_at_local_noon"]["timestamp"])
        self.assertLessEqual(used,current)
        self.assertEqual(used,datetime(2026,2,9,12))

    def test_successful_zero_detection_ingest_is_not_an_error(self):
        source = self.root / "source"
        data = source / "data/processed/firms_kalimantan_clean.parquet"
        data.parent.mkdir(parents=True)
        pd.DataFrame({"latitude":[-3.],"longitude":[113.9],"acq_date":["2026-10-28"],"acq_time":[1230],"satellite":["N20"],"instrument":["VIIRS"],"frp":[10.],"scan":[.4],"track":[.4],"source_product":["VIIRS_NOAA20_SP"],"confidence":["h"],"grid_id":["A"],"province":["Central Kalimantan"],"district":["Pulang Pisau"]}).to_parquet(data,index=False)
        availability = "data_id,min_date,max_date\nVIIRS_NOAA20_NRT,2026-01-01,2026-10-30\n"
        empty = "latitude,longitude,acq_date,acq_time,frp,scan,track,confidence,satellite\n"
        responses = [SimpleNamespace(text=availability,content=availability.encode()),SimpleNamespace(text=empty,content=empty.encode())]
        with patch.dict(os.environ,{"FIRMS_MAP_KEY":"unit-fixture-not-a-real-key"}), patch("src.firewatch.etl.current_snapshot",return_value=releases.Snapshot("fixture",source,{})), patch("src.firewatch.etl._get",side_effect=responses):
            result = ingest_firms(root=self.root,now=datetime(2026,10,30,12,tzinfo=timezone.utc))
        self.assertEqual(result["status"],"passed")
        self.assertEqual(result["rows"],1)
        self.assertTrue((self.root/"data/operational/firms_watermark.json").exists())

    def test_failed_ingest_does_not_advance_watermark(self):
        with patch.dict(os.environ,{"FIRMS_MAP_KEY":""}), self.assertRaises(ValueError):
            ingest_firms(root=self.root)
        self.assertFalse((self.root/"data/operational/firms_watermark.json").exists())

    def test_era5_daily_rain_uses_midnight_accumulation(self):
        times = pd.date_range("2024-01-01", periods=48, freq="h")
        rain = np.array([(t.hour if t.hour else 24) / 1000 for t in times])[:,None,None]
        dataset = xr.Dataset({"tp":(("valid_time","latitude","longitude"),rain,{"units":"m","GRIB_stepType":"accum"}),"t2m":(("valid_time","latitude","longitude"),np.full_like(rain,300.)),"swvl1":(("valid_time","latitude","longitude"),np.full_like(rain,.4))},coords={"valid_time":times,"latitude":[-3.],"longitude":[113.9]})
        grid = pd.DataFrame({"grid_id":["A"],"centroid_lat":[-3.],"centroid_lon":[113.9]})
        with patch("scripts.rebuild_operational_weather._open_era5_month",return_value=dataset):
            result = summarise(Path("fixture.nc"),grid)
        day = result.loc[result.date.eq(pd.Timestamp("2024-01-01"))].iloc[0]
        self.assertEqual(day.rainfall_mm,24)
        self.assertAlmostEqual(day.temperature_mean_c,26.85)

    def test_observation_is_incident_scoped_and_expires(self):
        old = (datetime.now(timezone.utc)-timedelta(days=2)).isoformat()
        save_fire_type_observation(grid_id="A",latitude=-3.,longitude=113.9,fire_type_class="surface",confidence="reported",note=None,incident_id="expired",observed_at_utc=old)
        self.assertIsNone(latest_fire_type_observation("A","expired"))
        save_fire_type_observation(grid_id="A",latitude=-3.,longitude=113.9,fire_type_class="surface",confidence="reported",note=None,incident_id="current")
        self.assertIsNotNone(latest_fire_type_observation("A","current"))
        self.assertIsNone(latest_fire_type_observation("A","other"))
        self.assertIsNone(latest_fire_type_observation("A"))

    def test_auth_and_stale_selection(self):
        with patch.dict(os.environ,{"FIREWATCH_AUTH_REQUIRED":"1","FIREWATCH_SESSION_SECRET":"session-fixture-"*4,"FIREWATCH_OPERATOR_TOKEN":"operator-fixture-"*3}):
            self.assertEqual(self.client.post("/api/v1/response-plans",json={}).status_code,401)
            self.assertEqual(self.client.get("/health").status_code,200)
            login = self.client.post("/api/v1/auth/login",json={"access_code":"operator-fixture-"*3})
            self.assertEqual(login.status_code,200)
            cookie = login.cookies.get(auth.COOKIE)
            self.assertEqual(auth.session_role(cookie),"operator")
            self.assertIsNone(auth.session_role(cookie+"x"))
        self.assertEqual(self.client.get("/api/v1/priority/grid?release_id=obsolete").status_code,409)

    def test_api_planner_keeps_area_provenance(self):
        weather = {"lookup_status":"unavailable","recommended_fwi_class":"unknown","message":"offline fixture"}
        with patch("api.main._fire_weather_lookup",return_value=weather):
            context = self.client.get("/api/v1/planner-context",params={"latitude":-3.04,"longitude":113.9,"map_area_ha":250,"map_merge_radius_km":3})
            self.assertEqual(context.status_code,200,context.text)
            self.assertEqual(context.json()["model_input_defaults"]["area_basis"],"firms_footprint")
            result = self.client.post("/api/v1/response-plans",json={"latitude":-3.04,"longitude":113.9,"area_ha":250,"area_basis":"firms_footprint"})
            self.assertEqual(result.status_code,200,result.text)
            self.assertTrue(result.json()["area_context"]["requires_confirmation"])
            self.assertGreater(result.json()["planning_output"]["planned_personnel"],0)
        metadata = self.client.get("/api/v1/metadata/status")
        self.assertEqual(metadata.status_code,200,metadata.text)
        self.assertEqual(metadata.json()["layers"]["forecast"]["status"],"expired")
        self.assertTrue(metadata.json()["not_live"])

    def test_failed_job_retains_release_and_has_audit(self):
        previous = releases.current_snapshot().token
        def fail():
            raise ValueError("fixture failure")
        with self.assertRaises(RuntimeError):
            run_job("fixture_job",fail,root=self.root)
        self.assertEqual(releases.current_snapshot().token,previous)
        with sqlite3.connect(self.root/"operations.sqlite3") as database:
            self.assertEqual(database.execute("SELECT COUNT(*) FROM operational_events WHERE kind='etl_run'").fetchone()[0],1)

if __name__ == "__main__":
    unittest.main()
