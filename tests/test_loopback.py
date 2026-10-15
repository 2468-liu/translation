import unittest

import numpy as np

from server.loopback import (
    LoopbackLevel,
    _to_pcm16,
    enable_prevolume_loopback,
)


class LoopbackTests(unittest.TestCase):
    def test_stereo_loopback_is_converted_to_clipped_mono_pcm16(self):
        data = np.array([[1.0, 1.0], [-1.0, -1.0], [2.0, -2.0]], dtype=np.float32)

        pcm = np.frombuffer(_to_pcm16(data), dtype=np.int16)

        self.assertEqual(pcm.tolist(), [32767, -32767, 0])

    def test_loopback_level_passthrough_maintains_unity_gain(self):
        level = LoopbackLevel(passthrough=True)
        # Feed 30 frames of quiet signal (peak 0.02)
        frame = np.full(1600, 0.02, dtype=np.float32)
        for _ in range(30):
            gain = level.observe(frame)
            self.assertEqual(gain, 1.0)
        self.assertEqual(level.gain, 1.0)
        self.assertTrue(level.passthrough)

    def test_loopback_level_always_maintains_unity_gain(self):
        # Strict Pre-Volume / Pre-Fader alignment: dirty software dynamic multipliers
        # are completely eradicated; unity gain (1.0) is strictly maintained.
        level = LoopbackLevel(passthrough=False)
        frame = np.full(1600, 0.02, dtype=np.float32)
        for _ in range(25):
            gain = level.observe(frame)
            self.assertEqual(gain, 1.0)
        self.assertEqual(level.gain, 1.0)

    def test_5point1_downmix_preserves_center_dialogue_and_omits_lfe(self):
        # 6 channels: [L, R, Center, LFE, SL, SR]
        # Frame 1: Center=1.0 (pure dialogue), LFE=0.0
        # Frame 2: Center=0.0, LFE=1.0 (pure explosion rumble)
        data = np.array([
            [0.0, 0.0, 1.0, 0.0, 0.0, 0.0],
            [0.0, 0.0, 0.0, 1.0, 0.0, 0.0],
        ], dtype=np.float32)

        pcm = np.frombuffer(_to_pcm16(data), dtype=np.int16)
        # Center channel (dialogue) should have strong amplitude (0.75 * 32767 ≈ 24575)
        # LFE channel should be completely excluded (0 amplitude)
        self.assertGreater(pcm[0], 20000)
        self.assertEqual(pcm[1], 0)

    def test_enable_prevolume_loopback_safe(self):
        res = enable_prevolume_loopback()
        self.assertIsInstance(res, bool)


if __name__ == "__main__":
    unittest.main()

