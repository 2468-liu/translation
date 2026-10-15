"""Deep tests for Windows Core Audio WASAPI (WAC) Hot-Plug and Endpoint Failover.

Tests the resilience of loopback audio capture when headphones/audio devices are unplugged
or the default audio render endpoint changes:
1. Native DLL device migration callback triggering on state_code=3.
2. Python fallback run_loopback resilience against device invalidation errors (AUDCLNT_E_DEVICE_INVALIDATED).
3. Loopback manager start with on_device_change_callback parameter and clean stop.
4. Hot-plug failover state transition contract verification.
"""

import asyncio
import ctypes
import os
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from server.process_loopback import ProcessLoopbackManager
from server.loopback import run_loopback


class WasapiHotplugFailoverTests(unittest.TestCase):
    def setUp(self):
        self.mgr = ProcessLoopbackManager()

    def tearDown(self):
        if self.mgr.is_available:
            self.mgr.stop()

    def test_native_state_callback_code_3_triggers_device_change_listener(self):
        """Verifies that native state code 3 (DeviceMigrated) invokes python listener."""
        device_changes = []

        def on_device_changed(dev_name: str):
            device_changes.append(dev_name)

        self.mgr._on_device_change_callback = on_device_changed

        # Simulate C++ calling _native_state_callback(3, "Speakers (Realtek Audio)")
        msg_bytes = "Speakers (Realtek Audio)".encode("utf-8")
        self.mgr._native_state_callback(3, msg_bytes)

        self.assertEqual(len(device_changes), 1)
        self.assertEqual(device_changes[0], "Speakers (Realtek Audio)")

    def test_native_state_callback_code_3_handles_null_message(self):
        """Verifies that null or empty message does not crash device change handler."""
        device_changes = []

        def on_device_changed(dev_name: str):
            device_changes.append(dev_name)

        self.mgr._on_device_change_callback = on_device_changed
        self.mgr._native_state_callback(3, None)

        self.assertEqual(len(device_changes), 1)
        self.assertEqual(device_changes[0], "")

    def test_start_loopback_with_device_change_callback(self):
        """Verifies ProcessLoopbackManager.start accepts and stores on_device_change_callback."""
        received = []
        device_changes = []

        def on_pcm(data: bytes):
            received.append(len(data))

        def on_dev(name: str):
            device_changes.append(name)

        if not self.mgr.is_available:
            self.skipTest("Native DLL not available on this platform")

        success = self.mgr.start(
            target_pid=0,
            pcm_callback=on_pcm,
            on_device_change_callback=on_dev,
        )
        self.assertTrue(success)
        self.assertTrue(self.mgr.is_running())
        self.assertEqual(self.mgr._on_device_change_callback, on_dev)

        time.sleep(0.1)
        self.mgr.stop()
        self.assertFalse(self.mgr.is_running())

    def test_python_fallback_run_loopback_recovers_on_device_invalidation(self):
        """Verifies that Python fallback loopback recovers and retries when device is unplugged."""
        async def run_test():
            stop_event = asyncio.Event()
            sent_messages = []

            async def mock_send(msg):
                sent_messages.append(msg)

            mock_session = MagicMock()
            mock_session.push_pcm.return_value = []

            call_count = 0

            class MockRecorder:
                def __init__(self):
                    self.record_calls = 0

                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc_val, exc_tb):
                    pass

                def record(self, numframes):
                    self.record_calls += 1
                    if self.record_calls == 1:
                        # First call succeeds
                        return [0.0] * (numframes * 2)
                    elif self.record_calls == 2:
                        # Second call simulates headphone unplugged / AUDCLNT_E_DEVICE_INVALIDATED
                        raise RuntimeError("AUDCLNT_E_DEVICE_INVALIDATED: Headphone unplugged")
                    else:
                        # Reconnected on new device
                        stop_event.set()
                        return [0.0] * (numframes * 2)

            mock_mic = MagicMock()
            mock_mic.name = "Mock Speaker Loopback"
            mock_recorder = MockRecorder()
            mock_mic.recorder.return_value = mock_recorder

            with patch("server.loopback._open_loopback", return_value=(mock_mic, "Mock Speaker")):
                loopback_task = asyncio.create_task(run_loopback(mock_session, mock_send, stop_event))

                # Allow loop to process recovery
                await asyncio.wait_for(stop_event.wait(), timeout=2.0)
                await loopback_task

            # Verify that disconnect warning was emitted
            warn_msgs = [m for m in sent_messages if m.get("level") == "warn"]
            self.assertTrue(len(warn_msgs) >= 1)
            self.assertIn("断开或变更", warn_msgs[0].get("message", ""))

        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()
