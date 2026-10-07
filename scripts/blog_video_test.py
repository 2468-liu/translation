"""Build a ~3 minute blog video and play it through the live subtitle server."""

from __future__ import annotations

import asyncio
import bisect
import json
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf
from PIL import Image, ImageDraw, ImageFont
from websockets.asyncio.client import connect

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from server.config import SAMPLE_RATE, WS_HOST, WS_PORT  # noqa: E402

FFMPEG = ROOT / "tools" / "ffmpeg" / "ffmpeg-9.0.2-essentials_build" / "bin" / "ffmpeg.exe"
OUT_DIR = ROOT / "samples"
LOG_DIR = ROOT / "logs"
WAV_PATH = OUT_DIR / "blog_3min.wav"
MP4_PATH = OUT_DIR / "blog_3min.mp4"


def _sample_wav() -> str:
    """Which clip to play, so a sweep can point the same machinery at new material.

    The TTS blog clip has a silence structure the real one does not: eighteen
    pauses of a second or more against zero. GEMMA_MIN_SILENCE_MS is 1000, so
    every conclusion about that threshold was measured on a clip that triggers
    it constantly, and a meeting recording triggers it never. The clip therefore
    has to be selectable, or the audio-native route can only ever be measured on
    the easy case.

    TRANSLATION_SAMPLE_WAV names the clip; TRANSLATION_SAMPLE_TIMING its sentence
    timeline, which is what the latency zero points come from and which has to
    match the clip or every number is measured against the wrong speech.
    """
    import os

    wav = os.environ.get("TRANSLATION_SAMPLE_WAV")
    return wav if wav else str(WAV_PATH)


def _sample_timing() -> Path:
    import os

    name = os.environ.get("TRANSLATION_SAMPLE_TIMING")
    return Path(name) if name else TIMING_PATH
CARD_PATH = OUT_DIR / "blog_3min_card.png"
SCRIPT_PATH = OUT_DIR / "blog_3min.txt"
TIMING_PATH = OUT_DIR / "blog_3min_timing.json"
RESULT_PATH = LOG_DIR / "blog_3min_test.json"
GAP_SEC = 0.7
TARGET_SEC = 180.0

SENTENCES = [
    "This week I spent three evenings turning a field notebook into a short blog video, because the notes were clear on paper and fell apart as soon as I tried to say them out loud.",
    "The first recording ran for almost nine minutes while I walked the same lake path, repeated the same observation about the ice, and still missed the sentence I meant to land.",
    "What I wanted to say was this: before the camera cut, the ice had already cracked from the dock to the far reed bed, and the lake had doubled in the reflection underneath.",
    "On paper that is one sentence, but in the video it becomes a breath, a pause, a wrong word, and then the corrected word a moment later.",
    "I kept the wrong word in the first edit, because cutting it made the picture of the dock jump away from the voice.",
    "On the second evening I left the house before dark, switched off the gas light, and gave myself twenty minutes with no signal, just to hear where the sentence should end.",
    "There is a red line on the valve behind the cabin, and if you follow that line with your eyes you can see that the door was never the point of the story.",
    "Artemis is the name painted on the boat, not the name of a mission, and I had to say it twice before it stopped sounding like an assignment.",
    "Hello. This paragraph is a subtitle check only in the sense that I am testing whether a long spoken passage survives the trip from a voice to the screen.",
    "A viewer should see the sentence grow, then sit still long enough to read it, and only then meet the next sentence.",
    "If one character is wrong, the line may change that character, but if the whole sentence is replaced, the line should stay until the thought has had time to land.",
    "By the third evening the recording was three minutes, which is short for a blog and long enough to show every place where the words start dancing.",
    "I am leaving the pauses where they are, including the half second after the dock, because a real video does not speak in one breath.",
    "The glacier above the lake looks close in the lens and far away on the walk, and that gap is why the sentence has to stay on screen after the voice stops.",
    "When the valve sticks, you do not restart the whole story. You correct the word and let the rest of the line remain.",
    "If you are hearing this, the test is the video itself: long sentences, a small correction inside one sentence, and a quiet moment before the next idea begins.",
]


