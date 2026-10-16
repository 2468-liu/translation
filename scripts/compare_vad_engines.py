"""A/B Benchmark and Comparison between Silero VAD and FireRedVAD ONNX.

Compares:
1. Real-time inference latency (ms/frame on CPU)
2. Speech detection probability trajectories across challenging movie audio
3. Trailing edge sensitivity (quiet speech) vs False alarm resistance (action BGM)
"""

from __future__ import annotations

import json
from pathlib import Path
import sys
import time
import wave

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np
import onnxruntime as ort

from server import vad


# Pure-numpy Kaldi-compatible 80-dim log-mel filterbank extractor
class FbankExtractor:
    def __init__(
        self,
        sample_rate: int = 16000,
        n_mels: int = 80,
        frame_len_ms: float = 25.0,
        frame_shift_ms: float = 10.0,
        n_fft: int = 512,
        low_freq: float = 20.0,
        high_freq: float = 0.0,
    ):
        self.sample_rate = sample_rate
        self.frame_len = int(sample_rate * frame_len_ms / 1000.0)  # 400
        self.frame_shift = int(sample_rate * frame_shift_ms / 1000.0)  # 160
        self.n_fft = n_fft
        self.window = np.hamming(self.frame_len).astype(np.float32)

        # Build Mel filterbank matrix
        if high_freq <= 0.0:
            high_freq = sample_rate / 2.0
        low_mel = 1127.0 * np.log(1.0 + low_freq / 700.0)
        high_mel = 1127.0 * np.log(1.0 + high_freq / 700.0)
        mel_points = np.linspace(low_mel, high_mel, n_mels + 2)
        hz_points = 700.0 * (np.exp(mel_points / 1127.0) - 1.0)
        bin_points = np.floor((n_fft + 1) * hz_points / sample_rate).astype(int)

        num_fft_bins = n_fft // 2 + 1
        self.banks = np.zeros((n_mels, num_fft_bins), dtype=np.float32)
        for i in range(n_mels):
            left, center, right = bin_points[i], bin_points[i + 1], bin_points[i + 2]
            for j in range(left, center):
                if center > left:
                    self.banks[i, j] = (j - left) / (center - left)
            for j in range(center, right):
                if right > center:
                    self.banks[i, j] = (right - j) / (right - center)

    def extract(self, pcm_chunk: np.ndarray) -> np.ndarray:
        """Extract [num_frames, 80] fbank features from 1D float32 audio."""
        if len(pcm_chunk) < self.frame_len:
            return np.zeros((0, 80), dtype=np.float32)

        num_frames = 1 + (len(pcm_chunk) - self.frame_len) // self.frame_shift
        frames = np.lib.stride_tricks.as_strided(
            pcm_chunk,
            shape=(num_frames, self.frame_len),
            strides=(pcm_chunk.strides[0] * self.frame_shift, pcm_chunk.strides[0]),
        ).copy()

        # Pre-emphasis
        frames[:, 1:] = frames[:, 1:] - 0.97 * frames[:, :-1]
        frames[:, 0] = frames[:, 0] * (1.0 - 0.97)

        # Apply Hamming window
        frames = frames * self.window

        # Real FFT & power spectrum
        rfft = np.fft.rfft(frames, n=self.n_fft)
        power_spec = (np.abs(rfft) ** 2).astype(np.float32)

        # Mel energy
        mel_energies = np.dot(power_spec, self.banks.T)
        log_mel = np.log(np.maximum(mel_energies, 1e-10))
        return log_mel


