"""Quality-and-latency bench for the subtitle translators.

Cases go from easy to hard. Each run prints the translation and the median
latency after one warmup. Results are appended to logs/bench_quality.json.
"""

from __future__ import annotations

import json
import statistics
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "logs" / "bench_quality.json"
RUNS = 3

# level: easy, mid, hard. lang limits Opus-MT to English.
CASES = [
    {"id": "en-hello", "level": "easy", "lang": "en", "text": "Hello."},
    {"id": "en-subtitle", "level": "easy", "lang": "en", "text": "This is a live subtitle test."},
    {"id": "en-colloquial", "level": "mid", "lang": "en", "text": "It's gonna be a long night, isn't it?"},
    {"id": "en-names", "level": "mid", "lang": "en", "text": "NASA launched Artemis II on April 3rd, 2026."},
    {"id": "en-negation", "level": "mid", "lang": "en", "text": "Don't turn off the lights until everyone leaves."},
    {"id": "en-partial", "level": "hard", "lang": "en", "text": "so the thing is we should probably"},
    {"id": "en-compound", "level": "hard", "lang": "en", "text": "If the train is late and the rain doesn't stop, we'll miss the opening and have to watch it online."},
    {"id": "ja-easy", "level": "easy", "lang": "ja", "text": "これは字幕のテストです。"},
    {"id": "ja-negation", "level": "hard", "lang": "ja", "text": "彼はまだ来ていないから、先に始めないで。"},
    {"id": "ko-easy", "level": "easy", "lang": "ko", "text": "안녕하세요, 자막 테스트입니다."},
    {"id": "ko-negation", "level": "hard", "lang": "ko", "text": "아직 시작하지 마세요. 사람이 다 안 왔어요."},
    {
        "id": "en-caption",
        "level": "long",
        "lang": "en",
        "text": "If you look closely, the crack started on the left side of the ice and then ran all the way across the frame before the camera cut away.",
    },
    {
        "id": "en-narration",
        "level": "long",
        "lang": "en",
        "text": "What you're looking at is the same glacier I filmed five years ago, except the ice that used to reach the ridge is gone, and the lake below it has doubled in size.",
    },
    {
        "id": "en-dialogue",
        "level": "long",
        "lang": "en",
        "text": "I kept telling them we should leave before dark, but nobody listened, so now we're stuck on this road with no signal and the gas light has been on for twenty minutes.",
    },
    {
        "id": "en-long-partial",
        "level": "long",
        "lang": "en",
        "text": "the reason this matters is that once the valve opens and the pressure drops below the red line",
    },
    {
        "id": "ja-long",
        "level": "long",
        "lang": "ja",
        "text": "五年前にここで撮った氷河は尾根まで届いていたんですが、今はその氷がもうなくて、下の湖だけが倍の大きさになっています。",
    },
    {
        "id": "ko-long",
        "level": "long",
        "lang": "ko",
        "text": "오 년 전 여기서 찍은 빙하는 능선까지 닿아 있었는데, 지금은 그 얼음이 사라지고 아래 호수만 두 배로 커졌습니다.",
    },
]


def median_ms(samples: list[float]) -> float:
    return statistics.median(samples)


