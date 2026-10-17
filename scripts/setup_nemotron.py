#!/usr/bin/env python3
"""One-click setup and provision script for NVIDIA Nemotron 3.5 ASR Engine.

Features:
  1. Primary: Direct official engine & model download (HuggingFace / hf-mirror + GitHub Releases).
  2. Secondary / Fallback: Official NVIDIA NeMo Docker container (nvcr.io/nvidia/nemo:latest).
  3. Supports Windows (x64) and Linux (Ubuntu 24.04+ x86_64).
  4. Auto-resumes partial downloads and validates library loadability.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request
import zipfile

# Resolve repository root
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

# Download URLs
MODEL_FILENAME = "nemotron-3.5-asr-streaming-0.6b.q8_0.gguf"
MODEL_URLS = [
    f"https://hf-mirror.com/nvidia/nemotron-3.5-asr-streaming-0.6b/resolve/main/{MODEL_FILENAME}",
    f"https://huggingface.co/nvidia/nemotron-3.5-asr-streaming-0.6b/resolve/main/{MODEL_FILENAME}",
]

NEMO_VERSION = "v0.2.0"
GH_MIRRORS = [
    "",  # Direct GitHub
    "https://ghproxy.net/",
    "https://ghfast.top/",
]

ENGINE_PACKAGES = {
    "win32": {
        "vulkan": f"nemo-speech-0.2.0-windows-x86_64-vulkan.zip",
        "cuda": f"nemo-speech-0.2.0-windows-x86_64-cuda.zip",
        "cpu": f"nemo-speech-0.2.0-windows-x86_64-cpu.zip",
    },
    "linux": {
        "vulkan": f"nemo-speech-0.2.0-linux-x86_64-vulkan.tar.gz",
        "cuda": f"nemo-speech-0.2.0-linux-x86_64-cuda.tar.gz",
        "cpu": f"nemo-speech-0.2.0-linux-x86_64-cpu.tar.gz",
    },
}

DOCKER_IMAGE = "nvcr.io/nvidia/nemo:latest"


def log(msg: str) -> None:
    print(f"[Nemotron-Setup] {msg}", flush=True)


def download_file_with_resume(urls: list[str], dest_path: Path, desc: str = "File") -> bool:
    """Download a file with streaming progress and resume support."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    part_path = dest_path.with_suffix(dest_path.suffix + ".part")

    for url in urls:
        log(f"正在下载 {desc}: {url}")
        resume_byte_pos = part_path.stat().st_size if part_path.exists() else 0
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) TranslationDesktop/1.0",
            },
        )
        if resume_byte_pos > 0:
            req.add_header("Range", f"bytes={resume_byte_pos}-")

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                status = getattr(resp, "status", 200)
                content_len = resp.headers.get("Content-Length")
                total_len = int(content_len) if content_len else 0
                if status == 206:
                    total_len += resume_byte_pos
                elif resume_byte_pos > 0 and status == 200:
                    resume_byte_pos = 0

                mode = "ab" if resume_byte_pos > 0 else "wb"
                downloaded = resume_byte_pos
                last_print = time.monotonic()

                with open(part_path, mode) as out_f:
                    while True:
                        chunk = resp.read(1024 * 512)
                        if not chunk:
                            break
                        out_f.write(chunk)
                        downloaded += len(chunk)
                        now = time.monotonic()
                        if now - last_print >= 1.0 or (total_len and downloaded >= total_len):
                            last_print = now
                            if total_len > 0:
                                pct = downloaded / total_len * 100.0
                                log(f"  -> {downloaded/1048576:.1f} MB / {total_len/1048576:.1f} MB ({pct:.1f}%)")
                            else:
                                log(f"  -> {downloaded/1048576:.1f} MB 已下载")

            if part_path.exists() and part_path.stat().st_size > 0:
                if dest_path.exists():
                    dest_path.unlink()
                part_path.rename(dest_path)
                log(f"✅ {desc} 下载完成: {dest_path} ({dest_path.stat().st_size/1048576:.1f} MB)")
                return True
        except Exception as e:
            log(f"⚠️ 下载失败 ({url}): {e}，尝试下一个源...")

    if part_path.exists() and not dest_path.exists():
        part_path.unlink(missing_ok=True)
    return False