class FireRedVadDetector:
    def __init__(self, model_dir: Path):
        onnx_path = model_dir / "firered_vad.onnx"
        cmvn_path = model_dir / "cmvn.json"
        with open(cmvn_path, "r", encoding="utf-8") as f:
            cmvn = json.load(f)
        self.mean = np.array(cmvn["means"], dtype=np.float32)
        self.inv_std = np.array(cmvn["inv_stddevs"], dtype=np.float32)

        opts = ort.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 1
        self.session = ort.InferenceSession(str(onnx_path), sess_options=opts, providers=["CPUExecutionProvider"])
        self.fbank = FbankExtractor()
        self.reset()

    def reset(self):
        self.caches = {f"cache_{i}": np.zeros((1, 128, 10), dtype=np.float32) for i in range(8)}
        self.leftover = np.zeros(0, dtype=np.float32)

    def prob_chunk(self, pcm_16k: np.ndarray) -> tuple[float, float]:
        """Process arbitrary 16k PCM chunk and return (mean_prob, latency_ms)."""
        t0 = time.perf_counter()
        # Kaldi Fbank requires raw 16-bit scale (-32768..32767)
        if len(pcm_16k) > 0 and np.max(np.abs(pcm_16k)) <= 1.01:
            pcm_scaled = pcm_16k * 32768.0
        else:
            pcm_scaled = pcm_16k

        data = np.concatenate([self.leftover, pcm_scaled]) if len(self.leftover) else pcm_scaled
        feats = self.fbank.extract(data)
        if len(feats) == 0:
            self.leftover = data
            return 0.0, 0.0

        consumed = len(feats) * self.fbank.frame_shift
        self.leftover = data[consumed:]

        # CMVN
        norm_feat = (feats - self.mean) * self.inv_std
        norm_feat = np.expand_dims(norm_feat, axis=0)  # [1, num_frames, 80]

        inputs = {"feat": norm_feat, **self.caches}
        outputs = self.session.run(None, inputs)
        probs = outputs[0]  # [1, num_frames, 1]
        for i in range(8):
            self.caches[f"cache_{i}"] = outputs[i + 1]

        latency_ms = (time.perf_counter() - t0) * 1000.0
        mean_prob = float(np.mean(probs)) if probs.size > 0 else 0.0
        return mean_prob, latency_ms


