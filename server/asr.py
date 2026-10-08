"""faster-whisper wrapper. GPU-first path to reduce recognition latency."""

from __future__ import annotations

import base64
import re
import threading

import numpy as np

from . import config
from .config import (
    ASR_BEAM_SIZE,
    ASR_COMPUTE_TYPE,
    ASR_CPU_THREADS,
    ASR_DEVICE,
    ASR_FALLBACK_DIR,
    ASR_MODEL_DIR,
    QWEN3_ASR_URL,
    SAMPLE_RATE,
)

_HALLUCINATIONS = {
    "thanks for watching",
    "thank you for watching",
    "thanks for listening",
    "subtitles by the amara.org community",
    "subscribe to my channel",
    "please subscribe",
}


def is_hallucination(text: str) -> bool:
    normalized = re.sub(r"[\s\.\,\!\?。！？…'\"“”]+", " ", text).strip().lower()
    if not normalized:
        return True
    if normalized in _HALLUCINATIONS:
        return True
    return False


def _model_dir() -> str:
    if (ASR_MODEL_DIR / "model.bin").exists():
        return str(ASR_MODEL_DIR)
    if (ASR_FALLBACK_DIR / "model.bin").exists():
        return str(ASR_FALLBACK_DIR)
    raise FileNotFoundError(
        f"ASR model missing. Run: python scripts/download_models.py (expected {ASR_MODEL_DIR / 'model.bin'})"
    )


class AsrEngine:
    def __init__(self):
        from faster_whisper import WhisperModel

        path = _model_dir()
        print(f"ASR loading {path} device={ASR_DEVICE} compute={ASR_COMPUTE_TYPE}")
        try:
            self.model = WhisperModel(
                path,
                device=ASR_DEVICE,
                compute_type=ASR_COMPUTE_TYPE,
                cpu_threads=ASR_CPU_THREADS,
            )
        except Exception as exc:
            if ASR_DEVICE == "cpu":
                raise
            print(f"ASR {ASR_DEVICE} failed ({exc}); using cpu int8")
            self.model = WhisperModel(
                path,
                device="cpu",
                compute_type="int8",
                cpu_threads=ASR_CPU_THREADS,
            )
        self._lock = threading.Lock()

    def transcribe(self, audio: np.ndarray, language: str | None) -> tuple[str, str | None, float]:
        if audio.size < 1600:
            return "", language, 0.0
        with self._lock:
            segments, info = self.model.transcribe(
                audio,
                beam_size=ASR_BEAM_SIZE,
                vad_filter=False,
                language=language,
                condition_on_previous_text=False,
                temperature=0.0,
                without_timestamps=True,
            )
            text = " ".join(segment.text.strip() for segment in segments).strip()
            detected = info.language or language
            probability = float(info.language_probability or 0.0)
        if is_hallucination(text):
            return "", detected, probability
        return text, detected, probability


_LANG_NAMES = {
    "english": "en", "chinese": "zh", "mandarin": "zh", "spanish": "es",
    "french": "fr", "german": "de", "japanese": "ja", "korean": "ko",
    "russian": "ru", "portuguese": "pt", "italian": "it", "arabic": "ar",
    "turkish": "tr", "croatian": "hr", "hindi": "hi", "dutch": "nl",
}


def parse_qwen3_asr(raw: str) -> tuple[str, str | None, float]:
    """Split the model's own framing off the transcript.

    Qwen3-ASR answers with a structured prefix before the text:

        language English<asr_text>I think you should go with your instincts.

    The language comes as a word, not a code, and the whole thing is one string, so
    without this the pipeline would caption "language English" as if it were speech.
    The separator is the reliable part; the language name is treated as advisory and
    only used when it is one we know, because a wrong guess here would set the
    recogniser's language for every later call.
    """
    marker = "<asr_text>"
    if marker in raw:
        head, text = raw.split(marker, 1)
        text = text.strip()
    else:
        head, text = "", raw.strip()
    detected: str | None = None
    lowered = head.strip().lower()
    if lowered.startswith("language"):
        name = lowered[len("language"):].strip().strip(".:")
        for word, code in _LANG_NAMES.items():
            if name.startswith(word):
                detected = code
                break
    return text, detected, 1.0 if text else 0.0


