"""Consultas e regressão de ingestão em SQLite temporário, sem tocar na coleta."""

import importlib
import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.dashboard import criar_router


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "test.db"
        self.db = sqlite3.connect(self.path)
        self.addCleanup(self.db.close)
        self.db.execute("CREATE TABLE batches (device_id TEXT, boot_id TEXT, batch_seq INTEGER, "
                        "payload_json TEXT, recebido_em TEXT)")
        app = FastAPI()
        app.include_router(criar_router(self.path))
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.example = json.loads((Path(__file__).parents[1] / "docs/exemplos/lote-sintetico.json").read_text())

    def insert(self, seq=0, boot="a" * 32, segment=1, t0=None, index=None, received=None, device="sensor-a", xyz=None):
        batch = {**self.example, "boot_id": boot, "batch_seq": seq, "segment_id": segment,
                 "t0_monotonic_us": t0 if t0 is not None else 1_000_000 + seq * 1_000_000,
                 "first_sample_index": seq * 500 if index is None else index,
                 "samples": [xyz or [0, 0, 16384] for _ in range(500)]}
        self.db.execute("INSERT INTO batches VALUES (?, ?, ?, ?, ?)",
                        (device, boot, seq, json.dumps(batch), received or
                         (datetime(2026, 9, 15, 12, tzinfo=timezone.utc) + timedelta(seconds=seq)).isoformat()))
        self.db.commit()

    def signal(self, **params):
        return self.client.get("/api/v1/dashboard/signal", params={"device_id": "sensor-a", **params})

    def series(self, **params):
        return self.client.get("/api/v1/dashboard/timeseries", params={"device_id": "sensor-a", **params})

    def test_timeseries_keeps_history_across_restarts(self):
        self.insert(0, received="2026-09-15T12:00:00+00:00")
        self.insert(1, received="2026-09-15T12:00:01+00:00")
        self.insert(0, boot="b" * 32, received="2026-09-15T12:00:05+00:00")
        data = self.series(segundos=10).json()
        self.assertEqual(data["sample_count"], 1500)
        self.assertEqual(data["boot_count"], 2)
        self.assertEqual(len(data["traces"]), 2)
        self.assertEqual([t["boot_id"] for t in data["traces"]], ["a" * 32, "b" * 32])
        # There is no connecting line or invented data during the restart.
        self.assertEqual(data["traces"][0]["points"][-1][0], -4)
        self.assertAlmostEqual(data["traces"][1]["points"][0][0], -.998)

    def test_timeseries_preserves_sample_spacing_during_network_bursts(self):
        self.insert(0, received="2026-09-15T12:00:00+00:00")
        self.insert(1, received="2026-09-15T12:00:04+00:00")
        self.insert(2, received="2026-09-15T12:00:04.100000+00:00")
        data = self.series().json()
        self.assertEqual(len(data["traces"]), 1)
        points = data["traces"][0]["points"]
        self.assertEqual(len(points), 1500)
        self.assertTrue(all(abs(b[0] - a[0] - .002) < 1e-8 for a, b in zip(points, points[1:])))

    def test_timeseries_clock_anchor_does_not_move_between_refreshes(self):
        self.insert(1, received="2026-09-15T12:00:00+00:00")
        first = self.series().json()
        original_time = first["end_time_ms"] + first["traces"][0]["points"][0][0] * 1000
        # A late earlier packet must not redefine this boot's clock anchor.
        self.insert(0, received="2026-09-15T12:00:03+00:00")
        self.insert(2, received="2026-09-15T12:00:04+00:00")
        second = self.series().json()
        same_point = second["traces"][0]["points"][500]
        self.assertAlmostEqual(second["end_time_ms"] + same_point[0] * 1000, original_time)
        self.assertEqual(len(second["traces"]), 1)

    def test_timeseries_bounds_and_device_isolation(self):
        self.assertEqual(self.series().status_code, 404)
        self.insert(0)
        self.insert(1)
        self.insert(0, boot="b" * 32, device="sensor-b")
        data = self.series(segundos=1).json()
        self.assertEqual(data["sample_count"], 500)
        self.assertEqual(data["boot_count"], 1)
        self.assertEqual(self.series(segundos=61).status_code, 422)

    def test_empty_and_static_files(self):
        self.assertEqual(self.client.get("/api/v1/dashboard/devices").json(), [])
        self.assertEqual(self.signal().status_code, 404)
        for path in ["/painel", "/painel/", "/painel/static/dashboard.js", "/painel/static/dashboard.css"]:
            self.assertEqual(self.client.get(path).status_code, 200)
        self.assertEqual(self.client.get("/painel/static/main.py").status_code, 404)

    def test_conversion_timing_and_window(self):
        self.insert(); self.insert(1)
        result = self.signal(segundos=1).json()
        self.assertEqual(result["sample_count"], 500)
        self.assertEqual(len(result["traces"]), 1)
        points = result["traces"][0]["points"]
        self.assertAlmostEqual(points[0][0], -.998)
        self.assertEqual(points[-1][0], 0)
        self.assertAlmostEqual(points[0][3], 9.80665, places=4)

    def test_orders_capture_not_receipt_and_joins_continuous_batches(self):
        self.insert(1, received="2026-09-15T12:00:01+00:00")
        self.insert(0, received="2026-09-15T12:00:10+00:00")
        result = self.signal().json()
        self.assertEqual(result["batch_seq"], 1)
        self.assertEqual(result["sample_count"], 1000)
        self.assertEqual(len(result["traces"]), 1)
        self.assertEqual(result["traces"][0]["points"][0][0], -1.998)

    def test_gaps_segments_indices_and_time_discontinuities(self):
        self.insert(0)
        self.insert(2)
        self.insert(3, segment=2, index=0)
        self.insert(4, segment=2, index=1000)
        self.insert(5, segment=2, index=1500, t0=6_200_000)
        result = self.signal().json()
        self.assertEqual(len(result["traces"]), 5)
        self.assertEqual(result["sample_count"], 2500)

    def test_selects_latest_boot_and_keeps_devices_separate(self):
        self.insert(9)
        self.insert(0, boot="b" * 32, received="2026-09-15T13:00:00+00:00")
        self.insert(0, boot="c" * 32, device="sensor-b", received="2026-09-15T14:00:00+00:00")
        self.assertEqual(self.signal().json()["boot_id"], "b" * 32)
        self.assertEqual(self.signal(boot_id="a" * 32).json()["batch_seq"], 9)
        self.assertEqual(self.signal(boot_id="c" * 32).status_code, 404)
        self.assertEqual(len(self.client.get("/api/v1/dashboard/sessions?device_id=sensor-a").json()), 2)

    def test_clipping_count_and_query_limits(self):
        self.insert(xyz=[32700, 0, -32768])
        self.assertEqual(self.signal().json()["clipped_samples"], 500)
        for seconds in [0, -1, 61, "invalid"]:
            self.assertEqual(self.signal(segundos=seconds).status_code, 422)
        self.assertEqual(self.signal(device_id="' OR 1=1 --").status_code, 404)

    def test_sixty_second_payload_is_bounded(self):
        for seq in range(65):
            self.insert(seq)
        result = self.signal(segundos=60).json()
        self.assertEqual(result["sample_count"], 30000)
        self.assertEqual(result["batch_count"], 60)