def save(engine: str, rows: list[dict]) -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    blob = {"engines": {}}
    if OUT.exists():
        try:
            blob = json.loads(OUT.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            blob = {"engines": {}}
    blob.setdefault("engines", {})[engine] = rows
    blob["cases"] = CASES
    OUT.write_text(json.dumps(blob, ensure_ascii=False, indent=2), encoding="utf-8")


def report(engine: str, rows: list[dict]) -> None:
    print(f"\n== {engine} ==")
    for row in rows:
        shown = "skip" if row["ms"] is None else f"{row['ms']:7.0f} ms"
        print(f"{shown}  [{row['level']}/{row['lang']}] {row['text']} -> {row['out']}")
    save(engine, rows)


def bench_nllb() -> None:
    import sys

    sys.path.insert(0, str(ROOT))
    from server.mt import MtEngine

    engine = MtEngine()
    engine.load()
    rows = []
    if not engine.ready:
        print("NLLB not ready")
        return
    for case in CASES:
        times = []
        last = ""
        for i in range(RUNS + 1):
            t0 = time.perf_counter()
            out = ""
            for piece in engine.stream(case["text"], case["lang"]):
                out = piece
            dt = (time.perf_counter() - t0) * 1000
            last = out
            if i:
                times.append(dt)
        rows.append({**case, "ms": round(median_ms(times)), "out": last})
    report("nllb", rows)


def bench_opus() -> None:
    import ctranslate2
    import sentencepiece as spm

    model = ROOT / "models" / "opus-mt-en-zh-ct2"
    src = ROOT / "models" / "opus-mt-en-zh"
    if not (model / "model.bin").exists():
        print("Opus-MT missing")
        return
    translator = ctranslate2.Translator(str(model), device="cuda", compute_type="int8")
    src_sp = spm.SentencePieceProcessor(model_file=str(model / "source.spm" if (model / "source.spm").exists() else src / "source.spm"))
    tgt_sp = spm.SentencePieceProcessor(model_file=str(model / "target.spm" if (model / "target.spm").exists() else src / "target.spm"))
    rows = []
    for case in CASES:
        if case["lang"] != "en":
            rows.append({**case, "ms": None, "out": ""})
            continue
        times = []
        last = ""
        for i in range(RUNS + 1):
            tokens = src_sp.encode(case["text"], out_type=str)
            t0 = time.perf_counter()
            result = translator.translate_batch([tokens], beam_size=1, max_decoding_length=256)
            hyp = [tok for tok in result[0].hypotheses[0] if tok not in ("</s>", "<unk>")]
            last = tgt_sp.decode(hyp).strip()
            dt = (time.perf_counter() - t0) * 1000
            if i:
                times.append(dt)
        rows.append({**case, "ms": round(median_ms(times)), "out": last})
    report("opus", rows)


def _llama_body(profile: str, text: str) -> bytes:
    if profile == "qwen":
        payload = {
            "model": "qwen3.5-4b",
            "stream": False,
            "temperature": 0.2,
            "max_tokens": 256,
            "messages": [
                {"role": "system", "content": "你只输出简体中文译文，不要解释，不要原样返回原文。"},
                {
                    "role": "user",
                    "content": "把下面的字幕译成简体中文。只输出译文。句子不完整就按现有内容翻译，不要补写。\n\n" + text,
                },
            ],
            "chat_template_kwargs": {"enable_thinking": False},
        }
    else:
        payload = {
            "model": "hy-mt2-1.8b",
            "stream": False,
            "temperature": 0.7,
            "top_p": 0.6,
            "top_k": 20,
            "repeat_penalty": 1.05,
            "max_tokens": 256,
            "messages": [
                {
                    "role": "user",
                    "content": "将以下文本翻译为中文，注意只需要输出翻译后的结果，不要额外解释：\n\n" + text,
                }
            ],
        }
    return json.dumps(payload).encode()


def _strip_think(text: str) -> str:
    while "<think>" in text and "</think>" in text:
        start = text.find("<think>")
        end = text.find("</think>", start)
        text = text[:start] + text[end + len("</think>") :]
    return text.replace("<think>", "").strip()


def bench_llama(profile: str) -> None:
    rows = []
    for case in CASES:
        times = []
        last = ""
        body = _llama_body(profile, case["text"])
        for i in range(RUNS + 1):
            req = urllib.request.Request(
                "http://127.0.0.1:8080/v1/chat/completions",
                data=body,
                headers={"Content-Type": "application/json"},
            )
            t0 = time.perf_counter()
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode())
            dt = (time.perf_counter() - t0) * 1000
            last = _strip_think(data["choices"][0]["message"]["content"])
            if i:
                times.append(dt)
        rows.append({**case, "ms": round(median_ms(times)), "out": last})
    report(profile, rows)


if __name__ == "__main__":
    import sys

    which = sys.argv[1] if len(sys.argv) > 1 else "nllb"
    if which == "nllb":
        bench_nllb()
    elif which == "opus":
        bench_opus()
    elif which in ("hymt", "qwen"):
        bench_llama(which)
    else:
        raise SystemExit("use nllb, opus, hymt, or qwen")
