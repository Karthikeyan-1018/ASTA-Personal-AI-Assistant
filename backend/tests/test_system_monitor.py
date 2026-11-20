"""Read-only System Monitor collector and response validation tests."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from httpx import ASGITransport, AsyncClient

from app.core.config import settings
from app.schemas.system_monitor import SystemMonitorSnapshotResponse
from app.services.system_monitor import collect_snapshot
from app.api.dependencies import get_current_user_id
from app.database.connection import get_db


class SystemMonitorLiveSmokeTests(unittest.TestCase):
    """Inspect real local sources without asserting machine-specific values."""

    def test_live_windows_metrics_are_real_and_response_is_validated(self):
        first = collect_snapshot(include_system_info=True, include_processes=False)
        second = collect_snapshot(include_system_info=True, include_processes=False)

        result = SystemMonitorSnapshotResponse(**second)
        self.assertGreaterEqual(result.refresh_interval_seconds, 1)
        self.assertLessEqual(result.refresh_interval_seconds, 10)
        self.assertIsInstance(result.collected_at.isoformat(), str)

        if first["memory"].get("available"):
            memory = second["memory"]
            self.assertGreater(memory["total_bytes"], 0)
            self.assertGreaterEqual(memory["available_bytes"], 0)
            self.assertGreaterEqual(memory["used_bytes"], 0)
            self.assertGreaterEqual(memory["usage_percent"], 0)
            self.assertLessEqual(memory["usage_percent"], 100)

        if second["cpu"].get("usage_percent") is not None:
            self.assertGreaterEqual(second["cpu"]["usage_percent"], 0)
            self.assertLessEqual(second["cpu"]["usage_percent"], 100)

        network = second["network"]
        if network.get("available"):
            self.assertGreaterEqual(network["bytes_sent"], 0)
            self.assertGreaterEqual(network["bytes_received"], 0)
            self.assertIsInstance(network["interfaces"], list)

        gpu = second["gpu"]
        self.assertIsInstance(gpu.get("available"), bool)
        if gpu["available"]:
            self.assertTrue(gpu.get("model"))
            if gpu.get("memory_total_mib") is not None:
                self.assertGreater(gpu["memory_total_mib"], 0)

    def test_refresh_interval_is_clamped(self):
        original = settings.SYSTEM_MONITOR_REFRESH_SECONDS
        try:
            settings.SYSTEM_MONITOR_REFRESH_SECONDS = 100
            result = collect_snapshot(include_system_info=False, include_processes=False)
            self.assertEqual(result["refresh_interval_seconds"], 10)
        finally:
            settings.SYSTEM_MONITOR_REFRESH_SECONDS = original

    def test_disabled_capabilities_do_not_fabricate_readings(self):
        result = collect_snapshot(include_system_info=False, include_processes=False)
        for section in ("cpu", "memory", "disk", "network", "gpu", "system", "processes"):
            self.assertFalse(result[section]["available"])
            self.assertIsNone(result[section].get("usage_percent"))


class SystemMonitorApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from app.main import app

        self.app = app

        async def no_database():
            yield None

        async def owner():
            return 1

        self.app.dependency_overrides[get_db] = no_database
        self.app.dependency_overrides[get_current_user_id] = owner
        self.client = AsyncClient(
            transport=ASGITransport(app=self.app),
            base_url="http://test",
        )
        self._settings = [
            patch.object(settings, name, value)
            for name, value in (
                ("TOOL_OS_AUTOMATION_ENABLED", True),
                ("TOOL_SYSTEM_INFO_ENABLED", True),
                ("TOOL_PROCESS_INSPECTION_ENABLED", True),
            )
        ]
        for item in self._settings:
            item.start()

    async def asyncTearDown(self):
        for item in reversed(self._settings):
            item.stop()
        self.app.dependency_overrides.clear()
        await self.client.aclose()

    async def test_endpoint_is_authenticated(self):
        self.app.dependency_overrides.pop(get_current_user_id, None)
        response = await self.client.get("/api/system-monitor/snapshot")
        self.assertEqual(response.status_code, 401)

    async def test_authenticated_snapshot_uses_validated_response(self):
        sample = {
            "collected_at": "2026-11-19T00:00:00+00:00",
            "refresh_interval_seconds": 2,
            **{
                key: {"available": False, "reason": "test host metric unavailable"}
                for key in ("cpu", "memory", "disk", "network", "gpu", "system", "processes")
            },
        }
        with patch("app.api.system_monitor.collect_snapshot", return_value=sample):
            response = await self.client.get("/api/system-monitor/snapshot")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["cpu"]["available"])
        self.assertEqual(response.json()["refresh_interval_seconds"], 2)

    async def test_deployment_policy_overrides_collector_data(self):
        sample = {
            "collected_at": "2026-11-19T00:00:00+00:00",
            "refresh_interval_seconds": 2,
            **{
                key: {"available": True, "usage_percent": 42}
                for key in ("cpu", "memory", "disk", "network", "gpu", "system", "processes")
            },
        }
        with (
            patch.object(settings, "TOOL_OS_AUTOMATION_ENABLED", False),
            patch("app.api.system_monitor.collect_snapshot", return_value=sample),
        ):
            response = await self.client.get("/api/system-monitor/snapshot")
        self.assertEqual(response.status_code, 200)
        for section in ("cpu", "memory", "disk", "network", "gpu", "system", "processes"):
            self.assertFalse(response.json()[section]["available"])
            self.assertIn("deployment policy", response.json()[section]["reason"])


if __name__ == "__main__":
    unittest.main()
