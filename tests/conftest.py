"""Keep the test run out of the real latency log.

``server/perf.py`` picks its path at import time, so this has to run before any
test imports it. Without this, running the suite drops a few ``pipeline.asr``
lines into ``logs/perf.log`` -- a fake recogniser reporting ``chars=5 lang=en``
-- and they land as their own short block just before the next replay, which is
enough to make a metrics run measure the test run instead of the replay.
"""

import os
import tempfile
from pathlib import Path

import sys

os.environ.setdefault(
    "TRANSLATION_PERF_LOG",
    str(Path(tempfile.gettempdir()) / "translation-test-perf.log"),
)

if sys.platform.startswith("linux"):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("GDK_BACKEND", "x11")
    os.environ.setdefault("QT_LOGGING_RULES", "*.debug=false;qt.qpa.*=false;qt.text.font.*=false")