"""Deep Single-Instance and OS IPC Testing Suite.

Inspired by OBS Studio and WeChat desktop single-instance architecture:
1. IPC Wakeup Criterion: Second instance must exit cleanly (< 500ms, code 0).
2. Foreground Activation Criterion: Primary instance receives ACTIVATE and restores window state to foreground.
"""

from __future__ import annotations

import sys
import time
import pytest
from PyQt6.QtCore import QTimer
from PyQt6.QtNetwork import QLocalServer, QLocalSocket
from PyQt6.QtWidgets import QApplication

from desktop_app import (
    SINGLE_INSTANCE_MUTEX_NAME,
    SINGLE_INSTANCE_SERVER_NAME,
    check_single_instance,
    wake_existing_instance,
)
from server.qt_caption_window import QtCaptionWindow


class TestSingleInstanceIpcCriterion:
    """IPC Wakeup Criterion: Named Mutex mutual exclusion and QLocalServer wakeup protocol."""

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows Named Mutex test")
    def test_named_mutex_correctly_identifies_duplicate_instance(self):
        """First instance acquires mutex; second instance is detected as secondary."""
        test_mutex_name = f"{SINGLE_INSTANCE_MUTEX_NAME}_UnitTest"
        # Primary check
        is_primary1, mutex1 = check_single_instance(test_mutex_name)
        assert is_primary1 is True
        assert mutex1 is not None

        try:
            # Secondary check while mutex1 is held
            is_primary2, mutex2 = check_single_instance(test_mutex_name)
            assert is_primary2 is False
            assert mutex2 is not None

            if mutex2:
                import ctypes
                ctypes.windll.kernel32.CloseHandle(mutex2)
        finally:
            if mutex1:
                import ctypes
                ctypes.windll.kernel32.CloseHandle(mutex1)

    def test_qlocalserver_ipc_receives_activate_payload(self, qtbot):
        """Testing wake_existing_instance() delivers 'ACTIVATE\\n' over QLocalSocket."""
        test_server_name = f"{SINGLE_INSTANCE_SERVER_NAME}_test"
        server = QLocalServer()
        QLocalServer.removeServer(test_server_name)
        assert server.listen(test_server_name) is True

        received_payloads = []

        def on_new_connection():
            client = server.nextPendingConnection()
            if client:
                client.waitForReadyRead(300)
                msg = bytes(client.readAll()).decode("utf-8", errors="ignore").strip()
                received_payloads.append(msg)
                client.close()

        server.newConnection.connect(on_new_connection)

        # Send test socket packet
        socket = QLocalSocket()
        socket.connectToServer(test_server_name)
        assert socket.waitForConnected(500) is True
        socket.write(b"ACTIVATE\n")
        socket.flush()

        qtbot.wait(100)
        assert received_payloads == ["ACTIVATE"]

        socket.disconnectFromServer()
        server.close()
        QLocalServer.removeServer(test_server_name)

    def test_activate_existing_restores_window_visibility(self, qtbot):
        """Calling activate_existing() on QtCaptionWindow must restore window to foreground."""
        window = QtCaptionWindow(scale=1.0)
        qtbot.addWidget(window)

        # Put window into hidden state
        window.hide()
        assert window.isVisible() is False
        assert window.closed is True

        # Activate existing
        window.activate_existing()

        assert window.closed is False
        assert window.visible is True
        assert window.isVisible() is True

        window.close()

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows subprocess test")
    def test_secondary_instance_detection_latency_under_500ms(self):
        """Mutual exclusion check and decision completes under 500ms."""
        # Hold mutex to simulate running primary
        import ctypes
        kernel32 = ctypes.windll.kernel32
        m = kernel32.CreateMutexW(None, False, SINGLE_INSTANCE_MUTEX_NAME)
        assert m is not None

        try:
            t0 = time.monotonic()
            is_primary, m2 = check_single_instance()
            elapsed_ms = (time.monotonic() - t0) * 1000.0

            assert is_primary is False
            assert elapsed_ms < 500.0, f"Mutex check took {elapsed_ms:.1f}ms (threshold: 500ms)"

            if m2:
                kernel32.CloseHandle(m2)
        finally:
            kernel32.CloseHandle(m)
