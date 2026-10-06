import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


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

    def test_state_sync_and_lock_controls_are_exposed(self):
        ws_server = (ROOT / "server" / "ws_server.py").read_text(encoding="utf-8")
        tk_caption = (ROOT / "server" / "caption_window.py").read_text(encoding="utf-8")

        self.assertIn('"toggle_transparent"', ws_server)
        self.assertIn('"get_state"', ws_server)
        self.assertIn('position_locked', tk_caption)
        self.assertIn('apply_position_lock', tk_caption)


if __name__ == "__main__":
    unittest.main()
