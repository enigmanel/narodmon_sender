"""Regression tests; run with python -m unittest discover -s tests -v.

Requires the Home Assistant Python dependencies.
"""

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from custom_components import narodmon_sender as integration


async def stall(*args):
    """Simulate an operation that never finishes by itself."""
    await asyncio.Future()


class UnloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_success_waits_for_task_before_removing_entry_data(self):
        entry = SimpleNamespace(entry_id="test")
        data = {}
        stopped = asyncio.Event()

        async def sender():
            try:
                await stall()
            finally:
                # Cleanup must still be able to access runtime data.
                data[entry.entry_id]["stopped"] = True
                stopped.set()

        task = asyncio.create_task(sender())
        data[entry.entry_id] = {"task": task}
        data["other"] = {"untouched": True}
        await asyncio.sleep(0)
        unload = AsyncMock(return_value=True)
        hass = SimpleNamespace(
            data={integration.DOMAIN: data},
            config_entries=SimpleNamespace(async_unload_platforms=unload),
        )

        self.assertTrue(await integration.async_unload_entry(hass, entry))
        unload.assert_awaited_once_with(entry, ["sensor"])
        self.assertTrue(stopped.is_set())
        self.assertTrue(task.done())
        self.assertNotIn(entry.entry_id, data)
        self.assertEqual(data["other"], {"untouched": True})

    async def test_failed_unload_preserves_data_and_running_task(self):
        task = asyncio.create_task(stall())
        entry = SimpleNamespace(entry_id="test")
        entry_data = {"task": task, "sensors_loaded": True}
        hass = SimpleNamespace(
            data={integration.DOMAIN: {entry.entry_id: entry_data}},
            config_entries=SimpleNamespace(
                async_unload_platforms=AsyncMock(return_value=False)
            ),
        )
        try:
            self.assertFalse(await integration.async_unload_entry(hass, entry))
            self.assertIs(hass.data[integration.DOMAIN][entry.entry_id], entry_data)
            self.assertEqual(task.cancelling(), 0)
        finally:
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task


class TCPTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.reader = SimpleNamespace(read=AsyncMock(return_value=b"OK\n"))
        self.writer = SimpleNamespace(
            write=Mock(), close=Mock(), drain=AsyncMock(), wait_closed=AsyncMock()
        )
        self.connect = AsyncMock(return_value=(self.reader, self.writer))
        for patcher in (
            patch.object(integration.asyncio, "open_connection", self.connect),
            patch.object(integration, "TCP_TIMEOUT", 0.02),
            patch.object(integration, "TCP_CLOSE_TIMEOUT", 0.02),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def assert_closed(self):
        self.writer.close.assert_called_once_with()
        self.writer.wait_closed.assert_awaited_once_with()

    async def test_success(self):
        self.assertEqual(await integration._send_via_tcp("packet"), "OK")
        self.connect.assert_awaited_once_with(
            integration.NARODMON_HOST, integration.NARODMON_PORT
        )
        self.writer.write.assert_called_once_with(b"packet")
        self.assert_closed()

    async def test_connect_timeout(self):
        self.connect.side_effect = stall
        with self.assertRaises(TimeoutError):
            await integration._send_via_tcp("packet")
        self.writer.close.assert_not_called()

    async def test_read_timeout(self):
        self.reader.read.side_effect = stall
        with self.assertRaises(TimeoutError):
            await integration._send_via_tcp("packet")
        self.assert_closed()

    async def test_drain_timeout(self):
        self.writer.drain.side_effect = stall
        with self.assertRaises(TimeoutError):
            await integration._send_via_tcp("packet")
        self.assert_closed()

    async def test_read_error(self):
        self.reader.read.side_effect = ConnectionResetError("reset")
        with self.assertRaises(ConnectionResetError):
            await integration._send_via_tcp("packet")
        self.assert_closed()

    async def test_cancellation_closes_connection(self):
        reading = asyncio.Event()

        async def read(*args):
            reading.set()
            await stall()

        self.reader.read.side_effect = read
        task = asyncio.create_task(integration._send_via_tcp("packet"))
        await reading.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assert_closed()

    async def test_stalled_close_does_not_hide_read_timeout(self):
        self.reader.read.side_effect = stall
        self.writer.wait_closed.side_effect = stall
        async with asyncio.timeout(1):
            with self.assertRaises(TimeoutError):
                await integration._send_via_tcp("packet")
        self.assert_closed()

    async def test_close_error_does_not_hide_read_timeout(self):
        self.reader.read.side_effect = stall
        self.writer.wait_closed.side_effect = ConnectionResetError("reset")
        with self.assertRaises(TimeoutError):
            await integration._send_via_tcp("packet")
        self.assert_closed()

    async def test_send_records_timeout_without_marking_readings_as_sent(self):
        entry = SimpleNamespace(
            data={"mac": "test", "sensor_map": {"sensor.temperature": "T1"}},
            options={},
        )
        state = SimpleNamespace(state="20", last_reported=datetime.now(timezone.utc))
        entry_data = {"last_sent_timestamps": {"T1": 0}}
        hass = SimpleNamespace(
            config_entries=SimpleNamespace(async_get_entry=Mock(return_value=entry)),
            states=SimpleNamespace(get=Mock(return_value=state)),
            data={integration.DOMAIN: {"test": entry_data}},
        )
        self.reader.read.side_effect = stall

        with self.assertLogs(integration.__name__, level="ERROR"):
            await integration._send_single_config(hass, "test")

        self.assertIn("Тайм-аут", entry_data["last_send"]["error"])
        self.assertEqual(entry_data["last_sent_timestamps"], {"T1": 0})
        self.assert_closed()
