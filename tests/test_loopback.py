import unittest

import numpy as np

from server.loopback import _to_pcm16


class LoopbackTests(unittest.TestCase):
    def test_stereo_loopback_is_converted_to_clipped_mono_pcm16(self):
        data = np.array([[1.0, 1.0], [-1.0, -1.0], [2.0, -2.0]], dtype=np.float32)

        pcm = np.frombuffer(_to_pcm16(data), dtype=np.int16)

        self.assertEqual(pcm.tolist(), [32767, -32767, 0])


if __name__ == "__main__":
    unittest.main()
