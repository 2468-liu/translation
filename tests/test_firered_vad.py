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
        # When TRANSLATION_VAD_BACKEND is unset or "silero", defaults to Silero or Energy
        vad._VAD = None
        os.environ["TRANSLATION_VAD_BACKEND"] = "silero"
        try:
            detector = vad.load_vad()
            if SILERO_ONNX.exists():
                self.assertIsInstance(detector, vad._Silero)
            else:
                self.assertIsInstance(detector, vad._Energy)
        finally:
            os.environ.pop("TRANSLATION_VAD_BACKEND", None)
            vad._VAD = None

    @unittest.skipUnless(FIRERED_ONNX.exists(), "FireRedVAD ONNX not found")
    def test_pluggable_vad_selection_firered(self):
        vad._VAD = None
        os.environ["TRANSLATION_VAD_BACKEND"] = "firered"
        try:
            detector = vad.load_vad()
            self.assertIsInstance(detector, vad._FireRedVad)
        finally:
            os.environ.pop("TRANSLATION_VAD_BACKEND", None)
            vad._VAD = None


if __name__ == "__main__":
    unittest.main()
