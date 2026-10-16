"""Unit tests for SSOT serve_config memory control and slot eviction parameters.

Ensures that:
1. CACHE_RAM_MB is strictly capped at 512 MiB (preventing 8GB default memory ballooning).
2. CTX_CHECKPOINTS is strictly set to 1 (preventing slot clone explosion).
3. NO_CACHE_IDLE_SLOTS is True (--no-cache-idle-slots).
4. get_qwen_server_args and get_mt_server_args always include these flags.
5. llama.exe prepends 'serve' subcommand, while llama-server.exe does not.
"""

from pathlib import Path
import pytest
from server.serve_config import (
    CACHE_RAM_MB,
    CTX_CHECKPOINTS,
    NO_CACHE_IDLE_SLOTS,
    get_common_memory_args,
    get_qwen_server_args,
    get_mt_server_args,
)


def test_ssot_memory_constants():
    assert CACHE_RAM_MB == 512
    assert CTX_CHECKPOINTS == 1
    assert NO_CACHE_IDLE_SLOTS is True


def test_get_common_memory_args():
    args = get_common_memory_args()
    assert "--cache-ram" in args
    assert args[args.index("--cache-ram") + 1] == "512"
    assert "--ctx-checkpoints" in args
    assert args[args.index("--ctx-checkpoints") + 1] == "1"
    assert "--no-cache-idle-slots" in args


def test_get_qwen_server_args_llama_exe():
    exe = Path("C:/Tools/llama.exe")
    args = get_qwen_server_args(
        exe=exe,
        model_path=Path("models/qwen.gguf"),
        mmproj_path=Path("models/mmproj.gguf"),
        host="127.0.0.1",
        port=8099,
        threads=4,
    )
    # Must have 'serve' subcommand right after llama.exe
    assert args[0] == str(exe)
    assert args[1] == "serve"

    # Must contain memory safety flags
    assert "--cache-ram" in args
    assert args[args.index("--cache-ram") + 1] == "512"
    assert "--ctx-checkpoints" in args
    assert args[args.index("--ctx-checkpoints") + 1] == "1"
    assert "--no-cache-idle-slots" in args


def test_get_qwen_server_args_llama_server_exe():
    exe = Path("C:/Tools/llama-server.exe")
    args = get_qwen_server_args(
        exe=exe,
        model_path=Path("models/qwen.gguf"),
        mmproj_path=Path("models/mmproj.gguf"),
        host="127.0.0.1",
        port=8099,
        threads=4,
    )
    # llama-server does NOT have 'serve' subcommand
    assert args[0] == str(exe)
    assert args[1] != "serve"

    # Must contain memory safety flags
    assert "--cache-ram" in args
    assert args[args.index("--cache-ram") + 1] == "512"
    assert "--ctx-checkpoints" in args
    assert args[args.index("--ctx-checkpoints") + 1] == "1"
    assert "--no-cache-idle-slots" in args


def test_get_mt_server_args_llama_exe():
    exe = Path("C:/Tools/llama.exe")
    args = get_mt_server_args(
        exe=exe,
        model_path=Path("models/mt.gguf"),
        host="127.0.0.1",
        port=8080,
        is_index=True,
        threads=4,
    )
    assert args[0] == str(exe)
    assert args[1] == "serve"

    assert "--cache-ram" in args
    assert args[args.index("--cache-ram") + 1] == "512"
    assert "--ctx-checkpoints" in args
    assert args[args.index("--ctx-checkpoints") + 1] == "1"
    assert "--no-cache-idle-slots" in args


def test_get_mt_server_args_llama_server_exe():
    exe = Path("C:/Tools/llama-server.exe")
    args = get_mt_server_args(
        exe=exe,
        model_path=Path("models/mt.gguf"),
        host="127.0.0.1",
        port=8080,
        is_index=False,
        threads=4,
    )
    assert args[0] == str(exe)
    assert args[1] != "serve"

    assert "--cache-ram" in args
    assert args[args.index("--cache-ram") + 1] == "512"
    assert "--ctx-checkpoints" in args
    assert args[args.index("--ctx-checkpoints") + 1] == "1"
    assert "--no-cache-idle-slots" in args