def _pcm_bytes(audio: np.ndarray) -> bytes:
    """The 16-bit PCM on its own, without a container.

    Factorised because the prefix property has to be testable. The bytes must be a
    pure function of the samples and a longer clip must start with a shorter one: if
    that ever stops holding, no prefix cache can hit and it fails silently as
    latency rather than as an error.
    """
    clipped = np.clip(audio, -1.0, 1.0)
    return (clipped * 32767.0).astype("<i2").tobytes()


def _wav_bytes(audio: np.ndarray) -> bytes:
    """The PCM in a real wav container, which is what the request must carry.

    _pcm_bytes on its own is not a valid audio file, and sending it while declaring
    format=wav gets a 400 "Failed to load image or audio file" from llama.cpp. The
    two are deliberately separate: the prefix test wants the payload alone, because
    a wav header carries the data length and would make every clip differ in its
    first 44 bytes, while the request needs the header.
    """
    import io
    import wave as wavemod

    buf = io.BytesIO()
    with wavemod.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(_pcm_bytes(audio))
    return buf.getvalue()


class Qwen3AsrEngine:
    """Qwen3-ASR over HTTP, served by a second llama-server.

    Same interface as AsrEngine so the pipeline cannot tell them apart, and the
    switch between them is one environment variable.

    The audio is sent as 16-bit PCM in a wav, which is deliberate rather than
    incidental: the bytes have to be identical between calls for llama.cpp's prefix
    cache to hit, and the probe that checked this found every partial inside an
    utterance to be a byte-prefix of the next, with 8 of 8 breaks falling exactly on
    utterance boundaries where continuing would be wrong. The cache still does not
    help us in practice -- llama.cpp keeps one prompt and a request must be a prefix
    of it, while our partials grow past the retained one every time -- so this is
    correctness for a possible future incremental mode, not a speed claim.
    """

    def __init__(self):
        self._lock = threading.Lock()
        print(f"ASR loading Qwen3-ASR via {QWEN3_ASR_URL} (backend=qwen3)")

    def transcribe(self, audio: np.ndarray, language: str | None) -> tuple[str, str | None, float]:
        if audio.size < 1600:
            return "", language, 0.0
        import httpx

        wav = _wav_bytes(audio)
        payload = {
            "model": "qwen3-asr",
            "temperature": 0,
            "messages": [{"role": "user", "content": [
                {"type": "input_audio", "input_audio": {
                    "data": base64.b64encode(wav).decode("ascii"),
                    "format": "wav"}},
                {"type": "text", "text": "Transcribe the audio."},
            ]}],
        }
        # A client per call, deliberately.
        #
        # Sharing one httpx.Client across the pipeline's executor threads hangs on
        # the first request with no exception anywhere: the connection pool is bound
        # to the loop that first used it, and a client built at engine construction
        # time is not on the loop transcribe() runs on. The symptom is
        # indistinguishable from a broken model -- vad.end fired 49 times,
        # pipeline.enqueue 630, pipeline.asr zero, and nothing in the log -- so
        # guessing wrong here costs a run that reads as a verdict on Qwen3-ASR
        # itself. At roughly one call per second a fresh client costs nothing
        # measurable and removes the whole class of problem.
        with self._lock:
            with httpx.Client(timeout=httpx.Timeout(120.0)) as client:
                reply = client.post(f"{QWEN3_ASR_URL}/v1/chat/completions", json=payload)
                reply.raise_for_status()
                raw = reply.json()["choices"][0]["message"]["content"]
        text, detected, confidence = parse_qwen3_asr(raw)
        # The model detects per call, so an explicit language is not forced: passing
        # one would need a different prompt, and the multilingual path is not what
        # was measured.
        if is_hallucination(text):
            return "", detected, confidence
        return text, detected, confidence


def build_asr():
    """The recogniser the configuration asks for.

    Read from config at call time rather than from the name imported above, so the
    effective configuration and the engine that actually got built cannot disagree.
    Kept behind one factory so ws_server does not grow a branch.
    """
    backend = config.ASR_BACKEND
    if backend == "qwen3":
        return Qwen3AsrEngine()
    if backend == "whisper":
        return AsrEngine()
    raise ValueError(
        f"unknown ASR_BACKEND {backend!r}; expected 'whisper' or 'qwen3'")
