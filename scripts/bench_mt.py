"""Quality-and-latency bench for the subtitle translators.

Cases go from easy to hard. Each run prints the translation and the median
latency after one warmup. Results are appended to logs/bench_quality.json.

The request body is built from server.translate's own build_messages and the
live sampling config, not from a copy pasted in here. The copy was the problem:
it carried hy-mt2 at temperature 0.7 with top_p/top_k/repeat_penalty while the
shipped path uses TRANSLATE_TEMPERATURE, so a bench run and a replay were
measuring two different requests and the numbers were comparable to neither.

The NLLB-600M and Opus-MT arms were removed. Both model directories were absent
from models/, so both printed their "missing" line and returned without benching
anything -- which is the failure mode AGENT.md rule 5 warns about: an empty
result read as a result.
"""

from __future__ import annotations

import json
import statistics
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from server import config  # noqa: E402
from server.translate import build_messages  # noqa: E402

OUT = ROOT / "logs" / "bench_quality.json"
RUNS = 3

# level: easy, mid, hard.
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


def _target_url(profile: str) -> str:
    return config.GEMMA_TRANSLATOR_URL if profile == "gemma4" else config.LLAMA_URL


def _llama_body(profile: str, text: str, lang: str | None) -> bytes:
    """The request the shipped path would send for this text.

    Same builder, same URL, same sampling values, so a bench row and a replay row
    measure the same request.

    Two things are deliberately absent, and both are stated rather than hidden:

      * context -- the pipeline feeds MT_CONTEXT_SENTENCES preceding lines and the
        bench has no history to offer, so its rows read slightly optimistic.
      * the model name stays LLAMA_MODEL for the gemma4 arm too. That is what
        stream_translation actually sends: each llama-server serves one model and
        ignores the field, so naming the gemma weights here would diverge from the
        shipped request for no gain.
    """
    payload = {
        "model": config.LLAMA_MODEL,
        "stream": False,
        # Not config.TRANSLATE_TEMPERATURE: both local branches overwrite it with
        # this family before sending. The config value only reaches the remote-API
        # arm, which has no such override.
        "temperature": 0.7,
        "top_p": 0.6,
        "top_k": 20,
        "repeat_penalty": 1.05,
        "max_tokens": config.TRANSLATE_MAX_TOKENS,
        "messages": build_messages(text, lang, [], profile, 0),
    }
    if profile == "gemma4":
        payload["chat_template_kwargs"] = dict(config.AUDIO_CHAT_TEMPLATE_KWARGS)
    return json.dumps(payload).encode()


def _strip_think(text: str) -> str:
    while "<think>" in text and "</think>" in text:
        start = text.find("<think>")
        end = text.find("</think>", start)
        text = text[:start] + text[end + len("</think>") :]
    return text.replace("<think>", "").strip()


def bench_llama(profile: str) -> None:
    url = _target_url(profile)
    rows = []
    for case in CASES:
        times = []
        last = ""
        body = _llama_body(profile, case["text"], case["lang"])
        for i in range(RUNS + 1):
            req = urllib.request.Request(
                url,
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
    which = sys.argv[1] if len(sys.argv) > 1 else config.MT_BACKEND
    if which not in ("hy-mt2", "gemma4"):
        raise SystemExit(f"use hy-mt2 or gemma4 (TRANSLATION_MT_BACKEND is {config.MT_BACKEND})")
    bench_llama(which)