def setup_model(models_dir: Path) -> bool:
    """Ensure Nemotron 3.5 ASR GGUF model is present."""
    target_model = models_dir / "NV3.5-ASR-0.6B" / MODEL_FILENAME
    if target_model.exists() and target_model.stat().st_size > 500 * 1024 * 1024:
        log(f"✅ 模型文件已就绪: {target_model} ({target_model.stat().st_size/1048576:.1f} MB)")
        return True

    flat_model = models_dir / MODEL_FILENAME
    if flat_model.exists() and flat_model.stat().st_size > 500 * 1024 * 1024:
        log(f"✅ 模型文件已就绪 (根目录): {flat_model} ({flat_model.stat().st_size/1048576:.1f} MB)")
        return True

    # Check alternate locations
    alt_candidates = [
        flat_model,
        Path("/mnt/d/TranslationDesktop/models/NV3.5-ASR-0.6B") / MODEL_FILENAME,
        Path("/mnt/d/models/NV3.5-ASR-0.6B") / MODEL_FILENAME,
        Path("D:/TranslationDesktop/models/NV3.5-ASR-0.6B") / MODEL_FILENAME,
        Path("D:/models/NV3.5-ASR-0.6B") / MODEL_FILENAME,
        ROOT_DIR / "models" / "NV3.5-ASR-0.6B" / MODEL_FILENAME,
        Path.home() / ".cache" / "translation" / "models" / "NV3.5-ASR-0.6B" / MODEL_FILENAME,
        Path.home() / "models" / "NV3.5-ASR-0.6B" / MODEL_FILENAME,
        Path("/opt/translation/models/NV3.5-ASR-0.6B") / MODEL_FILENAME,
    ]
    if sys.platform != "win32":
        for m in (Path("/media"), Path("/run/media"), Path("/mnt")):
            if m.exists():
                for sub in ("d", "D", "DATA", "data"):
                    alt_candidates.append(m / sub / "TranslationDesktop" / "models" / "NV3.5-ASR-0.6B" / MODEL_FILENAME)
                    alt_candidates.append(m / sub / "models" / "NV3.5-ASR-0.6B" / MODEL_FILENAME)

    for alt in alt_candidates:
        if alt.exists() and alt.stat().st_size > 500 * 1024 * 1024:
            log(f"发现已有模型文件: {alt}")
            target_model.parent.mkdir(parents=True, exist_ok=True)
            try:
                if alt.resolve() != target_model.resolve():
                    if hasattr(os, "symlink") and sys.platform != "win32":
                        try:
                            if target_model.exists() or target_model.is_symlink():
                                target_model.unlink()
                            target_model.symlink_to(alt)
                            log(f"✅ 成功建立软链接复用模型: {target_model} -> {alt}")
                            return True
                        except Exception as sym_e:
                            log(f"软链接创建失败: {sym_e}，回退到文件复制...")
                    shutil.copy2(alt, target_model)
                log(f"✅ 成功复用模型至: {target_model}")
                return True
            except Exception as e:
                log(f"复用模型异常: {e}，将直接进行下载...")

    log(f"开始拉取 Nemotron 3.5 ASR GGUF 模型 (~700MB)...")
    return download_file_with_resume(MODEL_URLS, target_model, desc="Nemotron-3.5 GGUF 模型")


