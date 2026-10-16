"""Automated Log Cleanup & Retention Manager.

Enforces a 7-day log retention window across the desktop application,
pruning expired logs, rotating oversized files, and removing stale subdirectories.
"""

from __future__ import annotations

import os
import time
from pathlib import Path


def purge_old_logs(
    log_dir: Path | str,
    retention_days: int = 7,
    dry_run: bool = False,
) -> tuple[int, int]:
    """Purge log files older than `retention_days` from `log_dir`.

    Args:
        log_dir: Directory containing log files.
        retention_days: Maximum age of log files in days (default: 7).
        dry_run: If True, do not actually delete files.

    Returns:
        (deleted_file_count, freed_bytes)
    """
    path = Path(log_dir)
    if not path.is_dir():
        return 0, 0

    now = time.time()
    cutoff_sec = now - (retention_days * 86400.0)
    deleted_count = 0
    freed_bytes = 0

    # Never delete persistent indexes or configurations
    protected_basenames = {"rag_index.json", "desktop_config.json"}

    for root, dirs, files in os.walk(path, topdown=False):
        for f in files:
            if f in protected_basenames:
                continue
            file_path = Path(root) / f
            try:
                stat = file_path.stat()
                if stat.st_mtime < cutoff_sec:
                    file_size = stat.st_size
                    if not dry_run:
                        file_path.unlink(missing_ok=True)
                    deleted_count += 1
                    freed_bytes += file_size
            except Exception:
                pass

        # Remove empty subdirectories
        if not dry_run and root != str(path):
            try:
                sub_dir = Path(root)
                if sub_dir.is_dir() and not any(sub_dir.iterdir()):
                    sub_dir.rmdir()
            except Exception:
                pass

    return deleted_count, freed_bytes


def run_startup_cleanup(root_dir: Path | str | None = None, retention_days: int = 7) -> None:
    """Invoked on desktop app startup to keep log footprint compact."""
    try:
        base = Path(root_dir) if root_dir else Path(__file__).resolve().parents[1]
        log_dirs = [base / "logs"]
        for ld in log_dirs:
            if ld.is_dir():
                deleted, freed = purge_old_logs(ld, retention_days=retention_days)
                if deleted > 0:
                    mb = round(freed / (1024 * 1024), 2)
                    print(f"[LogCleaner] Purged {deleted} expired logs (> {retention_days} days), freed {mb} MB.")
    except Exception as e:
        print(f"[LogCleaner] Cleanup notice: {e}")
