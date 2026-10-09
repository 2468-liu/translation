"""The extension must not spend its 120s waiting on a dead native port.

Chrome suspends and restarts the service worker. The restarted one still holds the
`nativePort` object from before, whose native side is gone. `postMessage` on such a
port is dropped rather than refused: nothing comes back, nothing throws, and the only
thing that fires is the request's own timeout. The popup then reports
"本地后端操作超时" for a backend that starts in seconds.

Confirmed by running the host by hand with the backend down and no pid file:
`ensure` answered `{"ok": true, "ready": true}` in 7.7 seconds. So the timeout was
never about the backend taking too long.

These assertions strip comments first. This file explains at length why the probe
exists, and an assertion satisfiable by that explanation is not an assertion -- the
same trap as the negative text assertions in test_caption_layout.py, where three
assertions all failed because the forbidden words were in the comments saying why
they were forbidden.

The assertions are on structure, not vocabulary: each command awaits the probe before
its own request, and the probe drops the port when the probe fails.
"""

import re
import unittest
from pathlib import Path

SOURCE = (Path(__file__).resolve().parents[1] / "extension" / "background.js").read_text(
    encoding="utf-8"
)


def executable() -> str:
    """The file with comments and string bodies blanked where they would confuse a match."""
    out = []
    in_block = False
    for raw in SOURCE.splitlines():
        line = raw
        if in_block:
            if "*/" in line:
                line = line.split("*/", 1)[1]
                in_block = False
            else:
                continue
        while "/*" in line:
            head, _, rest = line.partition("/*")
            if "*/" in rest:
                line = head + rest.split("*/", 1)[1]
            else:
                line = head
                in_block = True
                break
        # Trailing line comment, but not one inside a string such as a URL.
        hash_at = line.find("//")
        if hash_at >= 0:
            before = line[:hash_at]
            if before.count('"') % 2 == 0 and before.count("'") % 2 == 0:
                line = before
        out.append(line)
    return "\n".join(out)


def body_of(name: str) -> str:
    """The executable lines of a top-level function, up to the next top-level `}`."""
    code = executable()
    start = re.search(rf"(?:async )?function {name}\s*\([^)]*\)\s*\{{", code)
    assert start, f"{name} not found"
    rest = code[start.end():]
    end = rest.find("\n}")
    return rest[:end if end >= 0 else len(rest)]


class NativePortProbeTests(unittest.TestCase):
    def test_the_probe_drops_a_dead_port_before_reconnecting(self):
        body = body_of("liveNativeConnection")
        # Only the failure path matters here. The early `if (!nativePort) return
        # openNativeConnection()` is the fast path and legitimately opens one without a
        # reset, so the assertion looks at what follows the catch.
        catch = body[body.index("catch"):]
        self.assertIn("nativePort = null", catch)
        self.assertLess(
            catch.index("nativePort = null"),
            catch.index("openNativeConnection()"),
            "the stale port is handed back instead of being dropped first",
        )

    def test_the_probe_asks_for_status(self):
        # status starts nothing -- it answers from the ports -- so probing with it
        # cannot have the side effect of starting a backend.
        self.assertIn('nativeRequest("status"', body_of("liveNativeConnection"))

    def test_every_command_that_waits_probes_first(self):
        for name in ("ensureBackend", "shutdownBackend", "switchChain"):
            body = body_of(name)
            self.assertIn(
                "await liveNativeConnection()",
                body,
                f"{name} would wait out its timeout on a possibly dead port",
            )
            self.assertLess(
                body.index("await liveNativeConnection()"),
                body.index("nativeRequest("),
                f"{name} sends its real request before probing",
            )

    def test_a_request_no_longer_reuses_a_cached_port_blindly(self):
        # The old shape was `if (nativePort) return nativePort;` at the top of the
        # connection helper, which handed back a dead port forever.
        self.assertNotIn("if (nativePort) return nativePort", executable())

    def test_opening_a_connection_is_a_separate_function(self):
        self.assertIn("function openNativeConnection(", executable())
        self.assertNotIn("function nativeConnection(", executable())


if __name__ == "__main__":
    unittest.main()