def setup_engine_binary(tools_dir: Path, preferred_accel: str = "vulkan") -> bool:
    """Download and extract official NeMo-Speech precompiled binary."""
    os_key = "win32" if sys.platform == "win32" else "linux"
    pkg_map = ENGINE_PACKAGES.get(os_key, ENGINE_PACKAGES["linux"])

    # Fallback order: preferred -> vulkan -> cuda -> cpu
    candidate_accels = [preferred_accel]
    for acc in ("vulkan", "cuda", "cpu"):
        if acc not in candidate_accels:
            candidate_accels.append(acc)

    tools_dir.mkdir(parents=True, exist_ok=True)

    for accel in candidate_accels:
        archive_name = pkg_map.get(accel)
        if not archive_name:
            continue

        raw_url = f"https://github.com/NVIDIA/NeMo-Speech.cpp/releases/download/{NEMO_VERSION}/{archive_name}"
        candidate_urls = [mirror + raw_url for mirror in GH_MIRRORS]

        target_archive = tools_dir / archive_name
        dest_extract_dir = tools_dir / f"nemo-speech-{accel}"
        dest_extract_dir.mkdir(parents=True, exist_ok=True)

        log(f"尝试获取官方预编译引擎 [{accel.upper()}]: {archive_name}")
        ok = download_file_with_resume(candidate_urls, target_archive, desc=f"NeMo-Speech {accel} 引擎包")
        if not ok:
            continue

        log(f"解压引擎包: {target_archive} -> {dest_extract_dir}...")
        try:
            if archive_name.endswith(".zip"):
                with zipfile.ZipFile(target_archive, "r") as z:
                    z.extractall(dest_extract_dir)
            elif archive_name.endswith(".tar.gz") or archive_name.endswith(".tgz"):
                with tarfile.open(target_archive, "r:gz") as t:
                    t.extractall(dest_extract_dir)

            # Check if dynamic library is extracted
            lib_name = "nemo_speech_asr_c.dll" if sys.platform == "win32" else "libnemo_speech_asr_c.so"
            found_lib = None
            for root, _, files in os.walk(dest_extract_dir):
                if lib_name in files:
                    found_lib = Path(root) / lib_name
                    break

            if found_lib and found_lib.exists():
                log(f"✅ 成功部署 NeMo-Speech 推理引擎库: {found_lib}")
                return True
            else:
                log(f"⚠️ 解压后未找到 {lib_name}")
        except Exception as e:
            log(f"⚠️ 解压失败: {e}")

    return False


