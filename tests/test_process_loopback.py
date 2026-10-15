import os
import time
import unittest

from server.process_loopback import ProcessLoopbackManager


class ProcessLoopbackTests(unittest.TestCase):
    def setUp(self):
        self.mgr = ProcessLoopbackManager()

    def tearDown(self):
        if self.mgr.is_available:
            self.mgr.stop()

    def test_manager_loads_dll(self):
        self.assertTrue(self.mgr.is_available, "audio_process_loopback.dll should be loaded")

    def test_enumerate_sessions_format(self):
        sessions = self.mgr.enumerate_sessions()
        self.assertIsInstance(sessions, list)
        for s in sessions:
            self.assertIn("pid", s)
            self.assertIn("name", s)
            self.assertIn("is_active", s)
            self.assertIsInstance(s["pid"], int)
            self.assertIsInstance(s["name"], str)
            self.assertIsInstance(s["is_active"], bool)

    def test_start_and_stop_global_loopback(self):
        received = []

        def on_pcm(data: bytes):
            received.append(len(data))

        success = self.mgr.start(target_pid=0, pcm_callback=on_pcm)
        self.assertTrue(success)
        self.assertTrue(self.mgr.is_running())
        self.assertEqual(self.mgr.get_current_pid(), 0)

        time.sleep(0.1)
        self.mgr.stop()
        self.assertFalse(self.mgr.is_running())
        self.assertEqual(self.mgr.get_current_pid(), 0)

    def test_process_loopback_self_pid(self):
        received = []

        def on_pcm(data: bytes):
            received.append(len(data))

        my_pid = os.getpid()
        success = self.mgr.start(target_pid=my_pid, pcm_callback=on_pcm)
        self.assertTrue(success)
        self.assertTrue(self.mgr.is_running())
        self.assertEqual(self.mgr.get_current_pid(), my_pid)

        time.sleep(0.1)
        self.mgr.stop()
        self.assertFalse(self.mgr.is_running())


if __name__ == "__main__":
    unittest.main()
