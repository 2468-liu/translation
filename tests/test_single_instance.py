import sys
import time
import unittest
from pathlib import Path

from PyQt6.QtWidgets import QApplication
from PyQt6.QtNetwork import QLocalServer, QLocalSocket

from desktop_app import (
    SINGLE_INSTANCE_MUTEX_NAME,
    SINGLE_INSTANCE_SERVER_NAME,
    check_single_instance,
    wake_existing_instance,
)
from server.qt_caption_window import QtCaptionWindow


class SingleInstanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def test_windows_mutex_prevents_duplicate_instance(self):
        if sys.platform != "win32":
            return

        import ctypes
        kernel32 = ctypes.windll.kernel32
        test_mutex_name = f"Local\\TestSingleInstance_{time.time_ns()}"

        h1 = kernel32.CreateMutexW(None, False, test_mutex_name)
        self.assertNotEqual(h1, 0)
        self.assertEqual(kernel32.GetLastError(), 0)

        # Second attempt must report ERROR_ALREADY_EXISTS (183)
        h2 = kernel32.CreateMutexW(None, False, test_mutex_name)
        err2 = kernel32.GetLastError()
        self.assertEqual(err2, 183)

        kernel32.CloseHandle(h1)
        kernel32.CloseHandle(h2)

    def test_caption_window_activate_existing(self):
        window = QtCaptionWindow(scale=1.0)
        window.hide()
        self.assertTrue(window.closed)
        self.assertFalse(window.visible)

        window.activate_existing()
        self.assertFalse(window.closed)
        self.assertTrue(window.visible)
        self.assertIn("程序已在运行中", window.status_label.text())
        window.close()

    def test_single_instance_ipc_communication(self):
        test_srv_name = f"TestIPC_{time.time_ns()}"
        srv = QLocalServer()
        QLocalServer.removeServer(test_srv_name)
        self.assertTrue(srv.listen(test_srv_name))

        received = []
        def on_new_connection():
            client = srv.nextPendingConnection()
            if client:
                client.waitForReadyRead(200)
                received.append(bytes(client.readAll()).decode("utf-8").strip())
                client.close()

        srv.newConnection.connect(on_new_connection)

        # Client sends ACTIVATE
        client = QLocalSocket()
        client.connectToServer(test_srv_name)
        self.assertTrue(client.waitForConnected(500))
        client.write(b"ACTIVATE\n")
        client.flush()
        for _ in range(20):
            self.app.processEvents()
            if received:
                break
            time.sleep(0.02)

        self.assertEqual(received, ["ACTIVATE"])
        srv.close()
        QLocalServer.removeServer(test_srv_name)


if __name__ == "__main__":
    unittest.main()
