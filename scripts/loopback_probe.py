"""Capture default Windows speaker output through WASAPI loopback."""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "samples" / "loopback_test.wav"


def find_loopback_microphone(soundcard, speaker):
    microphones = soundcard.all_microphones(include_loopback=True)
    for microphone in microphones:
        if microphone.name == speaker.name:
            return microphone
    for microphone in microphones:
        if speaker.name in microphone.name or microphone.name in speaker.name:
            return microphone
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Record default speaker output without tabCapture.")
    parser.add_argument("--seconds", type=float, default=5.0)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.seconds <= 0:
        parser.error("--seconds must be positive")

    try:
        import soundcard as sc
    except ImportError:
        print("Missing dependency: python -m pip install soundcard")
        return 2

    speaker = sc.default_speaker()
    if speaker is None:
        print("No default Windows speaker was found.")
        return 2
    microphone = find_loopback_microphone(sc, speaker)
    if microphone is None:
        print(f"No WASAPI loopback microphone found for: {speaker.name}")
        print("Available loopback devices:")
        for item in sc.all_microphones(include_loopback=True):
            print(f"  - {item.name}")
        return 2

    sample_rate = 16_000
    frame_count = round(args.seconds * sample_rate)
    print(f"Speaker: {speaker.name}")
    print(f"Loopback: {microphone.name}")
    print(f"Recording {args.seconds:.1f}s at {sample_rate} Hz...")
    with microphone.recorder(samplerate=sample_rate, channels=2) as recorder:
        data = np.asarray(recorder.record(numframes=frame_count), dtype=np.float32)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        import soundfile as sf
    except ImportError:
        print("Captured audio, but soundfile is missing; install it to save WAV output.")
        print(f"Captured shape: {data.shape}")
        return 2
    sf.write(args.output, data, sample_rate, subtype="PCM_16")
    peak = float(np.max(np.abs(data))) if data.size else 0.0
    print(f"Saved: {args.output}")
    print(f"Captured shape: {data.shape}, peak: {peak:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
