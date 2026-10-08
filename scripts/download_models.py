"""Download ASR and translation weights from ModelScope into ./models.

Three downloaders used to live here and were unreachable from main(): Qwen2.5-3B
GGUF and the Qwen3.5-4B MTP draft, both superseded by Hy-MT2, and NLLB-600M,
which fed the CTranslate2 translator removed from server/. They were dead rather
than harmless, because the Gemma 4 E2B weights that the gemma4 backend needs were
never added here -- so the file's list of what the project runs on was wrong in
both directions at once. Adding the gemma4 fetch is a separate decision: those
weights are already on disk and start.ps1 expects them at a fixed path.
"""

from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODELS = ROOT / "models"
ASR_DIR = MODELS / "faster-whisper-small"
HYMT_DIR = MODELS / "hy-mt2-1.8b"
HYMT_FILE = "Hy-MT2-1.8B-Q4_K_M.gguf"
SILERO_PATH = MODELS / "silero_vad.onnx"

SILERO_URLS = [
    "https://cdn.jsdelivr.net/gh/snakers4/silero-vad@master/src/silero_vad/data/silero_vad.onnx",
    "https://github.com/snakers4/silero-vad/raw/master/src/silero_vad/data/silero_vad.onnx",
]


def download_asr() -> None:
    from modelscope.hub.snapshot_download import snapshot_download

    marker = ASR_DIR / "model.bin"
    if marker.exists() and marker.stat().st_size > 1_000_000:
        print(f"ASR already present: {marker}")
        return
    print("downloading pengzhendong/faster-whisper-small")
    snapshot_download("pengzhendong/faster-whisper-small", local_dir=str(ASR_DIR))
    print(f"ASR ready: {marker}")


def download_hymt() -> None:
    from modelscope.hub.snapshot_download import snapshot_download

    HYMT_DIR.mkdir(parents=True, exist_ok=True)
    marker = HYMT_DIR / HYMT_FILE
    if marker.exists() and marker.stat().st_size > 800_000_000:
        print(f"Hy-MT2 already present: {marker}")
        return
    print("downloading Tencent-Hunyuan/Hy-MT2-1.8B-GGUF Q4_K_M")
    snapshot_download(
        "Tencent-Hunyuan/Hy-MT2-1.8B-GGUF",
        local_dir=str(HYMT_DIR),
        allow_patterns=[HYMT_FILE],
    )
    print(f"Hy-MT2 ready: {marker} ({marker.stat().st_size} bytes)")


def download_silero() -> None:
    if SILERO_PATH.exists() and SILERO_PATH.stat().st_size > 100_000:
        print(f"Silero already present: {SILERO_PATH}")
        return
    MODELS.mkdir(parents=True, exist_ok=True)
    for url in SILERO_URLS:
        try:
            print(f"downloading silero vad: {url}")
            urllib.request.urlretrieve(url, SILERO_PATH)
            if SILERO_PATH.stat().st_size > 100_000:
                print(f"Silero ready: {SILERO_PATH}")
                return
        except Exception as exc:
            print(f"silero download failed: {exc}")
    print("Silero unavailable, server will use energy VAD")


def main() -> None:
    download_asr()
    download_silero()
    download_hymt()
    print("done")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
