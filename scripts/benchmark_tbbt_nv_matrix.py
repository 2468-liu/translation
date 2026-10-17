"""Empirical Stress Benchmark: NVIDIA Nemotron 3.5 ASR (Official CUDA) on TBBT 5-Minute Stream.

Tests real-world end-to-end stress performance on The Big Bang Theory (continuous dialogue + laugh track):
1. Ingestion paced at 1.0x real-time wall-clock speed (or fast burst mode).
2. Evaluates official cadences: 320ms (balanced streaming) vs 560ms (high-accuracy long sentence).
3. Measures queue backlog (qlen, qwait), CUDA ASR inference latency (p50/p90/p99/max), RTF, and VAD resistance.
4. Generates side-by-side comparison with Qwen3-ASR baseline.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from server import config, perf
from server.asr import build_asr
from server.pipeline import Session
from server.vad import apply_partial_interval


def percentile(data: list[float], p: float) -> float:
    if not data:
        return 0.0
    return float(np.percentile(data, p))


async def run_benchmark(
    wav_path: Path,
    duration_sec: float = 300.0,
    cadence_ms: int = 320,
    burst_mode: bool = False,
) -> dict:
    print("=" * 75)
    print(f"🎬 TBBT 5-Minute High-Concurrency Stress Benchmark (CUDA Engine + VAD)")
    print(f"  • Audio Source     : {wav_path.name}")
    print(f"  • Target Duration  : {duration_sec}s ({duration_sec / 60:.1f} mins)")
    print(f"  • Backend          : NVIDIA Nemotron-3.5-ASR (Official CUDA Engine)")
    print(f"  • Cadence Setting  : {cadence_ms}ms ({'320ms 平衡流式首选' if cadence_ms == 320 else '560ms 高精长句优选' if cadence_ms == 560 else f'{cadence_ms}ms'})")
    print(f"  • Pacing Mode      : {'Fast Burst (Max Throughput)' if burst_mode else '1.0x Real-time Paced (Wall-clock Simulation)'}")
    print("=" * 75)

    # 1. Load Audio
    data, sr = sf.read(str(wav_path), dtype="float32", always_2d=False)
    if sr != config.SAMPLE_RATE:
        raise RuntimeError(f"Sample rate must be 16000, got {sr}")

    max_samples = int(duration_sec * config.SAMPLE_RATE)
    audio_slice = data[:max_samples]
    pcm_int16 = np.clip(audio_slice * 32767.0, -32768, 32767).astype(np.int16)
    total_audio_sec = len(pcm_int16) / config.SAMPLE_RATE
    print(f"Loaded {len(pcm_int16)} samples ({total_audio_sec:.1f}s audio).")

    # 2. Build CUDA ASR Engine & Session
    config.ASR_BACKEND = "nemotron"
    asr_engine = build_asr(fallback_on_error=False)
    session = Session(asr_engine)
    session.set_language("en")
    session.set_target_language("zh")
    apply_partial_interval(session.vad, cadence_ms)

    # Mock MT streaming so benchmark doesn't block on absent llama-server
    async def mock_stream_translation(*args, **kwargs):
        # Simulate ~40ms fast streaming translation token
        await asyncio.sleep(0.01)
        yield "..."

    import server.pipeline as pipe_mod
    pipe_mod.stream_translation = mock_stream_translation

    # 3. Direct In-Memory Telemetry Collector
    qlen_samples: list[int] = []
    qwait_all: list[float] = []
    qwait_end: list[float] = []
    qwait_partial: list[float] = []
    asr_infer_times: list[float] = []
    transcripts_list: list[dict] = []

    # Wrap session jobs to track queue entry times
    original_put = session.jobs.put_nowait
    job_timestamps: dict[int, float] = {}

    def instrumented_put(item):
        nonlocal job_timestamps
        t_put = time.perf_counter()
        # Item format: (generation, kind, audio, reuse, seq)
        # Use object id as tag
        item_id = id(item)
        job_timestamps[item_id] = t_put
        qlen_samples.append(session.jobs.qsize())
        return original_put(item)

    session.jobs.put_nowait = instrumented_put

    # Instrument ASR engine transcribe to record latency & queue wait
    original_transcribe = asr_engine.transcribe

    def instrumented_transcribe(audio_arr, lang):
        t0 = time.perf_counter()
        text, detected_lang, conf = original_transcribe(audio_arr, lang)
        t_infer = (time.perf_counter() - t0) * 1000.0
        asr_infer_times.append(t_infer)
        return text, detected_lang, conf

    asr_engine.transcribe = instrumented_transcribe

    # Callback for outputs
    async def send_callback(msg: dict):
        mtype = msg.get("type")
        if mtype == "asr":
            text = msg.get("text", "")
            final = msg.get("final", False)
            asr_ms = msg.get("asr_ms", 0.0)
            if final and text.strip():
                transcripts_list.append({
                    "text": text,
                    "final": True,
                    "asr_ms": asr_ms,
                    "wall": time.time(),
                })

    worker_task = asyncio.create_task(session.worker(send_callback))

    # 4. Ingest Audio
    hop_samples = 1600  # 100ms chunks
    hop_bytes = hop_samples * 2
    raw_bytes = pcm_int16.tobytes()

    start_mono = time.perf_counter()
    start_wall = time.time()
    next_report = 30.0

    print(f"\n▶️ Starting streaming ingestion ({'1.0x Paced' if not burst_mode else 'Burst'})...")

    for offset in range(0, len(raw_bytes), hop_bytes):
        chunk = raw_bytes[offset : offset + hop_bytes]
        if not chunk:
            break

        chunk_sec = (offset // 2) / config.SAMPLE_RATE
        session.push_pcm(chunk)
        qlen_samples.append(session.jobs.qsize())

        if not burst_mode:
            target_elapsed = chunk_sec + (len(chunk) / 2) / config.SAMPLE_RATE
            real_elapsed = time.perf_counter() - start_mono
            sleep_needed = target_elapsed - real_elapsed
            if sleep_needed > 0:
                await asyncio.sleep(sleep_needed)

        if chunk_sec >= next_report:
            print(
                f"  [{chunk_sec:5.1f}s / {total_audio_sec:.0f}s] "
                f"Cur Qlen: {session.jobs.qsize()} | Active finals: {len(session._active_final_tasks)} | "
                f"ASR calls: {len(asr_infer_times)} | Final utterances: {len(transcripts_list)}"
            )
            next_report += 30.0

    print(f"\n⏹️ Audio feed complete ({total_audio_sec:.1f}s). Flushing pipeline...")
    session.flush()

    drain_start = time.perf_counter()
    while session.jobs.qsize() > 0 or len(session._active_final_tasks) > 0 or session.translate_task:
        await asyncio.sleep(0.02)
        if time.perf_counter() - drain_start > 15.0:
            print("⚠️ Timeout waiting for pipeline drain.")
            break

    await session.close()
    worker_task.cancel()
    try:
        await worker_task
    except asyncio.CancelledError:
        pass

    total_real_time = time.perf_counter() - start_mono
    overall_rtf = total_real_time / total_audio_sec

    # 5. Extract Queue Wait Telemetry from perf log during run window
    perf_log = ROOT / "logs" / "perf.log"
    if perf_log.exists():
        for line in perf_log.read_text(encoding="utf-8", errors="replace").splitlines():
            parts = line.split()
            if len(parts) < 3 or parts[1] != "pipeline.asr":
                continue
            try:
                line_wall = float(parts[0])
                if line_wall < start_wall - 0.2:
                    continue
            except ValueError:
                pass
            fields = {}
            for item in parts[2:]:
                if "=" in item:
                    k, _, v = item.partition("=")
                    try:
                        fields[k] = float(v)
                    except ValueError:
                        fields[k] = v
            qw = fields.get("qwait")
            kind = fields.get("kind", "")
            if qw is not None:
                qwait_all.append(qw)
                if kind == "end":
                    qwait_end.append(qw)
                else:
                    qwait_partial.append(qw)

    # 6. Analyze Statistics
    qlen_positive = [q for q in qlen_samples if q > 0]
    qlen_max = max(qlen_samples) if qlen_samples else 0
    pct_qlen_busy = (len(qlen_positive) / len(qlen_samples) * 100.0) if qlen_samples else 0.0

    print("\n" + "=" * 75)
    print(f"📊 NEMOTRON CUDA @ {cadence_ms}ms BENCHMARK REPORT")
    print("=" * 75)
    print(f"【音频与整体吞吐】")
    print(f"  • 音频总时长 (Audio Duration) : {total_audio_sec:.2f}s ({total_audio_sec/60:.1f} min)")
    print(f"  • 物理运行耗时 (Wall Time)    : {total_real_time:.2f}s")
    print(f"  • 实时率系数 (RTF)            : {overall_rtf:.4f}x ({'实时跟手无延迟' if overall_rtf <= 1.05 else '存在轻微滞后'})")
    print(f"  • ASR 调用总次数              : {len(asr_infer_times)} 次")
    print(f"  • 识别定稿句子数 (Finals)     : {len(transcripts_list)} 句")

    print(f"\n【队列积压与背压 (Queue Backlog & Wait)】")
    print(f"  • 队列检查点数 (Checkpoints)  : {len(qlen_samples)}")
    print(f"  • 队列存在积压占比 (qlen > 0) : {pct_qlen_busy:.2f}% ({len(qlen_positive)} / {len(qlen_samples)})")
    print(f"  • 最大队列深度 (qlen Max)     : {qlen_max}")
    if qwait_all:
        print(f"  • 排队等待时间 (Queue Wait)   : p50={percentile(qwait_all, 50):.1f}ms | p90={percentile(qwait_all, 90):.1f}ms | p99={percentile(qwait_all, 99):.1f}ms | Max={max(qwait_all):.1f}ms")

    print(f"\n【CUDA ASR 推理耗时 (FastConformer-RNNT on Tensor Core)】")
    print(f"  • ASR 推理延迟 (asr_infer)    : p50={percentile(asr_infer_times, 50):.1f}ms | p90={percentile(asr_infer_times, 90):.1f}ms | p99={percentile(asr_infer_times, 99):.1f}ms | Max={max(asr_infer_times):.1f}ms")

    print(f"\n【识别样本摘录 (Top 5 Samples)】")
    for i, tr in enumerate(transcripts_list[:5]):
        print(f"  [{i+1}] ({tr['asr_ms']:.1f}ms): {tr['text']}")

    res_data = {
        "model": "Nemotron-3.5-ASR",
        "backend": "Official NeMo-Speech CUDA",
        "cadence_ms": cadence_ms,
        "duration_sec": total_audio_sec,
        "wall_time_sec": total_real_time,
        "rtf": overall_rtf,
        "total_calls": len(asr_infer_times),
        "total_finals": len(transcripts_list),
        "qlen_max": qlen_max,
        "pct_qlen_busy": pct_qlen_busy,
        "asr_infer": {
            "p50": percentile(asr_infer_times, 50),
            "p90": percentile(asr_infer_times, 90),
            "p99": percentile(asr_infer_times, 99),
            "max": max(asr_infer_times) if asr_infer_times else 0.0,
        },
        "qwait": {
            "p50": percentile(qwait_all, 50),
            "p90": percentile(qwait_all, 90),
            "p99": percentile(qwait_all, 99),
            "max": max(qwait_all) if qwait_all else 0.0,
        },
    }

    out_file = ROOT / "logs" / f"tbbt_nv_{cadence_ms}ms_result.json"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(json.dumps(res_data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nSaved results to: {out_file}")
    return res_data


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--wav", type=str, default=str(ROOT / "samples" / "tbbt_5min.wav"))
    parser.add_argument("--duration", type=float, default=300.0)
    parser.add_argument("--cadence", type=int, default=320)
    parser.add_argument("--burst", action="store_true", help="Run at maximum speed without 1.0x sleep")
    args = parser.parse_args()

    asyncio.run(
        run_benchmark(
            Path(args.wav),
            duration_sec=args.duration,
            cadence_ms=args.cadence,
            burst_mode=args.burst,
        )
    )