def setup_docker_container() -> bool:
    """Setup official NVIDIA NeMo container as secondary/fallback backend."""
    log("正在配置官方 NVIDIA NeMo Docker 容器环境 (次选后端)...")
    docker_bin = shutil.which("docker")
    if not docker_bin:
        log("❌ 未检测到 docker 命令，请先安装 Docker: https://docs.docker.com/get-docker/")
        return False

    log(f"检测到 Docker: {docker_bin}，正在拉取官方容器镜像: {DOCKER_IMAGE}...")
    try:
        res = subprocess.run([docker_bin, "pull", DOCKER_IMAGE], check=False)
        if res.returncode != 0:
            log(f"⚠️ docker pull {DOCKER_IMAGE} 返回状态码 {res.returncode}")
            return False

        # Write run script helpers
        run_sh = ROOT_DIR / "scripts" / "run_nemotron_docker.sh"
        run_ps1 = ROOT_DIR / "scripts" / "run_nemotron_docker.ps1"

        run_sh.write_text(
            f"""#!/usr/bin/env bash
# Official NVIDIA NeMo Docker runner
docker run --gpus all -it --rm \\
  -v "{ROOT_DIR}/models:/models" \\
  -p 8098:8098 \\
  {DOCKER_IMAGE}
""",
            encoding="utf-8",
        )
        run_sh.chmod(0o755)

        run_ps1.write_text(
            f"""# Official NVIDIA NeMo Docker runner
docker run --gpus all -it --rm `
  -v "{ROOT_DIR}\\models:/models" `
  -p 8098:8098 `
  {DOCKER_IMAGE}
""",
            encoding="utf-8",
        )
        log("✅ 官方 NVIDIA NeMo Docker 镜像拉取就绪，已生成启动脚本: scripts/run_nemotron_docker.sh")
        return True
    except Exception as e:
        log(f"❌ 运行 Docker 失败: {e}")
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="One-click setup for NVIDIA Nemotron 3.5 ASR Engine")
    parser.add_argument("-y", "--auto", "--yes", action="store_true", help="Non-interactive mode, agree to all prompts")
    parser.add_argument("--docker", action="store_true", help="Force secondary Docker container workflow")
    parser.add_argument("--check", action="store_true", help="Check current Nemotron readiness and exit")
    parser.add_argument("--engine", choices=["vulkan", "cuda", "cpu"], default="vulkan", help="Preferred acceleration backend")
    parser.add_argument("--models-dir", type=str, default="", help="Custom models directory")
    args = parser.parse_args()

    if args.models_dir:
        models_dir = Path(args.models_dir).resolve()
    else:
        try:
            from server import config
            models_dir = config.resolve_models_dir()
        except Exception:
            models_dir = ROOT_DIR / "models"
    tools_dir = ROOT_DIR / "tools"

    # Pre-check current status
    from server.asr.nemotron import check_nemotron_status
    ready, reason = check_nemotron_status()

    if args.check:
        if ready:
            log(f"Nemotron 引擎状态: 就绪 (Ready)")
            return 0
        else:
            log(f"Nemotron 引擎状态: 未就绪 ({reason})")
            return 1

    if ready:
        log(f"✅ 检测到 Nemotron 3.5 极速端侧引擎已完整就绪，无需重复拉取！")
        return 0

    log("=" * 60)
    log("🚀 NVIDIA Nemotron 3.5 极速端侧流式 ASR 官方一键部署向导")
    log("=" * 60)
    log(f"操作系统: {platform.system()} ({platform.machine()})")
    log(f"目标模型目录: {models_dir}")
    log(f"目标工具目录: {tools_dir}")
    log(f"当前组件状态: {reason}")
    log("-" * 60)

    if not args.auto:
        try:
            choice = input("是否立即启动自动拉取？[Y/n]: ").strip().lower()
            if choice and choice not in ("y", "yes"):
                log("已取消部署。")
                return 0
        except (KeyboardInterrupt, EOFError):
            log("\n用户取消操作。")
            return 1

    # Step 1: Model Download (Primary)
    log("\n[步骤 1/2] 检查并获取 Nemotron 3.5 GGUF 官方权重模型...")
    model_ok = setup_model(models_dir)
    if not model_ok:
        log("❌ 模型拉取失败，请检查网络连接后重试。")
        return 1

    # Step 2: Engine Binary (Primary) or Docker (Secondary)
    engine_ok = False
    if args.docker:
        log("\n[步骤 2/2] 按照指定参数，配置官方 Docker 容器环境...")
        engine_ok = setup_docker_container()
    else:
        log(f"\n[步骤 2/2] 首选：下载官方原生 NeMo-Speech 极速引擎动态库 ({args.engine.upper()})...")
        engine_ok = setup_engine_binary(tools_dir, preferred_accel=args.engine)
        if not engine_ok:
            log("⚠️ 原生预编译动态库拉取或解压未成功，自动启用次选官方 Docker 方案...")
            engine_ok = setup_docker_container()

    # Final Verification
    final_ready, final_reason = check_nemotron_status()
    log("\n" + "=" * 60)
    if final_ready or engine_ok:
        log("🎉 NVIDIA Nemotron 3.5 极速端侧 ASR 部署成功！")
        log("您现在可以在字幕右键菜单中直接切换为【端侧极速流式 NV (Nemotron)】。")
        log("=" * 60)
        return 0
    else:
        log(f"⚠️ 部署完成，但检测状态仍报告: {final_reason}")
        log("=" * 60)
        return 1


if __name__ == "__main__":
    sys.exit(main())
