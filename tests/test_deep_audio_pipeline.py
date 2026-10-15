"""Deep Audio Pipeline and Streaming State Machine Testing Suite.

Inspired by Windows Live Captions and WebRTC audio pipeline engineering:
1. Chaos Reset Criterion: Clean coroutine cancellation (< 20ms) and buffer zeroing during mid-stream aborts.
2. VAD Boundary Criterion: Zero spurious emissions on continuous silence/noise, and cadence-governed slice scheduling.
"""

from __future__ import annotations

import asyncio
import numpy as np
import pytest
from unittest.mock import AsyncMock, MagicMock

from server.config import SAMPLE_RATE
from server.pipeline import Session
from server.vad import UtteranceSplitter, apply_partial_interval


class MockAsrEngine:
    """Deterministic Mock ASR Engine for pipeline stress testing."""
    def __init__(self, transcribe_delay: float = 0.05):
        self.transcribe_delay = transcribe_delay
        self.is_transcribing = False
        self.call_count = 0

    def transcribe(self, pcm_data, language=None, initial_prompt=None):
        self.call_count += 1
        return "Deterministic recognition text", "en"


@pytest.fixture
def session_pipeline():
    """Create a Session with mocked ASR."""
    mock_asr = MockAsrEngine(transcribe_delay=0.01)
    session = Session(mock_asr)
    session.set_language("en")
    session.set_target_language("zh")
    apply_partial_interval(session.vad, 160)
    return session


class TestChaosResetCriterion:
    """Chaos Reset Criterion: Mid-flight aborts must not leave orphan tasks or ghost contexts."""

    @pytest.mark.asyncio
    async def test_reset_aborts_active_translation_task_cleanly(self, session_pipeline):
        """When translate_task is running, reset() cancels it without leaving orphan tasks."""
        async def slow_mock_job(*args, **kwargs):
            await asyncio.sleep(2.0)
            return {"type": "translation", "text": "should never finish"}

        # Simulate an ongoing translation task
        task = asyncio.create_task(slow_mock_job())
        session_pipeline.translate_task = task
        session_pipeline.pending_translate = ("pending text", False, "en", session_pipeline.media_generation)
        session_pipeline.last_text = "ghost previous utterance"
        session_pipeline.shown_translation = "ghost translation"

        assert not task.done()

        # Perform chaos reset
        session_pipeline.reset()

        # 1. translate_task must be canceled or detached
        assert session_pipeline.translate_task is None
        assert session_pipeline.pending_translate is None
        # In Python 3.11+, task enters cancelling state immediately
        is_cancelling = hasattr(task, "cancelling") and task.cancelling()
        assert is_cancelling or task.cancelled() or task.done()

        # 2. State and context must be zeroed
        assert session_pipeline.last_text == ""
        assert session_pipeline.shown_translation == ""
        assert session_pipeline.shown_source == ""

        # Let the event loop process task cancellation
        await asyncio.sleep(0.02)
        assert task.cancelled()

    @pytest.mark.asyncio
    async def test_rapid_alternating_push_and_reset(self, session_pipeline):
        """Alternating push_pcm and reset() rapidly must not raise exceptions or deadlock."""
        # Generate dummy 16kHz 16-bit PCM (0.1s bursts)
        samples = np.random.uniform(-0.8, 0.8, int(SAMPLE_RATE * 0.1)).astype(np.float32)
        pcm_bytes = (samples * 32767).astype(np.int16).tobytes()

        for _ in range(20):
            session_pipeline.push_pcm(pcm_bytes)
            session_pipeline.reset()

        assert session_pipeline.last_text == ""
        assert session_pipeline.translate_task is None


class TestVadBoundaryCriterion:
    """VAD Boundary Criterion: Strict zero-emission on silence/noise, and cadence enforcement."""

    def test_ten_seconds_silence_yields_zero_emissions(self, session_pipeline):
        """10 seconds of 0-amplitude PCM pushed in 100ms slices must yield exactly 0 utterances."""
        slice_samples = int(SAMPLE_RATE * 0.1)  # 100ms
        silence_bytes = np.zeros(slice_samples, dtype=np.int16).tobytes()

        total_utterances = 0
        # 100 slices = 10.0 seconds
        for _ in range(100):
            results = session_pipeline.push_pcm(silence_bytes)
            total_utterances += len(results)

        assert total_utterances == 0
        assert session_pipeline.last_text == ""

    def test_low_amplitude_gaussian_noise_rejected(self, session_pipeline):
        """Low amplitude background noise (below speech threshold) must yield 0 utterances."""
        slice_samples = int(SAMPLE_RATE * 0.1)
        # White noise with very small standard deviation (whisper floor)
        noise = (np.random.normal(0, 0.003, slice_samples) * 32767).astype(np.int16)
        noise_bytes = noise.tobytes()

        total_utterances = 0
        for _ in range(50):  # 5.0 seconds
            results = session_pipeline.push_pcm(noise_bytes)
            total_utterances += len(results)

        assert total_utterances == 0

    def test_partial_interval_cadence_reconfiguration(self, session_pipeline):
        """Cadence reconfiguration updates UtteranceSplitter partial interval accurately."""
        apply_partial_interval(session_pipeline.vad, 80)
        assert session_pipeline.vad.tuning.partial_interval_ms == 80

        apply_partial_interval(session_pipeline.vad, 320)
        assert session_pipeline.vad.tuning.partial_interval_ms == 320
