import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


def _code_only(source: str) -> str:
    """The source with comments and docstrings removed.

    The prose explaining a removal names the thing that was removed, so a bare
    assertNotIn over the whole file fails on its own explanation. REPORT 37 records
    hitting this with `kind == "config"`; the position-lock test hit it again with
    锁定. Assertions about what the code does run on the code.
    """
    kept = []
    in_doc = False
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.count('"""') % 2 == 1:
            in_doc = not in_doc
            continue
        if in_doc or stripped.startswith("#"):
            continue
        kept.append(line)
    return "\n".join(kept)


class ExtensionBoundaryTests(unittest.TestCase):
    def test_extension_has_no_webpage_injection_permissions(self):
        manifest = json.loads((ROOT / "extension" / "manifest.json").read_text(encoding="utf-8"))

        self.assertNotIn("scripting", manifest["permissions"])
        self.assertNotIn("activeTab", manifest["permissions"])
        self.assertNotIn("host_permissions", manifest)
        self.assertNotIn("web_accessible_resources", manifest)

    def test_background_does_not_inject_content_script(self):
        background = (ROOT / "extension" / "background.js").read_text(encoding="utf-8")

        self.assertNotIn("executeScript", background)
        self.assertNotIn('files: ["content.js"]', background)
        self.assertNotIn("chrome.scripting", background)
        self.assertNotIn("chrome.windows.create", background)
        self.assertNotIn("videoWindowId", background)
        self.assertNotIn("windowId", background)

    def test_extension_does_not_capture_tab_audio(self):
        background = (ROOT / "extension" / "background.js").read_text(encoding="utf-8")
        offscreen = (ROOT / "extension" / "offscreen.js").read_text(encoding="utf-8")
        popup = (ROOT / "extension" / "popup.js").read_text(encoding="utf-8")
        manifest = json.loads((ROOT / "extension" / "manifest.json").read_text(encoding="utf-8"))

        self.assertNotIn("tabCapture", manifest["permissions"])
        self.assertNotIn("chrome.tabCapture", background)
        self.assertNotIn("getUserMedia", offscreen)
        self.assertNotIn("chromeMediaSource", offscreen)
        self.assertIn("audioOnly", background)
        self.assertIn("audioOnly: Boolean(audioOnly)", background)
        self.assertIn("audioOnly", popup)
        self.assertIn("audioOnly: Boolean(message.audioOnly)", offscreen)
        self.assertIn("color: saved.captionColor", background)
        self.assertIn("payload.color", offscreen)

    def test_audio_only_connection_cannot_paint_caption_window(self):
        ws_server = (ROOT / "server" / "ws_server.py").read_text(encoding="utf-8")

        self.assertIn("caption_enabled = not bool(data.get(\"audioOnly\"))", ws_server)
        self.assertIn("if not caption_enabled:", ws_server)
        self.assertIn("hide_caption()", ws_server)
        self.assertIn("if caption_enabled:", ws_server)

    def test_caption_output_has_one_native_window_owner(self):
        pipeline = (ROOT / "server" / "pipeline.py").read_text(encoding="utf-8")
        ws_server = (ROOT / "server" / "ws_server.py").read_text(encoding="utf-8")

        self.assertNotIn("from .caption_window import", pipeline)
        self.assertIn("from .caption_window import (", ws_server)
        self.assertIn("update_caption(payload)", ws_server)
        self.assertIn("TRANSLATION_AUDIO_ONLY", ws_server)

    def test_text_only_control_is_in_caption_window_header(self):
        tk_caption = (ROOT / "server" / "caption_window.py").read_text(encoding="utf-8")

        self.assertIn('text_only_control = tk.Checkbutton', tk_caption)
        self.assertIn('self.text_only_control.place(x=8, y=3', tk_caption)

    def test_one_text_only_state_shared_by_popup_and_caption_window(self):
        """Both controls, one state, and a press on either one has to land.

        只有字 hides the caption window's header, its own control included, so the popup
        is the only way back -- and it therefore has to be showing the window's real
        state, or the first press is spent on a state the window is not in. Two things
        carry that: the popup sends the style frame itself (storage.onChanged does not
        fire when the value did not change), and it asks the window what it is set to
        when it opens (durable storage only records what the next start will read).
        """
        popup = (ROOT / "extension" / "popup.js").read_text(encoding="utf-8")
        offscreen = (ROOT / "extension" / "offscreen.js").read_text(encoding="utf-8")

        self.assertIn("function tellCaptionWindow()", popup)
        self.assertIn('type: "caption-style"', popup)
        self.assertIn("tellCaptionWindow();", popup)
        self.assertIn("function askCaptionWindowState()", popup)
        self.assertIn("askCaptionWindowState();", popup)
        self.assertIn('type: "caption-state"', popup)
        self.assertIn('message.type === "caption-style"', offscreen)
        self.assertIn('message.type === "caption-state"', offscreen)
        self.assertIn('sendResponse({ ok: true, sent: sendJson({ type: "get_state" }) })', offscreen)
        # And the note says where to press to get the frame back, rather than claiming a
        # control that is no longer on screen.
        self.assertIn("再点一下即可恢复", popup)

    def test_the_three_caption_states_are_distinct(self):
        """在跑 / 没在跑 / 服务没连上 —— 三种情形分开说，不用定时器猜。

        猜出来的那次误报是用户实测到的「一次有一次没有」：线路切换会重启后端，socket
        已经开着而字幕窗还没起来，超时告警就会亮。真正能回答"有没有窗口"的只有服务端
        （它持有 CAPTION_PORT），所以由它明说 `caption-absent`。
        """
        popup = (ROOT / "extension" / "popup.js").read_text(encoding="utf-8")
        ws_server = (ROOT / "server" / "ws_server.py").read_text(encoding="utf-8")

        self.assertIn("captionStateHint", popup)
        self.assertIn('message.type === "caption-absent"', popup)
        self.assertIn("captionPresent", popup)
        self.assertNotIn("没有回应", popup, "定时器猜出来的告警又回来了")
        # 服务端：没有窗口就明说，别用超时假装知道
        self.assertIn("if caption_running():", ws_server)
        self.assertIn('await send({"type": "caption-absent"})', ws_server)
        self.assertIn("def caption_running()", (ROOT / "server" / "caption_window.py").read_text(encoding="utf-8"))

    def test_a_line_switch_does_not_ask_or_report_while_the_backend_is_down(self):
        """切换线路时后端要重启，字幕窗是它的子进程 —— 那段时间没有可问的对象。"""
        popup = (ROOT / "extension" / "popup.js").read_text(encoding="utf-8")

        self.assertIn("captionBusy = true;", popup)
        self.assertIn("captionBusy = false;", popup)
        self.assertIn("正在重启后端，字幕窗会自动回来。", popup)
        self.assertIn("字幕会自动回来", popup)

    def test_the_socket_comes_back_by_itself_after_a_line_switch(self):
        """这就是"切换引擎一次有一次没有"：断了以后没人重连，用户得再按一次开始。"""
        offscreen = (ROOT / "extension" / "offscreen.js").read_text(encoding="utf-8")

        self.assertIn("function scheduleReconnect()", offscreen)
        self.assertIn("function cancelReconnect()", offscreen)
        self.assertIn("lastStart = message;", offscreen)
        self.assertIn('type: "capture-restored"', offscreen)
        # 断开的那一刻不再立刻报错：后端可能正在重启，报错会每次都亮。
        self.assertIn("scheduleReconnect();\n    };", offscreen)
        self.assertIn("重连超时", offscreen)
        # 用户主动停止时不重连。
        self.assertIn("capturing = false;\n  cancelReconnect();", offscreen)

    def test_get_state_asks_the_window_instead_of_keeping_a_copy(self):
        """No cache anywhere: the window is asked, and it answers for itself.

        There was a cache here -- first a local of the WebSocket handler, then one
        written from both ends. Both are second sources of truth, and the stale one is
        what showed an unticked 只有字 while the window was in it (2026-10-09: the
        screenshot the user sent). The window is one UDP question away, so it is asked.
        """
        ws_server = (ROOT / "server" / "ws_server.py").read_text(encoding="utf-8")
        caption = (ROOT / "server" / "caption_window.py").read_text(encoding="utf-8")

        self.assertIn("request_caption_state()", ws_server)
        self.assertNotIn("_CAPTION_TEXT_ONLY", ws_server)
        self.assertIn("def request_caption_state()", caption)
        self.assertIn('_send("state?", None)', caption)
        self.assertIn('kind == "state?"', caption)
        # Forced: the asker has no other copy, so "nothing changed" is not an answer.
        self.assertIn("self._broadcast_state(force=True)", caption)

    def test_state_sync_is_exposed_and_the_position_lock_is_gone(self):
        ws_server = (ROOT / "server" / "ws_server.py").read_text(encoding="utf-8")
        tk_caption = _code_only(
            (ROOT / "server" / "caption_window.py").read_text(encoding="utf-8")
        )
        offscreen = (ROOT / "extension" / "offscreen.js").read_text(encoding="utf-8")

        self.assertIn('"toggle_transparent"', ws_server)
        self.assertIn('"get_state"', ws_server)
        # 锁定 left on 2026-10-09. It was a second checkbox in the caption window's
        # header, and it made 只有字 undraggable -- a view mode that had to be undone
        # from the popup. Nothing may reintroduce it under the same name.
        self.assertNotIn("position_locked", tk_caption)
        self.assertNotIn("apply_position_lock", tk_caption)
        self.assertNotIn("锁定", tk_caption)
        self.assertNotIn("positionLocked", ws_server)
        self.assertNotIn("positionLocked", offscreen)
        # Click-through is the bit that actually stopped the drag, so the only thing
        # allowed to mention it is the line that clears it.
        self.assertNotIn("|= WS_EX_TRANSPARENT", tk_caption)


if __name__ == "__main__":
    unittest.main()
