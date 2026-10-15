import unittest
from unittest.mock import patch

from scripts import native_host


class NativeHostTests(unittest.TestCase):
    def test_existing_backend_is_not_started_again(self):
        # A peer this host started is still coming up: pid file present, so
        # start_backend waits for it rather than racing it for the port.
        #
        # read_pid returning None used to be enough to reach the wait, which is
        # what let an orphaned llama-server hold the button down for 90 seconds.
        # That case now returns immediately instead, so the pid file is part of
        # the contract and this test has to supply one.
        existing = {"ok": True, "ready": True, "owned": False}
        with (
            patch.object(native_host, "read_pid", return_value=777),
            patch.object(native_host, "backend_ready", return_value=False),
            patch.object(native_host, "wait_for_existing_backend", return_value=existing) as wait,
            patch.object(native_host.subprocess, "Popen") as popen,
        ):
            result = native_host.start_backend()

        self.assertEqual(result, existing)
        wait.assert_called_once_with(777)
        popen.assert_not_called()

    def test_wait_for_existing_backend_returns_none_when_no_port_is_open(self):
        with patch.object(native_host, "listening", return_value=False):
            self.assertIsNone(native_host.wait_for_existing_backend(777))
            self.assertIsNone(native_host.wait_for_existing_backend(None))

    def test_audio_only_mode_is_passed_to_child_environment(self):
        process = type("Process", (), {"pid": 1234, "poll": lambda _self: None})()
        with (
            patch.object(native_host, "read_pid", return_value=None),
            patch.object(native_host, "backend_ready", side_effect=[False, True]),
            patch.object(native_host, "wait_for_existing_backend", return_value=None),
            patch.object(native_host.subprocess, "Popen", return_value=process) as popen,
            patch.object(native_host, "write_pid"),
        ):
            result = native_host.start_backend(audio_only=True)

        self.assertTrue(result["ready"])
        self.assertEqual(popen.call_args.kwargs["env"]["TRANSLATION_AUDIO_ONLY"], "1")

    def test_native_host_main_stops_owned_backend_when_chrome_disconnects(self):
        with (
            patch.object(native_host, "receive", return_value=None),
            patch.object(native_host, "stop_backend") as stop_backend,
        ):
            native_host.main()

        stop_backend.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
