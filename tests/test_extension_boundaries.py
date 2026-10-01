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

    def test_background_does_not_inject_content_script(self):
        background = (ROOT / "extension" / "background.js").read_text(encoding="utf-8")

        self.assertNotIn("executeScript", background)
        self.assertNotIn('files: ["content.js"]', background)

    def test_text_only_control_is_in_caption_window_header(self):
        caption = (ROOT / "extension" / "caption.js").read_text(encoding="utf-8")

        self.assertIn('<label id="textOnlyControl">只有字', caption)
        self.assertNotIn('<label>只有字 <input id="textOnly"', caption)


if __name__ == "__main__":
    unittest.main()
