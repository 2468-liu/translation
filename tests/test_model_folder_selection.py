"""Test custom model folder selection, normalization, and dynamic resolution.

Validates that:
1. When a user chooses a subfolder (e.g., Index-Translate or Qwen-ASR-0.6B), normalize_models_dir
   steps up to the parent models directory.
2. When a user chooses a root directory containing a models/ subdirectory, normalize_models_dir
   steps into models/.
3. When a user has a flat directory with .gguf models at the root, set_models_dir and dynamic
   resolvers discover them without requiring specific subfolder names.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from server import config


class ModelFolderSelectionTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_normalize_when_user_selects_subfolder(self):
        models_root = self.root / "models"
        idx_dir = models_root / "Index-Translate"
        qwen_dir = models_root / "Qwen-ASR-0.6B"
        idx_dir.mkdir(parents=True)
        qwen_dir.mkdir(parents=True)

        # Create dummy weights in both subfolders
        dummy_idx = idx_dir / "Index-Translate-2B.Q4_K_M.gguf"
        dummy_idx.write_bytes(b"x" * 1024)

        # User clicked into Index-Translate and selected it
        norm = config.normalize_models_dir(idx_dir)
        self.assertEqual(norm, models_root.resolve())

        # User clicked into Qwen-ASR-0.6B and selected it
        norm_qwen = config.normalize_models_dir(qwen_dir)
        self.assertEqual(norm_qwen, models_root.resolve())

    def test_normalize_when_user_selects_app_root_with_models(self):
        models_root = self.root / "models"
        qwen_dir = models_root / "Qwen-ASR-0.6B"
        qwen_dir.mkdir(parents=True)
        dummy_qwen = qwen_dir / "Qwen3-ASR-0.6B-Q8_0.gguf"
        dummy_qwen.write_bytes(b"x" * (51 * 1024 * 1024))

        norm = config.normalize_models_dir(self.root)
        self.assertEqual(norm, models_root.resolve())

    def test_flat_models_directory_discovery(self):
        flat_dir = self.root / "my_flat_models"
        flat_dir.mkdir(parents=True)

        qwen_file = flat_dir / "Qwen3-ASR-0.6B-Q8_0.gguf"
        mmproj_file = flat_dir / "mmproj-Qwen3-ASR-0.6B-Q8_0.gguf"
        silero_file = flat_dir / "silero_vad.onnx"
        nv_file = flat_dir / "nemotron-3.5-asr-streaming-0.6b.q8_0.gguf"

        qwen_file.write_bytes(b"x" * (60 * 1024 * 1024))
        mmproj_file.write_bytes(b"x" * (15 * 1024 * 1024))
        silero_file.write_bytes(b"x" * 1024)
        nv_file.write_bytes(b"x" * (510 * 1024 * 1024))

        config.set_models_dir(flat_dir)

        self.assertEqual(config.MODELS_DIR, flat_dir.resolve())
        self.assertEqual(config.QWEN3_ASR_MODEL, qwen_file.resolve())
        self.assertEqual(config.QWEN3_ASR_MMPROJ, mmproj_file.resolve())
        self.assertEqual(config.SILERO_ONNX, silero_file.resolve())

        # Test Nemotron model resolver finding flat model
        from server.asr.nemotron import _resolve_nemotron_model_path
        resolved_nv = _resolve_nemotron_model_path()
        self.assertEqual(resolved_nv, nv_file.resolve())


if __name__ == "__main__":
    unittest.main()
