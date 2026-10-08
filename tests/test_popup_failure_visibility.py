"""A failure in the popup has to survive long enough to be read.

The click handler put the error into the status line and then called refresh()
unconditionally in its finally block, which reset that line to 未开始. Every
failure was therefore erased milliseconds after it appeared -- indistinguishable
from a dead button. That is why two rounds of debugging the start path produced
no error text to work from, and why "no reaction" was all there was to go on.

These pin the fix and the reachability probe that tells "the native host cannot
be reached" apart from "the backend would not start".
"""

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class PopupFailureVisibilityTests(unittest.TestCase):
    def setUp(self):
        self.popup = (ROOT / "extension" / "popup.js").read_text(encoding="utf-8")
        self.background = (ROOT / "extension" / "background.js").read_text(encoding="utf-8")
        self.html = (ROOT / "extension" / "popup.html").read_text(encoding="utf-8")

    def test_refresh_keeps_the_failure_instead_of_resetting_it(self):
        self.assertIn("async function refresh(keepMessage)", self.popup)
        self.assertIn("status.textContent = keepMessage || (running", self.popup)

    def test_click_handler_forwards_the_failure_to_refresh(self):
        self.assertIn("await refresh(failure)", self.popup)
        self.assertIn("failure = `${step}失败", self.popup)

    def test_stop_reply_is_checked_instead_of_only_awaited(self):
        # The stop branch threw nothing: a reply of {ok:false} was discarded and
        # the popup went on to say it had stopped something.
        self.assertIn('const reply = await chrome.runtime.sendMessage({ type: "stop" })', self.popup)
        self.assertIn('if (!reply || !reply.ok)', self.popup)

    def test_failure_names_the_step_that_failed(self):
        # 启动后端 and 启动字幕窗 are different faults with different fixes, and one
        # generic message hides which one happened.
        self.assertIn('step = "启动后端"', self.popup)
        self.assertIn('step = "启动字幕窗"', self.popup)
        self.assertIn('step = "终止"', self.popup)

    def test_native_host_reachability_is_probed_before_the_slow_start(self):
        # status is answered without starting anything, so an unreachable host is
        # reported in seconds instead of after the 120s ensure timeout.
        self.assertIn('message.type === "ping-backend"', self.background)
        self.assertIn('nativeRequest("status", 8000)', self.background)
        self.assertIn('type: "ping-backend"', self.popup)
        self.assertIn("ready.ok === false", self.popup)

    def test_failure_is_visually_distinct(self):
        self.assertIn("#status.error", self.html)
        self.assertIn('status.classList.add("error")', self.popup)


class PackagingTests(unittest.TestCase):
    """Two silent failure modes that no unit test could reach, both fixed.

    The host runs frozen, so the code under test is whatever was frozen into the
    exe. Two things about the packaging itself decided whether the extension could
    start anything at all, and neither showed up in the source.
    """

    @staticmethod
    def code_only(text):
        """Drop comment lines before a negative assertion.

        A "must not appear" check over a file that documents the mistake it guards
        against always fails: these files say "--onedir, not --onefile" and
        "Set-Content -Encoding UTF8 is UTF8 with BOM" in prose explaining why, so
        the very words being banned appear in the explanation. The negative
        assertions below run over the executable lines instead.
        """
        return "\n".join(line for line in text.splitlines()
                         if not line.strip().startswith("#"))

    def setUp(self):
        self.build = (ROOT / "scripts" / "build_native_host.ps1").read_text(encoding="utf-8")
        self.install = (ROOT / "scripts" / "install_native_host.ps1").read_text(encoding="utf-8")
        self.host = (ROOT / "scripts" / "native_host.py").read_text(encoding="utf-8")

    def test_build_is_onedir_not_onefile(self):
        # A onefile bootloader unpacks into %TEMP%\\_MEI<pid> first. When it
        # cannot create that directory it exits 1 and Chrome sees the port die
        # with nothing to connect to: no backend, no message, a dead button.
        executable = self.code_only(self.build)
        self.assertIn("--onedir", executable)
        self.assertNotIn("--onefile", executable)

    def test_build_fails_loudly_instead_of_reporting_a_stale_exe(self):
        # PyInstaller refuses a non-empty onedir output and exits non-zero, while
        # the previous build's exe is still on disk. Checking only that the file
        # exists printed "Built" while shipping the old code -- the same shape as
        # the stale-exe bug this whole section is about.
        self.assertIn("$BuildExit = $LASTEXITCODE", self.build)
        self.assertIn("if ($BuildExit -ne 0)", self.build)

    def test_build_removes_the_old_onefile_exe(self):
        self.assertIn("Removed stale onefile exe", self.build)

    def test_manifest_is_written_without_a_byte_order_mark(self):
        # Set-Content -Encoding UTF8 emits a BOM on PowerShell 5.1, which is the
        # shell this project runs in. The installed manifest carried EF BB BF and
        # strict UTF-8 JSON parsing rejects it.
        executable = self.code_only(self.install)
        self.assertIn("New-Object System.Text.UTF8Encoding($false)", executable)
        self.assertNotIn("Set-Content", executable)

    def test_install_refuses_to_register_a_manifest_with_a_bom(self):
        self.assertIn("refusing to register a host Chrome may not parse", self.install)

    def test_root_discovery_looks_for_the_marker_not_parent_count(self):
        # parents[2] was the repository root for the onefile layout and scripts/
        # for onedir, so the frozen build looked for scripts/scripts/start.ps1 and
        # answered "找不到启动脚本" with nothing started.
        self.assertIn('marker = Path("scripts") / "start.ps1"', self.host)
        # The old arithmetic survives as a fallback, so what matters is that the
        # search runs first rather than being the only way there.
        self.assertIn("for candidate in (origin, *origin.parents):", self.host)
        self.assertLess(
            self.host.index("for candidate in (origin, *origin.parents):"),
            self.host.index("return origin.parents[2]"),
            "the search must come before the fallback, not after it",
        )

    def test_installed_layout_is_where_the_install_script_looks(self):
        self.assertIn("native-host\\translation-native-host\\translation-native-host.exe",
                      self.install)


if __name__ == "__main__":
    unittest.main()