def _speak(rate: int, work: Path) -> None:
    work.mkdir(parents=True, exist_ok=True)
    payload = work / "lines.json"
    payload.write_text(json.dumps(SENTENCES, ensure_ascii=False), encoding="utf-8")
    ps = work / "speak.ps1"
    ps.write_text(
        "\n".join(
            [
                "Add-Type -AssemblyName System.Speech",
                "$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer",
                "$voice = $synth.GetInstalledVoices() | Where-Object { $_.VoiceInfo.Culture.Name -like 'en*' } | Select-Object -First 1",
                "if ($voice) { $synth.SelectVoice($voice.VoiceInfo.Name) }",
                f"$synth.Rate = {rate}",
                "$synth.Volume = 100",
                f"$lines = Get-Content -Raw -Encoding UTF8 '{payload}' | ConvertFrom-Json",
                "for ($i = 0; $i -lt $lines.Count; $i++) {",
                f"  $path = Join-Path '{work}' ('line_{{0:D2}}.wav' -f $i)",
                "  $synth.SetOutputToWaveFile($path)",
                "  $synth.Speak($lines[$i])",
                "  $synth.SetOutputToNull()",
                "}",
                "$synth.Dispose()",
            ]
        ),
        encoding="utf-8",
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps)],
        check=True,
    )


def _load_mono(path: Path) -> tuple[np.ndarray, int]:
    audio, rate = sf.read(path, dtype="float32", always_2d=False)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    return audio.astype(np.float32), int(rate)


def _resample(audio: np.ndarray, rate: int) -> np.ndarray:
    if rate == SAMPLE_RATE or audio.size == 0:
        return audio
    dst_n = int(audio.shape[0] / rate * SAMPLE_RATE)
    src = np.linspace(0, 1, num=audio.shape[0], endpoint=False)
    dst = np.linspace(0, 1, num=dst_n, endpoint=False)
    return np.interp(dst, src, audio).astype(np.float32)


def _build_wav(rate: int) -> list[dict]:
    work = OUT_DIR / "_blog_lines"
    _speak(rate, work)
    pieces: list[np.ndarray] = []
    timing: list[dict] = []
    cursor = 0.4
    pieces.append(np.zeros(int(SAMPLE_RATE * cursor), dtype=np.float32))
    gap = np.zeros(int(SAMPLE_RATE * GAP_SEC), dtype=np.float32)
    for i, text in enumerate(SENTENCES):
        audio, src_rate = _load_mono(work / f"line_{i:02d}.wav")
        audio = _resample(audio, src_rate)
        start = cursor
        pieces.append(audio)
        cursor += audio.shape[0] / SAMPLE_RATE
        timing.append({"i": i, "start": round(start, 3), "end": round(cursor, 3), "text": text})
        pieces.append(gap)
        cursor += GAP_SEC
    pieces.append(np.zeros(int(SAMPLE_RATE * 0.8), dtype=np.float32))
    full = np.clip(np.concatenate(pieces), -1.0, 1.0)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    sf.write(WAV_PATH, full, SAMPLE_RATE)
    TIMING_PATH.write_text(json.dumps(timing, ensure_ascii=False, indent=2), encoding="utf-8")
    SCRIPT_PATH.write_text("\n\n".join(SENTENCES) + "\n", encoding="utf-8")
    return timing


def _card() -> None:
    image = Image.new("RGB", (1280, 720), (16, 24, 22))
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 64)
    small = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 28)
    title = "Field Notes"
    sub = "a three-minute blog"
    tw = draw.textlength(title, font=font)
    sw = draw.textlength(sub, font=small)
    draw.text(((1280 - tw) / 2, 300), title, fill=(236, 242, 236), font=font)
    draw.text(((1280 - sw) / 2, 390), sub, fill=(150, 176, 160), font=small)
    image.save(CARD_PATH)


def _video() -> None:
    _card()
    subprocess.run(
        [
            str(FFMPEG),
            "-y",
            "-loop",
            "1",
            "-i",
            str(CARD_PATH),
            "-i",
            str(WAV_PATH),
            "-c:v",
            "libx264",
            "-tune",
            "stillimage",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-shortest",
            "-movflags",
            "+faststart",
            str(MP4_PATH),
        ],
        check=True,
        capture_output=True,
    )


