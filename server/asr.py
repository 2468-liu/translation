"""faster-whisper wrapper. GPU-first path to reduce recognition latency."""

from __future__ import annotations

import base64
import re
import threading

import numpy as np

from . import config, perf
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
    "chinese": "zh", "mandarin": "zh", "cantonese": "zh",
    "english": "en", "japanese": "ja", "korean": "ko",
    "spanish": "es", "french": "fr", "german": "de",
    "russian": "ru", "portuguese": "pt", "italian": "it",
    "arabic": "ar", "turkish": "tr", "vietnamese": "vi",
    "thai": "th", "indonesian": "id", "hindi": "hi",
    "dutch": "nl", "polish": "pl", "swedish": "sv",
    "czech": "cs", "hungarian": "hu", "romanian": "ro",
    "ukrainian": "uk", "greek": "el", "hebrew": "he",
    "malay": "ms", "persian": "fa", "urdu": "ur",
    "tamil": "ta", "croatian": "hr",
}

_CODE_TO_FULL_LANG = {
    "zh": "Chinese",
    "en": "English",
    "ja": "Japanese",
    "ko": "Korean",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "ru": "Russian",
    "pt": "Portuguese",
    "it": "Italian",
    "ar": "Arabic",
    "tr": "Turkish",
    "vi": "Vietnamese",
    "th": "Thai",
    "id": "Indonesian",
    "hi": "Hindi",
    "nl": "Dutch",
    "pl": "Polish",
    "sv": "Swedish",
    "cs": "Czech",
    "hu": "Hungarian",
    "ro": "Romanian",
    "uk": "Ukrainian",
    "el": "Greek",
    "he": "Hebrew",
    "ms": "Malay",
    "fa": "Persian",
    "ur": "Urdu",
    "ta": "Tamil",
    "hr": "Croatian",
}


def parse_qwen3_asr(raw: str) -> tuple[str, str | None, float]:
    """Split the model's own framing off the transcript.

    Qwen3-ASR answers with a structured prefix before the text:
        language English<asr_text>I think you should go with your instincts.
    or
        language Japanese<asr_text>こんにちは
    or without marker:
        language: Japanese\nこんにちは
    or bare text:
        こんにちは

    This parser strips all metadata framing, extracts detected language,
    and returns pure subtitle transcript text.
    """
    if not raw:
        return "", None, 0.0

    detected: str | None = None
    text = raw.strip()

    # 1. Standard Qwen3 <asr_text> marker
    marker = "<asr_text>"
    if marker in text:
        head, body = text.split(marker, 1)
        text = body.strip()
        lowered = head.strip().lower()
        if lowered.startswith("language"):
            name = lowered[len("language"):].strip().strip(".:=")
            for word, code in _LANG_NAMES.items():
                if name.startswith(word):
                    detected = code
                    break
        return text, detected, 1.0 if text else 0.0

    # 2. Fallback prefix check (e.g. "language Japanese: ..." or "language: ja ...")
    m = re.match(r"^language\s*[:=\s]?\s*([a-zA-Z]+)[\s:：\n]+(.*)$", text, re.DOTALL | re.IGNORECASE)
    if m:
        name = m.group(1).lower()
        for word, code in _LANG_NAMES.items():
            if name.startswith(word) or name == code:
                detected = code
                break
        text = m.group(2).strip()
        return text, detected, 1.0 if text else 0.0

    return text, None, 1.0 if text else 0.0


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



# A canonical wav header is 44 bytes. _wav_bytes writes a fixed-layout one, and the
# audio_ms figure for the truncation event is derived from the payload length, so the
# constant has to be named rather than written as a 44 in the middle of an expression.
WAV_HEADER_BYTES = 44

# How much of a dropped ASR response the truncation event carries. It exists because the
# decision the event informs -- raise the cap or not -- is unreadable without it: a long
# utterance that legitimately exceeds 128 tokens and a repetition loop that ran to the cap
# are identical in chars= and tokens=, because both stop there.
#
# 80 characters of head is enough to see a phrase repeating four times, and the tail is
# what a reader would have been shown if the response had been kept. Bounded because these
# are the only ASR outputs ever written to a log, and an unbounded one would put arbitrary
# speech in a file that gets committed alongside the code.
#
# A numeric repetition score was tried here and removed: it shingled the text and counted
# repeats, which scored a legitimately long transcription that quoted itself at 0.75 --
# indistinguishable from a 0.89 repetition loop. It measures lexical repetition, and a
# speaker repeating a phrase is not a recogniser failing. The text is the instrument; a
# number beside it that cannot separate the two cases would be worse than nothing,
# because it would look like the separation had been made.
HEAD_CHARS = 80
TAIL_CHARS = 40


