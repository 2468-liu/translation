"""Adaptive Audio Enhancer for Cinema Dialogue and Weak Speech.

Provides:
1. Loudness Normalization: Aligns input audio towards target RMS level (~ -20 dBFS, EBU R128).
2. Dynamic Range Compression (DRC): Conservative 1.8:1 compression above threshold,
   boosting weak dialogue while gently taming loud passages.
3. Automatic Gain Control (AGC): Smooth asymmetric attack/release tracking.
4. Tanh Soft-Limiter: Ensures output strictly in [-1.0, 1.0] without harsh digital clipping.
5. Adaptive Noise Gate: Blocks background floor hiss while preserving whispering dialogue.
"""

from __future__ import annotations

import numpy as np


class AudioEnhancer:
    """Realtime streaming audio dynamic range normalizer & enhancer."""

    def __init__(
        self,
        target_rms: float = 0.10,         # ~ -20 dBFS target level
        max_gain_db: float = 24.0,        # Maximum +24 dB boost (~15.8x)
        min_gain_db: float = -6.0,        # Minimum gain (-6 dB = 0.5x)
        noise_floor_rms: float = 0.0006,  # Noise gate cutoff (~ -64 dBFS) for whisper sensitivity
        attack_alpha: float = 0.35,       # Fast gain reduction on loud bursts
        release_alpha: float = 0.55,      # Fast onset boost on quiet speech
        drc_threshold_rms: float = 0.15,  # DRC compression threshold (~ -16 dBFS)
        drc_ratio: float = 1.8,           # Conservative 1.8:1 compression ratio
        enabled: bool = True,
    ):
        self.target_rms = float(target_rms)
        self.max_gain = float(10.0 ** (max_gain_db / 20.0))
        self.min_gain = float(10.0 ** (min_gain_db / 20.0))
        self.noise_floor_rms = float(noise_floor_rms)
        self.attack_alpha = float(attack_alpha)
        self.release_alpha = float(release_alpha)
        self.drc_threshold_rms = float(drc_threshold_rms)
        self.drc_ratio = float(drc_ratio)
        self.enabled = bool(enabled)

        # Internal state
        self._current_gain = 1.0
        self._last_speech_gain = 1.0

    def reset(self):
        """Reset internal gain tracking filter."""
        self._current_gain = 1.0
        self._last_speech_gain = 1.0

    def process_samples(self, samples: np.ndarray) -> np.ndarray:
        """Process float32 audio samples in [-1.0, 1.0] range."""
        if not self.enabled or samples.size == 0:
            return samples

        # 1. Compute chunk RMS energy
        rms = float(np.sqrt(np.mean(samples ** 2) + 1e-9))

        # 2. Noise Gate & Target Gain with Conservative DRC
        if rms < self.noise_floor_rms:
            # Below noise gate: gently decay current gain towards 1.0 without crashing instantly,
            # ensuring pre-amp readiness for sudden isolated quiet utterances following silence
            decay_alpha = 0.015
            self._current_gain = (1.0 - decay_alpha) * self._current_gain + decay_alpha * 1.0
            target_gain = self._current_gain
        elif rms < self.target_rms:
            # Low speech level: boost towards target_rms
            desired = self.target_rms / max(rms, 1e-5)
            target_gain = min(self.max_gain, max(self.min_gain, desired))
            self._last_speech_gain = target_gain
        else:
            # Loud passage: apply conservative DRC
            if rms > self.drc_threshold_rms:
                excess = rms / self.drc_threshold_rms
                compressed = excess ** (1.0 / self.drc_ratio)
                target_gain = (self.drc_threshold_rms * compressed) / rms
                target_gain = max(self.min_gain, target_gain)
            else:
                target_gain = 1.0
            self._last_speech_gain = target_gain

        # 3. Smooth exponential moving average (asymmetric attack/release)
        if rms >= self.noise_floor_rms:
            if target_gain < self._current_gain:
                # Audio got louder: fast attack to avoid blasting
                self._current_gain = (
                    self.attack_alpha * target_gain
                    + (1.0 - self.attack_alpha) * self._current_gain
                )
            else:
                # Speech onset: fast boost so initial syllables are captured cleanly by VAD
                self._current_gain = (
                    self.release_alpha * target_gain
                    + (1.0 - self.release_alpha) * self._current_gain
                )

        # 4. Apply gain
        amplified = samples * self._current_gain

        # 5. Tanh soft-limiting: smooth saturation curve, output strictly in (-1.0, 1.0)
        # Linear for |x| < 0.9, smoothly saturates without harsh digital clipping
        limited = np.where(
            np.abs(amplified) > 0.9,
            np.tanh(amplified),
            amplified
        )

        return limited.astype(np.float32)

    def process_pcm_bytes(self, pcm_bytes: bytes) -> bytes:
        """Process 16-bit signed integer mono PCM bytes."""
        if not self.enabled or not pcm_bytes:
            return pcm_bytes

        # Convert to float32
        int16_arr = np.frombuffer(pcm_bytes, dtype=np.int16)
        if int16_arr.size == 0:
            return pcm_bytes

        float_samples = int16_arr.astype(np.float32) / 32768.0

        # Process through dynamic AGC & limiter
        enhanced = self.process_samples(float_samples)

        # Scale back to int16
        clipped = np.clip(enhanced * 32767.0, -32768.0, 32767.0).astype(np.int16)
        return clipped.tobytes()
