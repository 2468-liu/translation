"""Does the start/stop cycle actually reconnect? These are the proofs.

The user pressed stop, then start, and nothing came up. The defects below were
found by reading; this file is what makes them facts rather than suspicions, and
each test names the failure it prevents coming back.

Every test drives the real scripts/native_host.py functions with only the process
and socket edges mocked, because the defects live in the seams between them. The
clocks are mocked too -- unmocked they turn a four-test file into a 94-second one.
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import native_host


class FakeClock:
    """monotonic that advances by `step` per call and never really sleeps.

    Callable so it can stand in for time.monotonic directly, and it carries its
    own sleep so a caller only has to patch the one object.
    """

    def __init__(self, step=0.4):
        self.now = 0.0
        self.step = step

    def __call__(self):
        value = self.now
        self.now += self.step
        return value

    monotonic = __call__

    def sleep(self, _seconds):
        return None


def pid_paths(tmp):
    """Point the host at one pid file inside tmp.

    The host now keeps a list of candidates rather than a single PID_FILE,
    because that one path is the only handle on the backend process tree. These
    tests care about the bookkeeping, not about which directory wins, so they
    narrow it to one.
    """
    path = Path(tmp) / "server.pid"
    patcher = patch.object(native_host, "_pid_candidates", return_value=[path])
    patcher.start()
    return patcher, path


def fake_run(returncode=0, stdout="", stderr=""):
    return type("R", (), {"stdout": stdout, "returncode": returncode,
                          "stderr": stderr})()


class ProcessExistsTests(unittest.TestCase):
    """read_pid() decides whether anything is killable. If it says no, stop is a no-op."""

    def test_tasklist_saying_no_tasks_is_not_a_hit(self):
        out = "\n信息: 没有运行的任务匹配指定标准。\n"
        with patch.object(native_host.subprocess, "run", return_value=fake_run(stdout=out)):
            self.assertFalse(native_host.process_exists(1234))

    def test_a_real_powershell_line_is_a_hit(self):
        out = "powershell.exe          1234 Console                    1     9,000 K\n"
        with patch.object(native_host.subprocess, "run", return_value=fake_run(stdout=out)):
            self.assertTrue(native_host.process_exists(1234))


class StopIsRetryableTests(unittest.TestCase):
    """A stop that fails must leave the backend killable.

    Otherwise one bad stop is a one-way door: the pid file is gone, so every
    later stop finds nothing to kill and returns 'no backend was started by the
    extension' while the backend is still running.
    """

    def test_failed_stop_keeps_the_pid_file_so_a_retry_can_kill(self):
        with tempfile.TemporaryDirectory() as tmp:
            patcher, pid_file = pid_paths(tmp)
            pid_file.write_text("1234", encoding="ascii")

            with (
                patch.object(native_host, "process_exists", return_value=True),
                patch.object(native_host, "listening", return_value=True),
                patch.object(native_host.time, "monotonic", FakeClock()),
                patch.object(native_host.subprocess, "run",
                             return_value=fake_run(returncode=128, stderr="not found")),
            ):
                result = native_host.stop_backend()

            self.assertFalse(result["ok"], "the stop failed, so it must be retryable")
            self.assertTrue(
                pid_file.exists(),
                "the pid file was removed before the ports were free, so the next "
                "stop finds no pid and the backend can never be killed again",
            )

    def test_taskkill_failure_is_reported_not_swallowed(self):
        """check=False plus a discarded returncode turns 'access denied' into
        'the backend did not exit in time' -- different problems, different fixes."""
        with tempfile.TemporaryDirectory() as tmp:
            patcher, pid_file = pid_paths(tmp)
            pid_file.write_text("1234", encoding="ascii")

            with (
                patch.object(native_host, "process_exists", return_value=True),
                patch.object(native_host, "listening", return_value=True),
                patch.object(native_host.time, "monotonic", FakeClock()),
                patch.object(native_host.subprocess, "run",
                             return_value=fake_run(returncode=128,
                                                   stderr="错误: 没有找到指定的进程。")),
            ):
                result = native_host.stop_backend()

            self.assertFalse(result["ok"])
            self.assertIn("taskkill",
                          json.dumps(result, ensure_ascii=False).lower(),
                          "the taskkill exit status was dropped, so the message "
                          "describes a symptom instead of the cause")


class RestartAfterAFailedStopTests(unittest.TestCase):
    """The half-open state: llama-server alive on 8080, the python server gone.

    wait_for_existing_backend treats any open port as another instance starting,
    so it waits out the clock and then refuses to start anything. From the popup
    that is a button that does nothing for a minute and a half and then fails.
    """

    def test_orphan_port_does_not_block_a_restart(self):
        # The real wait_for_existing_backend is exercised here, not a mock: the
        # defect was inside it, and mocking it away is what let it ship. An open
        # port with no pid file means an orphan, so the wait must return at once
        # and let the caller start the missing half.
        with (
            patch.object(native_host, "listening", return_value=True),
            patch.object(native_host.time, "monotonic", FakeClock()),
            patch.object(native_host.time, "sleep", lambda _s: None),
        ):
            self.assertIsNone(native_host.wait_for_existing_backend(None))

        # With a pid file it is a peer still coming up, so waiting is correct.
        with (
            patch.object(native_host, "backend_ready", side_effect=[False, True]),
            patch.object(native_host, "listening", return_value=True),
            patch.object(native_host.time, "monotonic", FakeClock()),
            patch.object(native_host.time, "sleep", lambda _s: None),
        ):
            reply = native_host.wait_for_existing_backend(4321)
        self.assertEqual(reply, {"ok": True, "ready": True, "owned": False})

    def test_start_spawns_alongside_an_orphaned_llama_server(self):
        with (
            patch.object(native_host, "read_pid", return_value=None),
            # first the pre-spawn check (8765 is dead), then ready once the
            # python server comes up next to the orphaned llama-server.
            patch.object(native_host, "backend_ready", side_effect=[False, True]),
            patch.object(native_host, "listening", return_value=True),
            patch.object(native_host.time, "monotonic", FakeClock()),
            patch.object(native_host.time, "sleep", lambda _s: None),
            patch.object(native_host.subprocess, "Popen") as popen,
            patch.object(native_host, "write_pid"),
        ):
            popen.return_value = type("P", (), {"pid": 4321, "poll": lambda _s: None})()
            result = native_host.start_backend()

        popen.assert_called_once()
        self.assertTrue(
            result.get("ok"),
            f"an orphaned llama-server blocked the restart: {result}",
        )


class PidBookkeepingMustNotKillTheStartTests(unittest.TestCase):
    """write_pid runs after the backend is already spawning.

    It used to raise, and handle() turned that into ok:false. The observed result
    was the worst of both: the popup reported failure, the button stayed on 开始,
    and a working backend was already coming up behind it. Found because the exe's
    own ensure left a live llama-server and python server on the card while
    answering "本地后端启动失败" -- the run looked like nothing had started.
    """

    def test_unwritable_pid_file_still_reports_a_ready_backend(self):
        with (
            patch.object(native_host, "read_pid", return_value=None),
            patch.object(native_host, "backend_ready", side_effect=[False, True]),
            patch.object(native_host, "wait_for_existing_backend", return_value=None),
            patch.object(native_host.time, "monotonic", FakeClock()),
            patch.object(native_host.subprocess, "Popen") as popen,
            patch.object(native_host, "write_pid",
                         return_value="PermissionError: server.pid") as write,
        ):
            popen.return_value = type("P", (), {"pid": 777, "poll": lambda _s: None})()
            result = native_host.start_backend()

        write.assert_called_once_with(777)
        self.assertTrue(result["ok"], f"a pid-file failure failed the start: {result}")
        self.assertEqual(result["pid_file_error"], "PermissionError: server.pid")

    def test_write_pid_returns_the_error_instead_of_raising(self):
        class UnwritableParent:
            @staticmethod
            def mkdir(*_a, **_k):
                return None

        def deny(*_a, **_k):
            raise PermissionError(13, "denied", "server.pid")

        fake = type("F", (), {"parent": UnwritableParent, "write_text": deny})()
        with patch.object(native_host, "_pid_candidates", return_value=[fake]):
            note = native_host.write_pid(1234)

        self.assertIsNotNone(note)
        self.assertIn("PermissionError", note)

    def test_a_second_candidate_saves_the_pid_when_the_first_is_unwritable(self):
        """The whole point of the list: one bad directory must not cost the
        extension its only handle on the backend."""
        with tempfile.TemporaryDirectory() as tmp:
            good = Path(tmp) / "fallback.pid"

            class UnwritableParent:
                @staticmethod
                def mkdir(*_a, **_k):
                    return None

            def deny(*_a, **_k):
                raise PermissionError(13, "denied", "first")

            bad = type("F", (), {"parent": UnwritableParent, "write_text": deny})()
            with patch.object(native_host, "_pid_candidates", return_value=[bad, good]):
                note = native_host.write_pid(4321)

            self.assertIsNone(note)
            self.assertEqual(good.read_text(encoding="ascii"), "4321")


class CycleTests(unittest.TestCase):
    """Two full cycles, which is what the button actually does."""

    def test_stop_then_start_reaches_a_spawn_again(self):
        with tempfile.TemporaryDirectory() as tmp:
            patcher, pid_file = pid_paths(tmp)
            pid_file.write_text("1111", encoding="ascii")

            pid_file.write_text("1111", encoding="ascii")
            with (
                patch.object(native_host, "process_exists", return_value=True),
                patch.object(native_host, "listening", return_value=False),
                patch.object(native_host.subprocess, "run", return_value=fake_run()),
            ):
                first = native_host.stop_backend()

            self.assertTrue(first["ok"])
            self.assertFalse(pid_file.exists())

            with (
                patch.object(native_host, "read_pid", return_value=None),
                patch.object(native_host, "backend_ready", side_effect=[False, True]),
                patch.object(native_host, "wait_for_existing_backend", return_value=None),
                patch.object(native_host.time, "monotonic", FakeClock()),
                patch.object(native_host.subprocess, "Popen") as popen,
                patch.object(native_host, "write_pid") as write_pid,
            ):
                popen.return_value = type("P", (), {"pid": 2222, "poll": lambda _s: None})()
                second = native_host.start_backend()

            popen.assert_called_once()
            write_pid.assert_called_once_with(2222)
            self.assertTrue(second.get("ready"), f"second cycle never became ready: {second}")

    def test_shutdown_on_disconnect_does_not_leave_a_stale_pid_file(self):
        """main()'s finally kills the backend when Chrome's port closes. The MV3
        service worker suspends after ~30s idle, so this fires mid-session."""
        with tempfile.TemporaryDirectory() as tmp:
            patcher, pid_file = pid_paths(tmp)
            pid_file.write_text("3333", encoding="ascii")
            with (
                patch.object(native_host, "process_exists", return_value=False),
                patch.object(native_host, "receive", return_value=None),
            ):
                native_host.main()

            self.assertFalse(pid_file.exists(),
                             "a stale pid file survives and read_pid keeps "
                             "reporting a pid that is gone")

    def test_repeated_stops_are_all_safe(self):
        """Stop twice in a row must not raise, and must not invent a backend."""
        with tempfile.TemporaryDirectory() as tmp:
            patcher, pid_file = pid_paths(tmp)
            pid_file.write_text("4444", encoding="ascii")
            with (
                patch.object(native_host, "process_exists", return_value=True),
                patch.object(native_host, "listening", return_value=False),
                patch.object(native_host.subprocess, "run", return_value=fake_run()) as run,
            ):
                first = native_host.stop_backend()
            second = native_host.stop_backend()

            self.assertTrue(first["ok"])
            self.assertTrue(second["ok"], second)
            run.assert_called_once()


if __name__ == "__main__":
    unittest.main()