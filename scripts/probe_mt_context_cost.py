"""What does injected context cost when the prefix is NOT cached?

The context probe runs every arm over the same sentence set, which means each
arm's requests share a long cached prefix -- llama-server prefills only the new
tokens and reports prompt_n in single digits. That is the right way to compare
two arms against each other and the wrong way to estimate what the live pipeline
pays, because there each utterance is a new sentence with a new trailing context
and little to reuse.

So this measures the other case on purpose: a long list of unrelated sentences,
no shared prefix, cold cache. Same request shape as the pipeline, one variable
(the context count), and the number that matters is prompt_ms -- the prefill --
because that is the part context adds to.

Usage:
    conda activate pytorch
    python scripts/probe_mt_context_cost.py --samples 0 2 6
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from server import config  # noqa: E402
from server.translate import build_messages  # noqa: E402

# Mutually unrelated lines, deliberately: nothing here shares a prefix with
# anything else, so llama-server cannot answer any of them from its prompt cache.
# They are the shape of a real conversation's turns, not of a repeated probe.
SENTENCES = [
    "The ferry leaves the harbour at six, so we should eat before then.",
    "She counted the crates twice and still came up one short.",
    "Nobody had told the night shift about the change of plan.",
    "A thin layer of ice had formed on the inside of the window.",
    "He kept the receipt folded in the back of his passport.",
    "The signal dropped just as the recording started, which ruined it.",
    "We walked the last kilometre without saying anything at all.",
    "The generator needs oil every forty hours, not every week.",
    "Her flight was moved to a gate at the far end of the terminal.",
    "The dog would not cross the bridge until someone went first.",
    "By the time the rain reached us, the tarpaulin was already up.",
    "He learned the whole route by heart before the maps arrived.",
    "The kitchen smelled of bread and something faintly metallic.",
    "They agreed to meet at the same bench as the year before.",
    "A single lamp was left on above the stairs all night.",
    "The engine turned over twice and then caught properly.",
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", default="gemma4", choices=["hy-mt2", "gemma4"])
    ap.add_argument("--samples", default="0,2,6")
    ap.add_argument("--url", default="")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    url = args.url or (config.GEMMA_TRANSLATOR_URL if args.backend == "gemma4" else config.LLAMA_URL)
    arms = [int(x) for x in str(args.samples).split(",") if x.strip() != ""]

    print(f"backend {args.backend}  {url}")
    print("cold prefix by construction: 16 unrelated lines, no shared context\n")
    results: dict[str, dict] = {}

    with httpx.Client() as client:
        try:
            client.get(url.replace("/chat/completions", "/models"), timeout=10.0).raise_for_status()
        except Exception as exc:
            raise SystemExit(f"no llama-server at {url}: {exc}")

        for sentences in arms:
            history: list[tuple[str, str]] = []
            rows = []
            for text in SENTENCES:
                messages = build_messages(text, "en", history, args.backend, sentences)
                started = httpx.Timeout(180.0)
                payload = {
                    "model": "local",
                    "messages": messages,
                    "temperature": 0,
                    "max_tokens": config.TRANSLATE_MAX_TOKENS,
                    "stream": False,
                    "cache_prompt": True,
                }
                response = client.post(url, json=payload, timeout=started)
                response.raise_for_status()
                data = response.json()
                timings = data.get("timings") or {}
                out = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
                rows.append({
                    "prompt_n": timings.get("prompt_n"),
                    "cache_n": timings.get("cache_n"),
                    "prompt_ms": timings.get("prompt_ms"),
                    "predicted_n": timings.get("predicted_n"),
                    "predicted_ms": timings.get("predicted_ms"),
                    "text": (out or "").strip(),
                })
                if out:
                    history.append((text, out.strip()))
            prompts = [r["prompt_ms"] for r in rows if isinstance(r["prompt_ms"], (int, float))]
            tokens = [r["prompt_n"] for r in rows if isinstance(r["prompt_n"], (int, float))]
            decoded = [r["predicted_ms"] for r in rows if isinstance(r["predicted_ms"], (int, float))]
            results[str(sentences)] = {
                "median_prompt_ms": round(statistics.median(prompts), 1),
                "max_prompt_ms": round(max(prompts), 1),
                "median_prompt_n": statistics.median(tokens) if tokens else None,
                "median_predicted_ms": round(statistics.median(decoded), 1) if decoded else None,
                "cache_hits": sum(1 for r in rows if (r.get("cache_n") or 0) > 0),
                "rows": rows,
            }
            r = results[str(sentences)]
            print(f"-- context = {sentences}")
            print(f"   prefill   median {r['median_prompt_ms']} ms   max {r['max_prompt_ms']} ms"
                  f"   (median prompt_n {r['median_prompt_n']})")
            print(f"   decode    median {r['median_predicted_ms']} ms")
            print(f"   cache hits {r['cache_hits']}/{len(rows)}"
                  f"   {'<-- cached, context cost hidden' if r['cache_hits'] else ''}")
            print()

    base = results.get("0")
    if base:
        print("context cost, against the 0 arm (same sentences, same order):")
        for key, r in results.items():
            if key == "0":
                continue
            d = r["median_prompt_ms"] - base["median_prompt_ms"]
            print(f"   ctx={key}: prefill {d:+.1f} ms ({r['median_prompt_ms']} vs {base['median_prompt_ms']}),"
                  f" decode {r['median_predicted_ms'] - base['median_predicted_ms']:+.1f} ms")

    if args.out:
        Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
