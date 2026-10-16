"""Download real-world dialogue clips with background music for VAD testing."""

import subprocess
import sys
from pathlib import Path

import imageio_ffmpeg

FFMPEG = Path(imageio_ffmpeg.get_ffmpeg_exe())
PYTHON = Path(r"D:\minanconda\envs\pytorch\python.exe")
SAMPLES = Path(r"C:\Users\20693\Desktop\translation\samples")

CLIPS = [
    {
        "name": "bgm_movie_trailer.wav",
        # Christopher Nolan Inception Trailer - dialogue over Hans Zimmer dramatic BGM
        "query": "ytsearch1:Inception official trailer",
        "ss": "25",
        "t": "55",
    },
    {
        "name": "bgm_vlog_speech.wav",
        # MKBHD Studio dialogue with persistent modern BGM bed
        "query": "ytsearch1:MKBHD studio tour background music",
        "ss": "20",
        "t": "60",
    },
]

def main():
    SAMPLES.mkdir(parents=True, exist_ok=True)
    for clip in CLIPS:
        out_wav = SAMPLES / clip["name"]
        if out_wav.exists() and out_wav.stat().st_size > 100_000:
            print(f"[Skip] {out_wav.name} already exists ({out_wav.stat().st_size} bytes)")
            continue
        print(f"[Resolving Stream URL] {clip['query']}...")
        res = subprocess.run(
            [str(PYTHON), "-m", "yt_dlp", "-f", "bestaudio", "-g", clip["query"]],
            capture_output=True, text=True
        )
        url = None
        for line in res.stdout.strip().splitlines():
            line = line.strip()
            if line.startswith("http"):
                url = line
                break
        if not url:
            print(f"[Error] Failed to resolve URL: {res.stderr}")
            continue

        print(f"[Extracting via FFmpeg] -> {clip['name']} (ss={clip['ss']}, t={clip['t']})...")
        ffmpeg_cmd = [
            str(FFMPEG), "-y",
            "-ss", clip["ss"],
            "-t", clip["t"],
            "-i", url,
            "-ar", "16000",
            "-ac", "1",
            "-c:a", "pcm_s16le",
            str(out_wav)
        ]
        fres = subprocess.run(ffmpeg_cmd, capture_output=True, text=True)
        if out_wav.exists() and out_wav.stat().st_size > 100_000:
            print(f"[Success] Saved {clip['name']} ({out_wav.stat().st_size} bytes, {out_wav.stat().st_size / 32000:.1f}s)")
        else:
            print(f"[Failed] ffmpeg failed: {fres.stderr[-300:]}")

if __name__ == "__main__":
    main()
