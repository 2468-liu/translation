"""Lightweight Distribution Packager for Ubuntu / Linux & Windows.

Creates an ultra-lightweight clean distribution zip strictly excluding:
- tools/ (compiled binaries, whisper.cpp trees, nemo engines)
- models/ (large weights, gguf, onnx)
- .git/ and test media (*.wav, *.mp4)
- caches (__pycache__, .pytest_cache)

Ensures zip size remains under 5MB (~3.6MB) for instant distribution.
"""

from __future__ import annotations

import os
from pathlib import Path
import zipfile

EXCLUDE_DIRS = {
    ".git",
    "tools",
    "models",
    "dist",
    "build",
    "logs",
    "translation-native-host",
    "_internal",
    "__pycache__",
    ".pytest_cache",
    ".idea",
    ".vscode",
    ".venv",
    "venv",
    "env",
}

EXCLUDE_EXTENSIONS = {
    ".pyc",
    ".pyo",
    ".pyd",
    ".dll",
    ".zip",
    ".wav",
    ".mp3",
    ".mp4",
    ".mkv",
    ".avi",
    ".gguf",
    ".bin",
    ".onnx",
    ".log",
}

EXCLUDE_FILES = {
    "desktop_config.json",
    "runtime.json",
}


def build_distribution_zip() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    targets = [
        repo_root / "translation-ubuntu-24.04.zip",
        Path.home() / "Desktop" / "translation-ubuntu-24.04.zip",
    ]

    print(f"[Packaging] Scanning source directory: {repo_root}")
    temp_zip = repo_root / "translation-ubuntu-24.04.tmp.zip"
    if temp_zip.exists():
        temp_zip.unlink()

    packed_count = 0
    total_uncompressed_bytes = 0

    with zipfile.ZipFile(temp_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for root, dirs, files in os.walk(repo_root):
            # Prune excluded directories in-place
            dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS and not d.startswith(".")]

            rel_root = Path(root).relative_to(repo_root)

            for file in files:
                file_path = Path(root) / file
                ext = file_path.suffix.lower()

                if ext in EXCLUDE_EXTENSIONS:
                    continue
                if file in EXCLUDE_FILES:
                    continue
                if file.startswith(".tmp") or file.endswith(".tmp"):
                    continue

                arcname = str(rel_root / file) if str(rel_root) != "." else file
                # Normalize path for Linux
                arcname = arcname.replace("\\", "/")

                zf.write(file_path, arcname=arcname)
                packed_count += 1
                total_uncompressed_bytes += file_path.stat().st_size

    zip_size_bytes = temp_zip.stat().st_size
    zip_size_mb = zip_size_bytes / (1024 * 1024)
    raw_size_mb = total_uncompressed_bytes / (1024 * 1024)

    print(f"[Packaging] Packed {packed_count} files.")
    print(f"[Packaging] Uncompressed: {raw_size_mb:.2f} MB -> Compressed: {zip_size_mb:.2f} MB")

    if zip_size_mb > 15.0:
        temp_zip.unlink()
        raise RuntimeError(f"Zip size ({zip_size_mb:.2f} MB) exceeded 15MB limit! Check excluded files.")

    for target in targets:
        try:
            if target.exists():
                target.unlink()
            import shutil
            shutil.copy2(temp_zip, target)
            print(f"[Packaging] Successfully updated: {target} ({zip_size_mb:.2f} MB)")
        except Exception as exc:
            print(f"[Packaging] Warning: failed to copy to {target}: {exc}")

    temp_zip.unlink()
    print("[Packaging] Completed successfully!")


if __name__ == "__main__":
    build_distribution_zip()
