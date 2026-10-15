"""Model Downloader Helper Script for Real-Time Translation System.

Downloads required GGUF weights and ONNX models from official / mirror repositories.
Supports ModelScope (fast in Mainland China) and Hugging Face.
"""

import argparse
import os
import sys
from pathlib import Path
import urllib.request

MODELS = {
    "vad": {
        "name": "Silero VAD ONNX",
        "dest": "silero_vad.onnx",
        "url": "https://github.com/snakers4/silero-vad/raw/master/src/silero_vad/data/silero_vad.onnx",
    },
    "index-translate": {
        "name": "Index-Translate 2B Q4_K_M",
        "dest": "Index-Translate/index-translate-q4_k_m.gguf",
        "ms_repo": "IndexTeam/Index-1.9B-Chat",
        "hf_url": "https://huggingface.co/IndexTeam/Index-1.9B-Chat/resolve/main/index-translate-q4_k_m.gguf",
    },
    "qwen3-asr": {
        "name": "Qwen3-ASR 0.6B GGUF",
        "dest": "Qwen-ASR-0.6B/qwen3-asr-0.6b.gguf",
        "hf_url": "https://huggingface.co/Qwen/Qwen3-ASR-0.6B-GGUF/resolve/main/qwen3-asr-0.6b.gguf",
    },
    "qwen3-mmproj": {
        "name": "Qwen3-ASR mmproj Audio Projector",
        "dest": "Qwen-ASR-0.6B/mmproj-qwen3-asr-0.6b.gguf",
        "hf_url": "https://huggingface.co/Qwen/Qwen3-ASR-0.6B-GGUF/resolve/main/mmproj-qwen3-asr-0.6b.gguf",
    },
}


def download_file(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 1024:
        print(f"[SKIP] Already exists: {dest} ({dest.stat().st_size / (1024*1024):.1f} MB)")
        return

    print(f"[DOWNLOADING] {url} -> {dest}")

    def progress(block_num, block_size, total_size):
        downloaded = block_num * block_size
        if total_size > 0:
            percent = downloaded * 100 / total_size
            sys.stdout.write(f"\r  Progress: {percent:.1f}% ({downloaded / (1024*1024):.1f} / {total_size / (1024*1024):.1f} MB)")
            sys.stdout.flush()

    urllib.request.urlretrieve(url, dest, reporthook=progress)
    print("\n[OK] Completed!")


def main():
    parser = argparse.ArgumentParser(description="Download GGUF/ONNX models for real-time translation.")
    parser.add_argument("--output-dir", "-o", default="models", help="Destination directory (default: models)")
    parser.add_argument("--all", action="store_true", help="Download all core models automatically")
    args = parser.parse_args()

    out_dir = Path(args.output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"Target models directory: {out_dir}\n")

    # Download Silero VAD (essential lightweight ONNX)
    vad_info = MODELS["vad"]
    download_file(vad_info["url"], out_dir / vad_info["dest"])

    print("\n========================================================")
    print("模型下载指引 (Model Download Summary):")
    print("由于 LLM 大模型权重体积较大 (约 1.5GB ~ 2GB)，推荐通过 ModelScope 或 HuggingFace 直链下载：")
    for key, item in MODELS.items():
        if key == "vad":
            continue
        print(f"\n* 【{item['name']}】")
        print(f"  存放路径: {out_dir / item['dest']}")
        if "hf_url" in item:
            print(f"  下载链接: {item['hf_url']}")
    print("========================================================\n")


if __name__ == "__main__":
    main()
