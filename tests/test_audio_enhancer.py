"""Unit tests for AudioEnhancer module."""

import numpy as np
import pytest

from server.audio_enhancer import AudioEnhancer


def test_audio_enhancer_boosts_quiet_speech():
    enhancer = AudioEnhancer(target_rms=0.12, max_gain_db=16.0, release_alpha=1.0)
    # Generate quiet sine wave: amplitude 0.03 (~ -30 dBFS)
    t = np.linspace(0, 1, 16000, endpoint=False, dtype=np.float32)
    quiet_signal = 0.03 * np.sin(2 * np.pi * 440 * t)

    enhanced = enhancer.process_samples(quiet_signal)

    in_rms = np.sqrt(np.mean(quiet_signal ** 2))
    out_rms = np.sqrt(np.mean(enhanced ** 2))

    # Output RMS should be significantly higher (+6dB ~ +14dB boost)
    gain_linear = out_rms / in_rms
    assert gain_linear > 2.0  # At least +6 dB
    assert np.max(enhanced) <= 1.0
    assert np.min(enhanced) >= -1.0


def test_audio_enhancer_preserves_silence_noise_gate():
    enhancer = AudioEnhancer(noise_floor_rms=0.003)
    # Below noise gate: amplitude 0.0005
    noise = np.full(1600, 0.0005, dtype=np.float32)
    enhanced = enhancer.process_samples(noise)

    # Should not blow up noise
    assert np.allclose(enhanced, noise, atol=1e-4)


def test_audio_enhancer_limits_loud_bursts():
    enhancer = AudioEnhancer(target_rms=0.12)
    # Extremely loud signal that would normally clip (> 1.0)
    loud_burst = 2.5 * np.sin(np.linspace(0, 10, 16000, dtype=np.float32))
    enhanced = enhancer.process_samples(loud_burst)

    # Soft limiter tanh guarantees within [-1.0, 1.0]
    assert np.max(enhanced) < 1.0
    assert np.min(enhanced) > -1.0


def test_audio_enhancer_pcm_bytes_roundtrip():
    enhancer = AudioEnhancer()
    # 100ms of int16 PCM (1600 samples = 3200 bytes)
    raw_samples = (np.sin(np.linspace(0, 10, 1600)) * 1000).astype(np.int16)
    raw_bytes = raw_samples.tobytes()

    processed_bytes = enhancer.process_pcm_bytes(raw_bytes)
    assert len(processed_bytes) == len(raw_bytes)

    out_samples = np.frombuffer(processed_bytes, dtype=np.int16)
    assert len(out_samples) == 1600
    # Values should be amplified but not clip int16
    assert np.max(np.abs(out_samples)) > np.max(np.abs(raw_samples))