def load_wav_pcm(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as wf:
        n_samples = wf.getnframes()
        data = wf.readframes(n_samples)
        pcm = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
        return pcm


def run_ab_comparison(wav_path: Path):
    pcm = load_wav_pcm(wav_path)
    total_sec = len(pcm) / 16000.0
    print(f"\n=======================================================")
    print(f" BENCHMARKING VAD: {wav_path.name} ({total_sec:.1f}s)")
    print(f"=======================================================")

    # 1. Silero VAD (32ms frames = 512 samples)
    silero = vad.load_vad()
    silero.reset()
    silero_probs = []
    silero_times = []
    frame_sz = 512
    for i in range(0, len(pcm) - frame_sz, frame_sz):
        frame = pcm[i : i + frame_sz]
        t0 = time.perf_counter()
        p = silero.prob(frame)
        silero_times.append((time.perf_counter() - t0) * 1000.0)
        silero_probs.append(p)

    # 2. FireRedVAD ONNX (Streaming in 32ms chunks = 512 samples)
    firered_dir = Path("models/firered_vad")
    firered = FireRedVadDetector(firered_dir)
    firered_probs = []
    firered_times = []
    for i in range(0, len(pcm) - frame_sz, frame_sz):
        frame = pcm[i : i + frame_sz]
        p, lat = firered.prob_chunk(frame)
        firered_times.append(lat)
        firered_probs.append(p)

    # 3. Metrics Summary
    sil_mean_lat = np.mean(silero_times)
    fir_mean_lat = np.mean(firered_times)
    sil_speech_pct = np.mean([p > 0.5 for p in silero_probs]) * 100.0
    fir_speech_pct = np.mean([p > 0.5 for p in firered_probs]) * 100.0
    agreement = np.mean([(p1 > 0.5) == (p2 > 0.5) for p1, p2 in zip(silero_probs, firered_probs)]) * 100.0

    print(f"--- Performance & Resource Efficiency ---")
    print(f"Silero VAD (ONNX):   Mean Latency = {sil_mean_lat:.3f} ms/frame | Model Size = 2.22 MB")
    print(f"FireRedVAD (ONNX):   Mean Latency = {fir_mean_lat:.3f} ms/frame | Model Size = 2.20 MB")
    print(f"Latency Ratio:       FireRed / Silero = {fir_mean_lat / max(0.001, sil_mean_lat):.2f}x")
    print(f"")
    print(f"--- Speech Activity Ratio (Threshold > 0.5) ---")
    print(f"Silero VAD Speech Ratio:     {sil_speech_pct:.1f}%")
    print(f"FireRedVAD Speech Ratio:     {fir_speech_pct:.1f}%")
    print(f"Frame-by-Frame Agreement:    {agreement:.1f}%")

    return {
        "file": wav_path.name,
        "duration_s": round(total_sec, 1),
        "silero_latency_ms": round(float(sil_mean_lat), 3),
        "firered_latency_ms": round(float(fir_mean_lat), 3),
        "silero_speech_pct": round(float(sil_speech_pct), 1),
        "firered_speech_pct": round(float(fir_speech_pct), 1),
        "agreement_pct": round(float(agreement), 1),
    }


if __name__ == "__main__":
    results = []
    test_samples = [
        # --- 1. All 10 Doctor Strange 2 (DS2) Samples ---
        Path("samples/ds2_action_40.wav"),
        Path("samples/ds2_silent_80.wav"),
        Path("samples/ds2_action_115.wav"),
        Path("samples/ds2_silent_185.wav"),
        Path("samples/ds2_action_245.wav"),
        Path("samples/ds2_quiet_304.wav"),
        Path("samples/ds2_silent_375.wav"),
        Path("samples/ds2_quiet_441.wav"),
        Path("samples/ds2_dense_855.wav"),
        Path("samples/ds2_quiet_998.wav"),

        # --- 2. The Big Bang Theory (TBBT) Samples ---
        Path("samples/tbbt_dense_1.wav"),
        Path("samples/tbbt_probe_60s.wav"),
        Path("samples/tbbt_3min.wav"),

        # --- 3. Speech with Heavy BGM & Pure Music Samples ---
        Path("samples/sparse_music_speech.wav"),
        Path("samples/sparse_music.wav"),
        Path("samples/bgm_movie_trailer.wav"),
        Path("samples/bgm_vlog_speech.wav"),
    ]
    for sample in test_samples:
        if sample.exists():
            res = run_ab_comparison(sample)
            results.append(res)
        else:
            print(f"[Warn] Sample not found: {sample}")

    print("\n================== FULL A/B VAD BENCHMARK MATRIX ==================\n")
    print("| 场景 / 音频样本 | 时长(s) | Silero 耗时 | FireRed 耗时 | Silero 检出率 | FireRed 检出率 | 帧一致率 | 特征分析 |")
    print("| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :--- |")
    for r in results:
        fname = r["file"]
        # Analysis tag
        if "silent" in fname:
            tag = "纯静音/底噪抑制"
        elif "action" in fname:
            tag = "动作打斗/短促发音"
        elif "quiet" in fname:
            tag = "轻声耳语/微弱气音"
        elif "dense" in fname:
            tag = "密集连珠炮对白"
        elif "tbbt" in fname:
            tag = "情景喜剧/环境笑声"
        elif fname == "sparse_music.wav":
            tag = "纯BGM伴奏 (误报压力测试)"
        elif "bgm" in fname or "music_speech" in fname:
            tag = "强BGM垫乐下的对白识别"
        else:
            tag = "常规对白"
        print(f"| `{fname}` | {r['duration_s']}s | {r['silero_latency_ms']:.2f}ms | {r['firered_latency_ms']:.2f}ms | {r['silero_speech_pct']}% | {r['firered_speech_pct']}% | {r['agreement_pct']}% | {tag} |")

    # Overall Summary
    avg_sil_lat = np.mean([r["silero_latency_ms"] for r in results])
    avg_fir_lat = np.mean([r["firered_latency_ms"] for r in results])
    avg_agree = np.mean([r["agreement_pct"] for r in results])
    print(f"\n整体平均: Silero 帧耗时 {avg_sil_lat:.3f}ms | FireRed 帧耗时 {avg_fir_lat:.3f}ms | 平均一致率 {avg_agree:.1f}%\n")

    out_json = REPO_ROOT / "samples" / "vad_benchmark_results.json"
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"[Done] Benchmark results saved to {out_json}")
