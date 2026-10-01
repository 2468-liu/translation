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
        self.assertNotIn("overlay.html", background)
        self.assertNotIn("chrome.windows.create", background)
        self.assertNotIn("chrome.windows", background)

    def test_text_only_control_is_in_caption_window_header(self):
        tk_caption = (ROOT / "server" / "caption_window.py").read_text(encoding="utf-8")

        self.assertIn('text_only_control = tk.Checkbutton', tk_caption)
        self.assertIn('self.text_only_control.place(x=8, y=3', tk_caption)


if __name__ == "__main__":
    unittest.main()
