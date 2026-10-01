"""Download ASR and translation weights from ModelScope into ./models."""

from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODELS = ROOT / "models"
ASR_DIR = MODELS / "faster-whisper-small"
GGUF_DIR = MODELS / "qwen2.5-3b-gguf"
MTP_DIR = MODELS / "qwen3.5-4b-mtp"
MTP_FILE = "Qwen3.5-4B-UD-Q4_K_XL.gguf"
NLLB_DIR = MODELS / "nllb-200-distilled-600M"
MT_DIR = MODELS / "nllb-600m-ct2"
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


def download_gguf() -> None:
    from modelscope.hub.snapshot_download import snapshot_download

    GGUF_DIR.mkdir(parents=True, exist_ok=True)
    existing = list(GGUF_DIR.glob("*q4_k_m*.gguf"))
    if existing and existing[0].stat().st_size > 100_000_000:
        print(f"GGUF already present: {existing[0]}")
        return
    print("downloading Qwen/Qwen2.5-3B-Instruct-GGUF q4_k_m")
    snapshot_download(
        "Qwen/Qwen2.5-3B-Instruct-GGUF",
        local_dir=str(GGUF_DIR),
        allow_patterns=["qwen2.5-3b-instruct-q4_k_m.gguf", "configuration.json"],
    )
    print(f"GGUF ready: {list(GGUF_DIR.glob('*.gguf'))}")


def download_mtp() -> None:
    from modelscope.hub.snapshot_download import snapshot_download

    MTP_DIR.mkdir(parents=True, exist_ok=True)
    marker = MTP_DIR / MTP_FILE
    if marker.exists() and marker.stat().st_size > 2_000_000_000:
        print(f"MTP GGUF already present: {marker}")
        return
    print("downloading unsloth/Qwen3.5-4B-MTP-GGUF UD-Q4_K_XL")
    snapshot_download(
        "unsloth/Qwen3.5-4B-MTP-GGUF",
        local_dir=str(MTP_DIR),
        allow_patterns=[MTP_FILE],
    )
    print(f"MTP GGUF ready: {marker} ({marker.stat().st_size} bytes)")


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


def download_nllb() -> None:
    import shutil

    import ctranslate2

    MT_DIR.mkdir(parents=True, exist_ok=True)
    marker = MT_DIR / "model.bin"
    sp_src = NLLB_DIR / "sentencepiece.bpe.model"
    if marker.exists() and marker.stat().st_size > 50_000_000 and (MT_DIR / "sentencepiece.bpe.model").exists():
        print(f"NLLB CT2 already present: {marker}")
        return
    weights = NLLB_DIR / "pytorch_model.bin"
    if not weights.exists() or weights.stat().st_size < 1_000_000_000:
        from modelscope.hub.snapshot_download import snapshot_download

        print("downloading facebook/nllb-200-distilled-600M")
        snapshot_download(
            "facebook/nllb-200-distilled-600M",
            local_dir=str(NLLB_DIR),
            allow_patterns=[
                "pytorch_model.bin",
                "config.json",
                "sentencepiece.bpe.model",
                "tokenizer.json",
                "tokenizer_config.json",
                "special_tokens_map.json",
                "generation_config.json",
            ],
        )
    print("converting NLLB to CTranslate2 int8")
    converter = ctranslate2.converters.TransformersConverter(str(NLLB_DIR))
    converter.convert(str(MT_DIR), quantization="int8", force=True)
    if sp_src.exists():
        shutil.copyfile(sp_src, MT_DIR / "sentencepiece.bpe.model")
    print(f"NLLB CT2 ready: {marker} ({marker.stat().st_size} bytes)")


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
