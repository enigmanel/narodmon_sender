"""Report selection and per-sensor optimistic settings."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import voluptuous as vol

from custom_components import narodmon_sender as integration
from custom_components.narodmon_sender.config_flow import (
    NarodmonConfigFlow,
    NarodmonOptionsFlow,
)
from custom_components.narodmon_sender.const import CONF_OPTIMISTIC_SENSORS


class ReportingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.reported = datetime(2026, 1, 1, microsecond=100000, tzinfo=timezone.utc)
        self.state = SimpleNamespace(
            state="20", last_updated=self.reported - timedelta(days=1),
            last_reported=self.reported,
        )
        self.states = {"sensor.temperature": self.state}
        self.entry = SimpleNamespace(
            data={"mac": "TEST", "sensor_map": {"sensor.temperature": "T1"}},
            options={},
        )
        self.runtime = {}
        self.hass = SimpleNamespace(
            config_entries=SimpleNamespace(async_get_entry=Mock(return_value=self.entry)),
            states=SimpleNamespace(get=self.states.get),
            data={integration.DOMAIN: {"test": self.runtime}},
        )
        self.send = AsyncMock(return_value="OK")
        patcher = patch.object(integration, "_send_via_tcp", self.send)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def send_current(self):
        await integration._send_single_config(self.hass, "test")

    async def test_same_value_new_report_is_sent_with_report_time(self):
        await self.send_current()
        # A second report in the same second must still be detected.
        self.state.last_reported += timedelta(microseconds=100000)
        await self.send_current()
        self.assertEqual(self.send.await_count, 2)
        packet = self.send.call_args.args[0]
        self.assertIn(f"#T1#20.00#{int(self.state.last_reported.timestamp())}\n", packet)
        self.assertEqual(
            self.runtime["last_sent_timestamps"]["T1"], self.state.last_reported.timestamp()
        )

    async def test_same_report_is_not_sent_twice(self):
        await self.send_current()
        with self.assertLogs(integration.__name__, level="ERROR"):
            await self.send_current()
        self.send.assert_awaited_once()

    async def test_optimistic_sends_repeated_reading_with_send_time(self):
        self.entry.options[CONF_OPTIMISTIC_SENSORS] = ["sensor.temperature"]
        await self.send_current()
        now = datetime.now(timezone.utc)
        await self.send_current()
        self.assertEqual(self.send.await_count, 2)
        packet_time = int(self.send.call_args.args[0].splitlines()[1].split("#")[3])
        self.assertGreaterEqual(packet_time, int(now.timestamp()))
        self.assertNotEqual(packet_time, int(self.reported.timestamp()))

    async def test_modes_are_independent_and_can_be_disabled(self):
        self.entry.data["sensor_map"]["sensor.humidity"] = "H1"
        self.states["sensor.humidity"] = SimpleNamespace(
            state="50", last_reported=self.reported
        )
        self.entry.options[CONF_OPTIMISTIC_SENSORS] = ["sensor.temperature"]
        await self.send_current()
        await self.send_current()
        packet = self.send.call_args.args[0]
        self.assertIn("#T1#", packet)
        self.assertNotIn("#H1#", packet)
        self.entry.options[CONF_OPTIMISTIC_SENSORS] = []
        with self.assertLogs(integration.__name__, level="ERROR"):
            await self.send_current()
        self.assertEqual(self.send.await_count, 2)

    async def test_empty_options_override_optimistic_enabled_at_setup(self):
        self.entry.data[CONF_OPTIMISTIC_SENSORS] = ["sensor.temperature"]
        await self.send_current()
        await self.send_current()
        self.entry.options[CONF_OPTIMISTIC_SENSORS] = []
        with self.assertLogs(integration.__name__, level="ERROR"):
            await self.send_current()
        self.assertEqual(self.send.await_count, 2)

    async def test_invalid_readings_are_skipped_in_both_modes(self):
        for enabled in ([], ["sensor.temperature"]):
            self.entry.options[CONF_OPTIMISTIC_SENSORS] = enabled
            for value in ("unknown", "unavailable", "nan", "inf", "-inf", "1e309", "1e308", None):
                with self.subTest(enabled=enabled, value=value):
                    self.state.state = value
                    with self.assertLogs(integration.__name__):
                        await self.send_current()
            self.states.clear()
            with self.assertLogs(integration.__name__):
                await self.send_current()
            self.states["sensor.temperature"] = self.state
        self.send.assert_not_awaited()

    async def test_failed_send_is_retried_with_same_report(self):
        self.send.side_effect = [TimeoutError(), "OK"]
        with self.assertLogs(integration.__name__, level="ERROR"):
            await self.send_current()
        self.assertNotIn("last_sent_timestamps", self.runtime)
        await self.send_current()
        self.assertEqual(self.send.await_count, 2)
        self.assertIn("T1", self.runtime["last_sent_timestamps"])


class OptimisticFlowTests(unittest.IsolatedAsyncioTestCase):
    def hass(self):
        return SimpleNamespace(
            states=SimpleNamespace(get=Mock(return_value=None)),
            config_entries=SimpleNamespace(async_update_entry=Mock()),
        )

    async def test_setup_lists_only_mapped_sensors_and_saves_selection(self):
        flow = NarodmonConfigFlow()
        flow.hass = self.hass()
        flow._config = {"mac": "TEST"}
        flow._selected_entities = ["sensor.temperature", "sensor.humidity", "sensor.empty"]
        form = await flow.async_step_assign_ids({
            "sensor.temperature": "T1", "sensor.humidity": "H1", "sensor.empty": ""
        })
        self.assertEqual(form["step_id"], "optimistic")
        schema = form["data_schema"]
        self.assertEqual(schema({}), {CONF_OPTIMISTIC_SENSORS: []})
        with self.assertRaises(vol.Invalid):
            schema({CONF_OPTIMISTIC_SENSORS: ["sensor.empty"]})
        result = await flow.async_step_optimistic(schema({
            CONF_OPTIMISTIC_SENSORS: ["sensor.humidity"]
        }))
        self.assertEqual(result["data"][CONF_OPTIMISTIC_SENSORS], ["sensor.humidity"])
        self.assertEqual(result["data"]["sensor_map"], {
            "sensor.temperature": "T1", "sensor.humidity": "H1"
        })

    async def test_options_preserve_selection_and_allow_disabling_all(self):
        entry = SimpleNamespace(
            data={"mac": "TEST", "sensor_map": {"sensor.temperature": "T1"},
                  CONF_OPTIMISTIC_SENSORS: ["sensor.temperature"]},
            options={"other_option": "preserved"},
        )
        flow = NarodmonOptionsFlow(entry)
        flow.hass = self.hass()
        await flow.async_step_init({"mac": "TEST", "name": "Test", "timeout": 7})
        await flow.async_step_select_sensors({"sensors": ["sensor.temperature"]})
        form = await flow.async_step_assign_ids({"sensor.temperature": "T1"})
        flow.hass.config_entries.async_update_entry.assert_not_called()
        self.assertEqual(form["data_schema"]({}), {
            CONF_OPTIMISTIC_SENSORS: ["sensor.temperature"]
        })
        result = await flow.async_step_optimistic({CONF_OPTIMISTIC_SENSORS: []})
        self.assertEqual(result["data"][CONF_OPTIMISTIC_SENSORS], [])
        self.assertEqual(result["data"]["other_option"], "preserved")
        # Reopening must respect the empty options list over setup data.
        entry.options = result["data"]
        reopened = NarodmonOptionsFlow(entry)
        reopened.hass = flow.hass
        form = await reopened.async_step_optimistic()
        self.assertEqual(form["data_schema"]({}), {CONF_OPTIMISTIC_SENSORS: []})

    async def test_removed_sensor_is_not_in_optimistic_defaults(self):
        entry = SimpleNamespace(
            data={"sensor_map": {"sensor.temperature": "T1", "sensor.humidity": "H1"}},
            options={CONF_OPTIMISTIC_SENSORS: ["sensor.temperature", "sensor.humidity"]},
        )
        flow = NarodmonOptionsFlow(entry)
        flow.hass = self.hass()
        await flow.async_step_select_sensors({"sensors": ["sensor.humidity"]})
        form = await flow.async_step_assign_ids({"sensor.humidity": "H1"})
        self.assertEqual(form["data_schema"]({}), {
            CONF_OPTIMISTIC_SENSORS: ["sensor.humidity"]
        })
