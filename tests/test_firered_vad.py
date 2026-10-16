"""Unit tests for FireRedVAD ONNX engine, Fbank feature extractor, and pluggable VAD selection."""

from __future__ import annotations

import os
from pathlib import Path
import unittest

import numpy as np

from server import vad
from server.config import FIRERED_ONNX, SILERO_ONNX


class TestFireRedVad(unittest.TestCase):
    def test_fbank_extractor_dimensions(self):
        extractor = vad._FbankExtractor()
        pcm_100ms = np.random.randn(1600).astype(np.float32) * 1000.0
        feats = extractor.extract(pcm_100ms)
        self.assertEqual(feats.ndim, 2)
        self.assertEqual(feats.shape[1], 80)
        self.assertGreater(feats.shape[0], 0)

    @unittest.skipUnless(FIRERED_ONNX.exists(), "FireRedVAD ONNX not found in models/firered_vad")
    def test_firered_vad_inference_and_reset(self):
        detector = vad._FireRedVad(FIRERED_ONNX.parent)
        frame_32ms = np.random.randn(512).astype(np.float32)
        p1 = detector.prob(frame_32ms)
        self.assertIsInstance(p1, float)
        self.assertGreaterEqual(p1, 0.0)
        self.assertLessEqual(p1, 1.0)

        # Test reset
        detector.reset()
        self.assertEqual(len(detector.leftover), 0)
        for i in range(8):
            self.assertTrue(np.all(detector.caches[f"cache_{i}"] == 0.0))

    def test_pluggable_vad_selection_fallback(self):
        from server import config
        vad._VAD = None
        old_val = config.get_vad_backend()
        config.set_vad_backend("silero")
        try:
            detector = vad.load_vad()
            if SILERO_ONNX.exists():
                self.assertIsInstance(detector, vad._Silero)
            else:
                self.assertIsInstance(detector, vad._Energy)
        finally:
            config.set_vad_backend(old_val)
            vad._VAD = None

    @unittest.skipUnless(FIRERED_ONNX.exists(), "FireRedVAD ONNX not found")
    def test_pluggable_vad_selection_firered(self):
        from server import config
        vad._VAD = None
        old_val = config.get_vad_backend()
        config.set_vad_backend("firered")
        try:
            detector = vad.load_vad()
            self.assertIsInstance(detector, vad._FireRedVad)
        finally:
            config.set_vad_backend(old_val)
            vad._VAD = None

    def test_ui_vad_selection_and_persistence(self):
        from PyQt6.QtWidgets import QApplication
        from server.ui.caption_window import QtCaptionWindow
        from server import config

        app = QApplication.instance() or QApplication([])
        win = QtCaptionWindow()
        emitted = []
        win.config_changed.connect(lambda d: emitted.append(d))

        try:
            # 1. Trigger firered via UI action
            win._vad_firered_act.trigger()
            self.assertEqual(config.get_vad_backend(), "firered")
            self.assertTrue(any(e.get("vad_backend") == "firered" for e in emitted))
            self.assertTrue(win._vad_firered_act.isChecked())
            self.assertFalse(win._vad_silero_act.isChecked())

            # 2. Trigger silero via UI action
            emitted.clear()
            win._vad_silero_act.trigger()
            self.assertEqual(config.get_vad_backend(), "silero")
            self.assertTrue(any(e.get("vad_backend") == "silero" for e in emitted))
            self.assertTrue(win._vad_silero_act.isChecked())
            self.assertFalse(win._vad_firered_act.isChecked())
        finally:
            win.close()


if __name__ == "__main__":
    unittest.main()