def usage_tokens(body: dict) -> int:
    """completion_tokens, or -1 when the server left usage out.

    -1 rather than 0 because a missing count and a count of zero are different
    findings: one means the server is not reporting, the other means it generated
    nothing. Collapsing them would make the truncation counter quietly wrong.
    """
    try:
        return int(body["usage"]["completion_tokens"])
    except (KeyError, TypeError, ValueError):
        return -1


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
        lang_prompt = "Transcribe the audio."
        if language and language.lower() != "auto":
            code = language.lower().strip()
            full_name = _CODE_TO_FULL_LANG.get(code, code.capitalize())
            lang_prompt = f"Transcribe the {full_name} audio."

        payload = {
            "model": "qwen3-asr",
            "temperature": 0,
            "max_tokens": config.QWEN3_ASR_MAX_TOKENS,
            "messages": [{"role": "user", "content": [
                {"type": "input_audio", "input_audio": {
                    "data": base64.b64encode(wav).decode("ascii"),
                    "format": "wav"}},
                {"type": "text", "text": lang_prompt},
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
                body = reply.json()
        choice = body["choices"][0]
        raw = choice["message"]["content"]

        # A cap turns an unbounded failure into a bounded one, and the bound is the
        # whole point.
        #
        # Qwen3-ASR occasionally never emits an end token and generates until the
        # server's context is full. Measured on samples/lecture_mix.wav: 5 of 149
        # calls, 30 to 33 seconds each, eval 3912 to 3951 tokens against a normal 7 to
        # 46, with 184 prompt tokens, and 184 + 3912 = 4096 exactly -- it ran to the
        # server's -c and stopped because it ran out of room rather than because it
        # finished. The text went to the screen: 74 seconds with nothing after
        # "你真的，你真的，你真的". A truncation on any of them, a cap does not fix the
        # repetition; it removes the 30 seconds of silence that came with it.
        #
        # Nothing upstream should have to notice this. A truncated response is
        # dropped, not trimmed to a prefix: half a clause on screen is worse than no
        # clause, because the caption has already swallowed it by the time anyone
        # reads it, and a repeated fragment reads as the recogniser failing rather
        # than as nothing having been said.
        #
        # finish_reason is the only signal for this. The server reports "length" when
        # it stops at max_tokens and "stop" when the model finishes, and the char count
        # is not a substitute -- a long utterance can legitimately transcribe to more
        # than the cap, and dropping those would throw away real speech.
        if choice.get("finish_reason") == "length":
            truncated = usage_tokens(body)
            # The text, bounded, because the decision this event exists to inform cannot
            # be made without it.
            #
            # The 128 cap trades one failure for another: dropping whole costs a final
            # transcript. Measured on lecture_mix: 5 of these against whisper's 0, which is
            # why Qwen3 emits 23 finals where whisper emits 25 and coverage reads 91%
            # against 96%. Raising the cap removes the drop but returns a longer text --
            # and the whole reason the drop exists is that the text repeats until it
            # fills the context. So the experiment is only readable if each truncation
            # shows what the text actually was.
            head = raw[:HEAD_CHARS].replace("\n", " ")
            tail = raw[-TAIL_CHARS:].replace("\n", " ")
            perf.emit("asr", "truncated", chars=len(raw), tokens=truncated,
                      audio_ms=int(len(wav) * 1000 / (SAMPLE_RATE * 2)) - WAV_HEADER_BYTES,
                      cap=config.QWEN3_ASR_MAX_TOKENS,
                      head=head, tail=tail)
            print(f"ASR truncated at {truncated} tokens (cap "
                  f"{config.QWEN3_ASR_MAX_TOKENS}); dropped; head={head!r}")
            return "", language, 0.0

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