class IngestionRegressionTests(unittest.TestCase):
    def test_ingestion_ack_and_dashboard_share_database(self):
        # Import only after overriding the database location; production is untouched.
        with tempfile.TemporaryDirectory() as temp:
            with patch.dict(os.environ, {"CASCAVIBE_DB_PATH": str(Path(temp) / "ingestion.db")}):
                spec = importlib.util.spec_from_file_location("api._test_main", Path(__file__).with_name("main.py"))
                main = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(main)
                try:
                    with TestClient(main.app) as client:
                        batch = json.loads((Path(__file__).parents[1] / "docs/exemplos/lote-sintetico.json").read_text())
                        headers = {"Authorization": "Bearer TOKEN_TESTE"}
                        path = "/api/v1/telemetry/batches"
                        self.assertEqual(client.post(path, json=batch).status_code, 401)
                        first = client.post(path, json=batch, headers=headers)
                        self.assertEqual(first.status_code, 201)
                        self.assertEqual(first.json(), {"accepted": True, "batch_id": batch["batch_id"]})
                        self.assertEqual(int(first.headers["content-length"]), len(first.content))
                        self.assertEqual(client.post(path, json=batch, headers=headers).status_code, 200)
                        batch["samples"][0][0] = 100
                        self.assertEqual(client.post(path, json=batch, headers=headers).status_code, 409)
                        data = client.get("/api/v1/dashboard/signal", params={"device_id": batch["device_id"]}).json()
                        self.assertEqual(data["sample_count"], 500)
                        self.assertEqual(data["traces"][0]["points"][0][1], 0)
                        self.assertEqual(len(client.get("/api/v1/telemetry/batches/recentes").json()), 1)
                finally:
                    main.conexao.close()


if __name__ == "__main__":
    unittest.main()