def _overlap(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    import difflib

    return difflib.SequenceMatcher(None, a, b).ratio()


def _sentence_translation(
    translations: list[dict], asr_finals: list[dict], end: float
) -> str | None:
    """Rebuild one sentence from the final translations of its utterance(s).

    The soft cut in ``vad.py`` can close a long sentence as two utterances, so
    the first final for a sentence may only cover its first half and the halves
    have to be joined. Character overlap cannot tell a continuation from the
    next sentence -- the two halves of a short Chinese sentence barely overlap
    -- so membership comes from the ASR side: an utterance sends its ``asr``
    message before its ``translation``, and a translation belongs to whichever
    utterance end most recently preceded it.
    """
    marks = sorted(float(e["audio_sec"]) for e in asr_finals)
    if not marks:
        return None
    low, high = end - 3.0, end + 2.0
    picked = ""
    for event in translations:
        if not event.get("final"):
            continue
        at = float(event["audio_sec"])
        slot = bisect.bisect_right(marks, at) - 1
        if slot < 0 or not low <= marks[slot] < high:
            continue
        text = str(event.get("text") or "")
        if not text:
            continue
        if not picked:
            picked = text
        elif text.startswith(picked) or picked.startswith(text):
            # The same sentence repeated, or a shorter revision of it.
            if len(text) > len(picked):
                picked = text
        elif _overlap(picked, text) >= 0.45:
            picked = text
        else:
            picked += text
    return picked or None


def _summarize(events: list[dict], timing: list[dict]) -> dict:
    translations = [e for e in events if e.get("type") == "translation"]
    asr_final = [e for e in events if e.get("type") == "asr" and e.get("final")]
    shrinks = 0
    swaps = 0
    growths = 0
    shown = ""
    for event in translations:
        text = str(event.get("text") or "")
        if shown and shown.startswith(text) and len(text) < len(shown):
            shrinks += 1
        elif shown and text.startswith(shown) and len(text) > len(shown):
            growths += 1
        elif shown and _overlap(shown, text) < 0.45:
            swaps += 1
        if text:
            shown = text
    rows = []
    for row in timing:
        end = row["end"]
        first = next((e for e in translations if e["audio_sec"] >= row["start"]), None)
        asr = next((e for e in asr_final if e["audio_sec"] >= end - 0.2 and e["audio_sec"] < end + 4), None)
        rows.append(
            {
                "i": row["i"],
                "source": row["text"],
                "asr": None if asr is None else asr.get("text"),
                "asr_ratio": None if asr is None else round(_overlap(row["text"].lower(), str(asr.get("text") or "").lower()), 3),
                "translation": _sentence_translation(translations, asr_final, end),
                "lag_ms": None
                if first is None
                else int(max(0.0, first["audio_sec"] - end) * 1000),
            }
        )
    return {
        "translation_events": len(translations),
        "growths": growths,
        "shrinks": shrinks,
        "low_overlap_swaps": swaps,
        "asr_finals": len(asr_final),
        "sentences": rows,
    }


async def _play(timing: list[dict]) -> dict:
    audio, rate = sf.read(_sample_wav(), dtype="float32", always_2d=False)
    if rate != SAMPLE_RATE:
        audio = _resample(audio, rate)
    pcm = np.clip(audio * 32767.0, -32768, 32767).astype(np.int16)
    hop = int(SAMPLE_RATE * 0.1)
    events: list[dict] = []
    state = {"audio_sec": 0.0}

    async with connect(f"ws://{WS_HOST}:{WS_PORT}/ws") as ws:
        await ws.send(json.dumps({"type": "config", "language": "en"}))
        # The extension sends this when capture starts (offscreen.js). Without
        # it the caption window stays closed after the first disconnect and
        # every later run would record zero paints.
        await ws.send(json.dumps({"type": "arm"}))

        async def reader() -> None:
            async for message in ws:
                payload = json.loads(message)
                payload["audio_sec"] = round(state["audio_sec"], 3)
                payload["wall"] = round(time.time(), 3)
                events.append(payload)
                kind = payload.get("type")
                final = bool(payload.get("final"))
                if kind == "status" or (kind in ("translation", "asr") and final):
                    text = payload.get("text") or payload.get("message") or ""
                    print(f"{state['audio_sec']:6.1f}s {kind}: {text}", flush=True)

        task = asyncio.create_task(reader())
        started = time.perf_counter()
        try:
            for start in range(0, pcm.shape[0], hop):
                state["audio_sec"] = start / SAMPLE_RATE
                await ws.send(pcm[start : start + hop].tobytes())
                target = started + (start + hop) / SAMPLE_RATE
                delay = target - time.perf_counter()
                if delay > 0:
                    await asyncio.sleep(delay)
            state["audio_sec"] = pcm.shape[0] / SAMPLE_RATE
            await ws.send(json.dumps({"type": "flush"}))
            await asyncio.sleep(15)
        except Exception as exc:
            print(f"play stopped at {state['audio_sec']:.1f}s: {exc}", flush=True)
        finally:
            task.cancel()
    return _summarize(events, timing) | {"events": events, "duration_sec": round(pcm.shape[0] / SAMPLE_RATE, 3)}


def main() -> None:
    play_only = len(sys.argv) > 1 and sys.argv[1] == "play"
    if play_only:
        timing = json.loads(_sample_timing().read_text(encoding="utf-8"))
        # Fail here, before six minutes of audio has played, rather than in the
        # summary at the end.
        #
        # _summarize needs each row to carry its index, and a timeline built by
        # hand or by a different tool will not have it. The six-minute run played
        # the whole clip and then raised KeyError on a field it could have checked
        # at the start.
        missing = [k for k in ("start", "end", "text") if not timing or k not in timing[0]]
        if missing:
            raise SystemExit(
                f"{_sample_timing()} rows are missing {missing}; each row needs start, end, text"
            )
        # Everything the summary and the latency probes assume about the timeline,
        # checked here rather than discovered at the end of a full-length clip.
        bad: list[str] = []
        previous_end = None
        for n, row in enumerate(timing):
            try:
                start, end = float(row["start"]), float(row["end"])
            except (TypeError, ValueError):
                bad.append(f"row {n}: start/end are not numbers")
                continue
            if end <= start:
                bad.append(f"row {n}: end {end} is not after start {start}")
            if previous_end is not None and start < previous_end - 1e-6:
                bad.append(f"row {n}: starts at {start}, before the previous row ended")
            previous_end = max(previous_end or end, end)
        if bad:
            raise SystemExit(
                f"{_sample_timing()} is not a usable timeline:\n  " + "\n  ".join(bad[:8])
            )
        clip = sf.info(_sample_wav()).duration
        if previous_end is not None and previous_end > clip + 1.0:
            raise SystemExit(
                f"timeline ends at {previous_end:.1f}s but the clip is only {clip:.1f}s; "
                "the zero points would be measured against speech that does not exist"
            )
        if "i" not in timing[0]:
            for n, row in enumerate(timing):
                row["i"] = n
            print(
                f"timeline had no 'i' column; numbered {len(timing)} rows in order",
                flush=True,
            )
        duration = sf.info(_sample_wav()).duration
    else:
        duration = 0.0
        timing = []
        for rate in (-2, -4, 0):
            timing = _build_wav(rate)
            duration = sf.info(WAV_PATH).duration
            print(f"speech rate {rate}: {duration:.1f}s", flush=True)
            if 165 <= duration <= 210:
                break
        _video()
    print(f"audio {_sample_wav()} {duration:.1f}s", flush=True)
    result = asyncio.run(_play(timing))
    result["video"] = str(MP4_PATH)
    result["wav"] = str(WAV_PATH)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    RESULT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"events={result['translation_events']} growth={result['growths']} "
        f"shrink={result['shrinks']} swap={result['low_overlap_swaps']}",
        flush=True,
    )


if __name__ == "__main__":
    main()
